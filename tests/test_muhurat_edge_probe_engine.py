"""``_is_eclipse_day`` searches OUTSIDE the scanned day. What does that cost?

The eclipse veto asks "is there an eclipse, visible here, on this local day?",
and the only way to answer it is to find the next eclipse — which is almost never
on the scanned day. Measured from a day scanned at 2400-01-09: the lunar finder
returns a greatest-eclipse time of 2400-01-12 and the solar finder 2403-05-21.
The shipped Swiss files (``sepl_18.se1`` / ``semo_18.se1``) stop at 2400-01-10,
so both of those searches read past the data and swisseph answers them from its
built-in Moshier analytical ephemeris instead.

TWO SEPARATE QUESTIONS COME OUT OF THAT, AND THEY HAVE DIFFERENT ANSWERS
-----------------------------------------------------------------------

**1. Is the eclipse VERDICT wrong when the fallback answers?  No — measured.**

The verdict is a coarse boolean over a day boundary: ``day_start <= t_max <
day_end``. For it to flip, the two engines would have to place the greatest-
eclipse instant on opposite sides of a local midnight. Measured over 44 paired
eclipse events (two in 1800-01-02..1801-07-01, 26 in 2020..2035, 16 in
2200..2210, none in 2399-12-01..2400-01-10):

    worst |t_max| drift, Swiss vs Moshier    2.714 s
    closest any t_max came to local midnight   185 s
    ------------------------------------------------
    margin                                      68x
    eclipse-verdict disagreements    0 of 132 days tested

So the fallback answering an eclipse probe does not change the served answer,
and this module pins that equivalence rather than pretending the probe stays
inside the files. ``test_eclipse_verdict_is_engine_independent`` compares the two
engines directly; ``test_no_eclipse_lands_near_a_local_midnight`` pins the margin
that makes the first test's result a property rather than a coincidence.

**2. Does the probe leave the ENGINE degraded afterwards?  It did — that is a
   real defect, and this module's first test is what closed it.**

swisseph does not merely answer the out-of-range request from Moshier; it keeps
that fallback state, so a LATER lookup at an IN-RANGE Julian Day silently answers
from Moshier too. Measured on base, in a fresh process, at the scanned day's own
noon JD:

    probe Moon at 2400-01-09          -> SWISS   (retflag 65602)
    run _is_eclipse_day(2400-01-09)
    probe Moon at 2400-01-09 again    -> MOSHIER (retflag 65604)

That is accuracy silently lost on a date the service accepts as in-range, which
is the fail-closed rule's "no control may silently disable itself". The scanned
day is INSIDE the shipped files; nothing about it justifies a fallback answer.
``bphs_core.utils.restore_ephemeris_state()`` re-applies the ephemeris path, and
``_is_eclipse_day`` now calls it on the way out.

WHY A ``swe.calc_ut`` SPY CANNOT SEE ANY OF THIS
-----------------------------------------------
``swe.sol_eclipse_when_loc`` / ``swe.lun_eclipse_when`` compute their Sun and
Moon positions INSIDE the C library. They never re-enter the Python binding, so a
spy that wraps ``swe.calc_ut`` records **zero** calls for the whole of
``_is_eclipse_day`` — measured, at 2400-01-09, 1800-01-02 and 2026-05-26 alike.
Any Moshier tally such a spy reports for a muhurat scan therefore belongs to
OTHER limbs (``drik.tithi``'s next-day search, ``_is_adhik_maasa``'s lunar-month
search), never to the eclipse probe.
``test_a_calc_ut_spy_is_blind_to_the_eclipse_finders`` pins that, because the
predecessor of this module attributed a scan's Moshier count to ``_is_eclipse_day``
on exactly that reasoning and the attribution was wrong.
"""
import datetime
import tempfile

import pytest
import swisseph as swe
from jhora.panchanga import drik

from bphs_core import muhurat, utils

PLACE = drik.Place("sample", 7.0, 80.0, 5.5)

# The shipped Swiss files cover roughly 1800-01-01..2400-01-10 for Sun and Moon
# (measured at one-day steps with utils.probe_ephemeris_source). These two dates
# are INSIDE that span and are the ones whose eclipse probe was measured leaving
# the engine on the fallback.
_LEAK_DATES = (datetime.date(2400, 1, 1), datetime.date(2400, 1, 9))
_INTERIOR_DATE = datetime.date(2026, 5, 26)


def _day_noon_jd(d: datetime.date) -> float:
    """The scanned day's own noon, as the UTC JD a position lookup would use."""
    return swe.julday(d.year, d.month, d.day, 12.0 - PLACE.timezone)


def _engine(jd: float, body: int) -> str:
    swiss, _retflag = utils.probe_ephemeris_source(jd, body)
    return "SWISS" if swiss else "MOSHIER"


@pytest.fixture
def restored_engine():
    """Start AND finish with swisseph pointed at the shipped data.

    Restoring on the way out is the obvious half: several tests here
    deliberately knock swisseph onto the fallback, and a later test must not
    inherit that.

    Restoring on the way IN is the half that was missing, and the suite proved
    why: run after ``tests/test_date_field_ephemeris_range.py`` — which scans the
    very edges of the span on purpose — these tests arrived with the engine
    already degraded and failed their own preconditions. That is the defect this
    module is about, observed on the test session itself. A test that only holds
    when it runs first is not measuring what it claims to.
    """
    utils.restore_ephemeris_state()
    yield
    utils.restore_ephemeris_state()


# ---------------------------------------------------------------------------
# 1. The probe must not leave the engine degraded for the scanned day
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", _LEAK_DATES, ids=lambda d: d.isoformat())
def test_eclipse_probe_leaves_the_scanned_day_on_swiss_data(
    swiss_ephemeris: int, restored_engine, target: datetime.date
) -> None:
    """An in-span JD must still answer from Swiss data AFTER the eclipse probe.

    The probe legitimately reads past the end of the files — the next eclipse is
    simply not on the scanned day. What is NOT legitimate is the fallback state
    persisting, so that the scanned day's own Sun and Moon (well inside the
    shipped files) are then answered analytically with nothing saying so.

    Fails on base with AFTER=MOSHIER for both bodies on both dates.
    """
    jd = _day_noon_jd(target)

    before = {name: _engine(jd, body)
              for name, body in (("Sun", swe.SUN), ("Moon", swe.MOON))}
    assert before == {"Sun": "SWISS", "Moon": "SWISS"}, (
        f"precondition failed: {target} is supposed to be inside the shipped "
        f"Swiss files, but the engine reports {before} before the probe even "
        "runs. Re-measure the span before touching this test."
    )

    muhurat._is_eclipse_day(target, PLACE)

    after = {name: _engine(jd, body)
             for name, body in (("Sun", swe.SUN), ("Moon", swe.MOON))}
    assert after == {"Sun": "SWISS", "Moon": "SWISS"}, (
        f"_is_eclipse_day({target}) left swisseph on the Moshier fallback: "
        f"{after}. The scanned day is INSIDE the shipped files, so every later "
        "lookup on it must still come from Swiss data. The eclipse search reads "
        "past the end of the files by design (the next eclipse is not on this "
        "day); it must restore the ephemeris state on the way out."
    )


def test_the_interior_never_needed_the_restore(
    swiss_ephemeris: int, restored_engine
) -> None:
    """A mid-span day's probe never degraded the engine — pin that it stays so.

    Without this, the guard above could be "satisfied" by something that merely
    hides a fallback everywhere, and nobody would notice that the interior had
    started leaking too.
    """
    jd = _day_noon_jd(_INTERIOR_DATE)
    muhurat._is_eclipse_day(_INTERIOR_DATE, PLACE)
    after = {name: _engine(jd, body)
             for name, body in (("Sun", swe.SUN), ("Moon", swe.MOON))}
    assert after == {"Sun": "SWISS", "Moon": "SWISS"}, (
        f"a mid-span eclipse probe at {_INTERIOR_DATE} now degrades the engine "
        f"({after}). The edge behaviour has moved into the interior of the "
        "served range; re-measure it."
    )


# ---------------------------------------------------------------------------
# 2. The verdict itself does not depend on which engine answers
# ---------------------------------------------------------------------------

def _force_moshier(empty_dir: str) -> None:
    """Point swisseph at *empty_dir*, so every read falls back to Moshier.

    Must be re-applied before EVERY sample being compared, not once before a
    loop — see ``test_eclipse_verdict_is_engine_independent`` for why. The
    calling test MUST use the ``restored_engine`` fixture.
    """
    swe.set_ephe_path(empty_dir)


# Local days carrying an eclipse visible at PLACE, plus their neighbours. Chosen
# from the engine's own finders over 2020..2035 and 2200..2210, then written down
# so the assertion below is a comparison rather than a re-derivation.
_VERDICT_SAMPLE = [
    datetime.date(2026, 8, 28), datetime.date(2027, 8, 2),
    datetime.date(2028, 1, 12), datetime.date(2029, 6, 26),
    datetime.date(2030, 6, 1), datetime.date(2031, 5, 7),
    datetime.date(2032, 4, 25), datetime.date(2033, 4, 14),
    datetime.date(1800, 4, 9), datetime.date(2400, 1, 9),
    datetime.date(2399, 12, 1), datetime.date(2026, 5, 26),
]


def test_eclipse_verdict_is_engine_independent(
    swiss_ephemeris: int, restored_engine
) -> None:
    """The served boolean must be the same whichever engine answers the probe.

    This is what licenses the module docstring's claim that an edge-window probe
    falling back costs nothing observable. It is a DIRECT comparison, not an
    argument from tolerances: each day is answered twice, once with the Swiss
    files in use and once with swisseph pointed at an empty directory.

    THE ENGINE IS RE-FORCED BEFORE EVERY SAMPLE, AND THAT IS NOT A DETAIL.
    The first version of this test forced the fallback once and then looped. That
    is self-defeating HERE specifically, because the very fix this module exists
    to pin — ``_is_eclipse_day`` re-applying the ephemeris path in its ``finally``
    — undoes the forcing as a side effect of the first call. Measured on that
    version::

        after force, before any call : MOSHIER
        sample 0 2026-08-28: BEFORE = MOSHIER  AFTER = SWISS
        sample 1 2027-08-02: BEFORE = SWISS    AFTER = SWISS
        sample 2 2028-01-12: BEFORE = SWISS    AFTER = SWISS

    so 11 of 12 samples compared Swiss against Swiss while claiming to compare
    engines — a test that would have passed no matter how engine-sensitive the
    verdict became. The per-sample assertions below are what stop that from
    recurring silently: each one fails loudly if its half did not actually run on
    the engine it names, so the comparison cannot quietly become vacuous again.
    """
    empty = tempfile.mkdtemp(prefix="no_ephe_")

    swiss_verdicts = {}
    for d in _VERDICT_SAMPLE:
        utils.restore_ephemeris_state()
        assert _engine(_day_noon_jd(d), swe.MOON) == "SWISS", (
            f"the Swiss half of the comparison did not run on Swiss data for {d}. "
            "A previous sample left the engine degraded, so this half is not the "
            "reference it claims to be."
        )
        swiss_verdicts[d] = muhurat._is_eclipse_day(d, PLACE)

    assert any(swiss_verdicts.values()), (
        "not one sampled day is an eclipse day, so this test would pass without "
        "comparing anything that matters. Re-pick _VERDICT_SAMPLE."
    )

    moshier_verdicts = {}
    for d in _VERDICT_SAMPLE:
        _force_moshier(empty)
        assert _engine(_day_noon_jd(d), swe.MOON) == "MOSHIER", (
            f"the fallback was not actually forced for {d} (ephe path {empty}); "
            "this sample would have run BOTH halves on the same engine and "
            "compared nothing."
        )
        moshier_verdicts[d] = muhurat._is_eclipse_day(d, PLACE)

    disagreements = {d: (swiss_verdicts[d], moshier_verdicts[d])
                     for d in _VERDICT_SAMPLE
                     if swiss_verdicts[d] != moshier_verdicts[d]}
    assert not disagreements, (
        "the eclipse-day verdict changed with the ephemeris engine:\n"
        + "\n".join(f"  - {d}: swiss={s} moshier={m}"
                    for d, (s, m) in disagreements.items())
        + "\n\nThe served range's edge windows answer some eclipse probes on the "
        "fallback, which was documented as costing nothing because the verdict "
        "is a coarse day-boundary boolean. That is no longer true, and the "
        "probe span must now be handled explicitly rather than documented."
    )


def test_no_eclipse_lands_near_a_local_midnight(
    swiss_ephemeris: int, restored_engine
) -> None:
    """The MARGIN behind the test above — why it is a property, not luck.

    The verdict can only differ between engines if an eclipse's greatest phase
    falls within the inter-engine drift of a local midnight. Measured: the drift
    is at most 2.714 s, and the closest any eclipse came to a local midnight is
    185 s. This pins the second number, which is the one that can move: a future
    ephemeris bump that put an eclipse within a few seconds of midnight would
    make the equivalence above coincidental, and this test would say so.
    """
    drift_seconds = 2.714           # measured worst case, Swiss vs Moshier
    required_margin = 10 * drift_seconds

    jd = swe.julday(2020, 1, 1, 0.0)
    end = swe.julday(2035, 1, 1, 0.0)
    closest = None
    events = 0
    for finder in (drik.next_solar_eclipse, drik.next_lunar_eclipse):
        cursor, guard = jd, 0
        while cursor < end and guard < 200:
            t_max = finder(cursor, PLACE)[1][0]
            if t_max <= cursor:
                cursor += 0.05
                guard += 1
                continue
            if t_max >= end:
                break
            events += 1
            _y, _m, _d, fh = swe.revjul(t_max + PLACE.timezone / 24.0)
            margin = min(fh, 24.0 - fh) * 3600.0
            if closest is None or margin < closest[0]:
                closest = (margin, t_max)
            cursor = t_max + 0.05
            guard += 1

    assert events >= 20, (
        f"only {events} eclipse events were found in 2020..2035, too few for "
        "this margin to mean anything. The finders have changed behaviour."
    )
    assert closest is not None and closest[0] > required_margin, (
        f"an eclipse now falls {closest[0]:.0f}s from a local midnight, inside "
        f"the {required_margin:.0f}s margin this equivalence needs (the measured "
        f"Swiss-vs-Moshier drift is {drift_seconds}s). "
        "test_eclipse_verdict_is_engine_independent can no longer be read as a "
        "property of the geometry — the day-boundary comparison in "
        "_is_eclipse_day is now genuinely engine-sensitive."
    )


# ---------------------------------------------------------------------------
# 3. The attribution correction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "target",
    [datetime.date(2400, 1, 9), datetime.date(1800, 1, 2), _INTERIOR_DATE],
    ids=lambda d: d.isoformat(),
)
def test_a_calc_ut_spy_is_blind_to_the_eclipse_finders(
    swiss_ephemeris: int, restored_engine, target: datetime.date
) -> None:
    """``_is_eclipse_day`` drives ZERO python-level ``swe.calc_ut`` calls.

    The eclipse finders do their position work inside the C library. This is
    pinned because the previous docstring in
    ``tests/test_date_field_ephemeris_range.py`` attributed a muhurat scan's
    Moshier tally to ``_is_eclipse_day`` on the strength of exactly such a spy —
    an instrument that cannot see the eclipse finders at all. Anyone re-deriving
    that attribution should red this test first.
    """
    seen: list[tuple[float, int, int]] = []
    real_calc_ut = swe.calc_ut

    def spy(jd, planet, flags, *args, **kwargs):
        values, retflag = real_calc_ut(jd, planet, flags, *args, **kwargs)
        seen.append((jd, planet, retflag))
        return values, retflag

    swe.calc_ut = spy
    try:
        muhurat._is_eclipse_day(target, PLACE)
    finally:
        swe.calc_ut = real_calc_ut

    assert seen == [], (
        f"_is_eclipse_day({target}) made {len(seen)} python-level swe.calc_ut "
        "calls. It used to make none — the finders computed entirely inside the "
        "C library — which is why a calc_ut spy could not attribute anything to "
        "this limb. If that has changed, a spy CAN now measure the eclipse "
        "probe, and the surrounding documentation should be re-derived rather "
        "than trusted."
    )
