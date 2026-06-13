"""
Tests for the diversified-alternatives feature on the muhurat scan endpoints.

Covers:
  - Unit tests for _select_alternatives (determinism, diversification, cap)
  - Unit test for _tolerance_window (shape, width bound)
  - Regression guard: best_window unchanged after _tolerance_window refactor
  - Integration: solo and family endpoint shapes include alternatives
  - Family: window=None, per-record band recomputed from own balam gate
"""
import os
import pytest

os.environ.setdefault("CALC_SERVICE_TOKEN", "test")
os.environ.setdefault("PUBLIC_SOURCE_URL", "https://example.com")

from fastapi.testclient import TestClient
from app.main import app
from bphs_core.lagna_shuddhi import (
    scan_lagna_shuddhi,
    scan_family_lagna_shuddhi,
    _select_alternatives,
    _tolerance_window,
    _hhmm_to_mins,
    MAX_ALTERNATIVES,
    ALT_MIN_SEPARATION_MINS,
)
from tests.conftest import SAMPLE_A, SAMPLE_B

client = TestClient(app, headers={"X-Calc-Service-Token": "test"})

# ---------------------------------------------------------------------------
# Shared request fixtures
# ---------------------------------------------------------------------------

_LAGNA_SHUDDHI_REQ = {
    **SAMPLE_A,
    "start_date": "2026-06-15",
    "end_date": "2026-06-17",
    "activity_category": "generic",
    "step_seconds": 60,
}

_FAMILY_REQ_CLEAN = {
    "members": [
        {**SAMPLE_A, "birth_date": "1950-06-15", "birth_time": "06:00:00"},
        {**SAMPLE_B, "birth_date": "1975-12-01", "birth_time": "12:30:00"},
    ],
    "start_date": "2026-06-15",
    "end_date": "2026-06-17",
    "activity_category": "generic",
    "step_seconds": 60,
}


# ---------------------------------------------------------------------------
# _select_alternatives unit tests (pure function, no ephemeris needed)
# ---------------------------------------------------------------------------

def _make_ranked(entries):
    """Build a minimal ranked list: entries is [(instant, score), ...], sorted desc."""
    return [{"instant": inst, "score": sc, "min_score": sc} for inst, sc in entries]


def test_select_alternatives_never_includes_best():
    ranked = _make_ranked([
        ("2026-06-15 09:00", 0.90),
        ("2026-06-16 10:00", 0.80),
        ("2026-06-17 08:00", 0.75),
    ])
    best = ranked[0]
    alts = _select_alternatives(ranked, best, score_key="score")
    assert all(a["instant"] != best["instant"] for a in alts)
    assert best not in alts


def test_select_alternatives_same_date_separation():
    """Two entries on the same date within ALT_MIN_SEPARATION_MINS of best are rejected."""
    # best at 09:00; second at 09:30 (30 min < 60 min threshold) — must be rejected
    ranked = _make_ranked([
        ("2026-06-15 09:00", 0.90),
        ("2026-06-15 09:30", 0.85),   # 30 min gap — too close
        ("2026-06-15 11:00", 0.80),   # 120 min gap — acceptable
        ("2026-06-16 10:00", 0.78),   # different date — always OK
    ])
    best = ranked[0]
    alts = _select_alternatives(ranked, best, score_key="score")
    instants = [a["instant"] for a in alts]
    assert "2026-06-15 09:30" not in instants, "30-min-gap entry must be rejected"
    assert "2026-06-15 11:00" in instants, "120-min-gap entry must be accepted"
    assert "2026-06-16 10:00" in instants, "Different-date entry must be accepted"


def test_select_alternatives_different_dates_always_allowed():
    """Entries on distinct dates are never same-day-filtered."""
    ranked = _make_ranked([
        ("2026-06-15 09:00", 0.90),
        ("2026-06-16 09:05", 0.85),   # same time-of-day but different date
        ("2026-06-17 09:10", 0.80),
    ])
    best = ranked[0]
    alts = _select_alternatives(ranked, best, score_key="score")
    assert len(alts) == 2


def test_select_alternatives_cap():
    """Never returns more than MAX_ALTERNATIVES entries."""
    entries = [("2026-06-15 09:00", 0.90)]
    # Add 10 entries on distinct dates (all pass the same-day filter)
    for day in range(16, 26):
        entries.append((f"2026-06-{day:02d} 10:00", 0.80 - day * 0.01))
    ranked = _make_ranked(entries)
    best = ranked[0]
    alts = _select_alternatives(ranked, best, score_key="score")
    assert len(alts) <= MAX_ALTERNATIVES


def test_select_alternatives_sorted_by_score_desc():
    """Returned alternatives are in score-descending order (greedy walk preserves it)."""
    entries = [("2026-06-15 09:00", 0.90)]
    for day in range(16, 22):
        entries.append((f"2026-06-{day:02d} 10:00", 0.90 - day * 0.02))
    ranked = _make_ranked(entries)
    best = ranked[0]
    alts = _select_alternatives(ranked, best, score_key="score")
    scores = [a["score"] for a in alts]
    assert scores == sorted(scores, reverse=True)


def test_select_alternatives_determinism():
    """Calling twice with identical inputs yields identical output."""
    entries = [("2026-06-15 09:00", 0.90)]
    for day in range(16, 22):
        entries.append((f"2026-06-{day:02d} 10:00", 0.85 - day * 0.01))
    ranked = _make_ranked(entries)
    best = ranked[0]
    alts1 = _select_alternatives(ranked, best, score_key="score")
    alts2 = _select_alternatives(ranked, best, score_key="score")
    assert [a["instant"] for a in alts1] == [a["instant"] for a in alts2]


def test_select_alternatives_same_date_mutual_exclusion():
    """Two alternatives on the same date must also be >= ALT_MIN_SEPARATION_MINS apart."""
    # best: 09:00 on day 15
    # alt1 candidate: 11:00 on day 15 (120 min from best — accepted)
    # alt2 candidate: 11:30 on day 15 (30 min from alt1 — must be rejected)
    # alt3 candidate: 13:00 on day 15 (120 min from alt1 — accepted)
    ranked = _make_ranked([
        ("2026-06-15 09:00", 0.90),
        ("2026-06-15 11:00", 0.85),
        ("2026-06-15 11:30", 0.83),
        ("2026-06-15 13:00", 0.80),
    ])
    best = ranked[0]
    alts = _select_alternatives(ranked, best, score_key="score")
    instants = [a["instant"] for a in alts]
    assert "2026-06-15 11:30" not in instants, "alt too close to accepted alt must be rejected"
    assert "2026-06-15 11:00" in instants
    assert "2026-06-15 13:00" in instants


def test_select_alternatives_empty_when_only_best():
    ranked = _make_ranked([("2026-06-15 09:00", 0.90)])
    best = ranked[0]
    alts = _select_alternatives(ranked, best, score_key="score")
    assert alts == []


# ---------------------------------------------------------------------------
# _tolerance_window unit test
# ---------------------------------------------------------------------------

def _make_pool(date_str, entries):
    """entries: [(hhmm, score), ...] — build a minimal pool for _tolerance_window."""
    return [{"instant": f"{date_str} {hhmm}", "score": sc} for hhmm, sc in entries]


def test_tolerance_window_width_le_11():
    """The window built around a centre must be at most 11 minutes wide."""
    date_str = "2026-06-15"
    # Dense pool around 10:00 with uniformly high scores
    pool = _make_pool(date_str, [
        ("09:55", 0.90), ("09:56", 0.90), ("09:57", 0.90), ("09:58", 0.90),
        ("09:59", 0.90), ("10:00", 0.92), ("10:01", 0.90), ("10:02", 0.90),
        ("10:03", 0.90), ("10:04", 0.90), ("10:05", 0.90),
    ])
    center = {"instant": f"{date_str} 10:00", "score": 0.92}
    w = _tolerance_window(center, pool, "Test window")
    start_m = _hhmm_to_mins(w["start"])
    end_m = _hhmm_to_mins(w["end"])
    assert "start" in w and "end" in w and "label" in w
    assert end_m - start_m <= 11, f"Window too wide: {end_m - start_m} min"


def test_tolerance_window_excludes_low_scores():
    """Scores below 0.85 * center_score must not extend the window."""
    date_str = "2026-06-15"
    pool = _make_pool(date_str, [
        ("09:58", 0.50),   # below threshold
        ("09:59", 0.90),   # above threshold (0.85 * 0.92 = 0.782)
        ("10:00", 0.92),
        ("10:01", 0.90),
        ("10:02", 0.50),   # below threshold
    ])
    center = {"instant": f"{date_str} 10:00", "score": 0.92}
    w = _tolerance_window(center, pool, "Test window")
    assert w["start"] == "09:59"
    assert w["end"] == "10:02"  # band_end=10:01, exclusive end=10:02


def test_tolerance_window_single_point():
    """A pool with only the centre returns a 1-minute window."""
    date_str = "2026-06-15"
    pool = _make_pool(date_str, [("10:00", 0.92)])
    center = {"instant": f"{date_str} 10:00", "score": 0.92}
    w = _tolerance_window(center, pool, "Test window")
    assert w["start"] == "10:00"
    assert w["end"] == "10:01"


# ---------------------------------------------------------------------------
# Integration: solo scan — alternatives shape
# ---------------------------------------------------------------------------

def test_solo_alternatives_present_in_result():
    result = scan_lagna_shuddhi(
        lat=SAMPLE_A["latitude"],
        lon=SAMPLE_A["longitude"],
        tz_offset=SAMPLE_A["timezone_offset_hours"],
        birth_nakshatra=None,
        birth_moon_sign=None,
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    assert "alternatives" in result
    assert isinstance(result["alternatives"], list)


def test_solo_alternatives_cap():
    result = scan_lagna_shuddhi(
        lat=SAMPLE_A["latitude"],
        lon=SAMPLE_A["longitude"],
        tz_offset=SAMPLE_A["timezone_offset_hours"],
        birth_nakshatra=None,
        birth_moon_sign=None,
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    assert len(result["alternatives"]) <= MAX_ALTERNATIVES


def test_solo_alternatives_sorted_desc():
    result = scan_lagna_shuddhi(
        lat=SAMPLE_A["latitude"],
        lon=SAMPLE_A["longitude"],
        tz_offset=SAMPLE_A["timezone_offset_hours"],
        birth_nakshatra=None,
        birth_moon_sign=None,
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    alts = result["alternatives"]
    scores = [a["score"] for a in alts]
    assert scores == sorted(scores, reverse=True)


def test_solo_alternatives_excludes_best():
    result = scan_lagna_shuddhi(
        lat=SAMPLE_A["latitude"],
        lon=SAMPLE_A["longitude"],
        tz_offset=SAMPLE_A["timezone_offset_hours"],
        birth_nakshatra=None,
        birth_moon_sign=None,
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    if result["best_instant"] is None:
        pytest.skip("no best instant for this window")
    best_inst = result["best_instant"]["instant"]
    for a in result["alternatives"]:
        assert a["instant"] != best_inst


def test_solo_alternatives_same_date_separation():
    result = scan_lagna_shuddhi(
        lat=SAMPLE_A["latitude"],
        lon=SAMPLE_A["longitude"],
        tz_offset=SAMPLE_A["timezone_offset_hours"],
        birth_nakshatra=None,
        birth_moon_sign=None,
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    if result["best_instant"] is None:
        pytest.skip("no best instant")
    best_date, best_time = result["best_instant"]["instant"].split(" ")
    best_m = _hhmm_to_mins(best_time)

    accepted = [(best_date, best_m)]
    for a in result["alternatives"]:
        a_date, a_time = a["instant"].split(" ")
        a_m = _hhmm_to_mins(a_time)
        for d, m in accepted:
            if d == a_date:
                assert abs(m - a_m) >= ALT_MIN_SEPARATION_MINS, (
                    f"Same-date separation violated: {a['instant']} vs accepted {d} {m}"
                )
        accepted.append((a_date, a_m))


def test_solo_alternatives_window_shape():
    result = scan_lagna_shuddhi(
        lat=SAMPLE_A["latitude"],
        lon=SAMPLE_A["longitude"],
        tz_offset=SAMPLE_A["timezone_offset_hours"],
        birth_nakshatra=None,
        birth_moon_sign=None,
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    for a in result["alternatives"]:
        w = a["window"]
        assert w is not None, "Solo alternative must have a window"
        assert "start" in w and "end" in w
        start_m = _hhmm_to_mins(w["start"])
        end_m = _hhmm_to_mins(w["end"])
        assert end_m - start_m <= 11, f"Alternative window too wide: {end_m - start_m}"


def test_solo_alternatives_determinism():
    kwargs = dict(
        lat=SAMPLE_A["latitude"],
        lon=SAMPLE_A["longitude"],
        tz_offset=SAMPLE_A["timezone_offset_hours"],
        birth_nakshatra=None,
        birth_moon_sign=None,
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    r1 = scan_lagna_shuddhi(**kwargs)
    r2 = scan_lagna_shuddhi(**kwargs)
    assert [a["instant"] for a in r1["alternatives"]] == [a["instant"] for a in r2["alternatives"]]
    assert [a["score"] for a in r1["alternatives"]] == [a["score"] for a in r2["alternatives"]]


def test_solo_best_window_unchanged_after_refactor():
    """Regression guard: _tolerance_window refactor must not change best_window behaviour."""
    result = scan_lagna_shuddhi(
        lat=SAMPLE_A["latitude"],
        lon=SAMPLE_A["longitude"],
        tz_offset=SAMPLE_A["timezone_offset_hours"],
        birth_nakshatra=None,
        birth_moon_sign=None,
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    if result["best_window"] is None:
        pytest.skip("no best window")
    bw = result["best_window"]
    start_m = _hhmm_to_mins(bw["start"])
    end_m = _hhmm_to_mins(bw["end"])
    assert end_m - start_m <= 11, f"best_window too wide after refactor: {end_m - start_m}"


def test_solo_alternatives_have_required_fields():
    result = scan_lagna_shuddhi(
        lat=SAMPLE_A["latitude"],
        lon=SAMPLE_A["longitude"],
        tz_offset=SAMPLE_A["timezone_offset_hours"],
        birth_nakshatra=None,
        birth_moon_sign=None,
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    for a in result["alternatives"]:
        assert "instant" in a
        assert "score" in a
        assert "score_100" in a
        assert "band" in a
        assert "window" in a
        assert a["band"] in ("Excellent", "Good", "Fair", "Avoid")
        assert isinstance(a["score_100"], int)
        assert 0.0 <= a["score"] <= 1.0


# ---------------------------------------------------------------------------
# Integration: family scan — alternatives shape
# ---------------------------------------------------------------------------

def test_family_alternatives_present():
    result = scan_family_lagna_shuddhi(
        members=[
            {
                "name": "member_a",
                "lat": SAMPLE_A["latitude"],
                "lon": SAMPLE_A["longitude"],
                "tz_offset": SAMPLE_A["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
            {
                "name": "member_b",
                "lat": SAMPLE_B["latitude"],
                "lon": SAMPLE_B["longitude"],
                "tz_offset": SAMPLE_B["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
        ],
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    assert "alternatives" in result
    assert isinstance(result["alternatives"], list)


def test_family_alternatives_cap():
    result = scan_family_lagna_shuddhi(
        members=[
            {
                "name": "member_a",
                "lat": SAMPLE_A["latitude"],
                "lon": SAMPLE_A["longitude"],
                "tz_offset": SAMPLE_A["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
            {
                "name": "member_b",
                "lat": SAMPLE_B["latitude"],
                "lon": SAMPLE_B["longitude"],
                "tz_offset": SAMPLE_B["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
        ],
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    assert len(result["alternatives"]) <= MAX_ALTERNATIVES


def test_family_alternatives_window_is_none():
    result = scan_family_lagna_shuddhi(
        members=[
            {
                "name": "member_a",
                "lat": SAMPLE_A["latitude"],
                "lon": SAMPLE_A["longitude"],
                "tz_offset": SAMPLE_A["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
            {
                "name": "member_b",
                "lat": SAMPLE_B["latitude"],
                "lon": SAMPLE_B["longitude"],
                "tz_offset": SAMPLE_B["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
        ],
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    for a in result["alternatives"]:
        assert a["window"] is None, "Family alternatives must have window=None"


def test_family_alternatives_sorted_desc():
    result = scan_family_lagna_shuddhi(
        members=[
            {
                "name": "member_a",
                "lat": SAMPLE_A["latitude"],
                "lon": SAMPLE_A["longitude"],
                "tz_offset": SAMPLE_A["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
            {
                "name": "member_b",
                "lat": SAMPLE_B["latitude"],
                "lon": SAMPLE_B["longitude"],
                "tz_offset": SAMPLE_B["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
        ],
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    scores = [a["score"] for a in result["alternatives"]]
    assert scores == sorted(scores, reverse=True)


def test_family_alternatives_excludes_best():
    result = scan_family_lagna_shuddhi(
        members=[
            {
                "name": "member_a",
                "lat": SAMPLE_A["latitude"],
                "lon": SAMPLE_A["longitude"],
                "tz_offset": SAMPLE_A["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
            {
                "name": "member_b",
                "lat": SAMPLE_B["latitude"],
                "lon": SAMPLE_B["longitude"],
                "tz_offset": SAMPLE_B["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
        ],
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    if result["instant"] is None:
        pytest.skip("no best instant")
    for a in result["alternatives"]:
        assert a["instant"] != result["instant"]


def test_family_alternatives_same_date_separation():
    result = scan_family_lagna_shuddhi(
        members=[
            {
                "name": "member_a",
                "lat": SAMPLE_A["latitude"],
                "lon": SAMPLE_A["longitude"],
                "tz_offset": SAMPLE_A["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
            {
                "name": "member_b",
                "lat": SAMPLE_B["latitude"],
                "lon": SAMPLE_B["longitude"],
                "tz_offset": SAMPLE_B["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
        ],
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    if result["instant"] is None:
        pytest.skip("no best instant")
    best_date, best_time = result["instant"].split(" ")
    best_m = _hhmm_to_mins(best_time)

    accepted = [(best_date, best_m)]
    for a in result["alternatives"]:
        a_date, a_time = a["instant"].split(" ")
        a_m = _hhmm_to_mins(a_time)
        for d, m in accepted:
            if d == a_date:
                assert abs(m - a_m) >= ALT_MIN_SEPARATION_MINS, (
                    f"Same-date separation violated: {a['instant']}"
                )
        accepted.append((a_date, a_m))


def test_family_alternatives_never_outscore_best():
    """Alternatives come from the same gated pool as the recommendation
    (strict pool when consensus is strict), so none may carry a higher
    score than the consensus best."""
    result = scan_family_lagna_shuddhi(
        members=[
            {
                "name": "member_a",
                "lat": SAMPLE_A["latitude"],
                "lon": SAMPLE_A["longitude"],
                "tz_offset": SAMPLE_A["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
            {
                "name": "member_b",
                "lat": SAMPLE_B["latitude"],
                "lon": SAMPLE_B["longitude"],
                "tz_offset": SAMPLE_B["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
        ],
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    if result["instant"] is None:
        pytest.skip("no best instant")
    for a in result["alternatives"]:
        assert a["score"] <= result["score"] + 1e-9, (
            f"Alternative {a['instant']} outscores best: {a['score']} > {result['score']}"
        )


def test_family_alternatives_band_field():
    """Each family alternative must have a valid band field."""
    result = scan_family_lagna_shuddhi(
        members=[
            {
                "name": "member_a",
                "lat": SAMPLE_A["latitude"],
                "lon": SAMPLE_A["longitude"],
                "tz_offset": SAMPLE_A["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
            {
                "name": "member_b",
                "lat": SAMPLE_B["latitude"],
                "lon": SAMPLE_B["longitude"],
                "tz_offset": SAMPLE_B["timezone_offset_hours"],
                "birth_nakshatra": None,
                "birth_moon_sign": None,
            },
        ],
        start_date="2026-06-15",
        end_date="2026-06-17",
        activity="generic",
        step_seconds=60,
    )
    for a in result["alternatives"]:
        assert a["band"] in ("Excellent", "Good", "Fair", "Avoid")


# ---------------------------------------------------------------------------
# Endpoint shape: alternatives present in API responses
# ---------------------------------------------------------------------------

def test_endpoint_solo_alternatives_in_body():
    r = client.post("/v1/muhurat/lagna-shuddhi", json=_LAGNA_SHUDDHI_REQ)
    assert r.status_code == 200
    body = r.json()
    assert "alternatives" in body
    assert isinstance(body["alternatives"], list)
    assert len(body["alternatives"]) <= MAX_ALTERNATIVES


def test_endpoint_family_alternatives_in_body():
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=_FAMILY_REQ_CLEAN)
    assert r.status_code == 200
    body = r.json()
    assert "alternatives" in body
    assert isinstance(body["alternatives"], list)
    assert len(body["alternatives"]) <= MAX_ALTERNATIVES


def test_endpoint_family_alternatives_window_null():
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=_FAMILY_REQ_CLEAN)
    assert r.status_code == 200
    body = r.json()
    for a in body["alternatives"]:
        assert a["window"] is None


def test_endpoint_solo_alternatives_contract_shape():
    """Each alternative in the API response has the contracted fields and valid values."""
    r = client.post("/v1/muhurat/lagna-shuddhi", json=_LAGNA_SHUDDHI_REQ)
    assert r.status_code == 200
    body = r.json()
    for a in body["alternatives"]:
        assert "instant" in a
        assert "score" in a
        assert "score_100" in a
        assert "band" in a
        assert "window" in a
        assert a["band"] in ("Excellent", "Good", "Fair", "Avoid")
        assert isinstance(a["score_100"], int)
        assert 0.0 <= a["score"] <= 1.0
        # Solo: window must be present (not null)
        assert a["window"] is not None
        assert "start" in a["window"]
        assert "end" in a["window"]
