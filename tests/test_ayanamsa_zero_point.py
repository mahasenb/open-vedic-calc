"""The sidereal zero point, anchored to the CONVENTION THAT DEFINES IT — not to this engine.

WHY THIS FILE EXISTS
--------------------
``tests/test_independent_reference_corpus.py`` pins eight quantities against NASA/JPL
Horizons, and one against nothing but itself: ``ayanamsa_deg``. That field is recorded
from this engine and honestly labelled ``independent_of_this_engine: false``, because
no solar-system ephemeris publishes an ayanamsa — the sidereal origin is a human
convention, not an astronomical observable, so Horizons has no counterpart for it at
any price. ``test_ayanamsa_is_pinned_but_not_independently_sourced`` therefore detects
the engine's ayanamsa MOVING and nothing more: re-record it and any error disappears.

A convention still has an authority, and that authority publishes a DEFINITION. This
file anchors the zero point to that definition instead of to a number this engine
produced. It is the follow-up work the corpus module's docstring recorded as
outstanding.

THE AUTHORITY AND THE TWO ANCHORS
---------------------------------
The Lahiri ayanamsa is the *Chitrapaksha* ayanamsa adopted for the Indian national
calendar by the **Calendar Reform Committee** (Government of India, chaired by
M. N. Saha; N. C. Lahiri, secretary), whose recommendations the Rashtriya Panchang and
the Indian Astronomical Ephemeris implement. Two published facts follow from it, and
this file pins BOTH because they fail in different directions:

A1  **The citta-Spica definition — the sidereal origin is set so that Chitra (Spica,
    alpha Virginis) has sidereal longitude exactly 180 deg.** This is the *defining*
    property of the Chitrapaksha family; 180.0 is not a measurement anyone made, it is
    the definition, so it carries no observational uncertainty at all. It is also
    checkable here without trusting any secondary source: ``sefstars.txt`` — the fourth
    checksum-pinned file in ``ci/swiss_ephemeris.json``, and until now the only one no
    test consumed — carries the star, so the whole assertion is computable offline from
    data this repo already verifies by sha256.

A2  **The reform-epoch value — ayanamsa = 23 deg 15' 00" at 1956 March 21, 0h ET**, the
    epoch at which the reformed calendar took effect (1 Chaitra 1878 Saka). Published
    to the arcminute, and revised in 1985 to 23 deg 15' 00.658" — a 0.658 arcsec
    refinement, immaterial at the tolerance below, which is why the tolerance is set
    from the arcminute the primary decree states rather than from the revision.

PROVENANCE, STATED HONESTLY
---------------------------
A1 is a definition, so it needs no page reference: the property is the convention. A2
is a published NUMBER, and the primary document (Report of the Calendar Reform
Committee, Government of India, 1955) was **not** consulted directly for this change —
the value is taken from the widely-consistent secondary transmission of that decree,
and no edition/page is cited here because none was verified. That is why A2 is the
CORROBORATING leg and A1 the load-bearing one, and why A2's tolerance is never
tightened below the precision its source states. Recording the limitation is the point:
a citation nobody checked, dressed up with a page number, would be worse than this
paragraph.

WHAT THESE ANCHORS PROVE — AND WHAT THEY DO NOT
-----------------------------------------------
They prove the engine is on the **Chitrapaksha family**, against an external
definition. Measured at the corpus epochs, every other sidereal convention swisseph
offers misses A1 by 3.7x to 185x the tolerance (Krishnamurti 274.7-312.7 arcsec,
Fagan/Bradley ~3.2e3, Raman ~5.2e3, True Pushya ~4.0e3, Yukteshwar ~4.9e3,
Suryasiddhanta ~1.1e4, True Revati ~1.4e4, De Luce ~1.4e4) — so a silent reversion to
swisseph's Fagan/Bradley default, the exact ~0.88 deg failure mode CLAUDE.md documents
for an unconfigured thread, fails here by 27x.

They do **NOT** separate Lahiri from swisseph's ``SIDM_TRUE_CITRA``, and this was
measured rather than assumed: true-Chitra satisfies A1 by construction (0.0000 arcsec)
and sits 51.92 arcsec from A2's published value, INSIDE A2's honest 60 arcsec
tolerance. The two models differ by only ~61 arcsec, which is finer than the precision
the authority publishes, so no honest reading of the published record can tell them
apart. What separates them is the existing 1e-6 deg regression pin in the corpus
module — which is precisely the division of labour intended: this file proves the
CONVENTION is right against an outside authority, that one proves the VALUE has not
moved. Neither replaces the other, and neither is re-recordable to make a failure go
away.

THREAD-LOCAL STATE (a live hazard, measured while writing this file)
-------------------------------------------------------------------
``swe.set_sid_mode`` mutates swisseph's thread-local sidereal state, and
``utils._ensure_thread_ephemeris_state()`` is idempotent per thread — it sets a
``threading.local`` flag on first call and returns immediately thereafter. So it does
**not** undo later pollution: measured, a probe that left ``SIDM_TRUE_REVATI`` in force
made ``drik.get_ayanamsa_value`` return 20.045162 instead of 23.857092 at J2000 for the
rest of the process, which would redden unrelated tests with a number that looks like
an engine defect. ``test_the_anchors_discriminate`` therefore restores the declared
mode through the engine's own sanctioned call in a ``finally``, and the autouse
``declared_ayanamsa_restored`` fixture below re-checks it after EVERY test in this
module, so a future edit that forgets cannot leak past this file.
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

# --- A1: the citta-Spica definition -----------------------------------------------
# 180.0 is the DEFINITION of the Chitrapaksha origin, not a measured quantity.
_CHITRA_SIDEREAL_LONGITUDE_DEG = 180.0
_CHITRA_STAR = "Spica"
# Tolerance covers the implementation residual, NOT uncertainty in the definition.
# swisseph's SIDM_LAHIRI is a t0/rate polynomial (t0 = J1900) evaluated under a modern
# precession model, not a re-derivation of the Committee's tables, so Spica drifts
# slightly from exactly 180 deg. Measured: worst 73.9328 arcsec across the corpus
# epochs, and worst 78.9051 arcsec swept yearly across the whole supported birth span
# (1800-01-01..2400-12-31, app/schemas.py). 120 arcsec is that span-wide envelope with
# margin, and still 2.3x inside the nearest competing convention (Krishnamurti, minimum
# 274.7359 arcsec over the same epochs) — see test_the_anchors_discriminate.
_CHITRA_TOLERANCE_ARCSEC = 120.0

# --- A2: the reform-epoch value ---------------------------------------------------
# 1956 March 21, 0h ET — the epoch the reformed calendar took effect. ET vs UT is
# immaterial here: Delta-T was ~31 s in 1956 and the ayanamsa moves ~50.3 arcsec/yr, so
# the distinction is worth ~5e-5 arcsec against a 60 arcsec tolerance.
_ICRC_EPOCH_YMD = (1956, 3, 21)
_ICRC_PUBLISHED_AYANAMSA_DEG = 23.0 + 15.0 / 60.0  # 23 deg 15' 00"
# The decree states arcminutes. Asserting tighter than the source's own precision would
# be claiming accuracy the publication does not carry, so this is NOT tightened even
# though the engine lands well inside it (measured 15.9805 arcsec, a 3.75x margin).
_ICRC_TOLERANCE_ARCSEC = 60.0


def _corpus_epochs() -> list[dict]:
    return json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))["epochs"]


def _arcsec_apart(first: float, second: float) -> float:
    """Absolute angular separation in arcseconds, correct across the 0/360 seam."""
    return abs(((first - second + 180.0) % 360.0) - 180.0) * 3600.0


def _chitra_sidereal_longitude(jd_ut: float) -> float:
    """Spica's sidereal longitude on the ayanamsa currently in force."""
    return swe.fixstar_ut(_CHITRA_STAR, jd_ut, swe.FLG_SWIEPH | swe.FLG_SIDEREAL)[0][0]


def _declared_ayanamsa(jd_ut: float) -> float:
    """The ayanamsa the SERVED path returns (pyjhora's drik, as chart.py uses)."""
    from jhora.panchanga import drik

    return drik.get_ayanamsa_value(jd_ut)


def _restore_declared_ayanamsa() -> None:
    """Re-apply the declared mode through the engine's OWN sanctioned call.

    Deliberately ``drik.set_ayanamsa_mode('LAHIRI')`` rather than a raw
    ``swe.set_sid_mode``: that is the call ``utils._ensure_thread_ephemeris_state``
    makes, so restoring by the same route cannot drift from what the engine declares.
    """
    from jhora.panchanga import drik

    drik.set_ayanamsa_mode("LAHIRI")


@pytest.fixture(autouse=True)
def declared_ayanamsa_restored():
    """Fail any test in this module that leaks a non-declared sidereal mode.

    The postcondition is checked against a value this module never asserts against
    elsewhere, so it cannot be satisfied by the same mistake it is meant to catch.
    """
    yield
    with utils.EPHEMERIS_LOCK:
        utils._ensure_thread_ephemeris_state()
        leaked = _declared_ayanamsa(2451545.0)
    assert math.isclose(leaked, 23.857092325, abs_tol=1e-6), (
        f"this module left a non-declared ayanamsa in force (J2000 reads {leaked}, "
        "declared Lahiri is 23.857092325). swe.set_sid_mode is thread-local state and "
        "_ensure_thread_ephemeris_state is idempotent, so it will NOT undo this — every "
        "later test in the process now computes on the wrong zero point."
    )


# ---------------------------------------------------------------------------
# A1 — the definition
# ---------------------------------------------------------------------------


def test_chitra_sits_at_the_chitrapaksha_origin(swiss_ephemeris: int) -> None:
    """Spica is 180 deg sidereal — the property that DEFINES this ayanamsa.

    The expected value comes from the Calendar Reform Committee's definition, not from
    this engine, so a re-record cannot make a failure here go away. This is the leg
    that fails loudly if a thread ever computes on swisseph's unconfigured
    Fagan/Bradley default (measured 3215-3253 arcsec out, 27x the tolerance).
    """
    failures: list[str] = []
    worst = 0.0

    with utils.EPHEMERIS_LOCK:
        utils._ensure_thread_ephemeris_state()
        for epoch in _corpus_epochs():
            observed = _chitra_sidereal_longitude(epoch["jd_ut"])
            delta = _arcsec_apart(observed, _CHITRA_SIDEREAL_LONGITUDE_DEG)
            worst = max(worst, delta)
            if delta > _CHITRA_TOLERANCE_ARCSEC:
                failures.append(
                    f"  {epoch['id']}: Chitra at {observed:.7f} deg sidereal, "
                    f"{delta:.4f} arcsec from the defining {_CHITRA_SIDEREAL_LONGITUDE_DEG} "
                    f"deg (> {_CHITRA_TOLERANCE_ARCSEC})"
                )

    assert not failures, (
        "the sidereal origin no longer satisfies the citta-Spica definition of the "
        "Chitrapaksha (Lahiri) ayanamsa.\n" + "\n".join(failures) + "\n\n"
        "This is a CONVENTION defect measured against the authority that defines the "
        "ayanamsa, not a golden that drifted — re-recording "
        "tests/goldens/independent_reference_corpus.json does not address it."
    )
    assert worst > 0.0, (
        "Chitra landed on exactly 180.0000000 deg at every epoch, which the polynomial "
        "Lahiri model cannot do — the comparison is not evaluating anything (check that "
        "SIDM_TRUE_CITRA has not been substituted for the declared mode)"
    )


# ---------------------------------------------------------------------------
# A2 — the published reform-epoch value
# ---------------------------------------------------------------------------


def test_ayanamsa_matches_the_published_reform_epoch_value(swiss_ephemeris: int) -> None:
    """23 deg 15' 00" at 1956 March 21 — the Committee's own decreed value.

    Corroborating, not load-bearing: see the module docstring's PROVENANCE note. The
    tolerance is the arcminute the decree is stated to, never tightened.
    """
    jd_icrc = swe.julday(*_ICRC_EPOCH_YMD, 0.0)

    with utils.EPHEMERIS_LOCK:
        utils._ensure_thread_ephemeris_state()
        observed = _declared_ayanamsa(jd_icrc)

    delta = _arcsec_apart(observed, _ICRC_PUBLISHED_AYANAMSA_DEG)
    assert delta <= _ICRC_TOLERANCE_ARCSEC, (
        f"at the reform epoch {_ICRC_EPOCH_YMD[0]}-{_ICRC_EPOCH_YMD[1]:02d}-"
        f"{_ICRC_EPOCH_YMD[2]:02d} 0h the engine's ayanamsa is {observed:.7f} deg, "
        f"{delta:.4f} arcsec from the published {_ICRC_PUBLISHED_AYANAMSA_DEG:.7f} deg "
        f"(23 deg 15 arcmin 00 arcsec), outside the {_ICRC_TOLERANCE_ARCSEC} arcsec the decree's "
        "own arcminute precision allows. The engine is not on the ayanamsa the Indian "
        "national calendar defines."
    )


# ---------------------------------------------------------------------------
# The anchors are only worth having if they reject the alternatives
# ---------------------------------------------------------------------------


def test_the_anchors_discriminate(swiss_ephemeris: int) -> None:
    """PROVE the tolerances still reject other conventions — do not assume it.

    A loose anchor that every ayanamsa satisfies pins nothing. This walks the sidereal
    modes swisseph offers and asserts each is rejected by at least one anchor, with the
    margin asserted rather than described.

    ``SIDM_TRUE_CITRA`` is the documented exception and is asserted AS an exception:
    it satisfies A1 exactly and sits inside A2, because it differs from Lahiri by ~61
    arcsec — finer than the authority's published precision. Asserting it explicitly
    means a future edit cannot quietly widen the exception list.
    """
    jd_icrc = swe.julday(*_ICRC_EPOCH_YMD, 0.0)
    epochs = _corpus_epochs()

    # Every sidereal convention swisseph offers that is NOT the Chitrapaksha family.
    rejectable = {
        "Krishnamurti": swe.SIDM_KRISHNAMURTI,
        "Fagan/Bradley": swe.SIDM_FAGAN_BRADLEY,
        "Raman": swe.SIDM_RAMAN,
        "True Pushya": swe.SIDM_TRUE_PUSHYA,
        "Yukteshwar": swe.SIDM_YUKTESHWAR,
        "Suryasiddhanta": swe.SIDM_SURYASIDDHANTA,
        "True Revati": swe.SIDM_TRUE_REVATI,
        "De Luce": swe.SIDM_DELUCE,
    }

    margins: dict[str, float] = {}
    true_citra: tuple[float, float] | None = None

    with utils.EPHEMERIS_LOCK:
        utils._ensure_thread_ephemeris_state()
        try:
            for name, mode_id in rejectable.items():
                swe.set_sid_mode(mode_id, 0, 0)
                worst_chitra = max(
                    _arcsec_apart(
                        _chitra_sidereal_longitude(epoch["jd_ut"]),
                        _CHITRA_SIDEREAL_LONGITUDE_DEG,
                    )
                    for epoch in epochs
                )
                margins[name] = worst_chitra

            swe.set_sid_mode(swe.SIDM_TRUE_CITRA, 0, 0)
            true_citra = (
                max(
                    _arcsec_apart(
                        _chitra_sidereal_longitude(epoch["jd_ut"]),
                        _CHITRA_SIDEREAL_LONGITUDE_DEG,
                    )
                    for epoch in epochs
                ),
                _arcsec_apart(swe.get_ayanamsa_ut(jd_icrc), _ICRC_PUBLISHED_AYANAMSA_DEG),
            )
        finally:
            # Non-negotiable: this state is thread-local and nothing else restores it.
            _restore_declared_ayanamsa()

    for name, worst_chitra in margins.items():
        assert worst_chitra > _CHITRA_TOLERANCE_ARCSEC, (
            f"the {name} ayanamsa satisfies the citta-Spica anchor "
            f"({worst_chitra:.4f} arcsec, inside {_CHITRA_TOLERANCE_ARCSEC}) — the anchor "
            "no longer distinguishes the Chitrapaksha convention from it, so it has "
            "stopped pinning anything. Tighten the tolerance rather than accepting this."
        )

    assert min(margins.values()) > 2.0 * _CHITRA_TOLERANCE_ARCSEC, (
        f"the tightest rejection margin is {min(margins.values()):.4f} arcsec, under 2x "
        f"the {_CHITRA_TOLERANCE_ARCSEC} arcsec tolerance — too little headroom to call "
        "this a guard on the convention"
    )

    assert true_citra is not None
    citra_chitra, citra_icrc = true_citra
    assert citra_chitra <= _CHITRA_TOLERANCE_ARCSEC and citra_icrc <= _ICRC_TOLERANCE_ARCSEC, (
        f"SIDM_TRUE_CITRA no longer satisfies both anchors (A1 {citra_chitra:.4f} arcsec, "
        f"A2 {citra_icrc:.4f} arcsec). The module docstring states that it does, and that "
        "the 1e-6 deg corpus pin is what separates it from Lahiri — if that changed, the "
        "docstring's division of labour is now wrong and must be corrected with it."
    )
