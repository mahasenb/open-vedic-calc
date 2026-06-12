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


# ---------------------------------------------------------------------------
# Indu Lagna (Chandra Lagna of wealth) — BPHS
# ---------------------------------------------------------------------------

# Planetary kalas used for the Indu Lagna sum (BPHS / classical Jataka).
_INDU_KALAS: dict[str, int] = {
    "Sun": 30, "Moon": 16, "Mars": 6, "Mercury": 8,
    "Jupiter": 10, "Venus": 12, "Saturn": 1,
}


@dataclass
class InduLagna:
    sign: str
    house_from_lagna: int          # whole-sign house of the Indu Lagna from the natal lagna
    occupants: list[str]           # planets tenanting the Indu Lagna sign
    lord: str                      # lord of the Indu Lagna sign
    lord_dignity: str              # that lord's dignity in the rasi chart
    lord_house: int                # that lord's whole-sign house (0 = unknown)


def _ninth_lord_from(snapshot: ChartSnapshot, base_sign: str) -> str:
    """Lord of the 9th sign counted from ``base_sign``."""
    base_idx = utils.SIGNS.index(base_sign)
    ninth_idx = (base_idx + 8) % 12          # 9th sign inclusive
    return utils.get_sign_lord(utils.SIGNS[ninth_idx])


def get_indu_lagna(snapshot: ChartSnapshot) -> InduLagna:
    """Indu Lagna — the classical wealth ascendant.

    Procedure (BPHS): take the lord of the 9th from the Lagna and the lord of the
    9th from the Moon; sum their kalas; divide by 12; the remainder (0 -> 12),
    counted from the MOON's sign, gives the Indu Lagna sign.
    """
    moon_pd = snapshot.rasi_chart.get("Moon")
    # Fail-closed: without the Moon there is no Indu Lagna anchor.
    if moon_pd is None:
        moon_sign = snapshot.lagna
    else:
        moon_sign = moon_pd.sign

    lord_9_lagna = _ninth_lord_from(snapshot, snapshot.lagna)
    lord_9_moon = _ninth_lord_from(snapshot, moon_sign)

    total_kalas = _INDU_KALAS.get(lord_9_lagna, 0) + _INDU_KALAS.get(lord_9_moon, 0)
    remainder = total_kalas % 12
    if remainder == 0:
        remainder = 12

    moon_idx = utils.SIGNS.index(moon_sign)
    indu_idx = (moon_idx + remainder - 1) % 12      # count `remainder` signs from the Moon
    indu_sign = utils.SIGNS[indu_idx]

    lagna_idx = utils.SIGNS.index(snapshot.lagna)
    house_from_lagna = (indu_idx - lagna_idx) % 12 + 1

    occupants = [p for p, d in snapshot.rasi_chart.items() if d.sign == indu_sign]

    lord = utils.get_sign_lord(indu_sign)
    lord_pd = snapshot.rasi_chart.get(lord)
    lord_dignity = lord_pd.dignity if lord_pd else "unknown"
    lord_house = lord_pd.house if lord_pd else 0

    return InduLagna(
        sign=indu_sign,
        house_from_lagna=house_from_lagna,
        occupants=occupants,
        lord=lord,
        lord_dignity=lord_dignity,
        lord_house=lord_house,
    )


# ---------------------------------------------------------------------------
# Beeja Sphuta & Kshetra Sphuta — fertility points (Jataka / Santana)
# ---------------------------------------------------------------------------

@dataclass
class Sphuta:
    name: str
    longitude: float               # sidereal longitude 0-360, 2dp
    sign: str
    navamsa_sign: str
    sign_parity: str               # "odd" | "even"
    navamsa_parity: str            # "odd" | "even"
    strength: str                  # "strong" | "middling" | "weak"
    sign_lord: str
    sign_lord_dignity: str


def _sidereal_longitude(snapshot: ChartSnapshot, planet: str) -> float:
    """Unrounded sidereal longitude (0-360) of a planet from the rasi chart."""
    pd = snapshot.rasi_chart.get(planet)
    if pd is None:
        return 0.0
    if pd.longitude_abs is not None:
        return pd.longitude_abs % 360.0
    # Fallback: reconstruct from sign index + degrees.
    return (utils.SIGNS.index(pd.sign) * 30.0 + pd.degrees) % 360.0


def _navamsa_sign_of_longitude(longitude: float) -> str:
    """D9 (navamsa) sign for an absolute sidereal longitude, via the BPHS rule.

    Each sign's 9 navamsas span 3°20'. The navamsa count from the start of the
    zodiac is continuous; mapping pada index mod 12 to signs Aries..Pisces gives
    the standard navamsa sign (the same pada->sign mapping used by
    utils.nakshatra_pada_lord, since a navamsa pada == a nakshatra pada).
    """
    longitude = longitude % 360.0
    pada_size = 30.0 / 9.0                 # 3°20'
    absolute_pada = int(longitude / pada_size)
    return utils.SIGNS[absolute_pada % 12]


def _parity(sign_idx: int) -> str:
    # Odd signs (Aries=1, Gemini=3, ...) have even zero-based index.
    return "odd" if sign_idx % 2 == 0 else "even"


def _sphuta_strength(favourable_parity: str, sign_parity: str, navamsa_parity: str) -> str:
    """both favourable -> strong; one -> middling; none -> weak."""
    hits = (sign_parity == favourable_parity) + (navamsa_parity == favourable_parity)
    if hits == 2:
        return "strong"
    if hits == 1:
        return "middling"
    return "weak"


def _build_sphuta(snapshot: ChartSnapshot, name: str, longitude: float,
                  favourable_parity: str) -> Sphuta:
    longitude = longitude % 360.0
    sign_idx = int(longitude // 30)
    sign = utils.SIGNS[sign_idx]
    navamsa_sign = _navamsa_sign_of_longitude(longitude)
    navamsa_idx = utils.SIGNS.index(navamsa_sign)

    sign_parity = _parity(sign_idx)
    navamsa_parity = _parity(navamsa_idx)
    strength = _sphuta_strength(favourable_parity, sign_parity, navamsa_parity)

    sign_lord = utils.get_sign_lord(sign)
    lord_pd = snapshot.rasi_chart.get(sign_lord)
    sign_lord_dignity = lord_pd.dignity if lord_pd else "unknown"

    return Sphuta(
        name=name,
        longitude=round(longitude, 2),
        sign=sign,
        navamsa_sign=navamsa_sign,
        sign_parity=sign_parity,
        navamsa_parity=navamsa_parity,
        strength=strength,
        sign_lord=sign_lord,
        sign_lord_dignity=sign_lord_dignity,
    )


def get_beeja_sphuta(snapshot: ChartSnapshot) -> Sphuta:
    """Beeja Sphuta (male/seed fertility factor) = Sun + Venus + Jupiter (sidereal).

    Favourable in an ODD sign and ODD navamsa (both odd -> strong).
    """
    lon = (_sidereal_longitude(snapshot, "Sun")
           + _sidereal_longitude(snapshot, "Venus")
           + _sidereal_longitude(snapshot, "Jupiter")) % 360.0
    return _build_sphuta(snapshot, "Beeja Sphuta", lon, favourable_parity="odd")


def get_kshetra_sphuta(snapshot: ChartSnapshot) -> Sphuta:
    """Kshetra Sphuta (female/field fertility factor) = Moon + Mars + Jupiter (sidereal).

    Favourable in an EVEN sign and EVEN navamsa (both even -> strong).
    """
    lon = (_sidereal_longitude(snapshot, "Moon")
           + _sidereal_longitude(snapshot, "Mars")
           + _sidereal_longitude(snapshot, "Jupiter")) % 360.0
    return _build_sphuta(snapshot, "Kshetra Sphuta", lon, favourable_parity="even")
