"""Edge/limit coverage: date-range guards, a varied compat pair, a longer scan.

Adds margin above the 90% gate and exercises the 422 range-limit branches in
app/main.py plus additional kuta/per-day branches in compat.py / muhurat.py.

FIX #7: reversed date-range (end < start) tests for all three endpoints.
FIX #11: bhavabala quartile rank distribution (4/4/4 split) test.
"""
import os

from fastapi.testclient import TestClient

os.environ.setdefault("CALC_SERVICE_TOKEN", "test")
os.environ.setdefault("PUBLIC_SOURCE_URL", "https://example.com")

from app.main import app
from tests.conftest import SAMPLE_A, SAMPLE_B, SAMPLE_C

client = TestClient(app, headers={"Authorization": "Bearer test"})


def test_muhurat_range_exceeding_limit_is_422():
    req = {**SAMPLE_A, "start_date": "2026-01-01", "end_date": "2027-06-01"}  # > 365 days
    r = client.post("/v1/muhurat", json=req)
    assert r.status_code == 422
    assert "exceeds" in r.text.lower()


def test_lagna_shuddhi_range_exceeding_limit_is_422():
    req = {
        **SAMPLE_A,
        "start_date": "2026-01-01",
        "end_date": "2027-06-01",  # > 365 days
        "activity_category": "generic",
        "step_seconds": 60,
    }
    r = client.post("/v1/muhurat/lagna-shuddhi", json=req)
    assert r.status_code == 422


def test_compat_alternate_pair_scores():
    """A second pairing hits kuta-scoring branches the A×B golden test misses."""
    r = client.post("/v1/compat", json={"person_a": SAMPLE_A, "person_b": SAMPLE_C})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "total_score" in body and "max_score" in body
    assert 0 <= body["total_score"] <= body["max_score"]
    assert isinstance(body.get("kutas"), list) and body["kutas"]


def test_muhurat_longer_scan_covers_more_day_branches():
    """A ~10-day scan exercises per-day chogadiya/muhurta/tara-bala variety."""
    req = {**SAMPLE_B, "start_date": "2026-03-01", "end_date": "2026-03-10"}
    r = client.post("/v1/muhurat", json=req)
    assert r.status_code == 200
    days = r.json()["days"]
    assert len(days) == 10


# ---------------------------------------------------------------------------
# FIX #7 — reversed date range must return 422 for all three endpoints
# ---------------------------------------------------------------------------

def test_muhurat_reversed_range_is_422():
    """end_date before start_date returns 422 (not a silent empty 200)."""
    req = {**SAMPLE_A, "start_date": "2026-05-10", "end_date": "2026-05-01"}
    r = client.post("/v1/muhurat", json=req)
    assert r.status_code == 422
    assert "start_date" in r.text.lower() or "end_date" in r.text.lower()


def test_muhurat_valid_range_returns_200():
    """Positive control: same-day range (end == start) returns 200."""
    req = {**SAMPLE_A, "start_date": "2026-05-26", "end_date": "2026-05-26"}
    r = client.post("/v1/muhurat", json=req)
    assert r.status_code == 200


def test_lagna_shuddhi_reversed_range_is_422():
    """end_date before start_date returns 422."""
    req = {
        **SAMPLE_A,
        "start_date": "2026-05-10",
        "end_date": "2026-05-01",
        "activity_category": "generic",
        "step_seconds": 60,
    }
    r = client.post("/v1/muhurat/lagna-shuddhi", json=req)
    assert r.status_code == 422


def test_lagna_shuddhi_valid_range_returns_200():
    """Positive control: end >= start returns 200."""
    req = {
        **SAMPLE_A,
        "start_date": "2026-05-26",
        "end_date": "2026-05-27",
        "activity_category": "generic",
        "step_seconds": 60,
    }
    r = client.post("/v1/muhurat/lagna-shuddhi", json=req)
    assert r.status_code == 200


def test_family_lagna_shuddhi_reversed_range_is_422():
    """end_date before start_date returns 422 for family endpoint."""
    req = {
        "members": [SAMPLE_A, SAMPLE_B],
        "start_date": "2026-05-10",
        "end_date": "2026-05-01",
        "activity_category": "generic",
        "step_seconds": 60,
    }
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=req)
    assert r.status_code == 422


def test_family_lagna_shuddhi_valid_range_returns_200():
    """Positive control: valid range returns 200 for family endpoint."""
    req = {
        "members": [SAMPLE_A, SAMPLE_B],
        "start_date": "2026-05-26",
        "end_date": "2026-05-27",
        "activity_category": "generic",
        "step_seconds": 60,
    }
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=req)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# FIX #11 — bhavabala quartile ranking (length-derived q1/q3 boundaries)
# ---------------------------------------------------------------------------

def test_bhavabala_quartile_ranking_is_monotonic():
    """All 12 bhavabala houses get a valid rank, both extremes are present, and
    the ranking is monotonic in bala_total — strong/average/weak are value bands
    around length-derived quartile boundaries (q1=index n//4, q3=index n-n//4-1),
    so they never interleave. The split targets ~4/4/4 but boundary ties can
    shift a house between adjacent bands, so exact counts are not asserted."""
    r = client.post("/v1/strength", json=SAMPLE_A)
    assert r.status_code == 200
    bhavabala = r.json()["bhavabala"]
    assert len(bhavabala) == 12
    ranks = [item["rank"] for item in bhavabala]
    assert all(rk in ("strong", "weak", "average") for rk in ranks), f"Unexpected ranks: {ranks}"
    # Real chart data has spread, so both extreme bands are populated.
    assert "strong" in ranks and "weak" in ranks, f"Missing an extreme band: {ranks}"
    # Bands are defined by value thresholds, so sorting houses by bala_total must
    # yield non-decreasing rank strength (no weak house above a stronger one).
    strength = {"weak": 0, "average": 1, "strong": 2}
    by_total = sorted(bhavabala, key=lambda x: x["bala_total"])
    levels = [strength[x["rank"]] for x in by_total]
    assert levels == sorted(levels), (
        "ranks not monotonic by bala_total: "
        f"{[(round(x['bala_total'], 2), x['rank']) for x in by_total]}"
    )
