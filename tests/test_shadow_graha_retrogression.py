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
    def test_the_library_never_reports_the_shadow_grahas_at_all(self):
        """The library filters both nodes out of ``planets_in_retrograde``.

        So the served value cannot come from it, and the declaration is the only
        thing standing between this engine and reporting both shadow grahas as
        permanently DIRECT. Structural, not a sampling artefact: it holds under
        both ephemeris runtimes.

        If a future dependency bump makes this go red, the library started
        reporting the nodes -- a deliberate-review event, exactly like the other
        pyjhora levers. The declared behaviour still wins; what changes is that
        the change is no longer invisible.
        """
        from jhora.panchanga import drik

        rahu_pid = utils.PLANETS.index("Rahu")
        ketu_pid = utils.PLANETS.index("Ketu")
        place = utils.make_place("Sample City", 7.0, 80.0, 5.5)

        seen = set()
        for i in range(40):
            jd = utils.swe.julday(1990, 1, 1, 0.0) + i * 37
            seen.update(drik.planets_in_retrograde(jd, place))

        assert rahu_pid not in seen and ketu_pid not in seen, (
            "the astronomy library now reports the shadow grahas in "
            "planets_in_retrograde; re-establish deliberately whether the "
            "declared override still describes what this engine wants to serve"
        )
        assert seen, (
            "the library reported NO retrograde grahas at all across the sweep -- "
            "this check has zero discovered inputs and is not evidence of anything"
        )
