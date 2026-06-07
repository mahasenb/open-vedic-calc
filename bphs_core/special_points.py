from dataclasses import dataclass
from .chart import ChartSnapshot
from . import utils


@dataclass
class SpecialPoint:
    name: str
    sign: str
    degrees: float
    description: str


def _sign_and_deg(lon: float) -> tuple[str, float]:
    return utils.longitude_to_sign_and_degree(lon % 360)


def _nth_house_from(house: int, n: int) -> int:
    """The n-th house counted from `house` (inclusive, 1-based, wraps 1..12)."""
    return ((house - 1 + n - 1) % 12) + 1


def _arudha_pada_house(lord_house: int) -> int:
    """Arudha pada house for a bhava, given the bhava lord's house measured from
    that bhava. The pada sits as far from the lord as the lord is from the bhava.

    BPHS/Jaimini exception: if the pada lands in the 1st or 7th from the bhava,
    the 10th house from the pada is taken (1st -> 10th, 7th -> 4th).
    """
    pada = ((2 * lord_house - 1) - 1) % 12 + 1
    if pada in (1, 7):
        pada = _nth_house_from(pada, 10)
    return pada


def get_arudha_lagna(snapshot: ChartSnapshot) -> SpecialPoint:
    """Arudha = 2 × lagna-lord-house − lagna house (counted from lagna)."""
    lagna_sign = snapshot.lagna
    lagna_idx = utils.SIGNS.index(lagna_sign)
    lord = snapshot.lagna_lord
    lord_pd = snapshot.rasi_chart.get(lord)
    if lord_pd is None:
        sign, deg = lagna_sign, 0.0
    else:
        lord_house = lord_pd.house
        arudha_house = _arudha_pada_house(lord_house)
        arudha_sign_idx = (lagna_idx + arudha_house - 1) % 12
        sign = utils.SIGNS[arudha_sign_idx]
        deg = 0.0

    return SpecialPoint(
        name="Arudha Lagna",
        sign=sign,
        degrees=deg,
        description="Public image and material manifestation of the self",
    )


def get_upapada(snapshot: ChartSnapshot) -> SpecialPoint:
    """Upapada = Arudha of the 12th house (from 12th lord)."""
    lagna_idx = utils.SIGNS.index(snapshot.lagna)
    twelfth_sign_idx = (lagna_idx + 11) % 12
    twelfth_sign = utils.SIGNS[twelfth_sign_idx]
    twelfth_lord = utils.get_sign_lord(twelfth_sign)
    lord_pd = snapshot.rasi_chart.get(twelfth_lord)

    if lord_pd is None:
        sign = twelfth_sign
    else:
        lord_house_from_12th = ((lord_pd.house - 12) % 12) + 1
        upapada_house_from_12th = _arudha_pada_house(lord_house_from_12th)
        upapada_sign_idx = (twelfth_sign_idx + upapada_house_from_12th - 1) % 12
        sign = utils.SIGNS[upapada_sign_idx]

    return SpecialPoint(
        name="Upapada Lagna",
        sign=sign,
        degrees=0.0,
        description="Nature of spouse and marriage",
    )


def get_atmakaraka(snapshot: ChartSnapshot) -> str:
    """Planet with highest degrees within its sign (excluding Rahu/Ketu)."""
    candidates = {p: d.degrees
                  for p, d in snapshot.rasi_chart.items()
                  if p not in ("Rahu", "Ketu")}
    if not candidates:
        return "Sun"
    return max(candidates, key=candidates.__getitem__)


# 7-karaka Jaimini scheme: all 7 planets sorted by degree within sign descending.
# Position 1 = AK (soul), position 7 = DK (spouse).
_JAIMINI_KARAKA_SEQUENCE = [
    ("AK",  "Atmakaraka",   "soul and self"),
    ("AmK", "Amatyakaraka", "career and key advisors"),
    ("BK",  "Bhratrukaraka","siblings and courage"),
    ("MK",  "Matrukaraka",  "mother and emotional roots"),
    ("PuK", "Putrakaraka",  "children and creativity"),
    ("GK",  "Gnatikaraka",  "relatives, rivals, and health"),
    ("DK",  "Darakaraka",   "spouse and partnerships"),
]
_JAIMINI_PLANETS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]


def get_jaimini_karakas(snapshot: ChartSnapshot) -> list[dict]:
    """Return all 7 Jaimini karakas sorted by degree descending.

    Each entry: {abbr, name, planet, degree, domain}.
    DK is always the planet with the lowest degree (7th position).
    """
    degrees = {
        p: snapshot.rasi_chart[p].degrees
        for p in _JAIMINI_PLANETS
        if p in snapshot.rasi_chart
    }
    ranked = sorted(degrees, key=degrees.__getitem__, reverse=True)
    result = []
    for i, (abbr, name, domain) in enumerate(_JAIMINI_KARAKA_SEQUENCE):
        planet = ranked[i] if i < len(ranked) else "unknown"
        result.append({
            "abbr": abbr,
            "name": name,
            "planet": planet,
            "degree": round(degrees.get(planet, 0.0), 4),
            "domain": domain,
        })
    return result


def get_karakamsa(snapshot: ChartSnapshot) -> SpecialPoint:
    """Navamsa sign of the Atmakaraka."""
    ak = get_atmakaraka(snapshot)
    ak_navamsa = snapshot.navamsa_chart.get(ak)
    if ak_navamsa is None:
        sign = snapshot.lagna
    else:
        sign = ak_navamsa.sign

    return SpecialPoint(
        name="Karakamsa",
        sign=sign,
        degrees=0.0,
        description=f"Soul purpose — Atmakaraka ({ak}) in Navamsa",
    )
