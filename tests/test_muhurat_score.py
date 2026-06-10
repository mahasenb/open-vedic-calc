"""Quality band + factor breakdown for electional samples.

Pure, deterministic helpers over an already-scored sample dict — no ephemeris.
The scan/endpoint integration (enrichment flowing into the response) is covered
by the /v1/muhurat/lagna-shuddhi and family endpoint tests.
"""
import pytest

from bphs_core import lagna_shuddhi as ls
from bphs_core.lagna_shuddhi import (
    derive_band,
    build_factors,
    _enrich_sample,
    _family_band,
)
from app.schemas import (
    LagnaShuddhiSample,
    FamilyLagnaShuddhiResponse,
    FamilyMemberSample,
)


def _sample(**over):
    """A clear, well-disposed sample; override fields per test."""
    base = {
        "instant": "2026-05-26 09:30",
        "lagna_sign": "Taurus", "lagna_lord": "Venus",
        "lagna_lord_house": 4, "lagna_lord_dignity": "own sign",
        "hora_lord": "Venus", "chogadiya_label": "Amrit (Highly Auspicious)",
        "in_rahu_kala": False, "in_yamaganda": False, "in_gulika": False,
        "in_durmuhurtam": False, "in_varjyam": False,
        "in_auspicious_muhurta": "Abhijit Muhurta",
        "tithi": "Shukla Panchami", "yoga": "Siddhi", "panchanga_suitable": True,
        "tara_bala": "Sampat", "chandra_bala": "Good",
        "event_navamsha": "Taurus", "event_navamsha_suitable": True,
        "score": 0.9,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# derive_band                                                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("score,expected", [
    (0.92, "Excellent"),
    (0.85, "Excellent"),
    (0.70, "Good"),
    (0.60, "Good"),
    (0.50, "Fair"),
    (0.01, "Fair"),
])
def test_band_by_score_when_signals_clean(score, expected):
    assert derive_band(score, _sample(score=score)) == expected


def test_band_zero_score_is_avoid():
    assert derive_band(0.0, _sample(score=0.0)) == "Avoid"


@pytest.mark.parametrize("flag", ["in_rahu_kala", "in_yamaganda", "in_gulika"])
def test_band_hard_period_is_avoid_regardless_of_score(flag):
    # Even a high score is vetoed to Avoid by a toxic period.
    assert derive_band(0.9, _sample(**{flag: True})) == "Avoid"


@pytest.mark.parametrize("tara", ["Vipat", "Pratyak", "Naidhana"])
def test_band_bad_tara_caps_at_fair(tara):
    assert derive_band(0.95, _sample(tara_bala=tara)) == "Fair"


def test_band_chandra_avoid_caps_at_fair():
    assert derive_band(0.95, _sample(chandra_bala="Inauspicious (Avoid)")) == "Fair"


@pytest.mark.parametrize("field", ["tara_bala", "chandra_bala"])
def test_band_unknown_balam_caps_at_fair(field):
    """A FAILED Tara/Chandra compute ('Unknown', birth data expected) fails
    closed: the band caps at Fair regardless of an otherwise-high score."""
    assert derive_band(0.95, _sample(**{field: "Unknown"})) == "Fair"


def test_band_nobirthdata_does_not_cap():
    """A generic scan ('NoBirthData') has no personal strength to check, so it
    does NOT cap — a high score can still be Excellent."""
    assert derive_band(
        0.95, _sample(tara_bala="NoBirthData", chandra_bala="NoBirthData")
    ) == "Excellent"


# --------------------------------------------------------------------------- #
# build_factors                                                               #
# --------------------------------------------------------------------------- #

def _by_name(factors):
    return {f["name"]: f for f in factors}


def test_factors_clean_sample_all_positive_signals_present():
    f = _by_name(build_factors(_sample()))
    assert f["Inauspicious periods"]["impact"] == "positive"
    assert f["Tara Bala"]["impact"] == "positive"
    assert f["Chandra Bala"]["impact"] == "positive"
    assert f["Panchanga"]["impact"] == "positive"
    assert f["Lagna lord dignity"]["impact"] == "positive"
    assert f["Lagna lord house"]["impact"] == "positive"
    assert f["Muhurta"]["impact"] == "positive"
    assert f["Chogadiya"]["impact"] == "positive"
    assert f["Event Navamsha"]["impact"] == "positive"
    # every factor carries the three display keys
    for fac in build_factors(_sample()):
        assert set(fac) == {"name", "impact", "detail"}


def test_factors_toxic_period_is_negative_and_flags_durmuhurtam_varjyam():
    f = _by_name(build_factors(_sample(
        in_rahu_kala=True, in_durmuhurtam=True, in_varjyam=True)))
    assert f["Inauspicious periods"]["impact"] == "negative"
    assert f["Durmuhurtam"]["impact"] == "negative"
    assert f["Varjyam"]["impact"] == "negative"


def test_factors_negative_balam_dignity_house_panchanga():
    f = _by_name(build_factors(_sample(
        tara_bala="Vipat", chandra_bala="Inauspicious (Avoid)",
        panchanga_suitable=False, lagna_lord_dignity="debilitated",
        lagna_lord_house=8)))
    assert f["Tara Bala"]["impact"] == "negative"
    assert f["Chandra Bala"]["impact"] == "negative"
    assert f["Panchanga"]["impact"] == "negative"
    assert f["Lagna lord dignity"]["impact"] == "negative"
    assert f["Lagna lord house"]["impact"] == "negative"


def test_factors_neutral_signals_are_omitted():
    # 'NoBirthData' (generic scan, no birth data) and 'Neutral' chandra carry no
    # factor — unlike 'Unknown' (failed compute), which IS a negative factor.
    f = _by_name(build_factors(_sample(
        tara_bala="NoBirthData", chandra_bala="Neutral",
        lagna_lord_dignity="neutral", lagna_lord_house=3,  # upachaya: neither
        in_auspicious_muhurta=None, chogadiya_label=None,
        event_navamsha_suitable=False)))
    for absent in ("Tara Bala", "Chandra Bala", "Lagna lord dignity",
                   "Lagna lord house", "Muhurta", "Chogadiya", "Event Navamsha"):
        assert absent not in f
    # clearance + panchanga are always reported
    assert f["Inauspicious periods"]["impact"] == "positive"
    assert "Panchanga" in f


def test_factors_unknown_balam_is_negative():
    """A FAILED Tara/Chandra compute ('Unknown') surfaces a negative factor —
    distinct from 'NoBirthData', which surfaces nothing."""
    f = _by_name(build_factors(_sample(
        tara_bala="Unknown", chandra_bala="Unknown")))
    assert f["Tara Bala"]["impact"] == "negative"
    assert f["Chandra Bala"]["impact"] == "negative"


def test_factors_trikona_house_is_positive():
    f = _by_name(build_factors(_sample(lagna_lord_house=9)))
    assert f["Lagna lord house"]["impact"] == "positive"


# --------------------------------------------------------------------------- #
# _enrich_sample                                                              #
# --------------------------------------------------------------------------- #

def test_enrich_sample_sets_band_score100_factors_in_place():
    s = _sample(score=0.9)
    out = _enrich_sample(s)
    assert out is s                       # mutates in place
    assert s["score_100"] == 90
    assert s["band"] == "Excellent"
    assert isinstance(s["factors"], list) and s["factors"]


def test_enrich_sample_rounds_score_and_defaults_missing_score():
    assert _enrich_sample(_sample(score=0.676))["score_100"] == 68
    bare = {"score": 0.0, "in_rahu_kala": True}   # missing most keys
    out = _enrich_sample(bare)
    assert out["score_100"] == 0 and out["band"] == "Avoid"


# --------------------------------------------------------------------------- #
# _family_band                                                                #
# --------------------------------------------------------------------------- #

def test_family_band_strict_weakest_member_governs():
    members = [_sample(score=0.9), _sample(score=0.62)]  # second → Good
    assert _family_band(0.62, "strict", members) == "Good"


def test_family_band_strict_all_excellent():
    members = [_sample(score=0.9), _sample(score=0.88)]
    assert _family_band(0.88, "strict", members) == "Excellent"


def test_family_band_best_effort_caps_at_fair():
    # A high joint score but a compromised consensus never beats Fair.
    members = [_sample(score=0.9), _sample(score=0.9)]
    assert _family_band(0.9, "best_effort", members) == "Fair"


def test_family_band_zero_min_score_is_avoid():
    assert _family_band(0.0, "strict", [_sample(score=0.0)]) == "Avoid"


def test_family_band_weak_member_drags_to_fair():
    members = [_sample(score=0.9), _sample(score=0.95, tara_bala="Vipat")]
    assert _family_band(0.9, "strict", members) == "Fair"


# --------------------------------------------------------------------------- #
# schema round-trips (additive, back-compatible)                              #
# --------------------------------------------------------------------------- #

def test_sample_schema_accepts_enriched_payload():
    m = LagnaShuddhiSample(**_enrich_sample(_sample(score=0.9)))
    assert m.band == "Excellent" and m.score_100 == 90
    assert m.factors and m.factors[0].impact in ("positive", "negative")


def test_sample_schema_defaults_when_band_fields_absent():
    # An older payload without score_100/band/factors still parses.
    legacy = {
        "instant": "2026-05-26 09:30", "lagna_sign": "Taurus",
        "lagna_lord": "Venus", "lagna_lord_house": 4,
        "lagna_lord_dignity": "own sign", "hora_lord": "Venus",
        "chogadiya_label": None, "in_rahu_kala": False, "in_yamaganda": False,
        "in_gulika": False, "in_durmuhurtam": False, "in_varjyam": False,
        "in_auspicious_muhurta": None, "score": 0.5,
    }
    m = LagnaShuddhiSample(**legacy)
    assert m.band == "Fair" and m.score_100 == 0 and m.factors == []


def test_family_response_carries_band_and_score100():
    member = FamilyMemberSample(**_enrich_sample(_sample(score=0.9)), name="A")
    resp = FamilyLagnaShuddhiResponse(
        instant="2026-05-26 09:30", best_window=None, score=0.9,
        score_100=90, band="Excellent", per_member=[member],
        consensus_quality="strict", compromised_members=[],
    )
    assert resp.band == "Excellent" and resp.score_100 == 90


# --------------------------------------------------------------------------- #
# _passes_balam_gate (via scan_family_lagna_shuddhi)                           #
# --------------------------------------------------------------------------- #
#
# _passes_balam_gate is a closure inside scan_family_lagna_shuddhi, so it is
# exercised through the public scan. The ephemeris-heavy helpers are stubbed so
# the test is deterministic and fast: a single candidate minute, a clean day, a
# fixed lagna, and per-member balam driven by the member's birth_nakshatra.

@pytest.fixture
def _stub_family_scan(monkeypatch):
    clean_day = {
        "date": "2026-05-26",
        "sunrise": "06:00", "sunset": "18:00",
        "inauspicious_periods": [],
        "auspicious_muhurtas": [{"start": "09:00", "end": "09:02",
                                 "label": "Abhijit Muhurta"}],
        "chogadiya": [],
        "panchanga": {"tithi": "Shukla Panchami", "yogam": "Siddhi",
                      "karana": "Bava", "vaara": "Tuesday"},
        "is_eclipse_day": False, "is_adhik_maasa": False,
        "hard_gate_failed": False,
    }
    monkeypatch.setattr(ls, "compute_muhurat_for_day", lambda *a, **k: clean_day)
    monkeypatch.setattr(ls, "compute_lagna_at_jd",
                        lambda *a, **k: ("Taurus", "Venus", 35.0))

    # balam keyed off the member's birth_nakshatra so each test picks a state.
    def fake_balam(jd, birth_nak, birth_sign):
        return {
            "good": ("Sampat", "Good"),
            "unknown": ("Unknown", "Unknown"),
            "nobirth": ("NoBirthData", "NoBirthData"),
        }[birth_nak]

    monkeypatch.setattr(ls, "compute_balam_at_jd", fake_balam)
    return None


def _member(name, state):
    return {
        "name": name, "lat": 7.0, "lon": 80.0, "tz_offset": 5.5,
        "birth_nakshatra": state, "birth_moon_sign": "Taurus",
    }


def test_passes_balam_gate_unknown_member_is_compromised(_stub_family_scan):
    """A member whose Tara/Chandra FAILED to compute ('Unknown') is compromised,
    forcing a best_effort consensus."""
    out = ls.scan_family_lagna_shuddhi(
        [_member("Asha", "good"), _member("Ravi", "unknown")],
        "2026-05-26", "2026-05-26", activity="generic",
    )
    assert out["consensus_quality"] == "best_effort"
    assert "Ravi" in out["compromised_members"]
    assert "Asha" not in out["compromised_members"]


def test_passes_balam_gate_nobirthdata_member_not_compromised(_stub_family_scan):
    """A 'NoBirthData' member (generic scan, no birth data) is NOT compromised."""
    out = ls.scan_family_lagna_shuddhi(
        [_member("Asha", "good"), _member("Ravi", "nobirth")],
        "2026-05-26", "2026-05-26", activity="generic",
    )
    assert out["consensus_quality"] == "strict"
    assert out["compromised_members"] == []
