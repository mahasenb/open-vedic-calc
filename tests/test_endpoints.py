"""
Deterministic golden tests for the calc service endpoints.
Run: pytest tests/ -v
After first successful run, freeze the actual values in the assertions.
"""
import os
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("CALC_SERVICE_TOKEN", "test")
os.environ.setdefault("PUBLIC_SOURCE_URL", "https://example.com")

from app.main import app
from tests.conftest import SAMPLE_A, SAMPLE_B, SAMPLE_C

client = TestClient(app, headers={"Authorization": "Bearer test"})


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------

def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# /source
# ---------------------------------------------------------------------------

def test_source():
    r = client.get("/source")
    assert r.status_code == 200
    body = r.json()
    assert body["license"] == "AGPL-3.0"
    assert "source_url" in body
    assert "commit" in body


# ---------------------------------------------------------------------------
# /v1/chart — structural tests (golden numerics added after first run)
# ---------------------------------------------------------------------------

_VALID_SIGNS = {
    "Aries","Taurus","Gemini","Cancer","Leo","Virgo",
    "Libra","Scorpio","Sagittarius","Capricorn","Aquarius","Pisces"
}


def test_chart_sample_a_structure():
    r = client.post("/v1/chart", json=SAMPLE_A)
    assert r.status_code == 200
    body = r.json()
    assert body["lagna"] in _VALID_SIGNS
    assert len(body["rasi"]) == 9   # 9 planets
    assert body["ayanamsa_value"] > 20.0  # Lahiri ~23-24° in modern era


def test_chart_new_vargas():
    """D2/D3/D7/D12 must be present and well-formed."""
    r = client.post("/v1/chart", json=SAMPLE_A)
    assert r.status_code == 200
    body = r.json()
    for varga in ("hora", "drekkana", "saptamsa", "dwadasamsa"):
        assert varga in body, f"Missing varga: {varga}"
        assert len(body[varga]) == 9, f"{varga} must have 9 planet entries"
        for p in body[varga]:
            assert p["sign"] in _VALID_SIGNS, f"{varga}/{p['planet']} invalid sign: {p['sign']}"

    # D2 Hora — every planet must be Cancer or Leo (the only two hora signs)
    for p in body["hora"]:
        assert p["sign"] in ("Cancer", "Leo"), (
            f"Hora sign must be Cancer or Leo, got {p['sign']} for {p['planet']}"
        )


def test_chart_deterministic():
    r1 = client.post("/v1/chart", json=SAMPLE_A)
    r2 = client.post("/v1/chart", json=SAMPLE_A)
    assert r1.json() == r2.json()


_SIGN_ORDER = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]


def test_chart_houses_are_whole_sign():
    """Primary `house` must be BPHS whole-sign: house == sign counted from the
    lagna sign. Regression guard against the Placidus-cusp house assignment."""
    r = client.post("/v1/chart", json=SAMPLE_A)
    assert r.status_code == 200
    body = r.json()
    lagna_idx = _SIGN_ORDER.index(body["lagna"])
    for p in body["rasi"]:
        expected = (_SIGN_ORDER.index(p["sign"]) - lagna_idx) % 12 + 1
        assert p["house"] == expected, (
            f"{p['planet']} in {p['sign']} (lagna {body['lagna']}): "
            f"whole-sign house should be {expected}, got {p['house']}"
        )


def test_chart_exposes_bhava_chalit_secondary():
    """Bhava-Chalit is exposed as secondary cusp-based data: 12 cusps plus a
    per-planet chalit_house in 1..12 — without disturbing the whole-sign house."""
    r = client.post("/v1/chart", json=SAMPLE_A)
    assert r.status_code == 200
    body = r.json()
    assert len(body["bhava_chalit_cusps"]) == 12
    for c in body["bhava_chalit_cusps"]:
        assert 0.0 <= c < 360.0
    for p in body["rasi"]:
        assert p["chalit_house"] in range(1, 13), (
            f"{p['planet']} chalit_house out of range: {p['chalit_house']}"
        )


def test_chart_placidus_fallback_logs_warning(monkeypatch, caplog):
    """FIX #6: when swe.houses raises on b'P', the equatorial fallback is used
    and a 'placidus_fallback_equatorial' warning is emitted. Chart still returns 200."""
    import logging
    import bphs_core.chart as chart_mod

    real_houses = chart_mod.swe.houses
    call_count = {"n": 0}

    def patched_houses(jd, lat, lon, hsys):
        call_count["n"] += 1
        if hsys == b"P":
            raise RuntimeError("placidus not available at polar latitude")
        return real_houses(jd, lat, lon, hsys)

    monkeypatch.setattr(chart_mod.swe, "houses", patched_houses)

    with caplog.at_level(logging.WARNING, logger="bphs_core.chart"):
        r = client.post("/v1/chart", json=SAMPLE_A)

    assert r.status_code == 200, r.text
    assert any(
        "placidus_fallback_equatorial" in record.message
        for record in caplog.records
    ), f"Expected placidus_fallback_equatorial warning. Records: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# /v1/strength
# ---------------------------------------------------------------------------

def test_strength_sample_a():
    r = client.post("/v1/strength", json=SAMPLE_A)
    assert r.status_code == 200
    body = r.json()
    assert len(body["shadbala"]) == 7   # 7 classical planets
    assert len(body["bhavabala"]) == 12
    for item in body["shadbala"]:
        assert "total_bala" in item
        assert isinstance(item["is_below_minimum"], bool)


# ---------------------------------------------------------------------------
# /v1/dashas
# ---------------------------------------------------------------------------

def test_dashas_sample_a():
    req = {**SAMPLE_A, "from_date": "2020-01-01", "to_date": "2030-01-01",
           "systems": ["vimshottari"]}
    r = client.post("/v1/dashas", json=req)
    assert r.status_code == 200
    periods = r.json()
    assert len(periods) > 0
    for p in periods:
        assert p["system"] == "vimshottari"
        assert p["level"] in ("mahadasha", "antardasha")


# ---------------------------------------------------------------------------
# /v1/yogas
# ---------------------------------------------------------------------------

def test_yogas_sample_a():
    r = client.post("/v1/yogas", json=SAMPLE_A)
    assert r.status_code == 200
    yogas = r.json()
    assert isinstance(yogas, list)
    for y in yogas:
        assert "name" in y
        assert "is_viparita_raja" in y


# ---------------------------------------------------------------------------
# /v1/transits
# ---------------------------------------------------------------------------

def test_transits_sample_a():
    req = {**SAMPLE_A, "at_date": "2025-01-01"}
    r = client.post("/v1/transits", json=req)
    assert r.status_code == 200
    body = r.json()
    saturn = next(p for p in body["planets"] if p["planet"] == "Saturn")
    assert saturn["sign"] in _VALID_SIGNS
    assert "sade_sati_active" in body
    # per-graha gochara signals for the seven grahas; null for the nodes.
    assert isinstance(saturn["bindu_score"], int) and 0 <= saturn["bindu_score"] <= 8
    assert 1 <= saturn["house_from_moon"] <= 12
    assert isinstance(saturn["favourable"], bool)
    ketu = next(p for p in body["planets"] if p["planet"] == "Ketu")
    assert ketu["bindu_score"] is None and ketu["favourable"] is None
    # full-move: house-from-lagna for all nine planets; chandrashtama + dhaiya top-level.
    assert 1 <= saturn["house_from_lagna"] <= 12
    assert 1 <= ketu["house_from_lagna"] <= 12
    assert isinstance(body["chandrashtama"], bool)
    assert isinstance(body["dhaiya_active"], bool)
    assert (body["dhaiya_phase"] is None) or ("natal Moon" in body["dhaiya_phase"])
    assert isinstance(body["gochara_vedha"], list)
    for v in body["gochara_vedha"]:
        assert v["blocked_planet"] and v["blocking_planet"]
        assert 1 <= v["blocked_house"] <= 12 and 1 <= v["vedha_house"] <= 12
        assert isinstance(v["neutralised"], bool)


# ---------------------------------------------------------------------------
# /v1/special-points
# ---------------------------------------------------------------------------

def test_special_points_sample_a():
    r = client.post("/v1/special-points", json=SAMPLE_A)
    assert r.status_code == 200
    body = r.json()
    assert "arudha_lagna" in body
    assert "atmakaraka" in body
    assert body["atmakaraka"] in [
        "Sun","Moon","Mars","Mercury","Jupiter","Venus","Saturn"
    ]


# ---------------------------------------------------------------------------
# /v1/muhurat
# ---------------------------------------------------------------------------

def test_muhurat_endpoint():
    req = {
        **SAMPLE_A,
        "start_date": "2026-05-26",
        "end_date": "2026-05-28"
    }
    r = client.post("/v1/muhurat", json=req)
    assert r.status_code == 200
    body = r.json()
    assert "days" in body
    assert len(body["days"]) == 3
    
    first_day = body["days"][0]
    assert first_day["date"] == "2026-05-26"
    assert "sunrise" in first_day
    assert "sunset" in first_day
    assert "panchanga" in first_day
    assert "auspicious_muhurtas" in first_day
    assert "chogadiya" in first_day
    assert "inauspicious_periods" in first_day
    assert "personal_balam" in first_day
    assert "tara_bala" in first_day["personal_balam"]
    assert "chandra_bala" in first_day["personal_balam"]


# ---------------------------------------------------------------------------
# /v1/muhurat/lagna-shuddhi
# ---------------------------------------------------------------------------

_LAGNA_SHUDDHI_REQ = {
    **SAMPLE_A,
    "start_date": "2026-05-26",
    "end_date": "2026-05-28",
    "activity_category": "generic",
    "step_seconds": 60,
}

_VALID_DIGNITIES = {
    "exalted", "moolatrikona", "own sign", "friendly", "neutral", "enemy",
    "debilitated", "unknown",
}


def test_lagna_shuddhi_structure():
    r = client.post("/v1/muhurat/lagna-shuddhi", json=_LAGNA_SHUDDHI_REQ)
    assert r.status_code == 200
    body = r.json()
    assert "best_instant" in body
    assert "best_window" in body
    assert "top_samples" in body

    bi = body["best_instant"]
    assert bi is not None
    assert "instant" in bi        # "YYYY-MM-DD HH:MM"
    assert "lagna_sign" in bi
    assert "lagna_lord" in bi
    assert "score" in bi
    assert 0.0 <= bi["score"] <= 1.0
    assert bi["lagna_sign"] in _VALID_SIGNS
    assert bi["lagna_lord_dignity"] in _VALID_DIGNITIES

    # factor values surfaced on each sample + a clearance summary on the body.
    assert body.get("clearance_summary")
    assert bi["tara_bala"] and bi["chandra_bala"]
    assert bi["event_navamsha"] in _VALID_SIGNS
    assert isinstance(bi["panchanga_suitable"], bool)
    assert isinstance(bi["event_navamsha_suitable"], bool)

    bw = body["best_window"]
    assert bw is not None
    assert "start" in bw
    assert "end" in bw
    # Window must be ≤ 11 minutes wide (band_start to band_end+1)
    from datetime import datetime
    s = datetime.strptime(bw["start"], "%H:%M")
    e = datetime.strptime(bw["end"], "%H:%M")
    width_mins = (e.hour * 60 + e.minute) - (s.hour * 60 + s.minute)
    assert width_mins <= 11, f"Window too wide: {width_mins} min"

    assert isinstance(body["top_samples"], list)
    assert len(body["top_samples"]) <= 20


def test_lagna_shuddhi_returns_minute_resolution():
    r = client.post("/v1/muhurat/lagna-shuddhi", json=_LAGNA_SHUDDHI_REQ)
    body = r.json()
    bi = body["best_instant"]
    # instant format is "YYYY-MM-DD HH:MM" — must resolve to a specific minute
    parts = bi["instant"].split(" ")
    assert len(parts) == 2
    assert len(parts[1]) == 5  # "HH:MM"


def test_lagna_shuddhi_best_not_in_rahu_kala():
    """Best instant should never be inside Rahu Kala."""
    r = client.post("/v1/muhurat/lagna-shuddhi", json=_LAGNA_SHUDDHI_REQ)
    body = r.json()
    bi = body["best_instant"]
    assert not bi["in_rahu_kala"], "Best instant must not be in Rahu Kala"
    assert not bi["in_yamaganda"], "Best instant must not be in Yamaganda"
    assert not bi["in_gulika"], "Best instant must not be in Gulika"


def test_lagna_shuddhi_top_samples_ordered():
    """top_samples must be sorted by score descending."""
    r = client.post("/v1/muhurat/lagna-shuddhi", json=_LAGNA_SHUDDHI_REQ)
    body = r.json()
    scores = [s["score"] for s in body["top_samples"]]
    assert scores == sorted(scores, reverse=True), "top_samples not sorted by score desc"


def test_lagna_shuddhi_activity_categories():
    """All activity categories must return 200 with valid structure."""
    for activity in ["generic", "business", "marriage", "travel", "surgery"]:
        req = {**_LAGNA_SHUDDHI_REQ, "activity_category": activity}
        r = client.post("/v1/muhurat/lagna-shuddhi", json=req)
        assert r.status_code == 200, f"Failed for activity={activity}"
        body = r.json()
        assert body["best_instant"] is not None or body["top_samples"] == []


def test_lagna_shuddhi_surgery_excludes_rahu_varjyam():
    """Surgery mode: no sample in top_samples should have Rahu Kala or Varjyam."""
    req = {**_LAGNA_SHUDDHI_REQ, "activity_category": "surgery"}
    r = client.post("/v1/muhurat/lagna-shuddhi", json=req)
    body = r.json()
    for sample in body["top_samples"]:
        assert not sample["in_rahu_kala"]
        assert not sample["in_varjyam"]
        assert not sample["in_durmuhurtam"]


# ---------------------------------------------------------------------------
# /v1/muhurat/family-lagna-shuddhi
# ---------------------------------------------------------------------------

_FAMILY_REQ = {
    "members": [
        {**SAMPLE_A, "birth_date": "1950-06-15", "birth_time": "06:00:00"},
        {**SAMPLE_B, "birth_date": "1975-12-01", "birth_time": "12:30:00"},
    ],
    "start_date": "2026-05-26",
    "end_date": "2026-05-28",
    "activity_category": "generic",
    "step_seconds": 60,
}

_VALID_TARA_LABELS = {
    "Janma", "Sampat", "Vipat", "Kshema", "Pratyak",
    "Sadhana", "Naidhana", "Mitra", "Paramitra", "Unknown",
}
_VALID_CHANDRA = {"Good", "Neutral", "Inauspicious (Avoid)"}
_VALID_DIGNITIES = {
    "exalted", "moolatrikona", "own sign", "friendly", "neutral", "enemy",
    "debilitated", "unknown",
}
_TARA_BAD = {"Vipat", "Pratyak", "Naidhana"}


def test_family_lagna_shuddhi_structure():
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=_FAMILY_REQ)
    assert r.status_code == 200
    body = r.json()

    assert "instant" in body
    assert "best_window" in body
    assert "score" in body
    assert "per_member" in body
    assert "consensus_quality" in body
    assert "compromised_members" in body

    assert body["consensus_quality"] in ("strict", "best_effort")
    assert isinstance(body["compromised_members"], list)
    assert isinstance(body["score"], float)
    assert 0.0 <= body["score"] <= 1.0
    assert "clearance_summary" in body


def test_family_lagna_shuddhi_per_member_fields():
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=_FAMILY_REQ)
    assert r.status_code == 200
    body = r.json()

    if body["instant"] is None:
        assert body["per_member"] == []
        return

    assert len(body["per_member"]) == len(_FAMILY_REQ["members"])
    for md in body["per_member"]:
        assert "name" in md
        assert "instant" in md
        assert "lagna_sign" in md
        assert "lagna_lord" in md
        assert "lagna_lord_house" in md
        assert "lagna_lord_dignity" in md
        assert "hora_lord" in md
        assert "in_rahu_kala" in md
        assert "in_yamaganda" in md
        assert "in_gulika" in md
        assert "in_durmuhurtam" in md
        assert "in_varjyam" in md
        assert "in_auspicious_muhurta" in md
        assert "score" in md
        assert "tara_bala" in md
        assert "chandra_bala" in md
        assert md["lagna_sign"] in _VALID_SIGNS
        assert md["lagna_lord_dignity"] in _VALID_DIGNITIES
        assert md["tara_bala"] in _VALID_TARA_LABELS
        assert md["chandra_bala"] in _VALID_CHANDRA


def test_family_lagna_shuddhi_determinism():
    r1 = client.post("/v1/muhurat/family-lagna-shuddhi", json=_FAMILY_REQ)
    r2 = client.post("/v1/muhurat/family-lagna-shuddhi", json=_FAMILY_REQ)
    assert r1.status_code == 200
    assert r2.status_code == 200
    body1 = r1.json()
    body2 = r2.json()
    assert body1["instant"] == body2["instant"], "instant not deterministic"
    assert body1["score"] == body2["score"], "score not deterministic"
    assert body1["consensus_quality"] == body2["consensus_quality"]


def test_family_lagna_shuddhi_strict_no_bad_balam():
    """When consensus_quality is strict, no per_member should have bad Tara/Chandra."""
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=_FAMILY_REQ)
    assert r.status_code == 200
    body = r.json()
    if body["consensus_quality"] == "strict" and body["instant"] is not None:
        for md in body["per_member"]:
            assert md["tara_bala"] not in _TARA_BAD, (
                f"{md['name']} has bad Tara Bala {md['tara_bala']} in strict mode"
            )
            assert md["chandra_bala"] != "Inauspicious (Avoid)", (
                f"{md['name']} has bad Chandra Bala in strict mode"
            )


def test_family_lagna_shuddhi_no_hard_excluded_instant():
    """Best instant must never be in Rahu Kala / Yamaganda / Gulika for any member."""
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=_FAMILY_REQ)
    assert r.status_code == 200
    body = r.json()
    if body["instant"] is not None:
        for md in body["per_member"]:
            assert not md["in_rahu_kala"], f"{md['name']} in Rahu Kala at best instant"
            assert not md["in_yamaganda"], f"{md['name']} in Yamaganda at best instant"
            assert not md["in_gulika"], f"{md['name']} in Gulika at best instant"


def test_family_lagna_shuddhi_too_few_members_422():
    bad = {**_FAMILY_REQ, "members": [_FAMILY_REQ["members"][0]]}
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=bad)
    assert r.status_code == 422


def test_family_lagna_shuddhi_date_range_422():
    bad = {**_FAMILY_REQ, "start_date": "2026-05-01", "end_date": "2027-06-30"}
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=bad)
    assert r.status_code == 422


def test_family_lagna_shuddhi_unauthorized_401():
    bad_client = TestClient(app, headers={"Authorization": "Bearer wrong"})
    r = bad_client.post("/v1/muhurat/family-lagna-shuddhi", json=_FAMILY_REQ)
    assert r.status_code == 401


def test_family_lagna_shuddhi_best_window_shape():
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=_FAMILY_REQ)
    assert r.status_code == 200
    body = r.json()
    if body["best_window"] is not None:
        bw = body["best_window"]
        assert "start" in bw
        assert "end" in bw
        assert "label" in bw
        assert "joint" in bw["label"]
        # Window must not be wider than 11 minutes
        from datetime import datetime as _dt
        s = _dt.strptime(bw["start"], "%H:%M")
        e = _dt.strptime(bw["end"], "%H:%M")
        width_mins = (e.hour * 60 + e.minute) - (s.hour * 60 + s.minute)
        assert width_mins <= 11, f"Joint window too wide: {width_mins} min"


# ---------------------------------------------------------------------------
# /v1/compat
# ---------------------------------------------------------------------------

_COMPAT_REQ = {"person_a": SAMPLE_A, "person_b": SAMPLE_B}

_VALID_SEVERITY = {"none", "mild", "strong"}
_VALID_QUALITY  = {"favorable", "neutral", "challenging"}
_KUTA_NAMES     = ["varna", "vasya", "tara", "yoni", "graha_maitri", "gana", "bhakoot", "nadi"]
_KUTA_MAX       = {"varna": 1, "vasya": 2, "tara": 3, "yoni": 4,
                   "graha_maitri": 5, "gana": 6, "bhakoot": 7, "nadi": 8}


def test_compat_structure():
    r = client.post("/v1/compat", json=_COMPAT_REQ)
    assert r.status_code == 200
    body = r.json()

    # top-level fields
    assert "total_score" in body
    assert "max_score" in body
    assert "kutas" in body
    assert "mangal_dosha_a" in body
    assert "mangal_dosha_b" in body
    assert "nakshatra_compatibility" in body
    assert "dasha_overlaps" in body
    assert "composite_strength_notes" in body


def test_compat_max_score_36():
    r = client.post("/v1/compat", json=_COMPAT_REQ)
    assert r.status_code == 200
    assert r.json()["max_score"] == 36.0


def test_compat_total_equals_sum_of_kutas():
    r = client.post("/v1/compat", json=_COMPAT_REQ)
    assert r.status_code == 200
    body = r.json()
    expected = round(sum(k["score"] for k in body["kutas"]), 4)
    assert round(body["total_score"], 4) == expected


def test_compat_kutas_well_formed():
    r = client.post("/v1/compat", json=_COMPAT_REQ)
    assert r.status_code == 200
    kutas = r.json()["kutas"]
    assert len(kutas) == 8
    names = [k["name"] for k in kutas]
    assert names == _KUTA_NAMES
    for k in kutas:
        assert k["max_score"] == _KUTA_MAX[k["name"]]
        assert 0.0 <= k["score"] <= k["max_score"]
        assert isinstance(k["interpretation"], str)
        assert len(k["interpretation"]) > 0


def test_compat_mangal_dosha_well_formed():
    r = client.post("/v1/compat", json=_COMPAT_REQ)
    assert r.status_code == 200
    body = r.json()
    for key in ("mangal_dosha_a", "mangal_dosha_b"):
        d = body[key]
        assert isinstance(d["has_dosha"], bool)
        assert d["severity"] in _VALID_SEVERITY
        assert isinstance(d["cancellation"], str)
        if not d["has_dosha"]:
            assert d["severity"] == "none"


def test_compat_dasha_overlaps_well_formed():
    r = client.post("/v1/compat", json=_COMPAT_REQ)
    assert r.status_code == 200
    overlaps = r.json()["dasha_overlaps"]
    assert isinstance(overlaps, list)
    for o in overlaps:
        assert "start_date" in o and "end_date" in o
        assert o["quality"] in _VALID_QUALITY
        assert o["start_date"] <= o["end_date"]


def test_compat_deterministic():
    r1 = client.post("/v1/compat", json=_COMPAT_REQ)
    r2 = client.post("/v1/compat", json=_COMPAT_REQ)
    assert r1.json() == r2.json()


def test_compat_unauthenticated():
    bad_client = TestClient(app, headers={"Authorization": "Bearer wrong"})
    r = bad_client.post("/v1/compat", json=_COMPAT_REQ)
    assert r.status_code == 401


def test_compat_missing_fields():
    r = client.post("/v1/compat", json={"person_a": SAMPLE_A})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_auth_rejected():
    bad_client = TestClient(app, headers={"Authorization": "Bearer wrong"})
    r = bad_client.post("/v1/chart", json=SAMPLE_A)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Configurable limits
# ---------------------------------------------------------------------------

def test_configurable_lagna_shuddhi_limit(monkeypatch):
    import app.main
    # Override limit to 20 days.
    monkeypatch.setattr(app.main, "MAX_LAGNA_SHUDDHI_DAYS", 20)
    
    # Request a 17-day range (May 26 to June 12 = 17 days), which exceeds default 14 days.
    req = {
        **_LAGNA_SHUDDHI_REQ,
        "start_date": "2026-05-26",
        "end_date": "2026-06-12",
    }
    r = client.post("/v1/muhurat/lagna-shuddhi", json=req)
    assert r.status_code == 200


