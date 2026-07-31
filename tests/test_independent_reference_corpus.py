"""Accuracy against an INDEPENDENTLY-SOURCED reference corpus (NASA/JPL Horizons).

WHY THIS EXISTS ALONGSIDE tests/test_swiss_ephemeris.py
-------------------------------------------------------
``swiss_ephemeris_goldens.json`` is recorded by running this engine and writing
down what it said. That detects the engine's numbers *moving*, which is valuable,
but it cannot detect that they were wrong the day they were recorded: expected and
observed come from the same code, so the file proves self-consistency and nothing
more. Re-recording a regression makes it disappear.

``independent_reference_corpus.json`` is different in exactly one respect that
matters: **every expected value in it came from somewhere else.** The planetary
longitudes and the lunar node are fetched from NASA/JPL Horizons — the reference
ephemeris (DE441) evaluated by JPL's own code on JPL's own servers, entirely
outside this repository's dependency set. See ``ci/fetch_reference_corpus.py`` for
the queries, and the ``provenance`` block inside the corpus for the per-field
source, recorded verbatim with the query URLs.

WHAT EACH LAYER PROVES — AND WHAT IT DOES NOT
---------------------------------------------
The layers have deliberately different tolerances, because they compare at
deliberately different points in the pipeline. Every tolerance below was *measured*
before it was written down; none was chosen to make a test pass.

L1  ``test_apparent_longitudes_match_jpl`` — 1 arcsec.
    swisseph's apparent geocentric longitude, requested through the engine's own
    declared body map (``utils._GRAHA_SWE_BODY``), against Horizons' ObsEcLon.
    Same frame, same corrections on both sides, so this is a like-for-like
    comparison and it is TIGHT: measured worst case 0.2368 arcsec across the whole
    corpus. This is the layer that pins the shipped Swiss data files, the pinned
    pyswisseph, the Julian-Day transport, and — because it goes through
    ``_GRAHA_SWE_BODY`` — the body identity of all seven visible grahas. The
    planet-id conflation that CLAUDE.md records (index 7 returning Uranus at
    40.7495 deg where the true node stood at 305.5916 deg) fails here by degrees.

L2  ``test_served_sidereal_longitudes_match_jpl`` — 75 arcsec.
    The value a caller actually receives, through the single sanctioned boundary
    ``utils.graha_sidereal_longitude``, recomposed to tropical by adding the
    ayanamsa, against the same Horizons value. This is LOOSER than L1 by two
    orders of magnitude, and the reason is a CONVENTION difference, not error:
    pyjhora's ``PLANET_FLAGS`` (measured: 65810 = FLG_SWIEPH | FLG_TRUEPOS |
    FLG_SPEED | FLG_SIDEREAL) sets **FLG_TRUEPOS**, so the served positions are
    geometric — no light-time, no aberration — while Horizons' ObsEcLon is
    apparent. Measured decomposition of the residual across this corpus:
    geometric-vs-apparent alone 50.8162 arcsec, nutation handling alone 15.4169
    arcsec, both together 61.0390 arcsec. The tolerance is that measured envelope
    plus a modest margin. It is NOT a claim that the engine is only good to 75
    arcsec — L1 shows it is good to 0.24 arcsec — and the gap is tracked as
    follow-up work, not accepted as an error budget.

L3  ``test_declared_lunar_node_matches_jpl`` — 600 arcsec, with a proven margin.
    The lunar node is the graha with the worst exposure in this engine (CLAUDE.md:
    up to 1.981465100 deg between the true and mean models) and the one a
    sign-level or self-recorded golden cannot police. Horizons publishes the Moon's
    osculating ascending node (``OM``) in the **ecliptic of J2000.0** only, so the
    comparison is made in that frame. The 600 arcsec tolerance is dominated by a
    frame-definition difference: swisseph's node is the intersection of the orbit
    with the ecliptic OF DATE, rotated into J2000 coordinates, while Horizons'
    ``OM`` is the node with respect to the J2000 ecliptic itself, and the ecliptic
    plane moves. Measured residual across the corpus: 0.4403 arcsec at J2000
    rising to 330.5685 arcsec at the 1936 epoch, tracking distance from J2000 as
    that explanation predicts.
    A loose tolerance is only worth having if it still discriminates, so
    ``test_the_node_assertion_can_tell_the_two_models_apart`` PROVES the margin
    rather than asserting it: the mean node misses Horizons by 2280.0964 to
    6930.1893 arcsec, at least 3.8x outside the tolerance at every epoch. Switching
    ``LUNAR_NODE_MODEL`` therefore fails this suite against JPL, not merely against
    our own recorded numbers.

WHAT THE CORPUS DELIBERATELY DOES NOT COVER
-------------------------------------------
* **The ayanamsa's absolute zero point.** The Lahiri/Chitrapaksha sidereal origin
  is an astrological convention fixed by the Indian Calendar Reform Committee, not
  an astronomical observable, and no solar-system ephemeris publishes it — Horizons
  cannot supply an expected value for it at any price. ``ayanamsa_deg`` is recorded
  from the engine and labelled ``independent_of_this_engine: false`` in the corpus.
  ``test_ayanamsa_is_pinned_but_not_independently_sourced`` pins it as a regression
  value and says so in its own name; pinning it to a published authority is
  follow-up work, recorded in the PR.
* **Dasha boundaries and muhurta.** Not astronomy any solar-system ephemeris
  publishes, so Horizons cannot supply expected values for them. Out of scope here,
  and — importantly — NOT claimed anywhere either: a gate that advertises coverage
  it does not have is worse than one that admits the gap.
* **Houses, vargas, yogas, strengths.** Derived quantities. Their inputs are what
  this corpus pins.

FAIL-CLOSED
-----------
L1, L2 and L3 are gated on the ``swiss_ephemeris`` fixture and so hard-FAIL rather
than skip under ``REQUIRE_SWISS_EPHEMERIS=1``.

This module is deliberately NOT advertised as a Swiss-data detector. An earlier
draft of it claimed to be a third one (after the retflag probe and the 1e-6
goldens) and the claim was FALSE: measured, the Moshier fallback matches these
Horizons values to 1.8437 arcsec worst case for the seven visible grahas, and to
~0.3 arcsec for six of the seven — the 1.8437 is the Moon alone. Moshier is a good
analytical ephemeris for the planets over this corpus's date range; the divergence
that makes the 1e-6 goldens a detector does not exist at arcsecond scale here.
What IS pinned is that the L1 tolerance stays below that divergence, so nobody can
loosen L1 to the point where it would pass on the fallback —
``test_l1_tolerance_stays_below_the_moshier_divergence`` guards the constant, and
its docstring states the (thin, 1.8x) margin honestly rather than implying a
robustness the numbers do not support.
"""
from __future__ import annotations

import json
import math
import pathlib

import pytest
import swisseph as swe

from bphs_core import utils

_CORPUS_PATH = (
    pathlib.Path(__file__).resolve().parent / "goldens" / "independent_reference_corpus.json"
)

# --- tolerances: every one measured before it was written (see module docstring) ---
_APPARENT_TOLERANCE_ARCSEC = 1.0      # measured worst case 0.2368 (4.2x margin)
_SERVED_TOLERANCE_ARCSEC = 75.0       # measured worst case 61.0390 (convention-dominated)
_NODE_TOLERANCE_ARCSEC = 600.0        # measured worst case 330.5685 (frame-dominated)
_AYANAMSA_TOLERANCE_DEG = 1e-6        # regression pin only; NOT independently sourced

_VISIBLE_GRAHAS = ("Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn")


def _corpus() -> dict:
    assert _CORPUS_PATH.is_file(), (
        f"independent reference corpus missing at {_CORPUS_PATH}. Re-record it from "
        "NASA/JPL Horizons with:\n    python ci/fetch_reference_corpus.py"
    )
    return json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))


def _arcsec_apart(first: float, second: float) -> float:
    """Absolute angular separation in arcseconds, correct across the 0/360 seam."""
    return abs(((first - second + 180.0) % 360.0) - 180.0) * 3600.0


# ---------------------------------------------------------------------------
# The corpus file itself
# ---------------------------------------------------------------------------


def test_corpus_is_well_formed_and_declares_its_provenance() -> None:
    """A corpus that cannot say where its numbers came from is not a corpus.

    Runs under BOTH ephemeris runtimes — it touches no ephemeris — so a gutted or
    silently re-sourced corpus file is caught even on the Moshier job.
    """
    raw = _CORPUS_PATH.read_bytes()
    assert b"\r" not in raw, "corpus must be LF-only"
    corpus = json.loads(raw.decode("utf-8"))

    provenance = corpus["provenance"]
    fields = provenance["fields"]

    # The two fields that carry the accuracy claim MUST be externally sourced, and
    # must say so. If a future edit re-records either of them from this engine, the
    # corpus stops being independent and this fails.
    for field in ("tropical_apparent_longitude_deg", "moon_osculating_node_j2000_deg"):
        assert fields[field]["source"] == "NASA/JPL Horizons", (
            f"{field} must be sourced from NASA/JPL Horizons, not {fields[field]['source']!r} "
            "— an expected value produced by the code under test proves only "
            "self-consistency, which is what tests/test_swiss_ephemeris.py already does"
        )
        assert fields[field]["independent_of_this_engine"] is True

    # And the one field that is NOT independent must keep admitting it.
    assert fields["ayanamsa_deg"]["independent_of_this_engine"] is False, (
        "ayanamsa_deg is computed by this engine and must not be advertised as an "
        "independently-sourced expected value"
    )

    for field in ("tropical_apparent_longitude_deg", "moon_osculating_node_j2000_deg"):
        url = provenance["horizons_queries"].get(field) or provenance["horizons_queries"].get(
            f"{field}.Sun"
        )
        assert url and url.startswith("https://ssd.jpl.nasa.gov/api/horizons.api"), (
            f"no recorded Horizons query URL for {field} — provenance must be "
            "reproducible, not merely asserted"
        )

    epochs = corpus["epochs"]
    assert len(epochs) >= 6, f"corpus has only {len(epochs)} epochs"
    seen: set[str] = set()
    for epoch in epochs:
        assert epoch["id"] not in seen, f"duplicate epoch id {epoch['id']!r}"
        seen.add(epoch["id"])
        assert epoch["why"].strip(), f"{epoch['id']}: every epoch must state why it is here"
        assert set(epoch["tropical_apparent_longitude_deg"]) == set(_VISIBLE_GRAHAS), (
            f"{epoch['id']}: pinned bodies changed — a graha silently dropped out"
        )
        for graha, longitude in epoch["tropical_apparent_longitude_deg"].items():
            assert 0.0 <= longitude < 360.0, f"{epoch['id']}.{graha}: {longitude} out of range"
        assert 0.0 <= epoch["moon_osculating_node_j2000_deg"] < 360.0
        assert epoch["jd_tdb"] > epoch["jd_ut"], (
            f"{epoch['id']}: jd_tdb must lead jd_ut by Delta-T"
        )


def test_the_corpus_covers_all_nine_grahas() -> None:
    """BPHS uses nine grahas; a corpus pinning seven is incomplete by definition.

    The nodes are not bodies Horizons knows, so they are pinned by a different
    field (the Moon's osculating node) rather than by a longitude column. This test
    exists so that difference can never quietly become an omission.
    """
    corpus = _corpus()
    for epoch in corpus["epochs"]:
        pinned = set(epoch["tropical_apparent_longitude_deg"])
        assert pinned == set(_VISIBLE_GRAHAS)
        assert isinstance(epoch["moon_osculating_node_j2000_deg"], float), (
            f"{epoch['id']}: no independent node value — Rahu and Ketu would be unpinned"
        )
    covered = set(_VISIBLE_GRAHAS) | {"Rahu", "Ketu"}
    assert covered == set(utils.PLANETS), (
        f"corpus covers {sorted(covered)} but the engine serves {sorted(utils.PLANETS)}"
    )


# ---------------------------------------------------------------------------
# L1 — apparent longitudes, tight, external
# ---------------------------------------------------------------------------


def test_apparent_longitudes_match_jpl(swiss_ephemeris: int) -> None:
    """Seven visible grahas vs JPL Horizons, like-for-like, at 2 arcsec.

    Requested through ``utils._GRAHA_SWE_BODY`` — the engine's own declared graha
    -> swisseph body map — so this fails by DEGREES if the two planet-id spaces
    are ever conflated again.
    """
    corpus = _corpus()
    failures: list[str] = []
    worst = 0.0

    for epoch in corpus["epochs"]:
        for graha in _VISIBLE_GRAHAS:
            body = utils._GRAHA_SWE_BODY[graha]
            with utils.EPHEMERIS_LOCK:
                utils._ensure_thread_ephemeris_state()
                values, retflag = swe.calc_ut(epoch["jd_ut"], body, swe.FLG_SWIEPH)
            assert retflag & swe.FLG_SWIEPH and not retflag & swe.FLG_MOSEPH, (
                f"{epoch['id']}.{graha}: retflag {retflag} — this value did not come "
                "from the Swiss data files"
            )
            expected = epoch["tropical_apparent_longitude_deg"][graha]
            delta = _arcsec_apart(values[0], expected)
            worst = max(worst, delta)
            if delta > _APPARENT_TOLERANCE_ARCSEC:
                failures.append(
                    f"  {epoch['id']}.{graha}: engine {values[0]:.7f} vs JPL {expected:.7f} "
                    f"-> {delta:.4f} arcsec > {_APPARENT_TOLERANCE_ARCSEC}"
                )

    assert not failures, (
        "apparent geocentric longitudes disagree with NASA/JPL Horizons.\n"
        + "\n".join(failures)
        + "\n\nThis is an accuracy defect against an external authority, not a golden "
        "that drifted. Re-recording the corpus does NOT fix it — re-record only when "
        "a deliberate dependency or data bump explains the move:\n"
        "    python ci/fetch_reference_corpus.py"
    )
    assert worst > 0.0, (
        "every body matched JPL to the last bit, which cannot happen across two "
        "independent ephemeris evaluations — the comparison is not doing anything"
    )


# ---------------------------------------------------------------------------
# L2 — the value a caller actually receives
# ---------------------------------------------------------------------------


def test_served_sidereal_longitudes_match_jpl(swiss_ephemeris: int) -> None:
    """The served value, through the sanctioned boundary, recomposed against JPL.

    Looser than L1 by a measured CONVENTION offset, not by error — see the module
    docstring. What it still catches: a wrong ayanamsa (every graha shifts
    together), a broken Julian-Day transport, a sidereal/tropical mix-up, and any
    body served from the wrong id space.
    """
    corpus = _corpus()
    failures: list[str] = []

    for epoch in corpus["epochs"]:
        ayanamsa = epoch["ayanamsa_deg"]
        for graha in _VISIBLE_GRAHAS:
            served = utils.graha_sidereal_longitude(epoch["jd_ut"], graha)
            expected = epoch["tropical_apparent_longitude_deg"][graha]
            delta = _arcsec_apart(served + ayanamsa, expected)
            if delta > _SERVED_TOLERANCE_ARCSEC:
                failures.append(
                    f"  {epoch['id']}.{graha}: served {served:.7f} + ayanamsa "
                    f"{ayanamsa:.7f} vs JPL {expected:.7f} -> {delta:.4f} arcsec "
                    f"> {_SERVED_TOLERANCE_ARCSEC}"
                )

    assert not failures, (
        "the SERVED sidereal longitudes disagree with NASA/JPL Horizons beyond the "
        "measured convention envelope.\n" + "\n".join(failures)
    )


def test_ketu_is_exactly_opposite_rahu(swiss_ephemeris: int) -> None:
    """Ketu is served as exactly Rahu + 180 deg on the declared node model.

    Not a JPL fact — a BPHS modelling declaration (``utils.graha_sidereal_longitude``
    computes it that way). Pinned here so the nodes are never half-covered.
    """
    corpus = _corpus()
    for epoch in corpus["epochs"]:
        rahu = utils.graha_sidereal_longitude(epoch["jd_ut"], "Rahu")
        ketu = utils.graha_sidereal_longitude(epoch["jd_ut"], "Ketu")
        assert math.isclose((rahu + 180.0) % 360.0, ketu, abs_tol=1e-9), (
            f"{epoch['id']}: Ketu {ketu} is not exactly opposite Rahu {rahu}"
        )


# ---------------------------------------------------------------------------
# L3 — the lunar node, against JPL, in the frame JPL publishes it
# ---------------------------------------------------------------------------


def _declared_node_j2000(jd_tdb: float, body: int) -> float:
    with utils.EPHEMERIS_LOCK:
        utils._ensure_thread_ephemeris_state()
        values, _retflag = swe.calc(jd_tdb, body, swe.FLG_SWIEPH | swe.FLG_J2000)
    return float(values[0])


def test_declared_lunar_node_matches_jpl(swiss_ephemeris: int) -> None:
    """The engine's DECLARED node body against the Moon's osculating node at JPL.

    ``utils.LUNAR_NODE_BODY`` is the declaration; ``tests/test_lunar_node_model.py``
    separately proves the served chart uses that same body. So this test carries
    JPL's number all the way to what a caller receives, without either test having
    to duplicate the other's job.
    """
    corpus = _corpus()
    failures: list[str] = []

    for epoch in corpus["epochs"]:
        observed = _declared_node_j2000(epoch["jd_tdb"], utils.LUNAR_NODE_BODY)
        expected = epoch["moon_osculating_node_j2000_deg"]
        delta = _arcsec_apart(observed, expected)
        if delta > _NODE_TOLERANCE_ARCSEC:
            failures.append(
                f"  {epoch['id']}: declared {utils.LUNAR_NODE_MODEL} node "
                f"{observed:.7f} vs JPL osculating OM {expected:.7f} -> "
                f"{delta:.4f} arcsec > {_NODE_TOLERANCE_ARCSEC}"
            )

    assert not failures, (
        f"the declared lunar node model ({utils.LUNAR_NODE_MODEL!r}, swisseph body "
        f"{utils.LUNAR_NODE_BODY}) disagrees with the Moon's osculating node as "
        "published by NASA/JPL Horizons.\n" + "\n".join(failures) + "\n\n"
        "Rahu and Ketu are two of the nine grahas and carry the largest accuracy "
        "exposure in this engine; this is an accuracy defect, not a golden drift."
    )


def test_the_node_assertion_can_tell_the_two_models_apart(swiss_ephemeris: int) -> None:
    """PROVE the loose node tolerance still discriminates — do not assume it.

    A 600 arcsec tolerance is only worth having if the wrong model lands well
    outside it. Measured: the mean node misses Horizons by 2280.0964 to 6930.1893
    arcsec across this corpus. This test asserts that margin directly, so a future
    epoch set chosen where the two models happen to coincide — which would silently
    turn L3 into a test that passes under either declaration — fails here instead.
    """
    corpus = _corpus()
    wrong_model_body = {swe.TRUE_NODE: swe.MEAN_NODE, swe.MEAN_NODE: swe.TRUE_NODE}[
        utils.LUNAR_NODE_BODY
    ]

    margins: list[float] = []
    for epoch in corpus["epochs"]:
        expected = epoch["moon_osculating_node_j2000_deg"]
        wrong = _arcsec_apart(_declared_node_j2000(epoch["jd_tdb"], wrong_model_body), expected)
        margins.append(wrong)
        assert wrong > _NODE_TOLERANCE_ARCSEC, (
            f"{epoch['id']}: the UNDECLARED node model is {wrong:.4f} arcsec from JPL, "
            f"inside the {_NODE_TOLERANCE_ARCSEC} arcsec tolerance — at this epoch the "
            "corpus cannot tell the two models apart, so L3 would pass under either "
            "declaration. Pick epochs where they diverge."
        )

    assert min(margins) > 3.0 * _NODE_TOLERANCE_ARCSEC, (
        f"the tightest model-discrimination margin in this corpus is {min(margins):.4f} "
        f"arcsec, less than 3x the {_NODE_TOLERANCE_ARCSEC} arcsec tolerance — too "
        "little headroom to call this a guard on the node model"
    )


# ---------------------------------------------------------------------------
# The ayanamsa — pinned, and honest about what that pin is worth
# ---------------------------------------------------------------------------


def test_ayanamsa_is_pinned_but_not_independently_sourced(swiss_ephemeris: int) -> None:
    """Regression pin on the Lahiri ayanamsa. NOT an accuracy assertion.

    The name says what the test is worth. The sidereal zero point is an
    astrological convention, not an astronomical observable, and Horizons has no
    counterpart for it — so this value came from this engine and only detects the
    engine's ayanamsa MOVING, exactly like swiss_ephemeris_goldens.json does for
    everything else. Sourcing it from a published authority is follow-up work.
    """
    from jhora.panchanga import drik

    corpus = _corpus()
    for epoch in corpus["epochs"]:
        with utils.EPHEMERIS_LOCK:
            utils._ensure_thread_ephemeris_state()
            observed = drik.get_ayanamsa_value(epoch["jd_ut"])
        assert math.isclose(observed, epoch["ayanamsa_deg"], abs_tol=_AYANAMSA_TOLERANCE_DEG), (
            f"{epoch['id']}: ayanamsa moved — corpus {epoch['ayanamsa_deg']} vs engine "
            f"{observed}. Re-record with: python ci/fetch_reference_corpus.py"
        )


# ---------------------------------------------------------------------------
# Guard on the L1 tolerance constant itself
# ---------------------------------------------------------------------------


def test_l1_tolerance_stays_below_the_moshier_divergence(swiss_ephemeris: int) -> None:
    """The L1 tolerance must stay under the measured Moshier-vs-JPL divergence.

    Not because this module is the Swiss-data detector — the retflag probe and the
    1e-6 Swiss goldens are, with orders of magnitude more headroom. The point is
    narrower and still worth pinning: a tolerance loosened past this line would let
    L1 pass while running on the fallback engine, quietly converting an accuracy
    assertion into nothing.

    The margin here is THIN and the honest numbers are these. Measured across this
    corpus: Swiss misses JPL by at most 0.2368 arcsec; Moshier by at most 1.8437
    arcsec, and that worst case is the Moon alone — Sun, Mars, Mercury, Jupiter,
    Venus and Saturn all agree with JPL to under 0.30 arcsec on Moshier too. So
    this assertion rests on a single body at 1.8x the tolerance. Treat it as a
    guard on the constant, not as evidence the Swiss data was loaded.
    """
    corpus = _corpus()
    worst_moshier = 0.0
    for epoch in corpus["epochs"]:
        for graha in _VISIBLE_GRAHAS:
            with utils.EPHEMERIS_LOCK:
                utils._ensure_thread_ephemeris_state()
                values, retflag = swe.calc_ut(
                    epoch["jd_ut"], utils._GRAHA_SWE_BODY[graha], swe.FLG_MOSEPH
                )
            assert retflag & swe.FLG_MOSEPH, (
                f"{epoch['id']}.{graha}: an explicit Moshier request was not honoured "
                f"(retflag {retflag}) — this control is not measuring what it claims"
            )
            worst_moshier = max(
                worst_moshier,
                _arcsec_apart(values[0], epoch["tropical_apparent_longitude_deg"][graha]),
            )

    assert worst_moshier > _APPARENT_TOLERANCE_ARCSEC, (
        f"the Moshier fallback matches JPL to {worst_moshier:.4f} arcsec, within the "
        f"{_APPARENT_TOLERANCE_ARCSEC} arcsec L1 tolerance — L1 would pass on the "
        "fallback engine, so it is no longer evidence about the Swiss data at all. "
        "Tighten _APPARENT_TOLERANCE_ARCSEC rather than accepting this."
    )
