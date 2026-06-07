"""Muhurta factor helpers: Event Navamsha + clearance summary.

These exercise the pure, deterministic helpers. The full scoring gates and
endpoint wiring are covered by the /v1/muhurat endpoint tests (which need the
real chart engine).
"""
from bphs_core.lagna_shuddhi import (
    _navamsa_sign,
    _build_clearance_summary,
    _event_navamsha_factor,
)


def test_navamsa_sign_movable_fixed_dual_starts():
    # Continuous navamsa reproduces the classical movable/fixed/dual start rule.
    assert _navamsa_sign(0.0) == "Aries"       # Aries (movable) starts at Aries
    assert _navamsa_sign(30.0) == "Capricorn"  # Taurus (fixed) starts at the 9th
    assert _navamsa_sign(60.0) == "Libra"      # Gemini (dual) starts at the 5th
    assert _navamsa_sign(120.0) == "Aries"     # Leo (fixed) starts at the 9th


def test_navamsa_sign_steps_one_sign_per_3deg20():
    assert _navamsa_sign(3.0) == "Aries"       # within the first navamsa pada
    assert _navamsa_sign(4.0) == "Taurus"      # past 3°20' → next navamsa


def _sample(**over):
    base = {
        "tithi": "Shukla Panchami", "yoga": "Siddhi", "panchanga_suitable": True,
        "tara_bala": "Sampat", "chandra_bala": "Good",
        "lagna_sign": "Taurus", "lagna_lord": "Venus",
        "lagna_lord_house": 4, "lagna_lord_dignity": "own sign",
        "event_navamsha": "Taurus", "event_navamsha_suitable": True,
    }
    base.update(over)
    return base


def test_clearance_summary_covers_every_factor():
    out = _build_clearance_summary(_sample(), "marriage")
    assert "Clear of Rahu Kala, Yamaganda and Gulika." in out
    assert "Shukla Panchami" in out and "Siddhi yoga" in out and "suitable" in out
    assert "Tara Bala: Sampat" in out and "Chandra Bala: Good" in out
    assert "Taurus" in out and "Venus" in out and "4th" in out
    assert "Event Navamsha lagna Taurus (suitable for marriage)." in out


def test_clearance_summary_flags_inauspicious_panchanga_and_omits_missing_navamsa():
    out = _build_clearance_summary(
        _sample(panchanga_suitable=False, event_navamsha=None), "generic"
    )
    assert "inauspicious (Rikta tithi or avoided yoga)" in out
    assert "Event Navamsha" not in out  # no clause when navamsa is absent


def test_event_navamsha_vargottama_is_strongest():
    # Navamsa lagna sign == rasi lagna sign → Vargottama (the strongest signal).
    assert _event_navamsha_factor("Taurus", "Taurus", "generic") == (True, 0.08)


def test_event_navamsha_sign_nature_matches_activity():
    # Travel favours movable signs; marriage favours fixed signs.
    assert _event_navamsha_factor("Cancer", "Aries", "travel") == (True, 0.05)   # Cancer movable
    assert _event_navamsha_factor("Leo", "Aries", "marriage") == (True, 0.05)    # Leo fixed


def test_event_navamsha_benefic_fallback_then_unsuitable():
    # A benefic-ruled navamsa lagna with no stronger signal → small bonus.
    assert _event_navamsha_factor("Pisces", "Aries", "generic") == (True, 0.03)  # Jupiter sign
    # Malefic-ruled, no Vargottama and no nature match → not suitable.
    assert _event_navamsha_factor("Scorpio", "Aries", "generic") == (False, 0.0)  # Mars sign


def test_event_navamsha_movable_does_not_satisfy_marriage():
    # Marriage wants a fixed nature; a movable, malefic-ruled navamsa lagna fails.
    assert _event_navamsha_factor("Aries", "Taurus", "marriage") == (False, 0.0)
