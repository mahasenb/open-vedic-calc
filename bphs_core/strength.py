from dataclasses import dataclass
from .chart import ChartSnapshot, PlanetData
from . import utils

SHADBALA_MINIMUMS: dict[str, float] = {
    "Sun": 5.0, "Moon": 6.0, "Mars": 5.0,
    "Mercury": 7.0, "Jupiter": 6.5, "Venus": 5.5, "Saturn": 5.0,
}

# Directional strength (dig bala) peak houses
_DIG_BALA_PEAK: dict[str, int] = {
    "Sun": 10, "Mars": 10, "Jupiter": 1, "Mercury": 1,
    "Moon": 4, "Venus": 4, "Saturn": 7,
}

# Natural strength (naisargika bala) order — fixed values per BPHS
_NAISARGIKA: dict[str, float] = {
    "Sun": 60.0, "Moon": 51.43, "Venus": 42.86, "Jupiter": 34.29,
    "Mercury": 25.71, "Mars": 17.14, "Saturn": 8.57,
}


@dataclass
class ShadbalaResult:
    planet: str
    sthana_bala: float
    dig_bala: float
    kaala_bala: float
    cheshta_bala: float
    naisargika_bala: float
    drik_bala: float
    total_bala: float
    minimum_bala: float
    is_below_minimum: bool


@dataclass
class BhavabalaResult:
    house_number: int
    bala_total: float
    bhava_adhipathi_bala: float
    bhava_drik: float
    rank: str


def _sthana_bala(pd: PlanetData, planet: str) -> float:
    dignity = pd.dignity
    if dignity == "exalted":
        return 60.0
    if dignity == "moolatrikona":
        return 45.0
    if dignity == "own sign":
        return 30.0
    if dignity == "friendly":
        return 15.0
    if dignity == "neutral":
        return 7.5
    if dignity == "enemy":
        return 3.75
    if dignity == "debilitated":
        return 0.0
    return 7.5


def _dig_bala(pd: PlanetData, planet: str) -> float:
    peak = _DIG_BALA_PEAK.get(planet)
    if peak is None:
        return 0.0
    diff = abs(pd.house - peak)
    if diff > 6:
        diff = 12 - diff
    return max(0.0, 60.0 - diff * 10.0)


def _kaala_bala(snapshot: ChartSnapshot, planet: str) -> float:
    pd = snapshot.rasi_chart.get(planet)
    if pd is None:
        return 0.0
    sun = snapshot.rasi_chart.get("Sun")
    is_day = sun and sun.house in range(7, 13)
    day_planets = {"Sun", "Jupiter", "Venus", "Saturn"}
    night_planets = {"Moon", "Mars", "Mercury"}
    if is_day and planet in day_planets:
        return 30.0
    if not is_day and planet in night_planets:
        return 30.0
    return 15.0


def _cheshta_bala(pd: PlanetData, planet: str) -> float:
    if planet in ("Sun", "Moon"):
        return 0.0
    return 30.0 if pd.is_retrograde else 15.0


def _drik_bala(snapshot: ChartSnapshot, planet: str) -> float:
    pd = snapshot.rasi_chart.get(planet)
    if pd is None:
        return 0.0
    score = 0.0
    for other_name, other_pd in snapshot.rasi_chart.items():
        if other_name == planet:
            continue
        if planet in other_pd.aspects:
            if other_name in ("Jupiter", "Venus", "Mercury"):
                score += 15.0
            elif other_name in ("Sun", "Moon", "Mars", "Saturn"):
                score -= 15.0
    return max(0.0, score)


def compute_shadbala(snapshot: ChartSnapshot, planet: str) -> ShadbalaResult:
    pd = snapshot.rasi_chart.get(planet)
    if pd is None:
        raise ValueError(f"Planet {planet} not found in chart")

    sthana = _sthana_bala(pd, planet)
    dig = _dig_bala(pd, planet)
    kaala = _kaala_bala(snapshot, planet)
    cheshta = _cheshta_bala(pd, planet)
    naisargika = _NAISARGIKA.get(planet, 0.0)
    drik = _drik_bala(snapshot, planet)
    total = sthana + dig + kaala + cheshta + naisargika + drik
    minimum = SHADBALA_MINIMUMS.get(planet, 5.0)

    # Convert from raw units to rupas (divide by 60 per BPHS convention)
    total_rupas = round(total / 60.0, 3)
    minimum_rupas = minimum

    return ShadbalaResult(
        planet=planet,
        sthana_bala=round(sthana / 60, 3),
        dig_bala=round(dig / 60, 3),
        kaala_bala=round(kaala / 60, 3),
        cheshta_bala=round(cheshta / 60, 3),
        naisargika_bala=round(naisargika / 60, 3),
        drik_bala=round(drik / 60, 3),
        total_bala=total_rupas,
        minimum_bala=minimum_rupas,
        is_below_minimum=total_rupas < minimum_rupas,
    )


def _bhava_adhipathi_bala(snapshot: ChartSnapshot, house: int) -> float:
    sign = utils.SIGNS[(int(snapshot.house_cusps[house - 1] // 30)) % 12]
    lord = utils.get_sign_lord(sign)
    lord_pd = snapshot.rasi_chart.get(lord)
    if lord_pd is None:
        return 0.0
    result = compute_shadbala(snapshot, lord)
    return result.total_bala


def _bhava_drik_bala(snapshot: ChartSnapshot, house: int) -> float:
    score = 0.0
    for name, pd in snapshot.rasi_chart.items():
        if pd.house == house:
            if name in ("Jupiter", "Venus", "Mercury"):
                score += 10.0
            elif name in ("Mars", "Saturn", "Sun"):
                score -= 10.0
    return score


def compute_bhavabala(snapshot: ChartSnapshot, house: int) -> BhavabalaResult:
    adhipathi = _bhava_adhipathi_bala(snapshot, house)
    drik = _bhava_drik_bala(snapshot, house)
    total = round(adhipathi + max(0.0, drik), 3)
    return BhavabalaResult(
        house_number=house,
        bala_total=total,
        bhava_adhipathi_bala=round(adhipathi, 3),
        bhava_drik=round(drik, 3),
        rank="",  # rank assigned after all 12 computed
    )


def compute_all_bhavabala(snapshot: ChartSnapshot) -> list[BhavabalaResult]:
    results = [compute_bhavabala(snapshot, h) for h in range(1, 13)]
    totals = [r.bala_total for r in results]
    # Quartile boundaries derived from length so the 4/4/4 split scales if
    # the house count ever differs from 12 (n=12 -> q1=index 3, q3=index 8).
    n = len(totals)
    ordered = sorted(totals)
    q1 = ordered[n // 4]            # bottom quartile boundary (n=12 -> index 3)
    q3 = ordered[n - n // 4 - 1]    # top quartile boundary  (n=12 -> index 8)
    for r in results:
        if r.bala_total >= q3:
            r.rank = "strong"
        elif r.bala_total <= q1:
            r.rank = "weak"
        else:
            r.rank = "average"
    return results


# Bhinnashtakavarga benefic places per BPHS chapter 66 (Parashara).
# For each planet's own ashtakavarga, every reference point (the 7 planets plus
# the Lagna) contributes a bindu to specific houses counted *from that reference
# point*. A planet's BAV is the sum of bindus from all 8 reference points (0-8
# per sign). Per-planet BAV totals: Sun 48, Moon 49, Mars 39, Mercury 54,
# Jupiter 56, Venus 52, Saturn 39; SAV (sum of the 7) = 337.
_ASHTAKAVARGA_BENEFICS: dict[str, dict[str, list[int]]] = {
    "Sun": {
        "Sun":     [1, 2, 4, 7, 8, 9, 10, 11],
        "Moon":    [3, 6, 10, 11],
        "Mars":    [1, 2, 4, 7, 8, 9, 10, 11],
        "Mercury": [3, 5, 6, 9, 10, 11, 12],
        "Jupiter": [5, 6, 9, 11],
        "Venus":   [6, 7, 12],
        "Saturn":  [1, 2, 4, 7, 8, 9, 10, 11],
        "Lagna":   [3, 4, 6, 10, 11, 12],
    },
    "Moon": {
        "Sun":     [3, 6, 7, 8, 10, 11],
        "Moon":    [1, 3, 6, 7, 10, 11],
        "Mars":    [2, 3, 5, 6, 9, 10, 11],
        "Mercury": [1, 3, 4, 5, 7, 8, 10, 11],
        "Jupiter": [1, 4, 7, 8, 10, 11, 12],
        "Venus":   [3, 4, 5, 7, 9, 10, 11],
        "Saturn":  [3, 5, 6, 11],
        "Lagna":   [3, 6, 10, 11],
    },
    "Mars": {
        "Sun":     [3, 5, 6, 10, 11],
        "Moon":    [3, 6, 11],
        "Mars":    [1, 2, 4, 7, 8, 10, 11],
        "Mercury": [3, 5, 6, 11],
        "Jupiter": [6, 10, 11, 12],
        "Venus":   [6, 8, 11, 12],
        "Saturn":  [1, 4, 7, 8, 9, 10, 11],
        "Lagna":   [1, 3, 6, 10, 11],
    },
    "Mercury": {
        "Sun":     [5, 6, 9, 11, 12],
        "Moon":    [2, 4, 6, 8, 10, 11],
        "Mars":    [1, 2, 4, 7, 8, 9, 10, 11],
        "Mercury": [1, 3, 5, 6, 9, 10, 11, 12],
        "Jupiter": [6, 8, 11, 12],
        "Venus":   [1, 2, 3, 4, 5, 8, 9, 11],
        "Saturn":  [1, 2, 4, 7, 8, 9, 10, 11],
        "Lagna":   [1, 2, 4, 6, 8, 10, 11],
    },
    "Jupiter": {
        "Sun":     [1, 2, 3, 4, 7, 8, 9, 10, 11],
        "Moon":    [2, 5, 7, 9, 11],
        "Mars":    [1, 2, 4, 7, 8, 10, 11],
        "Mercury": [1, 2, 4, 5, 6, 9, 10, 11],
        "Jupiter": [1, 2, 3, 4, 7, 8, 10, 11],
        "Venus":   [2, 5, 6, 9, 10, 11],
        "Saturn":  [3, 5, 6, 12],
        "Lagna":   [1, 2, 4, 5, 6, 7, 9, 10, 11],
    },
    "Venus": {
        "Sun":     [8, 11, 12],
        "Moon":    [1, 2, 3, 4, 5, 8, 9, 11, 12],
        "Mars":    [3, 5, 6, 9, 11, 12],
        "Mercury": [3, 5, 6, 9, 11],
        "Jupiter": [5, 8, 9, 10, 11],
        "Venus":   [1, 2, 3, 4, 5, 8, 9, 10, 11],
        "Saturn":  [3, 4, 5, 8, 9, 10, 11],
        "Lagna":   [1, 2, 3, 4, 5, 8, 9, 11],
    },
    "Saturn": {
        "Sun":     [1, 2, 4, 7, 8, 10, 11],
        "Moon":    [3, 6, 11],
        "Mars":    [3, 5, 6, 10, 11, 12],
        "Mercury": [6, 8, 9, 10, 11, 12],
        "Jupiter": [5, 6, 11, 12],
        "Venus":   [6, 11, 12],
        "Saturn":  [3, 5, 6, 11],
        "Lagna":   [1, 3, 4, 6, 10, 11],
    },
}

_ASHTAKAVARGA_PLANETS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]


def compute_ashtakavarga(snapshot: ChartSnapshot,
                         planet: str | None = None) -> dict:
    """Bhinnashtakavarga (per-planet) and Sarvashtakavarga bindus per sign.

    For each of the 7 planets, bindus are accumulated from all 8 reference points
    (the 7 planets + the Lagna) per the BPHS chapter 66 benefic-place tables.
    The Lagna is a reference point *inside* each planet's varga, never a separate
    binna column, so the SAV (samudaya) is the sum of the 7 planetary BAVs.

    Returns {"binna": {planet: {sign: bindus}}, "samudaya": {sign: bindus}}, or
    when ``planet`` is given, {"binna": {sign: bindus}, "samudaya": {sign: bindus}}.
    """
    signs = utils.SIGNS

    ref_idx: dict[str, int] = {
        name: utils.SIGNS.index(pd.sign)
        for name, pd in snapshot.rasi_chart.items()
    }
    ref_idx["Lagna"] = utils.SIGNS.index(snapshot.lagna)

    binna: dict[str, dict[str, int]] = {}
    samudaya = [0] * 12

    for p in _ASHTAKAVARGA_PLANETS:
        scores = [0] * 12
        for ref, houses in _ASHTAKAVARGA_BENEFICS[p].items():
            base = ref_idx.get(ref)
            if base is None:
                continue
            for house in houses:
                scores[(base + house - 1) % 12] += 1
        binna[p] = {signs[i]: scores[i] for i in range(12)}
        for i in range(12):
            samudaya[i] += scores[i]

    sav = {signs[i]: samudaya[i] for i in range(12)}

    if planet is not None:
        return {"binna": binna.get(planet, {}), "samudaya": sav}
    return {"binna": binna, "samudaya": sav}
