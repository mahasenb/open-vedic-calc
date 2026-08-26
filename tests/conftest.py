"""
Synthetic test fixtures — no real personal data committed here.
Fill in coordinates/dates with any well-documented public birth data
(e.g., historical figures) to make the goldens verifiable.
"""
import os

import pytest

# Set the auth environment variables before any app import in the test suite.
# ENVIRONMENT=test keeps the calc service in insecure mode (auth disabled)
# without touching a real token, so endpoint-level tests using the
# X-Calc-Service-Token="test" header work correctly.
# These must be set before app.auth is first imported, which happens when
# any test module that imports app.main is collected.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("CALC_SERVICE_TOKEN", "test")
os.environ.setdefault("PUBLIC_SOURCE_URL", "https://example.com")

# Synthetic sample A: arbitrary date, Sri Lanka coordinates
SAMPLE_A = {
    "name": "sample_a",
    "birth_date": "1950-06-15",
    "birth_time": "06:00:00",
    "birth_place": "Sample City",
    "latitude": 7.0,
    "longitude": 80.0,
    "timezone_offset_hours": 5.5,
}

# Synthetic sample B
SAMPLE_B = {
    "name": "sample_b",
    "birth_date": "1975-12-01",
    "birth_time": "12:30:00",
    "birth_place": "Sample City",
    "latitude": 6.9,
    "longitude": 79.8,
    "timezone_offset_hours": 5.5,
}

# Synthetic sample C
SAMPLE_C = {
    "name": "sample_c",
    "birth_date": "2000-03-21",
    "birth_time": "09:00:00",
    "birth_place": "Sample City",
    "latitude": 6.9,
    "longitude": 79.8,
    "timezone_offset_hours": 5.5,
}


# ---------------------------------------------------------------------------
# Swiss-data gate (shared)
# ---------------------------------------------------------------------------
# Lives here rather than in tests/test_swiss_ephemeris.py because BOTH accuracy
# modules need it: that one (self-recorded goldens) and
# tests/test_independent_reference_corpus.py (NASA/JPL Horizons values). A second
# copy would be a second thing to weaken.

REQUIRE_SWISS_EPHEMERIS_ENV = "REQUIRE_SWISS_EPHEMERIS"


def swiss_ephemeris_required() -> bool:
    return os.environ.get(REQUIRE_SWISS_EPHEMERIS_ENV) == "1"


@pytest.fixture(scope="session")
def swiss_ephemeris() -> int:
    """Gate an accuracy test on real Swiss data actually being in use.

    Hard-fails (never skips) when ``REQUIRE_SWISS_EPHEMERIS=1``: the whole point of
    the accuracy job is that it cannot pass by quietly not running.
    """
    import pathlib

    import swisseph as swe

    from bphs_core import utils

    swiss_active, retflag = utils.probe_ephemeris_source()
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
    if swiss_ephemeris_required():
        pytest.fail(f"{REQUIRE_SWISS_EPHEMERIS_ENV}=1 but {message}")
    pytest.skip(message)
