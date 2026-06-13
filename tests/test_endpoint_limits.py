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

client = TestClient(app, headers={"X-Calc-Service-Token": "test"})


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


# ---------------------------------------------------------------------------
# Malformed date strings are a 422 (schema validation), never a 500 from
# datetime.strptime inside the handler.
# ---------------------------------------------------------------------------

def test_dashas_malformed_date_is_422():
    req = {**SAMPLE_A, "from_date": "not-a-date", "to_date": "2026-01-01"}
    r = client.post("/v1/dashas", json=req)
    assert r.status_code == 422


def test_transits_malformed_date_is_422():
    req = {**SAMPLE_A, "at_date": "2026-13-99"}
    r = client.post("/v1/transits", json=req)
    assert r.status_code == 422


def test_muhurat_malformed_date_is_422():
    req = {**SAMPLE_A, "start_date": "2026/01/01", "end_date": "2026-01-02"}
    r = client.post("/v1/muhurat", json=req)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# step_seconds below the 60s floor is a 422 (DoS guard).
# ---------------------------------------------------------------------------

def test_lagna_shuddhi_sub_minute_step_is_422():
    req = {
        **SAMPLE_A,
        "start_date": "2026-05-26",
        "end_date": "2026-05-27",
        "activity_category": "generic",
        "step_seconds": 1,
    }
    r = client.post("/v1/muhurat/lagna-shuddhi", json=req)
    assert r.status_code == 422


def test_family_lagna_shuddhi_sub_minute_step_is_422():
    req = {
        "members": [SAMPLE_A, SAMPLE_B],
        "start_date": "2026-05-26",
        "end_date": "2026-05-27",
        "activity_category": "generic",
        "step_seconds": 1,
    }
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=req)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# /v1/dashas date-range guards (CALC-1 DoS fix)
# ---------------------------------------------------------------------------

def test_dashas_unbounded_far_future_is_422():
    """to_date=9999-12-31 must be rejected before any period materialization."""
    req = {**SAMPLE_A, "from_date": "2000-01-01", "to_date": "9999-12-31",
           "systems": ["vimshottari"]}
    r = client.post("/v1/dashas", json=req)
    assert r.status_code == 422
    assert "exceeds" in r.text.lower()


def test_dashas_reversed_range_is_422():
    """to_date before from_date must return 422."""
    req = {**SAMPLE_A, "from_date": "2030-01-01", "to_date": "2020-01-01",
           "systems": ["vimshottari"]}
    r = client.post("/v1/dashas", json=req)
    assert r.status_code == 422
    assert "from_date" in r.text.lower() or "to_date" in r.text.lower()


def test_dashas_full_life_120_year_span_returns_mahadashas():
    """A full Vimshottari cycle (birth → birth+120y, ~43,830 days) must NOT be
    rejected and must return a non-empty timeline with all 9 mahadashas.

    This is the anti-regression guard against a naive 365-day cap: the 120-year
    cycle is the legitimate upper bound of a real full-life dasha request."""
    birth = SAMPLE_A["birth_date"]  # "1950-06-15"
    from_date = birth
    # 120 years × 365.25 = 43,830 days; use a round 44,000 to stay comfortably
    # inside MAX_DASHA_DAYS=47000 while covering the full cycle.
    from datetime import date, timedelta
    birth_dt = date.fromisoformat(birth)
    to_dt = birth_dt + timedelta(days=44000)
    req = {**SAMPLE_A, "from_date": from_date, "to_date": to_dt.isoformat(),
           "systems": ["vimshottari"]}
    r = client.post("/v1/dashas", json=req)
    assert r.status_code == 200, f"Full 120-year span must not be rejected: {r.text}"
    periods = r.json()
    mahadashas = [p for p in periods if p["level"] == "mahadasha"]
    assert len(mahadashas) > 0, "No mahadashas returned for full-life span"
    # All 9 Vimshottari lords should appear over 120 years
    lords = {p["lord"] for p in mahadashas}
    assert len(lords) >= 9, f"Expected all 9 dasha lords, got {lords}"


def test_dashas_boundary_at_max_passes():
    """to_date exactly MAX_DASHA_DAYS days from birth must return 200.

    The cap is measured from birth (bounds cycle_count), not from from_date.
    SAMPLE_A birth is 1950-06-15; birth + 47000 days is still within the
    legitimate range and must not be rejected."""
    import app.main as main_mod
    from datetime import date, timedelta
    limit = main_mod.MAX_DASHA_DAYS
    birth_dt = date.fromisoformat(SAMPLE_A["birth_date"])
    to_dt = birth_dt + timedelta(days=limit)
    req = {**SAMPLE_A, "from_date": SAMPLE_A["birth_date"], "to_date": to_dt.isoformat(),
           "systems": ["vimshottari"]}
    r = client.post("/v1/dashas", json=req)
    assert r.status_code == 200, f"Exactly MAX_DASHA_DAYS from birth should pass: {r.text}"


def test_dashas_one_over_max_is_422():
    """to_date at MAX_DASHA_DAYS+1 days from birth must be rejected with 422.

    The cap is measured from birth; from_date is irrelevant to the guard."""
    import app.main as main_mod
    from datetime import date, timedelta
    limit = main_mod.MAX_DASHA_DAYS
    birth_dt = date.fromisoformat(SAMPLE_A["birth_date"])
    to_dt = birth_dt + timedelta(days=limit + 1)
    req = {**SAMPLE_A, "from_date": SAMPLE_A["birth_date"], "to_date": to_dt.isoformat(),
           "systems": ["vimshottari"]}
    r = client.post("/v1/dashas", json=req)
    assert r.status_code == 422, f"MAX_DASHA_DAYS+1 from birth must be rejected: {r.status_code}"


def test_dashas_far_future_narrow_window_is_422():
    """Far-future narrow window that bypassed the old from_date cap must now be 422.

    An attacker passing from_date=9900-01-01, to_date=9999-12-31 (span < 47000 days)
    with a normal birth year would force the engine to materialise ~60+ Vimshottari
    cycles from birth before window-filtering.  The new birth-relative guard catches
    this: (9999-12-31 - 1950-06-15).days >> MAX_DASHA_DAYS."""
    req = {**SAMPLE_A, "from_date": "9900-01-01", "to_date": "9999-12-31",
           "systems": ["vimshottari"]}
    r = client.post("/v1/dashas", json=req)
    assert r.status_code == 422, f"Far-future narrow window must be rejected: {r.status_code}"
    assert "exceeds" in r.text.lower()


# ---------------------------------------------------------------------------
# /v1/dashas request-field bounds (CALC-3 string caps, CALC-4 systems Literal)
# ---------------------------------------------------------------------------

def _dasha_req(**overrides):
    base = {**SAMPLE_A, "from_date": "2000-01-01", "to_date": "2010-01-01",
            "systems": ["vimshottari"]}
    base.update(overrides)
    return base


def test_dashas_overlong_name_is_422():
    r = client.post("/v1/dashas", json=_dasha_req(name="x" * 121))
    assert r.status_code == 422


def test_dashas_empty_name_is_422():
    r = client.post("/v1/dashas", json=_dasha_req(name=""))
    assert r.status_code == 422


def test_dashas_overlong_birth_place_is_422():
    r = client.post("/v1/dashas", json=_dasha_req(birth_place="p" * 201))
    assert r.status_code == 422


def test_dashas_unknown_system_is_422():
    """An unknown dasha system is rejected at the boundary, not silently dropped."""
    r = client.post("/v1/dashas", json=_dasha_req(systems=["kalachakra"]))
    assert r.status_code == 422


def test_dashas_too_many_systems_is_422():
    r = client.post("/v1/dashas", json=_dasha_req(systems=["vimshottari", "yogini", "vimshottari"]))
    assert r.status_code == 422


def test_dashas_yogini_system_accepted():
    r = client.post("/v1/dashas", json=_dasha_req(systems=["yogini"]))
    assert r.status_code == 200, r.text


def test_dashas_empty_systems_still_accepted():
    """Explicit empty systems remains a valid request (contract preserved)."""
    r = client.post("/v1/dashas", json=_dasha_req(systems=[]))
    assert r.status_code == 200, r.text
    assert r.json() == []
