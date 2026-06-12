"""Indu Lagna + Beeja/Kshetra Sphuta — unit tests.

Synthetic snapshots only (no birth data). Indu Lagna is hand-computed; the
sphuta parity/strength logic is exercised as a truth-table.
"""
import datetime

import pytest

from bphs_core.chart import ChartSnapshot, PlanetData, PersonalData
from bphs_core import special_points as sp
from bphs_core.special_points import (
    get_indu_lagna, get_beeja_sphuta, get_kshetra_sphuta,
    _sphuta_strength, _navamsa_sign_of_longitude, _parity,
)
from bphs_core import utils


def _mock_chart(planets: dict[str, dict], lagna: str = "Aries",
                lagna_lord: str = "Mars") -> ChartSnapshot:
    rasi = {}
    for p, d in planets.items():
        sign = d.get("sign", "Aries")
        deg = d.get("degrees", 0.0)
        lon = utils.SIGNS.index(sign) * 30.0 + deg
        rasi[p] = PlanetData(
            planet=p, sign=sign, degrees=deg,
            nakshatra=d.get("nakshatra", "Ashwini"),
            dignity=d.get("dignity", "neutral"),
            house=d.get("house", 1),
            conjunctions=[], aspects=[],
            is_retrograde=d.get("is_retrograde", False),
            longitude_abs=d.get("longitude_abs", lon),
        )
    return ChartSnapshot(
        person=PersonalData(
            name="Synthetic", birth_date=datetime.date(2000, 1, 1),
            birth_time=datetime.time(12, 0), birth_place="X",
            latitude=0.0, longitude=0.0, timezone_offset_hours=0.0,
        ),
        rasi_chart=rasi, hora_chart={}, drekkana_chart={}, navamsa_chart={},
        decamsa_chart={}, dwadasamsa_chart={}, chaturvimsa_chart={},
        trimshamsa_chart={}, saptamsa_chart={}, shashtyamsa_chart={},
        lagna=lagna, lagna_lord=lagna_lord, ayanamsa_value=0.0,
        house_cusps=[(i * 30.0) for i in range(12)],
    )


# ---------------------------------------------------------------------------
# Indu Lagna — hand-computed
# ---------------------------------------------------------------------------

def test_indu_lagna_hand_computed():
    # Lagna = Aries -> 9th sign from Aries is Sagittarius, lord Jupiter (kala 10).
    # Moon in Taurus -> 9th sign from Taurus is Capricorn, lord Saturn (kala 1).
    # sum = 10 + 1 = 11; 11 % 12 = 11; counted from Moon's sign Taurus:
    #   11 signs inclusive from Taurus (idx 1) -> idx (1 + 11 - 1) % 12 = 11 = Pisces.
    snap = _mock_chart(
        {
            "Moon": {"sign": "Taurus", "house": 2},
            "Jupiter": {"sign": "Leo", "house": 5, "dignity": "friendly"},
            "Saturn": {"sign": "Cancer", "house": 4, "dignity": "debilitated"},
        },
        lagna="Aries", lagna_lord="Mars",
    )
    indu = get_indu_lagna(snap)
    assert indu.sign == "Pisces"
    # Pisces is the 12th house from Aries lagna.
    assert indu.house_from_lagna == 12
    # Lord of Pisces is Jupiter; its dignity/house come from the rasi chart.
    assert indu.lord == "Jupiter"
    assert indu.lord_dignity == "friendly"
    assert indu.lord_house == 5
    # No planet tenants Pisces here.
    assert indu.occupants == []


def test_indu_lagna_remainder_zero_maps_to_twelfth():
    # Construct a case where the kala sum is a multiple of 12 -> remainder 12.
    # Lagna = Leo -> 9th = Aries, lord Mars (kala 6).
    # Moon in Cancer -> 9th = Pisces, lord Jupiter (kala 10). sum=16 -> %12 = 4.
    # Use a contrived placement giving exactly 12: 9th-from-lagna lord Sun(30) is
    # impossible directly; instead verify the remainder==0 -> 12 branch with kalas
    # summing to 24: Sun(30) requires lagna whose 9th is Leo. Lagna=Sagittarius ->
    # 9th=Leo (Sun, 30). Moon in Leo -> 9th=Aries (Mars, 6). sum=36 -> %12 == 0 -> 12.
    snap = _mock_chart(
        {
            "Moon": {"sign": "Leo", "house": 9},
            "Sun": {"sign": "Leo", "house": 9, "dignity": "own sign"},
            "Mars": {"sign": "Aries", "house": 5, "dignity": "own sign"},
        },
        lagna="Sagittarius", lagna_lord="Jupiter",
    )
    indu = get_indu_lagna(snap)
    # remainder 12 counted from Moon's sign Leo (idx 4): (4 + 12 - 1) % 12 = 3 = Cancer.
    assert indu.sign == "Cancer"


# ---------------------------------------------------------------------------
# Sphuta parity / strength truth-table
# ---------------------------------------------------------------------------

def test_parity_helper():
    # Odd signs (1-based odd): Aries(0), Gemini(2), Leo(4)... -> "odd".
    assert _parity(0) == "odd"     # Aries (1st)
    assert _parity(1) == "even"    # Taurus (2nd)
    assert _parity(2) == "odd"     # Gemini (3rd)


@pytest.mark.parametrize("favourable,sign_p,nav_p,expected", [
    # Beeja: favourable parity = odd
    ("odd",  "odd",  "odd",  "strong"),
    ("odd",  "odd",  "even", "middling"),
    ("odd",  "even", "odd",  "middling"),
    ("odd",  "even", "even", "weak"),
    # Kshetra: favourable parity = even (mirror)
    ("even", "even", "even", "strong"),
    ("even", "even", "odd",  "middling"),
    ("even", "odd",  "even", "middling"),
    ("even", "odd",  "odd",  "weak"),
])
def test_sphuta_strength_truth_table(favourable, sign_p, nav_p, expected):
    assert _sphuta_strength(favourable, sign_p, nav_p) == expected


def test_beeja_sphuta_sum_and_strength():
    # Sun 10° Aries (10), Venus 20° Aries (20), Jupiter 30°... use clean numbers.
    # Sun lon = 10, Venus lon = 20, Jupiter lon = 30 -> sum = 60 -> 0° Gemini.
    snap = _mock_chart({
        "Sun": {"sign": "Aries", "degrees": 10.0, "longitude_abs": 10.0},
        "Venus": {"sign": "Aries", "degrees": 20.0, "longitude_abs": 20.0},
        "Jupiter": {"sign": "Taurus", "degrees": 0.0, "longitude_abs": 30.0},
    })
    beeja = get_beeja_sphuta(snap)
    assert beeja.longitude == 60.0
    assert beeja.sign == "Gemini"            # 3rd sign -> odd
    assert beeja.sign_parity == "odd"
    # 60° -> navamsa: pada = int(60 / 3.3333) = 18; 18 % 12 = 6 -> Libra (odd).
    assert beeja.navamsa_sign == _navamsa_sign_of_longitude(60.0)
    assert beeja.navamsa_parity == _parity(utils.SIGNS.index(beeja.navamsa_sign))
    # both odd -> strong
    assert beeja.sign_parity == "odd" and beeja.navamsa_parity == "odd"
    assert beeja.strength == "strong"
    assert beeja.sign_lord == "Mercury"      # lord of Gemini


def test_kshetra_sphuta_sum_and_strength():
    # Moon 5° Taurus (35), Mars 10° Taurus (40), Jupiter 15° Taurus (45) -> 120 = 0° Leo.
    snap = _mock_chart({
        "Moon": {"sign": "Taurus", "degrees": 5.0, "longitude_abs": 35.0},
        "Mars": {"sign": "Taurus", "degrees": 10.0, "longitude_abs": 40.0},
        "Jupiter": {"sign": "Taurus", "degrees": 15.0, "longitude_abs": 45.0},
    })
    kshetra = get_kshetra_sphuta(snap)
    assert kshetra.longitude == 120.0
    assert kshetra.sign == "Leo"             # 5th sign -> odd (unfavourable for kshetra)
    assert kshetra.sign_parity == "odd"
    # favourable parity for kshetra is even; sign is odd -> at most middling
    assert kshetra.strength in ("weak", "middling")
    assert kshetra.sign_lord == "Sun"
