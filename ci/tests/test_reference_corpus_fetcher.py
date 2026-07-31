"""Guards on ci/fetch_reference_corpus.py — the recorder for the independent corpus.

The corpus is only as trustworthy as the request that produced it. Three ways it
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

Loaded by path via importlib, never ``from ci import ...``: ``ci/`` has no
``__init__.py`` by design, and CI runs the bare ``pytest ci/tests/ -q`` console
script, which does not put the CWD on sys.path (see CLAUDE.md).
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

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
        "corpus records. Re-record: python ci/fetch_reference_corpus.py"
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
