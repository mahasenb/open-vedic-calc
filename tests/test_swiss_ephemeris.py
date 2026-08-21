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
(``uv sync --frozen``: pyswisseph 2.10.3.2, pyjhora 4.8.7) against checksum-pinned
data files, so they are reproducible. A deliberate dependency or data bump that moves
a value is expected to fail here first and be re-recorded in the same review.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import pathlib
import subprocess
import sys
from collections.abc import Mapping

import pytest
import swisseph as swe

from bphs_core import utils  # sets the ephemeris path + Lahiri mode on import
from bphs_core.chart import Chart, PersonalData

from tests.conftest import (
    REQUIRE_SWISS_EPHEMERIS_ENV as _REQUIRE_ENV,
    SAMPLE_A,
    SAMPLE_B,
    SAMPLE_C,
)

_GOLDENS = pathlib.Path(__file__).resolve().parent / "goldens" / "swiss_ephemeris_goldens.json"
_UPDATE_ENV = "UPDATE_SWISS_GOLDENS"
_CI_ENV = "CI"

# 1e-6 deg = 3.6 mas. Chosen to be far below the Swiss/Moshier divergence (which is
# ~1e-4 deg and up) and far above any float-formatting noise in the JSON round trip.
_TOLERANCE_DEG = 1e-6

_SAMPLES = {"sample_a": SAMPLE_A, "sample_b": SAMPLE_B, "sample_c": SAMPLE_C}


# ---------------------------------------------------------------------------
# Swiss-data detection
# ---------------------------------------------------------------------------
#
# The ``swiss_ephemeris`` fixture that gates every accuracy test in this module
# now lives in tests/conftest.py, because tests/test_independent_reference_corpus.py
# needs the identical gate. One definition, two readers — never two copies.


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


def test_fail_closed_flag_is_honoured_by_the_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """The detector must not be able to certify Moshier as Swiss.

    This calls ``utils.probe_ephemeris_source()`` itself — the exact function the
    ``swiss_ephemeris`` fixture above and ``/healthz`` (app/main.py) both call — with
    ``swe.calc_ut`` monkeypatched to return a controlled retflag, rather than
    asserting against a hand-duplicated copy of its classification logic. A
    duplicate copy would keep passing even if the real probe were neutered (e.g.
    hardcoding its ``swiss_active`` result to ``True``); calling the real probe
    means that regression fails HERE. Runs regardless of which engine is actually
    present, so it also fails here even on the ordinary Moshier CI job.
    """
    swiss_only = swe.FLG_SWIEPH | swe.FLG_SIDEREAL
    moshier_only = swe.FLG_MOSEPH | swe.FLG_SIDEREAL
    # The real-world shape: a Moshier fallback carries BOTH the sidereal bit and
    # MOSEPH, and must still classify as "not Swiss".
    moshier_with_sidereal = swe.FLG_SIDEREAL | swe.FLG_MOSEPH

    for fixed_retflag, expected in (
        (swiss_only, True),
        (moshier_only, False),
        (moshier_with_sidereal, False),
        (swe.FLG_SIDEREAL, False),
    ):
        def fake_calc_ut(_jd, _body, _flags, _retflag=fixed_retflag):
            return (0.0,) * 6, _retflag

        monkeypatch.setattr(swe, "calc_ut", fake_calc_ut)
        swiss_active, observed_retflag = utils.probe_ephemeris_source()
        assert observed_retflag == fixed_retflag
        assert swiss_active is expected, (
            f"utils.probe_ephemeris_source() classified retflag={fixed_retflag} as "
            f"swiss_active={swiss_active}, expected {expected}"
        )


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


def golden_recording_refusal(environ: Mapping[str, str]) -> str | None:
    """Why re-recording the Swiss goldens is refused right now, or ``None``.

    A pure function of the environment, so the policy can be ASSERTED directly
    rather than inferred from what the recorder did to a file. Mirrors
    ``ci/fetch_reference_corpus.py::refusal_reason`` — deliberately, and now
    with the same ``CI`` semantics rather than weaker ones.

    ``CI`` IS READ FOR PRESENCE, NOT TRUTHINESS. The previous form here was
    ``assert not os.environ.get("CI")``, which is satisfied by every falsy
    string — ``""`` most obviously, and an empty ``CI`` is a value CI systems
    really do export. Under it, a CI run could re-record the engine-authored
    goldens and then compare them against themselves, reporting green having
    validated nothing. Reading for presence fails in the safe direction: a
    false refusal costs one ``unset CI``, a false permit costs the regression
    signal these goldens exist to carry.
    """
    if _CI_ENV in environ:
        return (
            f"REFUSED: {_UPDATE_ENV}=1 is not permitted when {_CI_ENV} is set "
            f"(value {environ[_CI_ENV]!r}).\n"
            f"\n"
            f"These goldens are recorded by running this engine and writing down "
            f"what it said, so a CI run that rewrites them replaces the expected "
            f"values with the observed ones and then reports green having compared "
            f"the engine against itself.\n"
            f"\n"
            f"{_CI_ENV} is read for PRESENCE, not truthiness: an empty-string "
            f"{_CI_ENV} is still {_CI_ENV}. If a red golden is genuinely explained "
            f"by a deliberate dependency or data-file bump, re-record it locally "
            f"against real Swiss data and commit the diff in that same review:\n"
            f"    python ci/fetch_swiss_ephemeris.py\n"
            f"    {_UPDATE_ENV}=1 python -m pytest tests/test_swiss_ephemeris.py"
        )
    return None


def record_goldens(
    observed: dict,
    environ: Mapping[str, str],
    *,
    target: pathlib.Path = _GOLDENS,
) -> None:
    """Write the goldens, or refuse — the ONE place this file is written.

    The refusal is checked BEFORE the write, not reported after it: a guard
    downstream of the side effect it guards is not a guard. ``target`` is a
    keyword argument so the refusal can be exercised against a throwaway path
    without a test seam in the recording arm itself.
    """
    refusal = golden_recording_refusal(environ)
    assert refusal is None, refusal
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_canonical(observed), encoding="utf-8", newline="\n")


def test_chart_matches_swiss_goldens(swiss_ephemeris: int) -> None:
    """Planetary longitudes, ayanamsa and lagna against committed Swiss-data goldens.

    The tolerance (1e-6 deg) is deliberately tighter than the Swiss/Moshier
    divergence, so these assertions double as an independent detector: they cannot be
    satisfied by the fallback engine even if the retflag check above were defeated.
    """
    observed = _observed()

    if os.environ.get(_UPDATE_ENV) == "1":
        # Recording mode for a deliberate dependency/data bump. Refused inside CI —
        # a CI run must never rewrite the values it is checking. The refusal and
        # the write both live in record_goldens(); see golden_recording_refusal()
        # for why CI is read for presence rather than truthiness.
        record_goldens(observed, os.environ)

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
# THE RE-RECORD REFUSAL, READ FOR PRESENCE
# ---------------------------------------------------------------------------
#
# `CI=""` used to slip past this guard. The recording arm read
# `assert not os.environ.get("CI")` — a TRUTHINESS test, which an empty-string
# `CI` satisfies — so a CI run with `CI` exported empty could re-record the
# engine-authored goldens and then "pass" against the values it had just
# written. That is the shape of a control that quietly disables itself: the
# assertion is present, reads correctly to a human, and does not fire.
#
# `ci/fetch_reference_corpus.py` already read its own `CI` for PRESENCE, and its
# module docstring recorded the asymmetry as deliberate — a lost regression
# signal here was judged survivable where a destroyed independent corpus was
# not. The asymmetry is now closed in the safe direction instead: BOTH read for
# presence. The cost of a false refusal is one `unset CI`; the cost of a false
# permit is a golden file that agrees with whatever the engine said that day.
#
# Every test below drives the REAL functions the recording arm runs
# (`golden_recording_refusal`, `record_goldens`) — never a re-implementation of
# the rule here, which would keep passing after the real guard was neutered.


def _environ(**overrides: str) -> dict[str, str]:
    """A deliberately EMPTY base environment plus explicit overrides.

    Never `os.environ.copy()`: on a GitHub runner the ambient `CI=true` would
    make the "local" cases refuse for the wrong reason, and these tests would
    pass while proving something else entirely.
    """
    return dict(overrides)


def test_recording_is_refused_in_ci() -> None:
    """A CI run must never rewrite the values it is checking."""
    for environ in (
        _environ(CI="true"),
        _environ(CI="1"),
        _environ(CI="true", **{_UPDATE_ENV: "1"}),
    ):
        reason = golden_recording_refusal(environ)
        assert reason is not None, (
            f"recording was permitted under {environ!r} — a CI run must never "
            "re-record the Swiss goldens"
        )
        assert _CI_ENV in reason, "the refusal must name the variable it read"


def test_recording_refusal_reads_ci_for_presence_not_truthiness() -> None:
    """`CI=""` must refuse. This is the defect this guard was fixed for.

    The previous form, `assert not os.environ.get("CI")`, passed on every falsy
    string — `""` most obviously, and that is a value CI systems really do
    export. `ci/fetch_reference_corpus.py::refusal_reason` has always read its
    own `CI` for presence; this asserts the same semantics here, so a future
    "simplification" back to truthiness reds.
    """
    for value in ("", "0", "false"):
        reason = golden_recording_refusal(_environ(CI=value, **{_UPDATE_ENV: "1"}))
        assert reason is not None, (
            f"CI={value!r} was treated as 'not CI'. Read the variable for "
            "PRESENCE, not truthiness — a falsy-but-set CI is still CI."
        )


def test_recording_proceeds_locally_when_ci_is_absent() -> None:
    """The sanctioned deliberate re-record must still be possible off CI.

    A guard that refused everywhere would be uncircumventable and useless: the
    dependency/data bumps this repo takes deliberately have to be re-recordable.
    """
    assert golden_recording_refusal(_environ()) is None
    assert golden_recording_refusal(_environ(**{_UPDATE_ENV: "1"})) is None


def test_the_two_recorders_agree_on_how_ci_is_read() -> None:
    """Both re-record guards in this repository read `CI` the same way.

    The asymmetry between them was deliberate and documented once
    (ci/fetch_reference_corpus.py's module docstring). It is now closed, and
    this pins it closed from the goldens side: if either guard drifts back to a
    truthiness read, exactly one of these two calls returns None and this reds.

    Loaded by path, not `from ci import …`: `ci/` has no `__init__.py` by
    design, and the accuracy job runs a bare console-script pytest that does not
    put the CWD on sys.path.
    """
    import importlib.util

    fetcher_path = pathlib.Path(__file__).resolve().parent.parent / "ci" / "fetch_reference_corpus.py"
    assert fetcher_path.is_file(), f"{fetcher_path} is missing"
    spec = importlib.util.spec_from_file_location("fetch_reference_corpus", fetcher_path)
    fetcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetcher)

    empty_ci = _environ(CI="", **{_UPDATE_ENV: "1"})
    assert golden_recording_refusal(empty_ci) is not None, (
        "the Swiss-goldens guard permitted an empty-string CI"
    )
    assert fetcher.refusal_reason(
        _environ(CI="", **{fetcher.UPDATE_ENV: "1"}), corpus_exists=True
    ) is not None, "the JPL-corpus guard permitted an empty-string CI"


def test_record_goldens_writes_nothing_when_it_refuses(tmp_path: pathlib.Path) -> None:
    """The REFUSAL is enforced by the writer, not merely available beside it.

    Drives the real `record_goldens` — the only function in this module that
    touches the golden file — against a throwaway target. A predicate that
    returns the right answer while the writer never consults it is exactly the
    failure mode this repo keeps finding, so the assertion is about the FILE.
    """
    target = tmp_path / "swiss_ephemeris_goldens.json"
    payload = {"sample_a": {"lagna": "Aries", "ayanamsa_value": 1.0, "longitudes": {}}}

    with pytest.raises(AssertionError) as excinfo:
        record_goldens(payload, _environ(CI="", **{_UPDATE_ENV: "1"}), target=target)

    assert _CI_ENV in str(excinfo.value)
    assert not target.exists(), (
        "record_goldens refused and wrote the file anyway — the refusal must "
        "happen BEFORE the write, not be reported after it"
    )


def test_record_goldens_writes_canonical_bytes_when_permitted(tmp_path: pathlib.Path) -> None:
    """The permitted path must still produce exactly what the checker expects.

    Positive control for the test above: without it, a `record_goldens` that
    refused unconditionally would satisfy every refusal assertion here and
    quietly break the one operation this arm exists to perform.
    """
    target = tmp_path / "nested" / "swiss_ephemeris_goldens.json"
    payload = {"sample_a": {"lagna": "Aries", "ayanamsa_value": 1.0, "longitudes": {}}}

    record_goldens(payload, _environ(**{_UPDATE_ENV: "1"}), target=target)

    raw = target.read_bytes()
    assert b"\r" not in raw, "the recorder must write LF-only bytes"
    assert raw.decode("utf-8") == _canonical(payload)


def test_the_recording_arm_itself_refuses_under_an_empty_ci(swiss_ephemeris: int) -> None:
    """End-to-end: the real test node, in a subprocess, with `CI=""` set.

    The tests above cover the predicate and the writer. This one covers the CALL
    SITE — that `test_chart_matches_swiss_goldens` actually routes its recording
    through `record_goldens` rather than keeping its own inline check. Deleting
    the call site leaves every other test in this block green; it reds here.

    Discriminating by construction: under the OLD truthiness guard this exact
    invocation RECORDED and the node PASSED. Under the fixed guard it must fail
    with the refusal. A skip (no Swiss data) would fail the message assertion
    rather than pass vacuously — and this test takes the `swiss_ephemeris`
    fixture, so it does not run at all without the data files.
    """
    environ = dict(os.environ)
    environ[_UPDATE_ENV] = "1"
    environ[_CI_ENV] = ""  # the exact value the old guard let through

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_swiss_ephemeris.py::test_chart_matches_swiss_goldens",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=pathlib.Path(__file__).resolve().parent.parent,
        env=environ,
        capture_output=True,
        text=True,
        timeout=600,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode != 0, (
        "the recording arm accepted UPDATE_SWISS_GOLDENS=1 with CI set to the "
        f"empty string and passed. Output:\n{output}"
    )
    assert "1 failed" in output, (
        "expected the golden node to FAIL with a refusal; it did not run as "
        f"expected (a skip means the subprocess found no Swiss data). Output:\n{output}"
    )
    assert _UPDATE_ENV in output and _CI_ENV in output, (
        f"the failure was not the re-record refusal. Output:\n{output}"
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
