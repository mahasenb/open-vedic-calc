from dataclasses import dataclass, field
from .chart import ChartSnapshot
from . import utils

DUSTHANA = {6, 8, 12}
KENDRA = {1, 4, 7, 10}
TRIKONA = {1, 5, 9}
UPACHAYA = {3, 6, 10, 11}


@dataclass
class Yoga:
    name: str
    description: str
    planets_involved: list[str]
    houses_involved: list[int]
    strength: str
    is_viparita_raja: bool = False
    activating_lords: list[str] = field(default_factory=list)


def _house_lord(snapshot: ChartSnapshot, house: int) -> str | None:
    sign = utils.SIGNS[int(snapshot.house_cusps[house - 1] // 30) % 12]
    return utils.get_sign_lord(sign)


def _planet_house(snapshot: ChartSnapshot, planet: str) -> int:
    pd = snapshot.rasi_chart.get(planet)
    return pd.house if pd else 0


def _compute_yoga_strength(snapshot: ChartSnapshot, planets: list[str]) -> str:
    dignities = []
    for p in planets:
        pd = snapshot.rasi_chart.get(p)
        if pd:
            dignities.append(pd.dignity)
    if not dignities:
        return "moderate"
    if any(d in ("debilitated", "enemy") for d in dignities):
        return "mild"
    if all(d in ("exalted", "own sign", "moolatrikona") for d in dignities):
        return "strong"
    return "moderate"


def detect_viparita_raja_yoga(snapshot: ChartSnapshot) -> list[Yoga]:
    yogas: list[Yoga] = []
    configs = [
        ("Harsha Yoga", 6, "6th lord in 6/8/12 — disease and enemies vanquished"),
        ("Sarala Yoga", 8, "8th lord in 6/8/12 — longevity and hidden gains"),
        ("Vimala Yoga", 12, "12th lord in 6/8/12 — liberation from expenditure"),
    ]
    for name, dusthana_house, desc in configs:
        lord = _house_lord(snapshot, dusthana_house)
        if lord is None:
            continue
        lord_house = _planet_house(snapshot, lord)
        if lord_house in DUSTHANA:
            yogas.append(Yoga(
                name=name, description=desc,
                planets_involved=[lord],
                houses_involved=[dusthana_house, lord_house],
                strength=_compute_yoga_strength(snapshot, [lord]),
                is_viparita_raja=True,
                activating_lords=[lord],
            ))
    return yogas


def detect_panchamahapurusha(snapshot: ChartSnapshot) -> list[Yoga]:
    configs = [
        ("Ruchaka Yoga", "Mars", ["Aries", "Scorpio", "Capricorn"], KENDRA,
         "Mars in own/exalt in kendra — fierce, commanding"),
        ("Bhadra Yoga", "Mercury", ["Gemini", "Virgo"], KENDRA,
         "Mercury in own/exalt in kendra — intelligent, eloquent"),
        ("Hamsa Yoga", "Jupiter", ["Sagittarius", "Pisces", "Cancer"], KENDRA,
         "Jupiter in own/exalt in kendra — wise, righteous"),
        ("Malavya Yoga", "Venus", ["Taurus", "Libra", "Pisces"], KENDRA,
         "Venus in own/exalt in kendra — artistic, prosperous"),
        ("Sasa Yoga", "Saturn", ["Capricorn", "Aquarius", "Libra"], KENDRA,
         "Saturn in own/exalt in kendra — disciplined, powerful"),
    ]
    yogas: list[Yoga] = []
    for name, planet, signs, houses, desc in configs:
        pd = snapshot.rasi_chart.get(planet)
        if pd and pd.sign in signs and pd.house in houses:
            yogas.append(Yoga(
                name=name, description=desc,
                planets_involved=[planet],
                houses_involved=[pd.house],
                strength=_compute_yoga_strength(snapshot, [planet]),
                activating_lords=[planet],
            ))
    return yogas


def detect_raja_yogas(snapshot: ChartSnapshot) -> list[Yoga]:
    yogas: list[Yoga] = []
    kendra_lords = [_house_lord(snapshot, h) for h in KENDRA if h != 1]
    trikona_lords = [_house_lord(snapshot, h) for h in TRIKONA if h != 1]
    for kl in kendra_lords:
        for tl in trikona_lords:
            if kl and tl and kl == tl:
                yogas.append(Yoga(
                    name="Raja Yoga",
                    description=f"{kl} lords both kendra and trikona",
                    planets_involved=[kl], houses_involved=[],
                    strength=_compute_yoga_strength(snapshot, [kl]),
                    activating_lords=[kl],
                ))
            elif kl and tl:
                kl_pd = snapshot.rasi_chart.get(kl)
                tl_pd = snapshot.rasi_chart.get(tl)
                if kl_pd and tl_pd and kl_pd.house == tl_pd.house:
                    yogas.append(Yoga(
                        name="Raja Yoga",
                        description=f"{kl} (kendra lord) conjunct {tl} (trikona lord)",
                        planets_involved=[kl, tl],
                        houses_involved=[kl_pd.house],
                        strength=_compute_yoga_strength(snapshot, [kl, tl]),
                        activating_lords=[kl, tl],
                    ))
    return yogas


def detect_dhana_yogas(snapshot: ChartSnapshot) -> list[Yoga]:
    yogas: list[Yoga] = []
    lord2 = _house_lord(snapshot, 2)
    lord11 = _house_lord(snapshot, 11)
    lord5 = _house_lord(snapshot, 5)
    lord9 = _house_lord(snapshot, 9)
    for a, b, houses in [(lord2, lord11, [2, 11]), (lord5, lord9, [5, 9])]:
        if not a or not b:
            continue
        a_pd = snapshot.rasi_chart.get(a)
        b_pd = snapshot.rasi_chart.get(b)
        if a_pd and b_pd and a_pd.house == b_pd.house:
            yogas.append(Yoga(
                name="Dhana Yoga",
                description=f"{a} and {b} conjunct — wealth accumulation",
                planets_involved=[a, b],
                houses_involved=houses,
                strength=_compute_yoga_strength(snapshot, [a, b]),
                activating_lords=[a, b],
            ))
    return yogas


# Sign ownership for Parivartana detection
_SIGN_LORD: dict[str, str] = {
    "Aries": "Mars",    "Scorpio": "Mars",
    "Taurus": "Venus",  "Libra": "Venus",
    "Gemini": "Mercury","Virgo": "Mercury",
    "Cancer": "Moon",
    "Leo": "Sun",
    "Sagittarius": "Jupiter", "Pisces": "Jupiter",
    "Capricorn": "Saturn",    "Aquarius": "Saturn",
}
_KARAKA_PLANETS = {"Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"}


def detect_parivartana_yoga(snapshot: ChartSnapshot) -> list[Yoga]:
    """Mutual sign exchange (Parivartana) between any two of the 7 planets.

    Planet A is in a sign owned by planet B, and B is in a sign owned by A.
    """
    yogas: list[Yoga] = []
    planets = [p for p in _KARAKA_PLANETS if p in snapshot.rasi_chart]
    checked: set[frozenset] = set()

    for p_a in planets:
        sign_a = snapshot.rasi_chart[p_a].sign
        lord_of_a = _SIGN_LORD.get(sign_a)
        if lord_of_a is None or lord_of_a == p_a:
            continue
        if lord_of_a not in snapshot.rasi_chart:
            continue
        sign_b = snapshot.rasi_chart[lord_of_a].sign
        lord_of_b = _SIGN_LORD.get(sign_b)
        if lord_of_b != p_a:
            continue
        pair = frozenset({p_a, lord_of_a})
        if pair in checked:
            continue
        checked.add(pair)
        house_a = snapshot.rasi_chart[p_a].house
        house_b = snapshot.rasi_chart[lord_of_a].house
        yogas.append(Yoga(
            name="Parivartana Yoga",
            description=(
                f"{p_a} in {sign_a} (owned by {lord_of_a}) exchanges signs with "
                f"{lord_of_a} in {sign_b} (owned by {p_a}) — "
                f"houses {house_a} and {house_b} deeply interlinked"
            ),
            planets_involved=sorted([p_a, lord_of_a]),
            houses_involved=sorted([house_a, house_b]),
            strength=_compute_yoga_strength(snapshot, sorted([p_a, lord_of_a])),
            activating_lords=sorted([p_a, lord_of_a]),
        ))
    return yogas


def detect_all_yogas(snapshot: ChartSnapshot) -> list[Yoga]:
    yogas: list[Yoga] = []
    yogas.extend(detect_viparita_raja_yoga(snapshot))
    yogas.extend(detect_panchamahapurusha(snapshot))
    yogas.extend(detect_raja_yogas(snapshot))
    yogas.extend(detect_dhana_yogas(snapshot))
    yogas.extend(detect_parivartana_yoga(snapshot))
    return yogas


def get_yoga_karaka_planet(snapshot: ChartSnapshot) -> str:
    lagna = snapshot.lagna
    # Yoga karakas by lagna sign (classic BPHS list)
    YOGA_KARAKAS: dict[str, str] = {
        "Taurus": "Saturn", "Libra": "Saturn",
        "Cancer": "Mars", "Leo": "Mars",
        "Capricorn": "Venus", "Aquarius": "Venus",
    }
    return YOGA_KARAKAS.get(lagna, "")
