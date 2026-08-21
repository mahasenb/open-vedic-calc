"""Guards on ci/fetch_reference_corpus.py — the recorder for the independent corpus.

The corpus is only as trustworthy as the request that produced it. Four ways it
could quietly stop being independent, each guarded here:

1. The epoch could be wrong. Every expected value is fetched FOR a Julian Day, so a
   Julian-Day bug shifts all of them together and the corpus still looks perfectly
   self-consistent — it would simply be pinning the wrong instants. The fetcher
   deliberately does not use ``swe.julday`` for this (the epoch definition must not
   come from the library under test), so its own conversion needs checking against
   published values.
2. The wrong target could be requested. Horizons distinguishes a planet's body
   centre (599) from its system barycentre (5); silently swapping them changes the
   answer.
3. An epoch could be added to the fetcher without re-recording the corpus, leaving
   the committed file describing a different set of instants than the code claims.
4. **The corpus could simply be re-recorded to make a red test go away** — the one
   thing ``CLAUDE.md`` says must never happen to this file, because re-recording it
   does not lose a regression signal (as re-recording the engine-authored Swiss
   goldens does), it destroys the only externally-sourced check in the repository.
   Until now that policy was PROSE ONLY: the recorder overwrote the corpus with no
   ceremony whatsoever, so the documented rule had nothing behind it. The
   ``test_recording_*`` block near the end of this file pins the mechanical
   refusal that now enforces it.

Loaded by path via importlib, never ``from ci import ...``: ``ci/`` has no
``__init__.py`` by design, and CI runs the bare ``pytest ci/tests/ -q`` console
script, which does not put the CWD on sys.path (see CLAUDE.md).
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

_CI_DIR = pathlib.Path(__file__).resolve().parent.parent
_REPO = _CI_DIR.parent
_FETCHER = _CI_DIR / "fetch_reference_corpus.py"
_CORPUS = _REPO / "tests" / "goldens" / "independent_reference_corpus.json"


def _load_fetcher():
    spec = importlib.util.spec_from_file_location("fetch_reference_corpus", _FETCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # must not touch the network at import
    return module


# Published Julian Days. Every one is independently verifiable and none comes from
# this repository: J2000.0 is the definition of the epoch, and the rest are the
# worked examples in Meeus, *Astronomical Algorithms*, chapter 7.
_KNOWN_JULIAN_DAYS = [
    ("2000-01-01T12:00:00Z", 2451545.0),      # J2000.0, by definition
    ("1987-01-27T00:00:00Z", 2446822.5),      # Meeus ch. 7
    ("1988-06-19T12:00:00Z", 2447332.0),      # Meeus ch. 7
    ("1900-01-01T00:00:00Z", 2415020.5),      # Meeus ch. 7
    ("1957-10-04T19:26:24Z", 2436116.31),     # Sputnik 1 launch, Meeus ch. 7
]


@pytest.mark.parametrize("timestamp,expected", _KNOWN_JULIAN_DAYS)
def test_julian_day_conversion_matches_published_values(timestamp: str, expected: float) -> None:
    """The fetcher's own Julian Day conversion, against values it did not produce.

    Tolerance 1e-6 day = 0.0864 s. The Moon moves ~0.55 arcsec/s, so an epoch error
    an order of magnitude larger than this would still be invisible in the corpus's
    tolerances — which is exactly why the conversion is checked directly rather than
    inferred from the corpus passing.
    """
    fetcher = _load_fetcher()
    assert fetcher._julian_day_ut(timestamp) == pytest.approx(expected, abs=1e-6)


def test_horizons_targets_are_body_centres_not_barycentres() -> None:
    """Horizons ids 1-9 are system barycentres; 199/299/499/599/699 are body centres.

    swisseph returns body centres. Requesting a barycentre instead would inject a
    real offset for the outer planets while every other check kept passing.
    """
    fetcher = _load_fetcher()
    assert fetcher.HORIZONS_TARGET["Mercury"] == "199"
    assert fetcher.HORIZONS_TARGET["Venus"] == "299"
    assert fetcher.HORIZONS_TARGET["Mars"] == "499"
    assert fetcher.HORIZONS_TARGET["Jupiter"] == "599"
    assert fetcher.HORIZONS_TARGET["Saturn"] == "699"
    assert fetcher.HORIZONS_TARGET["Sun"] == "10"
    assert fetcher.HORIZONS_TARGET["Moon"] == "301"
    assert fetcher.MOON_TARGET == "301"
    assert fetcher.GEOCENTRE == "500@399", (
        "the observer must be the geocentre; a topocentric centre would shift the "
        "Moon by up to a degree"
    )


def test_committed_corpus_describes_the_epochs_the_fetcher_declares() -> None:
    """A new epoch in the fetcher without a re-record leaves the corpus stale."""
    fetcher = _load_fetcher()
    corpus = json.loads(_CORPUS.read_text(encoding="utf-8"))

    declared = [(epoch["id"], epoch["utc"]) for epoch in fetcher.EPOCHS]
    recorded = [(epoch["id"], epoch["utc"]) for epoch in corpus["epochs"]]
    assert declared == recorded, (
        "ci/fetch_reference_corpus.py declares different epochs than the committed "
        "corpus records. Re-record DELIBERATELY, locally, and say why in the PR:\n"
        f"    {fetcher.UPDATE_ENV}=1 python ci/fetch_reference_corpus.py"
    )

    for epoch in corpus["epochs"]:
        expected_jd = fetcher._julian_day_ut(epoch["utc"])
        assert epoch["jd_ut"] == pytest.approx(expected_jd, abs=1e-6), (
            f"{epoch['id']}: recorded jd_ut does not match its own utc field"
        )


def test_every_visible_graha_has_a_horizons_target() -> None:
    fetcher = _load_fetcher()
    for graha in fetcher.VISIBLE_GRAHAS:
        assert graha in fetcher.HORIZONS_TARGET, f"{graha} has no Horizons target id"
    assert set(fetcher.VISIBLE_GRAHAS) == {
        "Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"
    }


# ---------------------------------------------------------------------------
# NEVER-RE-RECORD, MECHANICALLY (not merely documented)
# ---------------------------------------------------------------------------
#
# CLAUDE.md: "NEVER re-record the JPL corpus to make a failure go away — that is
# the one thing it exists to prevent." That sentence has been true since the
# corpus landed and enforced by nothing: `python ci/fetch_reference_corpus.py`
# silently truncated and rewrote the committed file. A policy whose only
# implementation is a paragraph is the same shape as a control that quietly
# disables itself — it looks enforced and is not.
#
# The refusal mirrors UPDATE_SWISS_GOLDENS (tests/test_swiss_ephemeris.py) with
# ONE deliberate difference, called out in `test_ci_refusal_is_stricter_than_a
# _truthiness_check` below: the goldens read `os.environ.get("CI")` for
# truthiness, so `CI=""` slips through; this reads for PRESENCE. The asymmetry is
# intentional and fails in the safe direction — a false refusal costs one `unset
# CI`, a false permit destroys the only independent check in the repository.
#
# Every test below drives the REAL functions the script runs (`refusal_reason`,
# `main`), never a re-implementation of their logic here. A duplicated copy of
# the rule would keep passing after the real gate was neutered, which is the
# exact failure this block exists to catch one level down.


def _environ(**overrides: str) -> dict[str, str]:
    """A deliberately EMPTY base environment plus explicit overrides.

    Never `os.environ.copy()`: on a GitHub runner the ambient `CI=true` would make
    the "local" cases refuse for the wrong reason and the tests would pass while
    proving something else entirely.
    """
    return dict(overrides)


def test_recording_over_an_existing_corpus_is_refused_without_the_flag() -> None:
    """The default, no-ceremony invocation must now stop."""
    fetcher = _load_fetcher()
    reason = fetcher.refusal_reason(_environ(), corpus_exists=True)
    assert reason is not None, (
        "`python ci/fetch_reference_corpus.py` over an existing corpus was permitted "
        "with no flag — the never-re-record policy is prose again"
    )
    assert fetcher.UPDATE_ENV in reason, (
        f"the refusal must name {fetcher.UPDATE_ENV}, or the operator cannot act on it"
    )


def test_recording_over_an_existing_corpus_proceeds_with_the_flag_locally() -> None:
    """The sanctioned deliberate re-record must still be possible off CI."""
    fetcher = _load_fetcher()
    assert fetcher.refusal_reason(
        _environ(**{fetcher.UPDATE_ENV: "1"}), corpus_exists=True
    ) is None


def test_recording_is_refused_in_ci_even_with_the_flag() -> None:
    """CI is refused ALWAYS — the flag does not buy a way through it.

    A CI run rewriting the file it is checking is the whole defect. Both the
    corpus-present and corpus-absent cases are refused: "record it fresh" is not a
    thing CI may do either, because the result would be an unreviewed corpus that
    agrees with whatever the engine said that day.
    """
    fetcher = _load_fetcher()
    for corpus_exists in (True, False):
        for environ in (
            _environ(CI="true"),
            _environ(CI="true", **{fetcher.UPDATE_ENV: "1"}),
            _environ(CI="1", **{fetcher.UPDATE_ENV: "1"}),
        ):
            reason = fetcher.refusal_reason(environ, corpus_exists=corpus_exists)
            assert reason is not None, (
                f"recording was permitted under {environ!r} with corpus_exists="
                f"{corpus_exists} — a CI run must never write this file"
            )
            assert "CI" in reason


def test_ci_refusal_is_stricter_than_a_truthiness_check() -> None:
    """`CI=""` must still refuse.

    `tests/test_swiss_ephemeris.py` guards the engine-recorded goldens with
    `assert not os.environ.get("CI")`, which an empty-string `CI` satisfies. That
    is survivable there (a lost regression signal); here it would let a CI run
    overwrite the only externally-sourced evidence in the repo. This asserts the
    PRESENCE reading, so a future "simplification" to truthiness reds here.
    """
    fetcher = _load_fetcher()
    reason = fetcher.refusal_reason(
        _environ(CI="", **{fetcher.UPDATE_ENV: "1"}), corpus_exists=True
    )
    assert reason is not None, (
        "an empty-string CI was treated as 'not CI'. Read the variable for "
        "PRESENCE, not truthiness — see this test's docstring for why the two "
        "corpora justify different strictness."
    )


def test_recording_a_corpus_that_does_not_exist_yet_needs_no_flag_locally() -> None:
    """First-time recording off CI is not the hazard; overwriting evidence is.

    Requiring the flag to create a file that does not exist would be ceremony with
    nothing behind it, and would make the refusal message misleading.
    """
    fetcher = _load_fetcher()
    assert fetcher.refusal_reason(_environ(), corpus_exists=False) is None


def test_check_mode_is_never_gated() -> None:
    """`--check` writes nothing, so it stays available everywhere — including CI.

    This matters: a CI drift job that fetches Horizons and DIFFS is exactly the
    right use of this script in CI, and gating it would push an operator toward
    the recording path to get the same answer.
    """
    fetcher = _load_fetcher()
    assert fetcher.refusal_reason(
        _environ(CI="true"), corpus_exists=True, check_only=True
    ) is None
    assert fetcher.refusal_reason(
        _environ(), corpus_exists=True, check_only=True
    ) is None


def test_main_refuses_before_it_ever_reaches_the_network(monkeypatch) -> None:
    """The gate must sit BEFORE `build()`, not after.

    A refusal that happens after the Horizons round trip still costs the fetch and,
    worse, means the write is guarded by ordering rather than by the check. Proved
    by making `build` itself the failure: if control reaches it, the test fails
    with that sentinel instead of the expected non-zero return.
    """
    fetcher = _load_fetcher()

    def _must_not_run() -> dict:
        raise AssertionError(
            "main() called build() — the refusal is downstream of the network call"
        )

    monkeypatch.setattr(fetcher, "build", _must_not_run)
    monkeypatch.setattr(sys, "argv", ["fetch_reference_corpus.py"])
    monkeypatch.delenv(fetcher.UPDATE_ENV, raising=False)
    monkeypatch.delenv("CI", raising=False)

    assert fetcher.main() != 0, "a refused record returned a SUCCESS exit code"


def test_main_reaches_the_fetch_once_the_flag_is_set(monkeypatch) -> None:
    """The inverse of the test above — the gate must not be a blanket refusal.

    Without this, `refusal_reason` returning a string unconditionally would satisfy
    every other test in this block while making the sanctioned re-record impossible.
    `build` raises a sentinel rather than fetching, so this proves control reached
    the fetch without touching the network or writing the corpus.
    """
    fetcher = _load_fetcher()

    class _ReachedTheFetch(Exception):
        pass

    def _sentinel() -> dict:
        raise _ReachedTheFetch

    monkeypatch.setattr(fetcher, "build", _sentinel)
    monkeypatch.setattr(sys, "argv", ["fetch_reference_corpus.py"])
    monkeypatch.setenv(fetcher.UPDATE_ENV, "1")
    monkeypatch.delenv("CI", raising=False)

    with pytest.raises(_ReachedTheFetch):
        fetcher.main()


def test_the_committed_corpus_is_not_overwritten_by_a_default_invocation(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end, as an operator would hit it: run the real script, unflagged.

    A subprocess is the honest form here — it proves the SCRIPT refuses, not merely
    that a function inside it would have. The environment is built explicitly (no
    `CI`, no flag) so this asserts the missing-flag refusal on a GitHub runner too,
    where an inherited `CI=true` would otherwise refuse for a different reason and
    make the test vacuous.
    """
    before = _CORPUS.read_bytes()
    environment = {
        # SYSTEMROOT is required for a Python subprocess to start on Windows.
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT")
        if key in os.environ
    }
    completed = subprocess.run(
        [sys.executable, str(_FETCHER)],
        cwd=str(_REPO),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode != 0, (
        f"the unflagged recorder exited 0.\nstdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    combined = completed.stdout + completed.stderr
    assert "UPDATE_INDEPENDENT_CORPUS" in combined, (
        f"the refusal was not loud enough to act on:\n{combined}"
    )
    assert _CORPUS.read_bytes() == before, "the committed corpus was modified"
    assert "fetching the independent reference corpus" not in combined, (
        "the script announced a Horizons fetch before refusing — the gate is "
        f"downstream of the network call:\n{combined}"
    )
