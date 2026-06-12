"""Rashi Drishti — Jaimini sign aspects (deterministic, no ephemeris).

Classical rule (Jaimini Sutras / BPHS sign-aspect doctrine):

* **Movable** signs (Aries, Cancer, Libra, Capricorn) aspect all **fixed** signs
  EXCEPT the one adjacent to them (the fixed sign immediately next in zodiacal
  order).
* **Fixed** signs (Taurus, Leo, Scorpio, Aquarius) aspect all **movable** signs
  EXCEPT the one adjacent to them (the movable sign immediately preceding).
* **Dual** signs (Gemini, Virgo, Sagittarius, Pisces) aspect the other three
  dual signs.

A sign therefore aspects exactly three signs. The relation is symmetric: if A
aspects B then B aspects A.

Source: Jaimini Upadesa Sutras 1.1; BPHS ch. on Rasi Drishti.
"""
from dataclasses import dataclass, field

from .chart import ChartSnapshot
from . import utils

# Sign quality (chara/sthira/dwiswabhava) by zodiacal index 0..11.
_MOVABLE = {"Aries", "Cancer", "Libra", "Capricorn"}
_FIXED = {"Taurus", "Leo", "Scorpio", "Aquarius"}
_DUAL = {"Gemini", "Virgo", "Sagittarius", "Pisces"}


def _quality(sign: str) -> str:
    if sign in _MOVABLE:
        return "movable"
    if sign in _FIXED:
        return "fixed"
    return "dual"


def _build_aspect_table() -> dict[str, list[str]]:
    """The full 12-sign rashi-drishti map: sign -> sorted-by-zodiac aspected signs.

    Derived from first principles so the table is provably the classical matrix
    (the test asserts the exact map). Each movable sign sits one place before a
    fixed sign in zodiacal order; the "adjacent" excluded fixed sign is that
    immediate successor. Each fixed sign's adjacent excluded movable sign is its
    immediate predecessor. The relation comes out symmetric.
    """
    signs = utils.SIGNS
    table: dict[str, set[str]] = {s: set() for s in signs}

    for i, sign in enumerate(signs):
        q = _quality(sign)
        if q == "movable":
            # the fixed sign immediately after this movable sign is adjacent (excluded)
            adjacent_fixed = signs[(i + 1) % 12]
            for t in _FIXED:
                if t != adjacent_fixed:
                    table[sign].add(t)
        elif q == "fixed":
            # the movable sign immediately before this fixed sign is adjacent (excluded)
            adjacent_movable = signs[(i - 1) % 12]
            for t in _MOVABLE:
                if t != adjacent_movable:
                    table[sign].add(t)
        else:  # dual aspects the other three dual signs
            for t in _DUAL:
                if t != sign:
                    table[sign].add(t)

    # Return sorted by zodiacal index for stable, deterministic output.
    order = {s: idx for idx, s in enumerate(signs)}
    return {s: sorted(table[s], key=order.__getitem__) for s in signs}


# Computed once; pure constant.
RASHI_DRISHTI_TABLE: dict[str, list[str]] = _build_aspect_table()


@dataclass
class PlanetRashiDrishti:
    planet: str
    sign: str
    aspects_signs: list[str] = field(default_factory=list)
    aspects_planets: list[str] = field(default_factory=list)


def get_rashi_drishti_table() -> dict[str, list[str]]:
    """Return a copy of the classical sign -> [aspected signs] map."""
    return {s: list(v) for s, v in RASHI_DRISHTI_TABLE.items()}


def get_planet_rashi_drishti(snapshot: ChartSnapshot) -> list[PlanetRashiDrishti]:
    """Per-planet rashi-drishti view over the rasi chart.

    A planet casts rashi drishti onto every sign its own sign aspects, and onto
    every (other) planet sitting in one of those aspected signs.
    """
    # sign -> planets occupying it (rasi chart)
    occupants: dict[str, list[str]] = {}
    for name, pd in snapshot.rasi_chart.items():
        occupants.setdefault(pd.sign, []).append(name)

    results: list[PlanetRashiDrishti] = []
    for name, pd in snapshot.rasi_chart.items():
        aspected_signs = RASHI_DRISHTI_TABLE.get(pd.sign, [])
        aspected_planets: list[str] = []
        for s in aspected_signs:
            for other in occupants.get(s, []):
                if other != name:
                    aspected_planets.append(other)
        results.append(PlanetRashiDrishti(
            planet=name,
            sign=pd.sign,
            aspects_signs=list(aspected_signs),
            aspects_planets=aspected_planets,
        ))
    return results
