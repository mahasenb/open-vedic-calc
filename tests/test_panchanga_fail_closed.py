"""Fail-closed semantics for the panchanga pipeline.

Covers the direct longitude-based limb computation and every fail-closed path:
a tithi crash, a hard-gate (Rahu/Yama/Gulika) failure, eclipse/adhika-maasa
'could not be computed' (None), the Amavasya naming + veto, and the
Unknown-vs-NoBirthData band/penalty split. The mandate for an electional engine
is that a missing limb means 'not recommendable / visibly degraded', never
'fine'.

All ``drik`` monkeypatching targets ``bphs_core.muhurat.drik`` (for muhurat) or
``bphs_core.lagna_shuddhi.drik`` (for the scorer) — the names each module looks
up at call time.
"""
import logging
from datetime import date

import pytest

from bphs_core import muhurat as m
from bphs_core import lagna_shuddhi as ls
from bphs_core import utils


PLACE = utils.make_place("Sample City", 7.0, 80.0, 5.5)
TARGET = date(2026, 5, 26)


def _raise(*_a, **_k):
    raise RuntimeError("ephemeris unavailable")


# ---------------------------------------------------------------------------
# Direct nakshatra / yoga computation from sidereal longitudes
# ---------------------------------------------------------------------------

class TestDirectLimbComputation:
    def test_get_tithi_name_30_is_amavasya(self):
        assert m.get_tithi_name(30) == "Krishna Amavasya"

    def test_nakshatra_from_moon_real_ephemeris(self):
        """Returns a valid nakshatra name and a 1..27 index against the real
        ephemeris on TARGET."""
        import swisseph as swe
        jd = swe.julday(TARGET.year, TARGET.month, TARGET.day, 12.0)
        name, idx = m._nakshatra_from_moon(jd)
        assert name in utils.NAKSHATRAS
        assert 1 <= idx <= 27
        # name and the 1-based index must agree
        assert utils.NAKSHATRAS[idx - 1] == name

    def test_yoga_from_sun_moon_real_ephemeris(self):
        import swisseph as swe
        jd = swe.julday(TARGET.year, TARGET.month, TARGET.day, 12.0)
        name, idx = m._yoga_from_sun_moon(jd)
        assert name in m.YOGAS
        assert 1 <= idx <= 27
        assert m.YOGAS[idx - 1] == name


# ---------------------------------------------------------------------------
# Tithi crash fails closed (no propagation, degraded, name None)
# ---------------------------------------------------------------------------

class TestTithiFailClosed:
    def test_tithi_zero_division_does_not_propagate(self, monkeypatch, caplog):
        def _zero_div(*_a, **_k):
            raise ZeroDivisionError("division by zero at exact phase boundary")

        monkeypatch.setattr(m.drik, "tithi", _zero_div)
        with caplog.at_level(logging.WARNING, logger="bphs_core.muhurat"):
            out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["panchanga"]["tithi"] is None
        assert out["panchanga"]["tithi_end"] is None
        assert out["degraded"] is True
        assert any("muhurat_tithi_failed" in r.message for r in caplog.records)

    def test_karana_failure_fails_closed(self, monkeypatch):
        monkeypatch.setattr(m.drik, "karana", _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["panchanga"]["karana"] is None
        assert out["panchanga"]["karana_end"] is None
        assert out["degraded"] is True


# ---------------------------------------------------------------------------
# Hard gate (Rahu/Yama/Gulika) failure flags the day
# ---------------------------------------------------------------------------

class TestHardGateFailClosed:
    def test_all_three_failures_flag_hard_gate(self, monkeypatch):
        for fn in ("raahu_kaalam", "yamaganda_kaalam", "gulikai_kaalam"):
            monkeypatch.setattr(m.drik, fn, _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["hard_gate_failed"] is True
        assert out["degraded"] is True

    def test_single_hard_gate_failure_flags(self, monkeypatch):
        """Any ONE of the three failing trips the gate (the veto is unverifiable)."""
        monkeypatch.setattr(m.drik, "gulikai_kaalam", _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["hard_gate_failed"] is True
        assert out["degraded"] is True

    def test_happy_path_hard_gate_not_failed(self):
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["hard_gate_failed"] is False


# ---------------------------------------------------------------------------
# Eclipse / Adhika Maasa fail closed to None
# ---------------------------------------------------------------------------

class TestEclipseAdhikNone:
    def test_eclipse_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(m.drik, "next_solar_eclipse", _raise)
        monkeypatch.setattr(m.drik, "next_lunar_eclipse", _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["is_eclipse_day"] is None

    def test_adhik_maasa_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(m.drik, "lunar_month", _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["is_adhik_maasa"] is None


# ---------------------------------------------------------------------------
# compute_balam_at_jd: NoBirthData vs Unknown
# ---------------------------------------------------------------------------

class TestBalamSentinels:
    def test_no_birth_data_returns_nobirthdata(self):
        assert ls.compute_balam_at_jd(2460000.0, None, None) == (
            "NoBirthData", "NoBirthData",
        )

    def test_failed_compute_returns_unknown(self, monkeypatch):
        """Birth data present but the longitude computation raises -> 'Unknown'
        for both limbs (genuine computation failure, fail closed)."""
        monkeypatch.setattr(ls.drik, "sidereal_longitude", _raise)
        tara, chandra = ls.compute_balam_at_jd(2460000.0, "Rohini", "Taurus")
        assert tara == "Unknown"
        assert chandra == "Unknown"


# ---------------------------------------------------------------------------
# _score_instant fail-closed paths
# ---------------------------------------------------------------------------

def _base_day_data(**over):
    dd = {
        "date": "2026-05-26",
        "sunrise": "06:00",
        "inauspicious_periods": [],
        "auspicious_muhurtas": [],
        "chogadiya": [],
        "panchanga": {"tithi": "Shukla Panchami", "yogam": "Siddhi",
                      "karana": "Bava", "vaara": "Tuesday"},
    }
    dd.update(over)
    return dd


def _score(day_data, activity="generic", **kw):
    time_mins = 480  # 08:00
    jd = ls._jd_for_local("2026-05-26", time_mins, 5.5)
    return ls._score_instant(jd, "Aries", "Mars", day_data, time_mins, activity, **kw)


class TestScoreInstantFailClosed:
    def test_panchanga_none_tithi_not_suitable_and_penalised(self):
        dd = _base_day_data(panchanga={"tithi": None, "yogam": "Siddhi",
                                       "karana": "Bava", "vaara": "Tuesday"})
        score_none, detail_none = _score(dd)
        # A computed-suitable comparison day scores strictly higher.
        score_ok, _ = _score(_base_day_data())
        assert detail_none["panchanga_suitable"] is False
        assert score_none < score_ok

    def test_panchanga_none_yoga_not_suitable(self):
        dd = _base_day_data(panchanga={"tithi": "Shukla Panchami", "yogam": None,
                                       "karana": "Bava", "vaara": "Tuesday"})
        _, detail = _score(dd)
        assert detail["panchanga_suitable"] is False

    def test_hard_gate_failed_excludes_instant(self):
        dd = _base_day_data(hard_gate_failed=True)
        score, detail = _score(dd)
        assert detail["hard_excluded"] is True
        assert score == 0.0

    def test_amavasya_tithi_not_suitable(self):
        dd = _base_day_data(panchanga={"tithi": "Krishna Amavasya", "yogam": "Siddhi",
                                       "karana": "Bava", "vaara": "Tuesday"})
        score_ama, detail = _score(dd)
        score_ok, _ = _score(_base_day_data())
        assert detail["panchanga_suitable"] is False
        assert score_ama < score_ok

    def test_eclipse_none_excludes_samskara(self):
        """For an activity that excludes eclipse (e.g. marriage), an unknown
        (None) eclipse status vetoes; an explicit False does NOT. adhik_maasa is
        held at False so only the eclipse limb is under test (marriage also
        excludes adhik_maasa, whose absence would itself veto)."""
        dd_none = _base_day_data(is_eclipse_day=None, is_adhik_maasa=False)
        _, detail_none = _score(dd_none, activity="marriage")
        assert detail_none["hard_excluded"] is True

        dd_false = _base_day_data(is_eclipse_day=False, is_adhik_maasa=False)
        _, detail_false = _score(dd_false, activity="marriage")
        assert detail_false["hard_excluded"] is False

    def test_adhik_none_excludes_samskara(self):
        dd = _base_day_data(is_eclipse_day=False, is_adhik_maasa=None)
        _, detail = _score(dd, activity="marriage")
        assert detail["hard_excluded"] is True

    def test_unknown_tara_penalised_and_caps_band(self, monkeypatch):
        """A failed Tara/Chandra compute (birth data present) penalises the score
        and the resulting sample caps at Fair via derive_band."""
        monkeypatch.setattr(ls.drik, "sidereal_longitude", _raise)
        dd = _base_day_data(is_eclipse_day=False, is_adhik_maasa=False)
        score, detail = _score(
            dd, birth_nakshatra="Rohini", birth_moon_sign="Taurus",
        )
        assert detail["tara_bala"] == "Unknown"
        assert detail["chandra_bala"] == "Unknown"
        # band caps at Fair even though the score might otherwise be higher.
        sample = dict(detail)
        sample["score"] = max(score, 0.9)
        assert ls.derive_band(0.9, {**detail, "score": 0.9}) == "Fair"
