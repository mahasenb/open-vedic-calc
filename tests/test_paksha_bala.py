"""Tests for paksha-based Moon benefic/malefic classification.

The Moon is benefic when waxing (shukla paksha) and malefic when waning
(krishna paksha). Waxing = (moon_longitude - sun_longitude) mod 360 is in
(0, 180). Waning = (180, 360).

Covers:
  1. _moon_is_benefic helper: waxing (elongation ~90°) → True,
     waning (elongation ~270°) → False.
  2. _drik_bala: Moon aspecting a planet contributes +15 when waxing,
     -15 when waning.
  3. _bhava_drik_bala: Moon in a house contributes +10 when waxing,
     -10 when waning — consistent with _drik_bala polarity.
  4. Edge cases: elongation exactly 0 and exactly 180 (boundary excluded
     from waxing per open interval rule).

All fixtures are synthetic; no personal data used.
"""
import datetime
import pytest
from bphs_core.chart import ChartSnapshot, PlanetData, PersonalData


# ---------------------------------------------------------------------------
# Shared helper: build a minimal synthetic ChartSnapshot
# ---------------------------------------------------------------------------

def _make_snap(
    planets: dict[str, dict],
    lagna: str = "Aries",
    lagna_lord: str = "Mars",
) -> ChartSnapshot:
    """Build a synthetic ChartSnapshot from a dict of planet descriptors.

    Required per-planet keys:
      sign (str), house (int), longitude_abs (float)
    Optional: degrees, nakshatra, dignity, is_retrograde, aspects (list[str])
    """
    rasi: dict[str, PlanetData] = {}
    for name, d in planets.items():
        rasi[name] = PlanetData(
            planet=name,
            sign=d.get("sign", "Aries"),
            degrees=d.get("degrees", 0.0),
            nakshatra=d.get("nakshatra", "Ashwini"),
            dignity=d.get("dignity", "neutral"),
            house=d.get("house", 1),
            conjunctions=[],
            aspects=d.get("aspects", []),
            is_retrograde=d.get("is_retrograde", False),
            longitude_abs=d.get("longitude_abs", 0.0),
        )
    return ChartSnapshot(
        person=PersonalData(
            name="Synthetic",
            birth_date=datetime.date(2000, 1, 1),
            birth_time=datetime.time(12, 0),
            birth_place="X",
            latitude=0.0,
            longitude=0.0,
            timezone_offset_hours=0.0,
        ),
        rasi_chart=rasi,
        hora_chart={},
        drekkana_chart={},
        navamsa_chart={},
        decamsa_chart={},
        dwadasamsa_chart={},
        chaturvimsa_chart={},
        trimshamsa_chart={},
        saptamsa_chart={},
        shashtyamsa_chart={},
        lagna=lagna,
        lagna_lord=lagna_lord,
        ayanamsa_value=0.0,
        house_cusps=[(i * 30.0) for i in range(12)],
    )


# ---------------------------------------------------------------------------
# 1. _moon_is_benefic helper
# ---------------------------------------------------------------------------

class TestMoonIsBeneficHelper:
    """Unit tests for _moon_is_benefic(snapshot) helper."""

    def test_waxing_moon_is_benefic(self):
        """Moon 90° ahead of Sun (shukla paksha) → True."""
        from bphs_core.strength import _moon_is_benefic

        # Sun at 0°, Moon at 90° → elongation (90 - 0) % 360 = 90 → waxing
        snap = _make_snap({
            "Sun":  {"sign": "Aries",  "house": 1, "longitude_abs": 0.0},
            "Moon": {"sign": "Cancer", "house": 4, "longitude_abs": 90.0},
        })
        assert _moon_is_benefic(snap) is True

    def test_waning_moon_is_malefic(self):
        """Moon 270° ahead of Sun (krishna paksha) → False."""
        from bphs_core.strength import _moon_is_benefic

        # Sun at 0°, Moon at 270° → elongation (270 - 0) % 360 = 270 → waning
        snap = _make_snap({
            "Sun":  {"sign": "Aries",       "house": 1, "longitude_abs": 0.0},
            "Moon": {"sign": "Capricorn",   "house": 10, "longitude_abs": 270.0},
        })
        assert _moon_is_benefic(snap) is False

    def test_elongation_exactly_zero_is_malefic(self):
        """Elongation = 0 (new moon, Sun conjunct Moon) is NOT waxing (open interval)."""
        from bphs_core.strength import _moon_is_benefic

        snap = _make_snap({
            "Sun":  {"sign": "Aries", "house": 1, "longitude_abs": 45.0},
            "Moon": {"sign": "Aries", "house": 1, "longitude_abs": 45.0},
        })
        # (45 - 45) % 360 = 0 → not in (0, 180) → malefic
        assert _moon_is_benefic(snap) is False

    def test_elongation_exactly_180_is_malefic(self):
        """Elongation = 180 (full moon exact) is NOT waxing (open interval)."""
        from bphs_core.strength import _moon_is_benefic

        snap = _make_snap({
            "Sun":  {"sign": "Aries",  "house": 1,  "longitude_abs": 0.0},
            "Moon": {"sign": "Libra",  "house": 7,  "longitude_abs": 180.0},
        })
        # (180 - 0) % 360 = 180 → not in (0, 180) → malefic
        assert _moon_is_benefic(snap) is False

    def test_elongation_wraps_correctly(self):
        """Sun at 300°, Moon at 30° → elongation (30 - 300) % 360 = 90 → waxing."""
        from bphs_core.strength import _moon_is_benefic

        snap = _make_snap({
            "Sun":  {"sign": "Aquarius", "house": 11, "longitude_abs": 300.0},
            "Moon": {"sign": "Aries",    "house": 1,  "longitude_abs": 30.0},
        })
        # (30 - 300) % 360 = -270 % 360 = 90 → waxing
        assert _moon_is_benefic(snap) is True

    def test_elongation_just_below_180_is_benefic(self):
        """Elongation 179.9° is still waxing (inside open interval)."""
        from bphs_core.strength import _moon_is_benefic

        snap = _make_snap({
            "Sun":  {"sign": "Aries",  "house": 1, "longitude_abs": 0.0},
            "Moon": {"sign": "Libra",  "house": 7, "longitude_abs": 179.9},
        })
        assert _moon_is_benefic(snap) is True

    def test_elongation_just_above_180_is_malefic(self):
        """Elongation 180.1° is waning."""
        from bphs_core.strength import _moon_is_benefic

        snap = _make_snap({
            "Sun":  {"sign": "Aries",  "house": 1, "longitude_abs": 0.0},
            "Moon": {"sign": "Libra",  "house": 7, "longitude_abs": 180.1},
        })
        assert _moon_is_benefic(snap) is False


# ---------------------------------------------------------------------------
# 2. _drik_bala: Moon as the aspecting planet
# ---------------------------------------------------------------------------

class TestDrikBalaForMoon:
    """_drik_bala for the *target* planet; Moon is in the aspects list
    of the target, so the Moon's phase determines its polarity."""

    def test_waxing_moon_aspects_target_as_benefic(self):
        """When Moon is waxing and aspects the target planet, it contributes +15.

        Setup: Saturn in house 1 (target); Moon in house 7 aspects Saturn via
        7th-house mutual aspect. Moon is waxing (elongation 90°).
        No other planets present → drik_bala score comes from Moon alone.
        """
        from bphs_core.strength import _drik_bala

        # Moon at 90°, Sun at 0° → elongation 90 → waxing benefic
        snap = _make_snap({
            "Sun":    {"sign": "Aries",  "house": 1,  "longitude_abs": 0.0},
            "Moon":   {"sign": "Cancer", "house": 4,  "longitude_abs": 90.0,
                       "aspects": ["Saturn"]},
            "Saturn": {"sign": "Libra",  "house": 7,  "longitude_abs": 180.0,
                       "aspects": ["Moon"]},
        })
        score = _drik_bala(snap, "Saturn")
        # Only Moon aspects Saturn; Moon is waxing → +15; max(0, 15) = 15
        assert score == 15.0, f"Waxing Moon should contribute +15 to drik_bala, got {score}"

    def test_waning_moon_aspects_target_as_malefic(self):
        """When Moon is waning and aspects the target planet, it contributes -15.

        Setup: Same geometry but Moon at 270° (waning).
        """
        from bphs_core.strength import _drik_bala

        # Moon at 270°, Sun at 0° → elongation 270 → waning malefic
        snap = _make_snap({
            "Sun":    {"sign": "Aries",      "house": 1,  "longitude_abs": 0.0},
            "Moon":   {"sign": "Capricorn",  "house": 10, "longitude_abs": 270.0,
                       "aspects": ["Saturn"]},
            "Saturn": {"sign": "Aries",      "house": 1,  "longitude_abs": 5.0,
                       "aspects": ["Moon"]},
        })
        score = _drik_bala(snap, "Saturn")
        # Moon is waning → -15; max(0, -15) = 0
        assert score == 0.0, f"Waning Moon drik_bala (clamped): expected 0.0, got {score}"

    def test_waning_moon_raw_score_negative(self):
        """Verify the UNCLAMPED Moon waning contribution is -15.

        We add a Jupiter (benefic +15) alongside a waning Moon (-15) so we can
        verify the composition: raw = 0. The clamp returns 0 but the internal
        math can be verified by having the Moon be the sole aspector with no
        other offset — already shown above. Here we verify sum neutralisation.
        """
        from bphs_core.strength import _drik_bala

        # Jupiter (benefic +15) + waning Moon (-15) = 0 raw → clamped to 0
        snap = _make_snap({
            "Sun":     {"sign": "Aries",     "house": 1,  "longitude_abs": 0.0},
            "Moon":    {"sign": "Capricorn", "house": 10, "longitude_abs": 270.0,
                        "aspects": ["Venus"]},
            "Jupiter": {"sign": "Gemini",    "house": 3,  "longitude_abs": 60.0,
                        "aspects": ["Venus"]},
            "Venus":   {"sign": "Cancer",    "house": 4,  "longitude_abs": 95.0,
                        "aspects": []},
        })
        score = _drik_bala(snap, "Venus")
        # Jupiter +15, waning Moon -15 → raw 0 → clamped 0
        assert score == 0.0, f"Jupiter +15 cancels waning Moon -15: expected 0.0, got {score}"

    def test_drik_bala_sun_mars_saturn_remain_malefic(self):
        """Sun, Mars, Saturn remain statically malefic regardless of Moon phase."""
        from bphs_core.strength import _drik_bala

        snap = _make_snap({
            "Sun":    {"sign": "Aries",   "house": 1, "longitude_abs": 0.0,
                       "aspects": ["Jupiter"]},
            "Mars":   {"sign": "Cancer",  "house": 4, "longitude_abs": 90.0,
                       "aspects": ["Jupiter"]},
            "Saturn": {"sign": "Libra",   "house": 7, "longitude_abs": 180.0,
                       "aspects": ["Jupiter"]},
            "Jupiter": {"sign": "Capricorn", "house": 10, "longitude_abs": 270.0,
                        "aspects": []},
        })
        score = _drik_bala(snap, "Jupiter")
        # Sun -15 + Mars -15 + Saturn -15 = -45 raw → clamped to 0
        assert score == 0.0, f"Sun+Mars+Saturn should be malefic: expected 0.0, got {score}"

    def test_drik_bala_jupiter_venus_remain_benefic(self):
        """Jupiter and Venus remain statically benefic."""
        from bphs_core.strength import _drik_bala

        snap = _make_snap({
            "Sun":     {"sign": "Aries",      "house": 1,  "longitude_abs": 0.0},
            "Jupiter": {"sign": "Cancer",     "house": 4,  "longitude_abs": 90.0,
                        "aspects": ["Saturn"]},
            "Venus":   {"sign": "Capricorn",  "house": 10, "longitude_abs": 270.0,
                        "aspects": ["Saturn"]},
            "Saturn":  {"sign": "Libra",      "house": 7,  "longitude_abs": 180.0,
                        "aspects": []},
        })
        score = _drik_bala(snap, "Saturn")
        # Jupiter +15 + Venus +15 = +30
        assert score == 30.0, f"Jupiter+Venus should be benefic: expected 30.0, got {score}"


# ---------------------------------------------------------------------------
# 3. _bhava_drik_bala: Moon in a house
# ---------------------------------------------------------------------------

class TestBhavaDrikBalaForMoon:
    """_bhava_drik_bala for a house occupied by the Moon.

    Moon in the target house contributes +10 when waxing, -10 when waning.
    """

    def test_waxing_moon_in_house_is_benefic(self):
        """Waxing Moon occupying the target house → +10 contribution."""
        from bphs_core.strength import _bhava_drik_bala

        # Moon at 90°, Sun at 0° → elongation 90 → waxing
        # Moon in house 4 (target house = 4)
        snap = _make_snap({
            "Sun":  {"sign": "Aries",  "house": 1, "longitude_abs": 0.0},
            "Moon": {"sign": "Cancer", "house": 4, "longitude_abs": 90.0},
        })
        score = _bhava_drik_bala(snap, 4)
        assert score == 10.0, (
            f"Waxing Moon in target house should contribute +10, got {score}"
        )

    def test_waning_moon_in_house_is_malefic(self):
        """Waning Moon occupying the target house → -10 contribution."""
        from bphs_core.strength import _bhava_drik_bala

        # Moon at 270°, Sun at 0° → elongation 270 → waning
        # Moon in house 10 (target house = 10)
        snap = _make_snap({
            "Sun":  {"sign": "Aries",      "house": 1,  "longitude_abs": 0.0},
            "Moon": {"sign": "Capricorn",  "house": 10, "longitude_abs": 270.0},
        })
        score = _bhava_drik_bala(snap, 10)
        assert score == -10.0, (
            f"Waning Moon in target house should contribute -10, got {score}"
        )

    def test_moon_not_in_house_has_no_effect(self):
        """Moon in a different house has no effect on the target house."""
        from bphs_core.strength import _bhava_drik_bala

        snap = _make_snap({
            "Sun":  {"sign": "Aries",  "house": 1, "longitude_abs": 0.0},
            "Moon": {"sign": "Cancer", "house": 4, "longitude_abs": 90.0},
        })
        score = _bhava_drik_bala(snap, 7)  # Moon is in house 4, not 7
        assert score == 0.0, (
            f"Moon in different house should not affect house 7, got {score}"
        )

    def test_bhava_drik_and_drik_bala_same_polarity(self):
        """_bhava_drik_bala and _drik_bala must agree on Moon polarity.

        When Moon is waxing: _bhava_drik_bala contributes +10 (positive),
        _drik_bala contributes +15 (positive) for any planet aspected by Moon.
        When Moon is waning: both contribute negative amounts.
        """
        from bphs_core.strength import _bhava_drik_bala, _drik_bala

        # Waxing Moon
        waxing_snap = _make_snap({
            "Sun":    {"sign": "Aries",  "house": 1, "longitude_abs": 0.0},
            "Moon":   {"sign": "Cancer", "house": 4, "longitude_abs": 90.0,
                       "aspects": ["Mars"]},
            "Mars":   {"sign": "Aries",  "house": 1, "longitude_abs": 5.0,
                       "aspects": ["Moon"]},
        })
        bhava_waxing = _bhava_drik_bala(waxing_snap, 4)   # Moon is in house 4
        drik_waxing  = _drik_bala(waxing_snap, "Mars")    # Moon aspects Mars

        assert bhava_waxing > 0, "Waxing Moon in house should be positive in bhava_drik"
        assert drik_waxing > 0,  "Waxing Moon aspecting planet should be positive in drik_bala"

        # Waning Moon
        waning_snap = _make_snap({
            "Sun":    {"sign": "Aries",      "house": 1,  "longitude_abs": 0.0},
            "Moon":   {"sign": "Capricorn",  "house": 10, "longitude_abs": 270.0,
                       "aspects": ["Mars"]},
            "Mars":   {"sign": "Aries",      "house": 1,  "longitude_abs": 5.0,
                       "aspects": ["Moon"]},
        })
        bhava_waning = _bhava_drik_bala(waning_snap, 10)  # Moon is in house 10
        drik_waning  = _drik_bala(waning_snap, "Mars")    # Moon aspects Mars (clamped)

        assert bhava_waning < 0,   "Waning Moon in house should be negative in bhava_drik"
        assert drik_waning == 0.0, "Waning Moon aspecting planet: drik_bala clamped to 0"

    def test_bhava_drik_mars_saturn_sun_remain_malefic(self):
        """Mars, Saturn, Sun in target house remain statically malefic."""
        from bphs_core.strength import _bhava_drik_bala

        snap = _make_snap({
            "Sun":    {"sign": "Aries", "house": 1, "longitude_abs": 0.0},
            "Mars":   {"sign": "Aries", "house": 1, "longitude_abs": 5.0},
            "Saturn": {"sign": "Aries", "house": 1, "longitude_abs": 10.0},
        })
        score = _bhava_drik_bala(snap, 1)
        # Mars -10 + Saturn -10 + (Sun: Sun is NOT in the bhava malefic list; check)
        # Per original code: "Mars", "Saturn", "Sun" are malefic in _bhava_drik_bala
        # Sun -10 + Mars -10 + Saturn -10 = -30
        assert score == -30.0, (
            f"Sun+Mars+Saturn in house should be -30, got {score}"
        )
