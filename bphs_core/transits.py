from dataclasses import dataclass
from datetime import datetime, timedelta
import swisseph as swe
from jhora.panchanga import drik
from .chart import ChartSnapshot
from . import utils

_PYJHORA_TRANSIT_PLANETS = {
    "Sun": 0, "Moon": 1, "Mars": 2, "Mercury": 3,
    "Jupiter": 4, "Venus": 5, "Saturn": 6, "Rahu": 7,
}

# The seven grahas that participate in classical gochara. Rahu and Ketu have no
# classical gochara-vedha positions and no bhinnashtakavarga column, so they are
# excluded from both the vedha veto and the bindu magnitude signal (their transit
# placements are still reported, just without those two signals).
_GOCHARA_GRAHAS = ("Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn")

# Classical gochara-vedha tables, positions counted from the natal Moon.
# For each graha: {favourable_house_from_moon: obstructing(vedha)_house_from_moon}.
# When the graha transits one of its favourable houses and another, non-exempt
# graha simultaneously occupies the paired vedha house, the favourable result is
# neutralised. (Standard set per Phaladeepika / B.V. Raman, "Gochara".)
_GOCHARA_VEDHA: dict[str, dict[int, int]] = {
    "Sun":     {3: 9, 6: 12, 10: 4, 11: 5},
    "Moon":    {1: 5, 3: 9, 6: 12, 7: 2, 10: 4, 11: 8},
    "Mars":    {3: 12, 6: 9, 11: 5},
    "Mercury": {2: 5, 4: 3, 6: 9, 8: 1, 10: 8, 11: 12},
    "Jupiter": {2: 12, 5: 4, 7: 3, 9: 10, 11: 8},
    "Venus":   {1: 8, 2: 7, 3: 1, 4: 10, 5: 9, 8: 5, 9: 11, 11: 3, 12: 6},
    "Saturn":  {3: 12, 6: 9, 11: 5},
}

# Mutual-exemption pairs: these grahas never obstruct each other's transit
# results, so a vedha between them is recorded but flagged as not neutralising.
_VEDHA_EXEMPT_PAIRS: frozenset = frozenset({
    frozenset({"Sun", "Saturn"}),
    frozenset({"Moon", "Mercury"}),
})


@dataclass
class TransitPlacement:
    planet: str
    sign: str
    degrees: float
    nakshatra: str


@dataclass
class SadeSatiInfo:
    is_active: bool
    phase: str
    start_date: datetime
    end_date: datetime


def _transit_longitude(jd: float, planet_id: int) -> float:
    # Ayanamsa mode is a process-global set once at import (utils.py). Setting it
    # on every call mutates that C-level global 60+ times per request and can race
    # with concurrent requests, so it is deliberately not set here.
    return drik.sidereal_longitude(jd, planet_id)


def _jd_from_date(dt: datetime) -> float:
    return swe.julday(dt.year, dt.month, dt.day,
                      dt.hour + dt.minute / 60 + dt.second / 3600)


def get_current_transits(snapshot: ChartSnapshot, at: datetime) -> dict:
    jd = _jd_from_date(at)
    result: dict[str, TransitPlacement] = {}
    for name, pid in _PYJHORA_TRANSIT_PLANETS.items():
        lon = _transit_longitude(jd, pid)
        sign, deg = utils.longitude_to_sign_and_degree(lon)
        nakshatra = utils.longitude_to_nakshatra(lon)
        result[name] = TransitPlacement(
            planet=name, sign=sign, degrees=round(deg, 4), nakshatra=nakshatra,
        )
    # Ketu is always exactly 180° from Rahu
    rahu_lon = _transit_longitude(jd, 7)
    ketu_lon = (rahu_lon + 180) % 360
    ketu_sign, ketu_deg = utils.longitude_to_sign_and_degree(ketu_lon)
    result["Ketu"] = TransitPlacement(
        planet="Ketu", sign=ketu_sign, degrees=round(ketu_deg, 4),
        nakshatra=utils.longitude_to_nakshatra(ketu_lon),
    )
    return result


def get_sade_sati_info(snapshot: ChartSnapshot, at: datetime) -> SadeSatiInfo:
    moon = snapshot.rasi_chart.get("Moon")
    if moon is None:
        return SadeSatiInfo(False, "none", at, at)

    moon_sign_idx = utils.SIGNS.index(moon.sign)
    jd = _jd_from_date(at)
    saturn_lon = _transit_longitude(jd, 6)  # Saturn ID is 6 in pyjhora
    saturn_sign_idx = int(saturn_lon // 30) % 12

    diff = (saturn_sign_idx - moon_sign_idx) % 12
    if diff == 11:
        phase = "first"
    elif diff == 0:
        phase = "second"
    elif diff == 1:
        phase = "third"
    else:
        return SadeSatiInfo(False, "none", at, at)

    # Approximate start/end: Saturn spends ~2.46 years per sign (29.46yr / 12)
    phase_offset = {"first": -1, "second": 0, "third": 1}[phase]
    target_sign_idx = (moon_sign_idx + phase_offset) % 12

    # Find ingress and egress for that sign (search ±4 years)
    start_est = at - timedelta(days=2.5 * 365)
    end_est = at + timedelta(days=2.5 * 365)

    def saturn_in_sign(dt: datetime) -> bool:
        j = _jd_from_date(dt)
        lon = _transit_longitude(j, 6)
        return int(lon // 30) % 12 == target_sign_idx

    # Binary search for ingress
    lo, hi = start_est, at
    for _ in range(30):
        mid = lo + (hi - lo) / 2
        if saturn_in_sign(mid):
            hi = mid
        else:
            lo = mid
    ingress = hi

    # Binary search for egress
    lo, hi = at, end_est
    for _ in range(30):
        mid = lo + (hi - lo) / 2
        if saturn_in_sign(mid):
            lo = mid
        else:
            hi = mid
    egress = lo

    return SadeSatiInfo(is_active=True, phase=phase, start_date=ingress, end_date=egress)


def check_ashtakavarga_vedha(snapshot: ChartSnapshot,
                               planet: str, sign: str) -> bool:
    from .strength import compute_ashtakavarga
    akv = compute_ashtakavarga(snapshot, planet)
    binna = akv.get("binna", {})
    score = binna.get(sign, 0)
    return score < 4


@dataclass
class GocharaVedhaResult:
    blocked_planet: str       # graha whose favourable transit is obstructed
    blocking_planet: str      # graha sitting in the vedha house
    blocked_house: int        # favourable house from the Moon (1-12)
    vedha_house: int          # obstructing house from the Moon (1-12)
    neutralised: bool         # True: result obstructed; False: exempt pair, result stands


def _house_from_moon(moon_sign_idx: int, sign: str) -> int:
    """Whole-sign house (1-12) of ``sign`` counted from the natal Moon's sign."""
    return ((utils.SIGNS.index(sign) - moon_sign_idx) % 12) + 1


def compute_transit_signals(snapshot: ChartSnapshot,
                            transits: dict[str, TransitPlacement]) -> dict[str, dict]:
    """Per-graha transit signals for the seven grahas.

    Returns ``{graha: {"house_from_moon": int, "favourable": bool,
    "bindu_score": int}}``. ``favourable`` is True when the graha occupies one of
    its classical favourable houses from the Moon; ``bindu_score`` is the graha's
    bhinnashtakavarga bindu count in its transited sign (0-8) — the strength of
    the transit. Rahu/Ketu are absent from the result.
    """
    moon = snapshot.rasi_chart.get("Moon")
    if moon is None:
        return {}
    moon_idx = utils.SIGNS.index(moon.sign)

    from .strength import compute_ashtakavarga
    binna = compute_ashtakavarga(snapshot).get("binna", {})

    signals: dict[str, dict] = {}
    for graha in _GOCHARA_GRAHAS:
        tp = transits.get(graha)
        if tp is None:
            continue
        house = _house_from_moon(moon_idx, tp.sign)
        signals[graha] = {
            "house_from_moon": house,
            "favourable": house in _GOCHARA_VEDHA[graha],
            "bindu_score": binna.get(graha, {}).get(tp.sign, 0),
        }
    return signals


def compute_gochara_vedha(snapshot: ChartSnapshot,
                          transits: dict[str, TransitPlacement]) -> list[GocharaVedhaResult]:
    """Classical gochara-vedha vetoes for the seven grahas.

    A graha transiting one of its favourable houses (from the Moon) is obstructed
    when another graha occupies the paired vedha house. Sun-Saturn and
    Moon-Mercury never obstruct each other: those pairings are still returned but
    flagged ``neutralised=False`` so the caller knows the favourable result
    stands. Rahu/Ketu do not participate.
    """
    moon = snapshot.rasi_chart.get("Moon")
    if moon is None:
        return []
    moon_idx = utils.SIGNS.index(moon.sign)

    house_of: dict[str, int] = {}
    for graha in _GOCHARA_GRAHAS:
        tp = transits.get(graha)
        if tp is not None:
            house_of[graha] = _house_from_moon(moon_idx, tp.sign)

    occupants: dict[int, list[str]] = {}
    for graha, house in house_of.items():
        occupants.setdefault(house, []).append(graha)

    results: list[GocharaVedhaResult] = []
    for graha, house in house_of.items():
        vedha_house = _GOCHARA_VEDHA[graha].get(house)
        if vedha_house is None:
            continue  # not in a favourable house — nothing to obstruct
        for blocker in occupants.get(vedha_house, []):
            if blocker == graha:
                continue
            exempt = frozenset({graha, blocker}) in _VEDHA_EXEMPT_PAIRS
            results.append(GocharaVedhaResult(
                blocked_planet=graha,
                blocking_planet=blocker,
                blocked_house=house,
                vedha_house=vedha_house,
                neutralised=not exempt,
            ))
    return results


def compute_house_from_lagna(snapshot: ChartSnapshot,
                             transits: dict[str, TransitPlacement]) -> dict[str, int]:
    """Whole-sign house (1-12) from the natal lagna for every transiting planet
    (all nine, including the nodes — this is plain house position, not gochara)."""
    lagna_idx = utils.SIGNS.index(snapshot.lagna)
    return {
        name: ((utils.SIGNS.index(tp.sign) - lagna_idx) % 12) + 1
        for name, tp in transits.items()
    }


def compute_transit_derived(snapshot: ChartSnapshot,
                            transits: dict[str, TransitPlacement]) -> dict:
    """Top-level gochara flags derived from the natal Moon: chandrashtama
    (transit Moon in the 8th from natal Moon) and dhaiya / Kantaka Sani (transit
    Saturn in the 4th or 8th from natal Moon)."""
    moon = snapshot.rasi_chart.get("Moon")
    if moon is None:
        return {"chandrashtama": False, "dhaiya_active": False, "dhaiya_phase": None}
    moon_idx = utils.SIGNS.index(moon.sign)
    tmoon = transits.get("Moon")
    tsat = transits.get("Saturn")
    chandrashtama = tmoon is not None and _house_from_moon(moon_idx, tmoon.sign) == 8
    dhaiya_phase = None
    if tsat is not None:
        sat_house = _house_from_moon(moon_idx, tsat.sign)
        if sat_house == 4:
            dhaiya_phase = "4th from natal Moon"
        elif sat_house == 8:
            dhaiya_phase = "8th from natal Moon"
    return {
        "chandrashtama": chandrashtama,
        "dhaiya_active": dhaiya_phase is not None,
        "dhaiya_phase": dhaiya_phase,
    }
