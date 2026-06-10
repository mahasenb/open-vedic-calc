"""Deep unit tests for bphs_core.muhurat.

These target the branches of ``compute_muhurat_for_day`` and its helpers that
the endpoint-level tests in ``test_coverage.py`` never reach:

  * the per-limb ``except Exception: pass`` fallbacks (every ``drik.*`` call is
    wrapped defensively so a single ephemeris failure never aborts the whole
    panchanga) — exercised by monkeypatching the relevant ``drik`` function to
    raise;
  * the personalised Tara-/Chandra-bala block, including both the "no natal
    Moon supplied" skip and the two failure fallbacks;
  * the ``get_karana_name`` fixed-karana table, the ``get_tithi_name`` Krishna
    branch, and the ``float_hours_to_hhmm`` minute-rounding carry;
  * the ``muhurthas`` list iteration with mixed tuple / non-tuple entries.

All ``drik`` monkeypatching targets ``bphs_core.muhurat.drik`` — the name the
module actually looks up at call time.
"""
from datetime import date

import pytest

from bphs_core import muhurat as m
from bphs_core import utils


PLACE = utils.make_place("Sample City", 7.0, 80.0, 5.5)
TARGET = date(2026, 5, 26)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestPureHelpers:
    def test_get_karana_name_fixed(self):
        """Fixed-karana indices map to their special names (line 85-86)."""
        assert m.get_karana_name(1) == "Kimstughna"
        assert m.get_karana_name(58) == "Shakuni"
        assert m.get_karana_name(59) == "Chatushpada"
        assert m.get_karana_name(60) == "Naga"

    def test_get_karana_name_movable(self):
        """Non-fixed indices cycle through the 7 movable karanas."""
        # idx 2 -> KARANAS[0] == "Bava"
        assert m.get_karana_name(2) == "Bava"
        # idx 8 -> KARANAS[(8-2)%7] == KARANAS[6] == "Vishti"
        assert m.get_karana_name(8) == "Vishti"

    def test_get_tithi_name_shukla_and_krishna(self):
        assert m.get_tithi_name(1) == "Shukla Prathama"
        assert m.get_tithi_name(15) == "Shukla Purnima"
        assert m.get_tithi_name(16) == "Krishna Prathama"
        # idx 30 IS produced at exact new moon (ceil(moon_phase/12) == 30), so it
        # is special-cased to "Krishna Amavasya" (TITHIS[29], the new-moon entry)
        # rather than wrapping to TITHIS[14] ("Purnima"). idx 29 (Chaturdashi) is
        # unchanged.
        assert m.get_tithi_name(30) == "Krishna Amavasya"
        assert m.get_tithi_name(29) == "Krishna Chaturdashi"

    def test_float_hours_to_hhmm_basic(self):
        assert m.float_hours_to_hhmm(6.5) == "06:30"
        assert m.float_hours_to_hhmm(0.0) == "00:00"

    def test_float_hours_to_hhmm_minute_carry(self):
        """Rounding that pushes minutes to 60 carries into the hour."""
        # 5.9999h -> 5h 59.994m -> rounds to 60 -> carries to 06:00
        assert m.float_hours_to_hhmm(5.9999) == "06:00"
        # 23.9999h wraps the hour back to 00:00
        assert m.float_hours_to_hhmm(23.9999) == "00:00"

    def test_float_hours_to_hhmm_wraps_past_24(self):
        assert m.float_hours_to_hhmm(25.0) == "01:00"


# ---------------------------------------------------------------------------
# Happy path — confirms the success branches and full response shape
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_full_day_structure(self):
        out = m.compute_muhurat_for_day(
            PLACE, TARGET,
            birth_nakshatra="Rohini", birth_moon_sign="Taurus",
        )
        assert out["date"] == "2026-05-26"
        # panchanga limbs present
        for key in ("tithi", "nakshatra", "yogam", "karana", "vaara"):
            assert out["panchanga"][key]
        assert out["panchanga"]["vaara"] == "Tuesday"
        # auspicious / inauspicious lists are populated by the real ephemeris
        assert isinstance(out["auspicious_muhurtas"], list)
        assert isinstance(out["inauspicious_periods"], list)
        assert isinstance(out["chogadiya"], list)
        # personalised balam computed because natal Moon supplied
        assert out["personal_balam"] is not None
        assert "tara_bala" in out["personal_balam"]
        assert "chandra_bala" in out["personal_balam"]
        assert out["personal_balam"]["chandra_bala"] in (
            "Good", "Neutral", "Inauspicious (Avoid)",
        )

    def test_personal_balam_skipped_when_no_natal_moon(self):
        """Branch 254->286: personal stays None without natal Moon data."""
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["personal_balam"] is None
        # only nakshatra given -> still skipped (both required)
        out2 = m.compute_muhurat_for_day(PLACE, TARGET, birth_nakshatra="Rohini")
        assert out2["personal_balam"] is None
        out3 = m.compute_muhurat_for_day(PLACE, TARGET, birth_moon_sign="Taurus")
        assert out3["personal_balam"] is None


# ---------------------------------------------------------------------------
# Defensive except branches — monkeypatch each drik call to raise
# ---------------------------------------------------------------------------

def _raise(*_a, **_k):
    raise RuntimeError("ephemeris unavailable")


class _BadStringElement:
    """Wraps a real ``drik.sunrise``/``sunset`` result tuple, delegating every
    index access to the real value EXCEPT element [1] (the ``"HH:MM:SS"`` string
    that muhurat slices as ``[1][:5]``), which raises.

    ``drik.tithi``/``nakshatra`` call sunrise internally and read the JD at [0]
    (and sometimes [2]); those must keep returning the genuine ephemeris values
    or the downstream sidereal-longitude calls compute a nonsense Julian Day.
    Only muhurat's own ``[1][:5]`` extraction is sabotaged, so just the sunrise/
    sunset ``except`` fallbacks fire."""

    def __init__(self, real):
        self._real = real

    def __getitem__(self, key):
        if key == 1:
            raise RuntimeError("no HH:MM string available")
        return self._real[key]


def _wrap_bad_string(real_fn):
    def wrapped(*a, **k):
        return _BadStringElement(real_fn(*a, **k))
    return wrapped


class TestSunMoonRiseFallbacks:
    def test_sunrise_sunset_fallbacks(self, monkeypatch):
        # sunrise/sunset are also called *inside* drik.tithi/nakshatra, so they
        # cannot raise outright. Wrap the real result so the JD elements ([0]/[2])
        # still work for those internals but muhurat's [1][:5] HH:MM extraction
        # fails, exercising the sunrise/sunset `except` fallbacks (lines 103-108).
        monkeypatch.setattr(m.drik, "sunrise", _wrap_bad_string(m.drik.sunrise))
        monkeypatch.setattr(m.drik, "sunset", _wrap_bad_string(m.drik.sunset))
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["sunrise"] == "06:00"
        assert out["sunset"] == "18:00"

    def test_moonrise_moonset_fallbacks(self, monkeypatch):
        monkeypatch.setattr(m.drik, "moonrise", _raise)
        monkeypatch.setattr(m.drik, "moonset", _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["moonrise"] is None
        assert out["moonset"] is None


class TestAuspiciousFallbacks:
    @pytest.mark.parametrize("fn", [
        "abhijit_muhurta", "brahma_muhurtha", "vijaya_muhurtha",
        "godhuli_muhurtha", "nishita_muhurtha",
    ])
    def test_single_auspicious_failure_does_not_abort(self, monkeypatch, fn):
        monkeypatch.setattr(m.drik, fn, _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        # response is still well formed; the failed window is just omitted
        labels = {a["label"] for a in out["auspicious_muhurtas"]}
        assert "Abhijit Muhurta" not in labels or fn != "abhijit_muhurta"

    def test_all_auspicious_failures(self, monkeypatch):
        for fn in ("abhijit_muhurta", "brahma_muhurtha", "vijaya_muhurtha",
                   "godhuli_muhurtha", "nishita_muhurtha"):
            monkeypatch.setattr(m.drik, fn, _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["auspicious_muhurtas"] == []


class TestChogadiyaFallback:
    def test_chogadiya_failure(self, monkeypatch):
        monkeypatch.setattr(m.drik, "gauri_choghadiya", _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["chogadiya"] == []

    def test_chogadiya_unknown_type_label(self, monkeypatch):
        """An out-of-range chogadiya type code maps to the 'Unknown' label."""
        def fake_chogadiya(*_a, **_k):
            return [(99, "08:00:00", "09:30:00")]
        monkeypatch.setattr(m.drik, "gauri_choghadiya", fake_chogadiya)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["chogadiya"] == [
            {"start": "08:00", "end": "09:30", "label": "Unknown"}
        ]


class TestInauspiciousFallbacks:
    @pytest.mark.parametrize("fn", [
        "raahu_kaalam", "yamaganda_kaalam", "gulikai_kaalam",
        "durmuhurtam", "varjyam",
    ])
    def test_single_inauspicious_failure(self, monkeypatch, fn):
        monkeypatch.setattr(m.drik, fn, _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert isinstance(out["inauspicious_periods"], list)

    def test_durmuhurtam_short_list_skips_both_periods(self, monkeypatch):
        """Branch 217->219: a <2-element durmuhurtam list yields no entries."""
        monkeypatch.setattr(m.drik, "durmuhurtam", lambda *_a, **_k: ["07:00:00"])
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        labels = {p["label"] for p in out["inauspicious_periods"]}
        assert "Durmuhurtam Period 1" not in labels
        assert "Durmuhurtam Period 2" not in labels

    def test_durmuhurtam_one_period_only(self, monkeypatch):
        """len==2 -> only Period 1; len<4 skips Period 2."""
        monkeypatch.setattr(
            m.drik, "durmuhurtam", lambda *_a, **_k: ["07:00:00", "08:00:00"]
        )
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        labels = {p["label"] for p in out["inauspicious_periods"]}
        assert "Durmuhurtam Period 1" in labels
        assert "Durmuhurtam Period 2" not in labels

    def test_durmuhurtam_two_periods(self, monkeypatch):
        monkeypatch.setattr(
            m.drik, "durmuhurtam",
            lambda *_a, **_k: ["07:00:00", "08:00:00", "13:00:00", "14:00:00"],
        )
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        labels = {p["label"] for p in out["inauspicious_periods"]}
        assert "Durmuhurtam Period 1" in labels
        assert "Durmuhurtam Period 2" in labels


class TestAmritaFallback:
    def test_amrita_failure(self, monkeypatch):
        monkeypatch.setattr(m.drik, "amrita_gadiya", _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["amrita_periods"] == []


class TestPanchakaFallback:
    def test_panchaka_failure_returns_none(self, monkeypatch):
        """A failed panchaka computation fails closed: panchaka_free is None
        ('could not be computed'), never a falsely-clean default of True."""
        monkeypatch.setattr(m.drik, "panchaka_rahitha", _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["panchaka_free"] is None

    def test_panchaka_dosha_spanning_noon_marks_not_free(self, monkeypatch):
        """A non-zero dosha window spanning local noon clears panchaka_free."""
        monkeypatch.setattr(
            m.drik, "panchaka_rahitha",
            lambda *_a, **_k: [(3, 10.0, 14.0)],  # dosha 3, brackets 12:00
        )
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["panchaka_free"] is False

    def test_panchaka_zero_dosha_keeps_free(self, monkeypatch):
        monkeypatch.setattr(
            m.drik, "panchaka_rahitha",
            lambda *_a, **_k: [(0, 10.0, 14.0)],  # dosha 0 -> still free
        )
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["panchaka_free"] is True


class TestPersonalBalamFallbacks:
    def test_tara_bala_fallback_on_bad_nakshatra(self):
        """An unknown birth nakshatra makes NAKSHATRAS.index raise -> 'Unknown'
        ('could not be computed'), aligning with compute_balam_at_jd. NOT
        'Neutral' (which would falsely read as a computed, benign result)."""
        out = m.compute_muhurat_for_day(
            PLACE, TARGET,
            birth_nakshatra="NotANakshatra", birth_moon_sign="Taurus",
        )
        assert out["personal_balam"]["tara_bala"] == "Unknown"

    def test_chandra_bala_fallback_on_bad_sign(self):
        """An unknown birth Moon sign makes SIGNS.index raise -> 'Unknown'."""
        out = m.compute_muhurat_for_day(
            PLACE, TARGET,
            birth_nakshatra="Rohini", birth_moon_sign="NotASign",
        )
        assert out["personal_balam"]["chandra_bala"] == "Unknown"

    # The transit Moon on 2026-05-26 (noon, Lahiri) sits in Virgo (sign idx 5).
    # Picking the birth Moon sign therefore selects each chandra-bala category
    # deterministically against the *real* ephemeris — no patching needed, so
    # the unguarded drik.tithi/nakshatra/yogam/karana calls keep working:
    #   Aries  -> diff 6  -> Good
    #   Taurus -> diff 5  -> Neutral
    #   Gemini -> diff 4  -> Inauspicious (Avoid)
    @pytest.mark.parametrize("birth_sign,expected", [
        ("Aries",  "Good"),
        ("Taurus", "Neutral"),
        ("Gemini", "Inauspicious (Avoid)"),
    ])
    def test_chandra_bala_categories(self, birth_sign, expected):
        out = m.compute_muhurat_for_day(
            PLACE, TARGET,
            birth_nakshatra="Rohini", birth_moon_sign=birth_sign,
        )
        assert out["personal_balam"]["chandra_bala"] == expected


class TestAllMuhurtas:
    def test_muhurthas_failure(self, monkeypatch):
        monkeypatch.setattr(m.drik, "muhurthas", _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["all_muhurtas"] == []

    def test_muhurthas_mixed_tuple_and_scalar(self, monkeypatch):
        """Branch 293->290: scalar entries are skipped, tuples are emitted."""
        def fake(*_a, **_k):
            # mix of valid 2-tuples and scalar/short entries
            return [
                (6.0, 6.8),     # emitted -> Rudra
                7.5,            # scalar -> skipped (continue)
                (8.0,),         # short tuple -> skipped
                (9.0, 9.8),     # emitted -> Pitri (index 3)
            ]
        monkeypatch.setattr(m.drik, "muhurthas", fake)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        labels = [x["label"] for x in out["all_muhurtas"]]
        assert labels == ["Rudra", "Pitri"]
        assert out["all_muhurtas"][0] == {
            "start": "06:00", "end": "06:48", "label": "Rudra"
        }

    def test_muhurthas_empty_list(self, monkeypatch):
        """Branch 290->298: an empty list exits the loop with no entries."""
        monkeypatch.setattr(m.drik, "muhurthas", lambda *_a, **_k: [])
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["all_muhurtas"] == []


# ---------------------------------------------------------------------------
# FIX #5 — degraded flag surfaces when sunrise or sunset fails
# ---------------------------------------------------------------------------

class TestDegradedFlag:
    def test_happy_path_not_degraded(self):
        """Normal ephemeris -> degraded is False."""
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["degraded"] is False

    def test_sunrise_failure_sets_degraded(self, monkeypatch, caplog):
        """sunrise raising -> degraded=True and warning logged."""
        import logging
        monkeypatch.setattr(m.drik, "sunrise", _wrap_bad_string(m.drik.sunrise))
        with caplog.at_level(logging.WARNING, logger="bphs_core.muhurat"):
            out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["degraded"] is True
        assert out["sunrise"] == "06:00"
        assert any("muhurat_sunrise_failed" in r.message for r in caplog.records)

    def test_sunset_failure_sets_degraded(self, monkeypatch, caplog):
        """sunset raising -> degraded=True and warning logged."""
        import logging
        monkeypatch.setattr(m.drik, "sunset", _wrap_bad_string(m.drik.sunset))
        with caplog.at_level(logging.WARNING, logger="bphs_core.muhurat"):
            out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["degraded"] is True
        assert out["sunset"] == "18:00"
        assert any("muhurat_sunset_failed" in r.message for r in caplog.records)

    def test_moonrise_failure_does_not_set_degraded(self, monkeypatch):
        """moonrise failing does NOT set degraded (no fallback value corruption)."""
        monkeypatch.setattr(m.drik, "moonrise", _raise)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["degraded"] is False
        assert out["moonrise"] is None


# ---------------------------------------------------------------------------
# Root-cause: nakshatra/yoga NAME comes from the sidereal longitudes directly,
# so a corrupt pyjhora index never wraps a wrong name — a valid name is always
# produced. The corrupt index only invalidates the end-time → day is degraded.
# ---------------------------------------------------------------------------

class TestNakshatraYogaIndexGuard:
    def test_nakshatra_index_zero_still_names_via_longitude(self, monkeypatch):
        """A corrupt pyjhora index (0) does NOT corrupt the name (computed from
        the Moon's longitude); only the end-time is lost and the day degrades."""
        real_nakshatra = m.drik.nakshatra

        def fake_nakshatra(jd, place):
            result = real_nakshatra(jd, place)
            # Return the same tuple structure but with the index forced to 0
            return (0,) + tuple(result[1:])

        monkeypatch.setattr(m.drik, "nakshatra", fake_nakshatra)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        # Name is still a VALID nakshatra (from the direct longitude computation).
        assert out["panchanga"]["nakshatra"] in utils.NAKSHATRAS
        # The out-of-range pyjhora index makes the end-time unavailable -> degraded.
        assert out["panchanga"]["nakshatra_end"] is None
        assert out["degraded"] is True

    def test_nakshatra_valid_index_returns_name(self):
        """Normal path: valid index returns the correct nakshatra name."""
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["panchanga"]["nakshatra"] in utils.NAKSHATRAS

    def test_yoga_index_zero_still_names_via_longitude(self, monkeypatch):
        """A corrupt pyjhora yoga index (0) does NOT corrupt the name (computed
        from the Sun+Moon longitude sum); only the end-time is lost -> degraded."""
        real_yogam = m.drik.yogam

        def fake_yogam(jd, place):
            result = real_yogam(jd, place)
            return (0,) + tuple(result[1:])

        monkeypatch.setattr(m.drik, "yogam", fake_yogam)
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["panchanga"]["yogam"] in m.YOGAS
        assert out["panchanga"]["yogam_end"] is None
        assert out["degraded"] is True

    def test_yoga_valid_index_returns_name(self):
        """Normal path: valid index returns the correct yoga name."""
        out = m.compute_muhurat_for_day(PLACE, TARGET)
        assert out["panchanga"]["yogam"] in m.YOGAS


# ---------------------------------------------------------------------------
# FIX #9 — hard_excluded flag in _score_instant detail
# ---------------------------------------------------------------------------

class TestHardExcluded:
    def test_hard_excluded_true_in_rahu_kala(self):
        """detail['hard_excluded'] is True when instant falls in Rahu Kala."""
        from bphs_core import lagna_shuddhi as ls

        # Build a minimal day_data where the entire day is Rahu Kala
        day_data = {
            "date": "2026-05-26",
            "sunrise": "06:00",
            "inauspicious_periods": [
                {"label": "Rahu Kala", "start": "00:00", "end": "23:59"},
            ],
            "auspicious_muhurtas": [],
            "chogadiya": [],
        }
        # Any minute of the day is inside Rahu Kala
        time_mins = 480  # 08:00
        jd = ls._jd_for_local("2026-05-26", time_mins, 5.5)
        _, detail = ls._score_instant(jd, "Aries", "Mars", day_data, time_mins, "generic")
        assert detail["hard_excluded"] is True
        assert detail["in_rahu_kala"] is True

    def test_hard_excluded_false_outside_inauspicious(self):
        """detail['hard_excluded'] is False for a clean instant."""
        from bphs_core import lagna_shuddhi as ls

        day_data = {
            "date": "2026-05-26",
            "sunrise": "06:00",
            "inauspicious_periods": [],
            "auspicious_muhurtas": [],
            "chogadiya": [],
        }
        time_mins = 480  # 08:00
        jd = ls._jd_for_local("2026-05-26", time_mins, 5.5)
        _, detail = ls._score_instant(jd, "Aries", "Mars", day_data, time_mins, "generic")
        assert detail["hard_excluded"] is False
