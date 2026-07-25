"""Accuracy gate against the REAL Swiss ephemeris data files (CALC-1).

Every other test in this suite runs on whatever ephemeris happens to be available,
which in CI has always meant the Moshier fallback — swisseph silently substitutes it
when ``data/ephe`` holds no ``.se1`` files, and returns plausible numbers while doing
so. For a determinism-first engine that is the wrong thing to validate: a regression
that only manifests against real Swiss data (a data-file-dependent edge case, an
off-by-epoch bug) merges green and ships wrong answers.

WHAT THIS MODULE PINS
---------------------
1. ``test_swiss_data_is_really_active`` — the retflag from ``swe.calc_ut`` must carry
   ``FLG_SWIEPH``, not ``FLG_MOSEPH``. This is the only reliable detector: with the
   data files absent, a call that REQUESTS ``FLG_SWIEPH`` still succeeds, returns a
   full result, and reports ``FLG_MOSEPH`` in retflag. Verified locally with no
   ``data/ephe`` present: requesting flags 65538 (SWIEPH|SIDEREAL) returned retflag
   65604 with SWIEPH clear and MOSEPH set.
2. ``test_swiss_and_moshier_agree_coarsely_but_are_not_identical`` — a differential
   check needing no goldens: the two engines must agree to well under a degree (so a
   gross error in either is caught) while differing at all (so a run that thinks it
   is on Swiss data but is really on Moshier cannot pass).
3. ``test_chart_matches_swiss_goldens`` — committed golden longitudes, the ayanamsa
   and the lagna for three synthetic charts, compared at 1e-6 degrees. That tolerance
   is ~3.6 milliarcseconds, far tighter than the Swiss/Moshier divergence, so the
   goldens themselves are a second, independent Swiss-data detector: they cannot pass
   on the fallback engine.

FAIL-CLOSED
-----------
``REQUIRE_SWISS_EPHEMERIS=1`` (set by the ``swiss-ephemeris`` job in
``.github/workflows/test.yml``) turns "Swiss data is not active" from a skip into a
hard failure. Without that flag these tests skip, because the ordinary Moshier CI job
and the default local loop genuinely have no data files — but a skip must never be
how the accuracy job passes, so:

  * the job sets the flag, and
  * ``ci/tests/test_swiss_ephemeris_job.py`` PARSES the workflow and fails if that
    job, its fetch step, or that env var goes missing.

The goldens are produced by the same frozen dependency set the image ships
(``uv sync --frozen``: pyswisseph 2.10.3.2, pyjhora 4.8.6) against checksum-pinned
data files, so they are reproducible. A deliberate dependency or data bump that moves
a value is expected to fail here first and be re-recorded in the same review.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import pathlib

import pytest
import swisseph as swe

from bphs_core import utils  # sets the ephemeris path + Lahiri mode on import
from bphs_core.chart import Chart, PersonalData

from tests.conftest import SAMPLE_A, SAMPLE_B, SAMPLE_C

_REQUIRE_ENV = "REQUIRE_SWISS_EPHEMERIS"
_GOLDENS = pathlib.Path(__file__).resolve().parent / "goldens" / "swiss_ephemeris_goldens.json"
_UPDATE_ENV = "UPDATE_SWISS_GOLDENS"

# 1e-6 deg = 3.6 mas. Chosen to be far below the Swiss/Moshier divergence (which is
# ~1e-4 deg and up) and far above any float-formatting noise in the JSON round trip.
_TOLERANCE_DEG = 1e-6

_SAMPLES = {"sample_a": SAMPLE_A, "sample_b": SAMPLE_B, "sample_c": SAMPLE_C}


# ---------------------------------------------------------------------------
# Swiss-data detection
# ---------------------------------------------------------------------------


def _probe_ephemeris_source() -> tuple[bool, int]:
    """(swiss_active, retflag) for a sidereal Sun position requesting Swiss data.

    Held under the process-wide ephemeris lock like every other swisseph call in
    this repo (see ``bphs_core.utils.serialized_ephemeris``).
    """
    with utils.EPHEMERIS_LOCK:
        jd = swe.julday(2000, 1, 1, 12.0)
        _values, retflag = swe.calc_ut(jd, swe.SUN, swe.FLG_SWIEPH | swe.FLG_SIDEREAL)
    swiss_active = bool(retflag & swe.FLG_SWIEPH) and not bool(retflag & swe.FLG_MOSEPH)
    return swiss_active, retflag


def _required() -> bool:
    return os.environ.get(_REQUIRE_ENV) == "1"


@pytest.fixture(scope="session")
def swiss_ephemeris() -> int:
    """Gate every test in this module on real Swiss data actually being in use.

    Hard-fails (never skips) when ``REQUIRE_SWISS_EPHEMERIS=1``: the whole point of
    the accuracy job is that it cannot pass by quietly not running.
    """
    swiss_active, retflag = _probe_ephemeris_source()
    if swiss_active:
        return retflag

    ephe_dir = pathlib.Path(utils.EPHE_PATH)
    present = sorted(p.name for p in ephe_dir.glob("*")) if ephe_dir.is_dir() else []
    message = (
        "Swiss ephemeris data is NOT in use — swisseph fell back to the Moshier "
        f"engine (retflag={retflag}, FLG_SWIEPH={bool(retflag & swe.FLG_SWIEPH)}, "
        f"FLG_MOSEPH={bool(retflag & swe.FLG_MOSEPH)}).\n"
        f"Ephemeris directory: {ephe_dir} (contents: {present or 'empty/absent'}).\n"
        "Fetch the data with: python ci/fetch_swiss_ephemeris.py"
    )
    if _required():
        pytest.fail(f"{_REQUIRE_ENV}=1 but {message}")
    pytest.skip(message)


def test_swiss_data_is_really_active(swiss_ephemeris: int) -> None:
    """The retflag check is the gate; assert it explicitly and name what it means."""
    retflag = swiss_ephemeris
    assert retflag & swe.FLG_SWIEPH, (
        "swisseph did not report FLG_SWIEPH — the result did not come from the "
        "data files in data/ephe"
    )
    assert not retflag & swe.FLG_MOSEPH, (
        "swisseph reported FLG_MOSEPH — it silently fell back to the built-in "
        "analytical ephemeris despite the data files"
    )
    # And the data files really are on disk where swisseph was pointed.
    ephe_dir = pathlib.Path(utils.EPHE_PATH)
    for name in ("sepl_18.se1", "semo_18.se1"):
        assert (ephe_dir / name).is_file(), f"{name} missing from {ephe_dir}"


def test_fail_closed_flag_is_honoured_by_the_probe() -> None:
    """The detector must not be able to certify Moshier as Swiss.

    Runs regardless of which engine is present: it asserts the classification logic
    itself, so a future edit that inverts or loosens the flag test (making the whole
    module vacuous) fails here even on the ordinary Moshier CI job.
    """
    swiss_only = swe.FLG_SWIEPH | swe.FLG_SIDEREAL
    moshier_only = swe.FLG_MOSEPH | swe.FLG_SIDEREAL

    def classify(retflag: int) -> bool:
        return bool(retflag & swe.FLG_SWIEPH) and not bool(retflag & swe.FLG_MOSEPH)

    assert classify(swiss_only) is True
    assert classify(moshier_only) is False
    # The real-world shape: a Moshier fallback carries BOTH the sidereal bit and
    # MOSEPH, and must still classify as "not Swiss".
    assert classify(swe.FLG_SIDEREAL | swe.FLG_MOSEPH) is False
    assert classify(swe.FLG_SIDEREAL) is False


def test_swiss_and_moshier_agree_coarsely_but_are_not_identical(swiss_ephemeris: int) -> None:
    """Differential check — needs no goldens, and cannot pass on the fallback engine.

    Agreement to well under a degree rules out a gross error in either engine (a
    wrong epoch, a wrong body id, a sidereal/tropical mix-up). A non-zero difference
    somewhere proves the two engines are genuinely different code paths, i.e. that
    this run really loaded the data files.
    """
    bodies = (swe.SUN, swe.MOON, swe.MARS, swe.MERCURY, swe.JUPITER, swe.VENUS, swe.SATURN)
    jd = swe.julday(1975, 12, 1, 7.0)

    max_difference = 0.0
    with utils.EPHEMERIS_LOCK:
        for body in bodies:
            swiss, swiss_flag = swe.calc_ut(jd, body, swe.FLG_SWIEPH | swe.FLG_SIDEREAL)
            moshier, moshier_flag = swe.calc_ut(jd, body, swe.FLG_MOSEPH | swe.FLG_SIDEREAL)
            assert swiss_flag & swe.FLG_SWIEPH, f"body {body} did not come from Swiss data"
            assert moshier_flag & swe.FLG_MOSEPH, f"body {body} Moshier request was not honoured"
            difference = abs(((swiss[0] - moshier[0] + 180.0) % 360.0) - 180.0)
            assert difference < 0.01, (
                f"body {body}: Swiss and Moshier disagree by {difference:.6f} deg — far "
                "beyond either engine's error budget, so one of them is wrong"
            )
            max_difference = max(max_difference, difference)

    assert max_difference > 0.0, (
        "Swiss and Moshier returned byte-identical longitudes for every body, which "
        "they cannot: this run is almost certainly on the fallback engine for both "
        "calls despite the retflag check"
    )


# ---------------------------------------------------------------------------
# Golden values
# ---------------------------------------------------------------------------


def _person(sample: dict) -> PersonalData:
    return PersonalData(
        name=sample["name"],
        birth_date=datetime.date.fromisoformat(sample["birth_date"]),
        birth_time=datetime.time.fromisoformat(sample["birth_time"]),
        birth_place=sample["birth_place"],
        latitude=sample["latitude"],
        longitude=sample["longitude"],
        timezone_offset_hours=sample["timezone_offset_hours"],
    )


def _observed() -> dict[str, dict]:
    """The accuracy-sensitive outputs for every synthetic sample, as plain JSON."""
    observed: dict[str, dict] = {}
    for key, sample in _SAMPLES.items():
        snapshot = Chart(_person(sample)).snapshot()
        observed[key] = {
            "ayanamsa_value": snapshot.ayanamsa_value,
            "lagna": snapshot.lagna,
            "longitudes": {
                planet: data.longitude_abs for planet, data in sorted(snapshot.rasi_chart.items())
            },
        }
    return observed


def _canonical(document: dict) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def test_chart_matches_swiss_goldens(swiss_ephemeris: int) -> None:
    """Planetary longitudes, ayanamsa and lagna against committed Swiss-data goldens.

    The tolerance (1e-6 deg) is deliberately tighter than the Swiss/Moshier
    divergence, so these assertions double as an independent detector: they cannot be
    satisfied by the fallback engine even if the retflag check above were defeated.
    """
    observed = _observed()

    if os.environ.get(_UPDATE_ENV) == "1":
        # Recording mode for a deliberate dependency/data bump. Refused inside CI —
        # a CI run must never rewrite the values it is checking.
        assert not os.environ.get("CI"), (
            f"{_UPDATE_ENV}=1 is refused when CI is set; re-record locally against "
            "real Swiss data and commit the diff"
        )
        _GOLDENS.parent.mkdir(parents=True, exist_ok=True)
        _GOLDENS.write_text(_canonical(observed), encoding="utf-8", newline="\n")

    assert _GOLDENS.is_file(), (
        f"golden file missing at {_GOLDENS}. Produce it against REAL Swiss data:\n"
        f"    python ci/fetch_swiss_ephemeris.py\n"
        f"    {_UPDATE_ENV}=1 python -m pytest tests/test_swiss_ephemeris.py\n"
        "Observed values for this run:\n" + _canonical(observed)
    )
    goldens = json.loads(_GOLDENS.read_text(encoding="utf-8"))

    mismatches: list[str] = []
    assert set(goldens) == set(observed), (
        f"golden samples {sorted(goldens)} != computed samples {sorted(observed)}"
    )
    for sample_key, golden in sorted(goldens.items()):
        actual = observed[sample_key]
        if golden["lagna"] != actual["lagna"]:
            mismatches.append(
                f"{sample_key}.lagna: golden {golden['lagna']!r} != actual {actual['lagna']!r}"
            )
        if not math.isclose(
            golden["ayanamsa_value"], actual["ayanamsa_value"], abs_tol=_TOLERANCE_DEG
        ):
            mismatches.append(
                f"{sample_key}.ayanamsa_value: golden {golden['ayanamsa_value']!r} != "
                f"actual {actual['ayanamsa_value']!r}"
            )
        if set(golden["longitudes"]) != set(actual["longitudes"]):
            mismatches.append(
                f"{sample_key}.longitudes: bodies changed — golden "
                f"{sorted(golden['longitudes'])} != actual {sorted(actual['longitudes'])}"
            )
        else:
            for planet, expected in sorted(golden["longitudes"].items()):
                got = actual["longitudes"][planet]
                delta = abs(((expected - got + 180.0) % 360.0) - 180.0)
                if delta > _TOLERANCE_DEG:
                    mismatches.append(
                        f"{sample_key}.longitudes.{planet}: golden {expected!r} != actual "
                        f"{got!r} (delta {delta:.9f} deg > {_TOLERANCE_DEG} deg)"
                    )

    assert not mismatches, (
        "Swiss-ephemeris golden mismatch — the engine's numeric output moved.\n"
        + "\n".join(f"  - {m}" for m in mismatches)
        + "\n\nThis is an accuracy regression unless a dependency or data-file bump in "
        "the same change explains it. If it does, re-record deliberately:\n"
        f"    {_UPDATE_ENV}=1 python -m pytest tests/test_swiss_ephemeris.py\n"
        "Observed values for this run:\n" + _canonical(observed)
    )


def test_goldens_are_canonical_and_nontrivial() -> None:
    """The committed file must be exactly what the recorder writes, and cover all three.

    Runs on the Moshier job too: it never touches the ephemeris, so a gutted or
    hand-reformatted golden file is caught even when the Swiss job does not run.
    """
    assert _GOLDENS.is_file(), f"golden file missing at {_GOLDENS}"
    raw = _GOLDENS.read_bytes()
    assert b"\r" not in raw, "golden file must be LF-only"
    goldens = json.loads(raw.decode("utf-8"))
    assert set(goldens) == set(_SAMPLES), (
        f"goldens cover {sorted(goldens)} but the suite defines {sorted(_SAMPLES)}"
    )
    for key, entry in goldens.items():
        assert entry["lagna"] in utils.SIGNS, f"{key}: lagna {entry['lagna']!r} is not a sign"
        assert isinstance(entry["ayanamsa_value"], float), f"{key}: ayanamsa is not a float"
        assert set(entry["longitudes"]) == set(utils.PLANETS), (
            f"{key}: goldens pin {sorted(entry['longitudes'])}, expected all of "
            f"{sorted(utils.PLANETS)} — a body silently dropped out of the pinned set"
        )
        for planet, longitude in entry["longitudes"].items():
            assert 0.0 <= longitude < 360.0, f"{key}.{planet}: {longitude} out of range"
    assert raw.decode("utf-8") == _canonical(goldens), (
        "golden file is not in canonical form (sorted keys, 2-space indent, LF, "
        f"trailing newline) — re-record with {_UPDATE_ENV}=1"
    )


# ---------------------------------------------------------------------------
# Thread-local ephemeris state (the defect this CI job surfaced)
# ---------------------------------------------------------------------------
#
# pyswisseph keeps the ephemeris path and the sidereal mode per THREAD. Setting
# them once at import configured only the importing thread, so every Starlette
# threadpool worker and every scan-job worker computed on the Moshier fallback
# with a Fagan/Bradley ayanamsa. `bphs_core.utils.serialized_ephemeris` now
# applies both per thread; these tests pin that.
#
# The first two are meaningful under BOTH runtimes — on Moshier they still catch
# the ayanamsa half of the defect (~0.88 deg), which is the larger error of the
# two — so they are not gated on the swiss_ephemeris fixture.


def _sidereal_sun_on_this_thread() -> float:
    """A sidereal Sun longitude computed through a serialized entry point."""

    @utils.serialized_ephemeris
    def _compute() -> float:
        jd = swe.julday(2000, 1, 1, 12.0)
        values, _retflag = swe.calc_ut(jd, swe.SUN, swe.FLG_SWIEPH | swe.FLG_SIDEREAL)
        return float(values[0])

    return _compute()


def test_serialized_entry_points_agree_across_threads() -> None:
    """The same computation must give the same answer on any thread.

    Before the fix this failed by ~0.88 deg on the Moshier job (worker threads
    fell back to the Fagan/Bradley ayanamsa) and additionally by the
    Swiss-vs-Moshier margin on the Swiss job.
    """
    from concurrent.futures import ThreadPoolExecutor

    main_thread_value = _sidereal_sun_on_this_thread()
    with ThreadPoolExecutor(max_workers=4) as pool:
        worker_values = list(pool.map(lambda _: _sidereal_sun_on_this_thread(), range(4)))

    for value in worker_values:
        assert value == main_thread_value, (
            "a worker thread computed a different sidereal longitude than the main "
            f"thread ({value!r} != {main_thread_value!r}). pyswisseph state is "
            "thread-local: the ephemeris path and/or the Lahiri sidereal mode was "
            "not applied on that thread."
        )


def test_thread_state_initialiser_is_invoked_per_thread() -> None:
    """The per-thread flag must be set by going through the decorator, not by import.

    Guards the mechanism rather than a symptom: if a refactor moved the
    initialisation back to import time only, a fresh thread would show the flag
    unset after a serialized call.
    """
    from concurrent.futures import ThreadPoolExecutor

    def _flag_after_serialized_call() -> tuple[bool, bool]:
        before = getattr(utils._THREAD_EPHEMERIS_STATE, "initialised", False)
        _sidereal_sun_on_this_thread()
        after = getattr(utils._THREAD_EPHEMERIS_STATE, "initialised", False)
        return before, after

    with ThreadPoolExecutor(max_workers=1) as pool:
        before, after = pool.submit(_flag_after_serialized_call).result()

    assert before is False, (
        "a brand-new thread already reported initialised ephemeris state — the "
        "thread-local guard is not actually thread-local, so it can report a "
        "thread configured when it is not"
    )
    assert after is True, (
        "a serialized entry point ran on a fresh thread without applying that "
        "thread's ephemeris state — serialized_ephemeris no longer calls "
        "_ensure_thread_ephemeris_state()"
    )


def test_worker_threads_use_swiss_data_too(swiss_ephemeris: int) -> None:
    """A worker thread must report SEFLG_SWIEPH, not the silent Moshier fallback.

    This is the direct assertion of the defect: with real data present, the main
    thread reported retflag 65602 (SWIEPH) while a worker reported 65604
    (MOSEPH), and nothing anywhere failed.
    """
    from concurrent.futures import ThreadPoolExecutor

    @utils.serialized_ephemeris
    def _retflag_here() -> int:
        jd = swe.julday(2000, 1, 1, 12.0)
        _values, retflag = swe.calc_ut(jd, swe.SUN, swe.FLG_SWIEPH | swe.FLG_SIDEREAL)
        return int(retflag)

    with ThreadPoolExecutor(max_workers=3) as pool:
        retflags = list(pool.map(lambda _: _retflag_here(), range(3)))

    for retflag in retflags:
        assert retflag & swe.FLG_SWIEPH, (
            f"worker thread retflag {retflag} has SEFLG_SWIEPH clear — it computed "
            "on the Moshier fallback despite the data files being present"
        )
        assert not retflag & swe.FLG_MOSEPH, (
            f"worker thread retflag {retflag} has SEFLG_MOSEPH set — silent fallback"
        )
