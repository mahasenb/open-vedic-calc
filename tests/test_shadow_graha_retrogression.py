"""Contract: the shadow grahas are served as permanently retrograde.

BPHS treats Rahu and Ketu as always vakri. ``bphs_core.utils`` declares that as
``PERPETUALLY_RETROGRADE_GRAHAS`` and ``graha_is_retrograde`` applies it; this
file is the tripwire. Extend it, never weaken it.

Two directions are pinned deliberately, because only one of them can fail on its
own:

* the DECLARED direction -- Rahu and Ketu report retrograde, in every varga, on
  every sampled date;
* the DANGEROUS direction -- a graha that is genuinely direct still reports NOT
  retrograde, and a graha that is genuinely retrograde still reports retrograde.

Without the second, a field hardcoded ``True`` for every graha would satisfy the
first, and the guard could not fail.

Every assertion here holds under BOTH ephemeris runtimes (real Swiss data files
and the built-in Moshier fallback) -- verified by running this file under each.
The discriminating dates below were chosen for that reason: their retrograde
sets are byte-identical across the two runtimes. Anything whose sign depends on
the ephemeris source does NOT belong in this file (measured 2026-08-07: the true
node's own speed at 2029-06-21 00:00 UT is +0.128202129 deg/day under Swiss and
-0.001408427 deg/day under Moshier -- opposite signs).
"""
import datetime as dt

import pytest

from bphs_core import utils
from bphs_core.chart import Chart, PersonalData


# --------------------------------------------------------------------------
# Synthetic, non-personal birth data.
# --------------------------------------------------------------------------
def _person(date: dt.date, time: dt.time) -> PersonalData:
    return PersonalData(
        name="sample_shadow",
        birth_date=dt.datetime(date.year, date.month, date.day),
        birth_time=time,
        birth_place="Sample City",
        latitude=7.0,
        longitude=80.0,
        timezone_offset_hours=5.5,
    )


def _snapshot(date: dt.date, time: dt.time = dt.time(12, 0, 0)):
    return Chart(_person(date, time)).snapshot()


def _varga_charts(snapshot) -> dict[str, dict]:
    """Every divisional chart on the snapshot, keyed by attribute name.

    Collected by introspection rather than by a hand-written list, so a varga
    added later is covered without editing this file. The rasi chart and the
    vargas are built by two DIFFERENT code paths in ``chart.py``, and pinning
    only one of them would read exactly like pinning both.
    """
    charts = {
        name: getattr(snapshot, name)
        for name in dir(snapshot)
        if name.endswith("_chart") and isinstance(getattr(snapshot, name), dict)
    }
    # 11 = the rasi chart plus the ten vargas the snapshot exposes, counted from
    # the snapshot itself on 2026-08-07, not from memory. A floor rather than an
    # equality so a varga added later is swept too, but a varga that silently
    # disappears cannot shrink this sweep down to nothing.
    assert len(charts) >= 11, (
        f"expected the rasi chart plus the divisional charts, found {sorted(charts)} "
        "-- if the snapshot stopped exposing them under a *_chart name this sweep "
        "silently stopped covering the vargas"
    )
    return charts


# Dates whose retrograde set is identical under both ephemeris runtimes
# (measured 2026-08-07, frozen resolve: pyjhora 4.8.6 / pyswisseph 2.10.3.2).
# Each names a graha measured RETROGRADE and grahas measured DIRECT, so the
# suite observes both outcomes and cannot be satisfied by a constant.
DISCRIMINATING_DATES = [
    # date,                 retrograde,                     direct
    (dt.date(2000, 1, 1), {"Saturn"},
     {"Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus"}),
    (dt.date(1975, 12, 1), {"Mars", "Jupiter", "Saturn"},
     {"Sun", "Moon", "Mercury", "Venus"}),
]


class TestDeclaration:
    def test_membership_is_exactly_the_two_shadow_grahas(self):
        """Pin the SET, not merely that it is non-empty.

        A literal collection whose membership nothing observes cannot detect a
        member being added or dropped.
        """
        assert utils.PERPETUALLY_RETROGRADE_GRAHAS == frozenset({"Rahu", "Ketu"})

    def test_every_declared_name_is_a_graha_this_engine_serves(self):
        """The declaration is matched by NAME against ``utils.PLANETS``.

        A name that is not in ``PLANETS`` matches nothing and silently restores
        the library's value -- the control disabling itself when its input goes
        missing.
        """
        assert utils.PERPETUALLY_RETROGRADE_GRAHAS <= set(utils.PLANETS)

    def test_an_unknown_declared_name_hard_fails_instead_of_matching_nothing(
        self, monkeypatch
    ):
        """Fail closed: the guard refuses an undeclarable graha."""
        monkeypatch.setattr(
            utils, "PERPETUALLY_RETROGRADE_GRAHAS",
            frozenset({"Rahu", "Ketu", "Nibiru"}),
        )
        with pytest.raises(RuntimeError, match="Nibiru"):
            utils._verify_perpetually_retrograde_grahas()

    def test_the_guard_is_actually_INVOKED_at_import(self):
        """A guard that is never called is not a guard.

        The test above proves the function rejects a bad declaration; it says
        nothing about whether anything runs it. Deleting the module-level call
        left that test -- and the whole of this file -- green, so this asserts
        the call site itself.

        Parsed with ``ast`` rather than scanned for a string, because a comment
        or a string literal mentioning the name would satisfy a line scan while
        the module-level call was gone.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(utils))
        module_level_calls = {
            node.value.func.id
            for node in tree.body
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
        }
        assert "_verify_perpetually_retrograde_grahas" in module_level_calls, (
            "bphs_core.utils no longer calls _verify_perpetually_retrograde_grahas() "
            "at module scope, so a declaration naming a graha this engine does not "
            f"serve would no longer fail closed. Module-level calls found: "
            f"{sorted(module_level_calls)}"
        )

    @pytest.mark.parametrize("graha", sorted({"Rahu", "Ketu"}))
    def test_resolver_overrides_the_library_for_each_declared_graha(self, graha):
        """Mirrored across EVERY member -- partial pinning reads like full pinning."""
        assert utils.graha_is_retrograde(graha, computed=False) is True
        assert utils.graha_is_retrograde(graha, computed=True) is True

    @pytest.mark.parametrize(
        "graha", ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]
    )
    def test_resolver_passes_every_other_graha_through_unchanged(self, graha):
        """The dangerous direction: the override must not leak onto the rest."""
        assert utils.graha_is_retrograde(graha, computed=False) is False
        assert utils.graha_is_retrograde(graha, computed=True) is True


class TestServedValue:
    @pytest.mark.parametrize("graha", sorted({"Rahu", "Ketu"}))
    def test_shadow_graha_is_retrograde_in_every_varga(self, graha):
        """Both construction paths in chart.py, every divisional chart."""
        snapshot = _snapshot(dt.date(1990, 3, 15), dt.time(6, 30, 0))
        for varga_name, varga in _varga_charts(snapshot).items():
            assert varga[graha].is_retrograde is True, (
                f"{graha} is served as direct in {varga_name} -- the declared "
                "perpetual retrogression was not applied on this code path"
            )

    @pytest.mark.parametrize("graha", sorted({"Rahu", "Ketu"}))
    def test_shadow_graha_is_retrograde_on_every_sampled_date(self, graha):
        """A sweep wide enough to have caught a direct spell if one existed.

        Stepped so the samples do not share a common period with the node's own
        oscillation. Measured on the pre-fix engine this same sweep returned
        retrograde on 0 of these dates for both shadow grahas.
        """
        dates = [dt.date(1950, 1, 1) + dt.timedelta(days=i * 37) for i in range(60)]
        served = [_snapshot(d).rasi_chart[graha].is_retrograde for d in dates]
        assert all(served), (
            f"{graha} was served as direct on "
            f"{[d for d, r in zip(dates, served) if not r]}"
        )

    @pytest.mark.parametrize("date,retro,direct", DISCRIMINATING_DATES)
    def test_genuinely_direct_grahas_still_report_not_retrograde(
        self, date, retro, direct
    ):
        """The dangerous direction, end to end through the chart path.

        This is what stops a blanket ``is_retrograde=True`` from passing the
        assertions above.
        """
        rasi = _snapshot(date).rasi_chart
        assert {g for g in direct if rasi[g].is_retrograde} == set(), (
            f"a graha measured DIRECT on {date} is served as retrograde"
        )
        assert {g for g in retro if not rasi[g].is_retrograde} == set(), (
            f"a graha measured RETROGRADE on {date} is served as direct"
        )


class TestTheOverrideIsLoadBearing:
    """The library and the declaration now DISAGREE, and the declaration wins.

    This class used to assert the opposite mechanism, and the pyjhora 4.8.6 ->
    4.8.7 bump is what changed it. Measured on the two resolves:

        4.8.6  ``_planet_list = {... if p not in [const._RAHU, const._KETU]}``
        4.8.7  ``_planet_list = {... if p not in [const._SUN, const._MOON]}``

    i.e. the library stopped filtering the two nodes out of
    ``planets_in_retrograde`` and started reporting their COMPUTED direction --
    its own 4.8.7 docstring now says "Rahu/Ketu as True nodes - retrograde,
    stationary or even direct". (Sun and Moon took their place in the filter,
    which changes nothing: their longitude speed is never negative, so they were
    never appended anyway.)

    That makes the override MORE load-bearing than before, not less. Measured on
    the 4.8.7 resolve over 400 instants from 1990-01-01 at 37-day steps: the
    library reports Rahu retrograde on 72.25% and **direct on 27.75%**, while the
    engine serves retrograde on 100% of charts. Under 4.8.6 the override turned
    an absence into True; now it overrules a live, contrary answer on roughly a
    quarter of all instants.

    Nothing served moved on the bump -- that is the point of declaring it. Had
    the flag been inherited, 4.8.7 would have flipped Rahu/Ketu retrogression on
    ~28% of charts with nothing in the diff to say so.
    """

    def _library_direct_dates(self, count: int) -> tuple[list, int]:
        """Dates where the library calls Rahu DIRECT, plus the sweep size.

        Discovered rather than hardcoded, deliberately. The true node's speed
        SIGN near a turning point is ephemeris-source-dependent (this file's
        module docstring records +0.128 deg/day under Swiss and -0.0014 under
        Moshier at the same instant), so a pinned list of "direct" instants would
        be a runtime-dependent fixture -- exactly what this file forbids. A
        discovered set is stable under both runtimes because only its membership
        near the turning points moves, never the fact that it is large.
        """
        from jhora.panchanga import drik

        rahu_pid = utils.PLANETS.index("Rahu")
        place = utils.make_place("Sample City", 7.0, 80.0, 5.5)

        direct = []
        for i in range(count):
            date = dt.date(1990, 1, 1) + dt.timedelta(days=i * 37)
            jd = utils.swe.julday(date.year, date.month, date.day, 12.0)
            if rahu_pid not in drik.planets_in_retrograde(jd, place):
                direct.append(date)
        return direct, count

    def test_the_library_now_reports_the_shadow_grahas(self):
        """Pin the NEW upstream behaviour, so the next move is visible too.

        Asserted as a disagreement that exists, not as an absence: the previous
        version of this test asserted the nodes never appeared, and an assertion
        of that shape goes green both when the library filters them out and when
        the sweep silently stops discovering anything.
        """
        direct, swept = self._library_direct_dates(200)

        assert direct, (
            "the astronomy library reported Rahu retrograde on every one of the "
            f"{swept} sampled instants. Either it has gone back to filtering the "
            "nodes out of planets_in_retrograde, or it switched to the mean node "
            "(which is retrograde 100% of the time) -- both are deliberate-review "
            "events for the lunar node model, not something to absorb here."
        )
        # A floor, not just non-emptiness: measured 27.75% across 400 instants,
        # so a sweep that discovered only one or two would mean the sampling had
        # degenerated rather than that the disagreement is genuinely rare.
        assert len(direct) >= swept // 10, (
            f"only {len(direct)} of {swept} sampled instants had the library "
            "calling Rahu direct; measured on the pinned resolve this is ~28%"
        )

    def test_the_declaration_overrules_the_library_where_they_disagree(self):
        """End-to-end, on the exact dates the library calls Rahu DIRECT.

        This is the assertion the old absence-based test could not make. It
        drives a real ``Chart`` on instants where the dependency actively
        reports the opposite of the declaration, so it fails if the override
        were ever reduced to passing the library's answer through.
        """
        direct, _swept = self._library_direct_dates(200)
        assert direct, "precondition: no disagreeing instants were discovered"

        checked = 0
        for date in direct[:6]:
            rasi = _snapshot(date).rasi_chart
            for graha in ("Rahu", "Ketu"):
                assert rasi[graha].is_retrograde, (
                    f"{graha} is served as DIRECT on {date}, where the astronomy "
                    "library also calls the node direct -- the declared override "
                    "in PERPETUALLY_RETROGRADE_GRAHAS is no longer reaching the "
                    "served chart, so this engine is now inheriting its "
                    "retrogression from the dependency"
                )
                checked += 1

        assert checked >= 12, (
            f"only {checked} served values were checked on disagreeing dates -- "
            "a loop that examined nothing would satisfy every assertion above"
        )

    def test_the_override_is_unconditional_at_the_boundary(self):
        """``graha_is_retrograde`` ignores ``computed`` for the declared names.

        The two tests above go through a Chart; this pins the boundary function
        itself, including the case the library now produces -- ``computed=True``
        -- so the override cannot decay into "True only when the library is
        silent", which is all 4.8.6 ever exercised.
        """
        for graha in sorted(utils.PERPETUALLY_RETROGRADE_GRAHAS):
            assert utils.graha_is_retrograde(graha, computed=False) is True
            assert utils.graha_is_retrograde(graha, computed=True) is True

        # And the pass-through still passes through, or the override would be
        # indistinguishable from a field hardcoded True for every graha.
        assert utils.graha_is_retrograde("Saturn", computed=False) is False
        assert utils.graha_is_retrograde("Saturn", computed=True) is True
