"""Rashi Drishti (Jaimini sign aspects) — unit tests."""
from datetime import datetime, time

from bphs_core.chart import Chart, PersonalData
from bphs_core import rashi_drishti as rd
from bphs_core.rashi_drishti import (
    get_rashi_drishti_table, get_planet_rashi_drishti, RASHI_DRISHTI_TABLE,
)
from tests.conftest import SAMPLE_A


def _snapshot(sample: dict):
    p = PersonalData(
        name=sample["name"],
        birth_date=datetime.strptime(sample["birth_date"], "%Y-%m-%d"),
        birth_time=time.fromisoformat(sample["birth_time"]),
        birth_place=sample["birth_place"],
        latitude=sample["latitude"],
        longitude=sample["longitude"],
        timezone_offset_hours=sample["timezone_offset_hours"],
    )
    return Chart(p).snapshot()


# The classical Jaimini rashi-drishti matrix (sign -> aspected signs), in
# zodiacal order. Movable aspects all fixed but the adjacent one; fixed aspects
# all movable but the adjacent one; dual aspects the other duals.
_CLASSICAL_TABLE = {
    "Aries":       ["Leo", "Scorpio", "Aquarius"],          # movable; excludes adjacent fixed Taurus
    "Taurus":      ["Cancer", "Libra", "Capricorn"],        # fixed; excludes adjacent movable Aries
    "Gemini":      ["Virgo", "Sagittarius", "Pisces"],      # dual
    "Cancer":      ["Taurus", "Scorpio", "Aquarius"],       # movable; excludes adjacent fixed Leo
    "Leo":         ["Aries", "Libra", "Capricorn"],         # fixed; excludes adjacent movable Cancer
    "Virgo":       ["Gemini", "Sagittarius", "Pisces"],     # dual
    "Libra":       ["Taurus", "Leo", "Aquarius"],           # movable; excludes adjacent fixed Scorpio
    "Scorpio":     ["Aries", "Cancer", "Capricorn"],        # fixed; excludes adjacent movable Libra
    "Sagittarius": ["Gemini", "Virgo", "Pisces"],           # dual
    "Capricorn":   ["Taurus", "Leo", "Scorpio"],            # movable; excludes adjacent fixed Aquarius
    "Aquarius":    ["Aries", "Cancer", "Libra"],            # fixed; excludes adjacent movable Capricorn
    "Pisces":      ["Gemini", "Virgo", "Sagittarius"],      # dual
}


def test_rashi_drishti_table_is_exact_classical_matrix():
    table = get_rashi_drishti_table()
    assert set(table) == set(_CLASSICAL_TABLE)
    for sign, expected in _CLASSICAL_TABLE.items():
        assert sorted(table[sign]) == sorted(expected), sign


def test_every_sign_aspects_exactly_three():
    for sign, aspected in RASHI_DRISHTI_TABLE.items():
        assert len(aspected) == 3, sign
        assert sign not in aspected  # no self-aspect


def test_rashi_drishti_is_symmetric():
    table = RASHI_DRISHTI_TABLE
    for sign, aspected in table.items():
        for other in aspected:
            assert sign in table[other], f"{sign}->{other} not mirrored"


def test_movable_only_aspects_fixed_and_fixed_only_movable():
    movable = {"Aries", "Cancer", "Libra", "Capricorn"}
    fixed = {"Taurus", "Leo", "Scorpio", "Aquarius"}
    dual = {"Gemini", "Virgo", "Sagittarius", "Pisces"}
    for sign, aspected in RASHI_DRISHTI_TABLE.items():
        if sign in movable:
            assert set(aspected) <= fixed
        elif sign in fixed:
            assert set(aspected) <= movable
        else:
            assert set(aspected) <= dual


def test_per_planet_view_over_chart():
    snap = _snapshot(SAMPLE_A)
    views = get_planet_rashi_drishti(snap)
    # one entry per planet in the rasi chart (9 grahas incl. nodes)
    planets = {v.planet for v in views}
    assert planets == set(snap.rasi_chart)
    for v in views:
        # aspected signs match the table for the planet's own sign
        assert sorted(v.aspects_signs) == sorted(RASHI_DRISHTI_TABLE[v.sign])
        # every aspected planet actually sits in an aspected sign and isn't self
        for other in v.aspects_planets:
            assert other != v.planet
            assert snap.rasi_chart[other].sign in v.aspects_signs
