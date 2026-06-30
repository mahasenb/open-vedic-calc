import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional
import swisseph as swe
from jhora.panchanga import drik
from jhora.horoscope.chart import charts
from . import utils  # sets ephemeris path and Lahiri mode on import

logger = logging.getLogger(__name__)


@dataclass
class PersonalData:
    name: str
    birth_date: datetime
    birth_time: time
    birth_place: str
    latitude: float
    longitude: float
    timezone_offset_hours: float


@dataclass
class PlanetData:
    planet: str
    sign: str
    degrees: float
    nakshatra: str
    dignity: str
    house: int                       # primary BPHS whole-sign house (from lagna sign)
    conjunctions: list[str]
    aspects: list[str]
    is_retrograde: bool
    is_combust: bool = False
    combust_proximity_degrees: Optional[float] = None
    chalit_house: Optional[int] = None   # secondary Bhava-Chalit (Placidus cusp) house
    longitude_abs: Optional[float] = None  # unrounded sidereal longitude (0-360), rasi chart only


@dataclass
class ChartSnapshot:
    person: PersonalData
    rasi_chart: dict[str, PlanetData]
    hora_chart: dict[str, PlanetData]          # D2
    drekkana_chart: dict[str, PlanetData]      # D3
    navamsa_chart: dict[str, PlanetData]       # D9
    decamsa_chart: dict[str, PlanetData]       # D10
    dwadasamsa_chart: dict[str, PlanetData]    # D12
    chaturvimsa_chart: dict[str, PlanetData]   # D24
    trimshamsa_chart: dict[str, PlanetData]    # D30
    saptamsa_chart: dict[str, PlanetData]      # D7
    shashtyamsa_chart: dict[str, PlanetData]   # D60
    lagna: str
    lagna_lord: str
    ayanamsa_value: float
    # D16 (Kalamsa) — required by Vimshopaka Dashavarga. Defaulted (added after the
    # other vargas) so existing ChartSnapshot constructors that predate D16 stay
    # valid; the real Chart builder always supplies it by keyword.
    shodasamsa_chart: dict[str, PlanetData] = field(default_factory=dict)   # D16
    house_cusps: list[float] = field(default_factory=list)   # whole-sign cusps (sign starts from lagna)
    chalit_cusps: list[float] = field(default_factory=list)   # sidereal Placidus cusps (Bhava-Chalit)
    jd: float = 0.0
    # 'placidus' normally; 'equatorial' when Placidus failed and the equatorial
    # fallback (swe.houses(..., b"E")) was used instead. Consumers that need cusp-
    # based secondary houses should check this field — the chalit_cusps are only
    # geometrically meaningful under the system that produced them. Defaulted so
    # synthetic test ChartSnapshot constructors (which predated this field) stay valid.
    house_system: str = "placidus"


# Aspects each planet casts (house offsets, 1-based from its own house)
_ASPECTS = {
    "Sun": [7], "Moon": [7], "Mercury": [7], "Venus": [7],
    "Mars": [4, 7, 8], "Jupiter": [5, 7, 9], "Saturn": [3, 7, 10],
    "Rahu": [5, 7, 9], "Ketu": [5, 7, 9],
}


def _jd_from_person(p: PersonalData) -> float:
    # pyjhora's chart/drik functions (charts.rasi_chart, drik.ascendant,
    # drik.dhasavarga, drik.planets_in_retrograde) expect a JD built from LOCAL
    # clock time and subtract the place timezone themselves (JD_UTC = JD - tz/24,
    # documented on drik.sidereal_longitude). Return the LOCAL JD — do NOT pre-
    # subtract the timezone, or every planet longitude is computed tz hours early
    # (double subtraction), shifting the Moon's nakshatra pada and corrupting the
    # Vimshottari dasha balance. swisseph calls that need true UT derive it via
    # jd - tz/24 at the call site (see _compute).
    naive = datetime.combine(p.birth_date, p.birth_time)
    local_hour = naive.hour + naive.minute / 60 + naive.second / 3600
    return swe.julday(naive.year, naive.month, naive.day, local_hour)


def _compute_house(lon: float, house_cusps: list[float]) -> int:
    for i in range(11, -1, -1):
        if _lon_gte(lon, house_cusps[i]):
            return i + 1
    return 1


def _lon_gte(lon: float, cusp: float) -> bool:
    return ((lon - cusp) % 360) < 180


def _find_conjunctions(planet: str, house: int,
                        all_planets: dict[str, "PlanetData"]) -> list[str]:
    return [p for p, d in all_planets.items()
            if p != planet and d.house == house]


def _find_aspects(planet: str, house: int,
                  all_planets: dict[str, "PlanetData"]) -> list[str]:
    aspected_houses = {((house - 1 + offset - 1) % 12) + 1
                       for offset in _ASPECTS.get(planet, [])}
    return [p for p, d in all_planets.items()
            if p != planet and d.house in aspected_houses]


# Combustion (astangata) orbs in degrees from the Sun (BPHS). Mercury and Venus
# take a tighter orb when retrograde. Sun (source) and the shadow planets
# Rahu/Ketu are never combust.
_COMBUSTION_ORB = {
    "Moon": 12.0, "Mars": 17.0, "Mercury": 14.0,
    "Jupiter": 11.0, "Venus": 10.0, "Saturn": 15.0,
}
_COMBUSTION_ORB_RETRO = {"Mercury": 12.0, "Venus": 8.0}


def _apply_combustion(rasi: dict[str, "PlanetData"], longitudes: dict[str, float]) -> None:
    """Flag planets within the Sun's combustion orb. Mutates PlanetData in place."""
    sun_lon = longitudes.get("Sun")
    if sun_lon is None:
        return
    for name, pd in rasi.items():
        orb = _COMBUSTION_ORB.get(name)
        if orb is None:
            continue
        if pd.is_retrograde and name in _COMBUSTION_ORB_RETRO:
            orb = _COMBUSTION_ORB_RETRO[name]
        diff = abs(longitudes[name] - sun_lon) % 360.0
        sep = min(diff, 360.0 - diff)
        if sep <= orb:
            pd.is_combust = True
            pd.combust_proximity_degrees = round(sep, 4)


def _build_varga_chart(varga_positions, retro_planets) -> dict[str, PlanetData]:
    # varga_positions[0] is the ascendant's position in this varga; the varga
    # lagna sign anchors house counting within the divisional chart. Without
    # this, house is meaningless (it was previously hardcoded to 1).
    varga_lagna_sign_idx = varga_positions[0][1][0]
    chart: dict[str, PlanetData] = {}
    for pid, (sign_idx, deg) in varga_positions[1:]:
        name = utils.PLANETS[pid]
        sign = utils.SIGNS[sign_idx]
        nakshatra = utils.longitude_to_nakshatra(sign_idx * 30 + deg)
        dignity = utils.get_planet_dignity(name, sign)
        is_retro = pid in retro_planets
        house = (sign_idx - varga_lagna_sign_idx) % 12 + 1

        chart[name] = PlanetData(
            planet=name, sign=sign, degrees=round(deg, 4),
            nakshatra=nakshatra, dignity=dignity, house=house,
            conjunctions=[], aspects=[], is_retrograde=is_retro,
        )
    return chart


class Chart:
    def __init__(self, person: PersonalData):
        self.person = person
        self._snapshot: Optional[ChartSnapshot] = None
        self._compute()

    def _compute(self):
        drik.set_ayanamsa_mode('LAHIRI')
        jd = _jd_from_person(self.person)  # LOCAL JD — pyjhora subtracts tz itself
        jd_utc = jd - self.person.timezone_offset_hours / 24.0  # true UT for swisseph
        place = utils.make_place(self.person.name, self.person.latitude, self.person.longitude, self.person.timezone_offset_hours)
        ayanamsa = drik.get_ayanamsa_value(jd)

        # Planetary positions come from pyjhora, which expects a LOCAL jd and
        # subtracts the place timezone internally (JD_UTC = JD - tz).
        rasi_positions = charts.rasi_chart(jd, place)
        retro_planets = drik.planets_in_retrograde(jd, place)

        # Ascendant/lagna is computed directly from swisseph's ascmc[0]. pyjhora's
        # rasi_chart()[0] subtracts the timezone a second time internally, so its
        # lagna lands tz hours off; swisseph.houses takes true UT (jd_utc) and
        # ascmc[0] is the correct ascendant, independent of the house system.
        # (Same method as lagna_shuddhi.py.)
        _house_system_used = "placidus"
        try:
            cusps, ascmc = swe.houses(jd_utc, self.person.latitude, self.person.longitude, b"P")
        except Exception:
            logger.warning(
                "placidus_fallback_equatorial lat=%s lon=%s",
                self.person.latitude, self.person.longitude, exc_info=True,
            )
            cusps, ascmc = swe.houses(jd_utc, self.person.latitude, self.person.longitude, b"E")
            _house_system_used = "equatorial"

        sid_asc = (ascmc[0] - ayanamsa) % 360
        lagna_sign_index = int(sid_asc // 30)
        lagna_sign = utils.SIGNS[lagna_sign_index]

        # Replace pyjhora's tz-broken ascendant (index 0) with the correct one so
        # every divisional chart derives its varga-lagna from the true ascendant.
        rasi_positions[0] = (rasi_positions[0][0], (lagna_sign_index, sid_asc % 30))

        # Primary houses are BPHS whole-sign: each sign is one house counted from
        # the lagna sign. house_cusps therefore holds the sidereal start-degree of
        # each whole-sign house (consumed as house->sign by yogas.py / strength.py).
        whole_sign_cusps = [(((lagna_sign_index + i) % 12) * 30.0) for i in range(12)]

        # Secondary Bhava-Chalit houses use the Placidus cusps (sidereal). Kept as
        # supplementary cusp-based data; never the primary interpretation house.
        chalit_cusps = [((c - ayanamsa) % 360) for c in cusps]

        # Build Rasi Chart with conjunctions and aspects
        rasi = {}
        longitudes: dict[str, float] = {}
        for pid, (sign_idx, deg) in rasi_positions[1:]:
            name = utils.PLANETS[pid]
            sign = utils.SIGNS[sign_idx]
            nakshatra = utils.longitude_to_nakshatra(sign_idx * 30 + deg)
            dignity = utils.get_planet_dignity(name, sign)
            is_retro = pid in retro_planets
            final_lon = sign_idx * 30.0 + deg
            house = (sign_idx - lagna_sign_index) % 12 + 1
            chalit_house = _compute_house(final_lon, chalit_cusps)
            longitudes[name] = final_lon

            rasi[name] = PlanetData(
                planet=name, sign=sign, degrees=round(deg, 4),
                nakshatra=nakshatra, dignity=dignity, house=house,
                conjunctions=[], aspects=[], is_retrograde=is_retro,
                chalit_house=chalit_house,
                longitude_abs=final_lon,
            )

        for name, pd in rasi.items():
            pd.conjunctions = _find_conjunctions(name, pd.house, rasi)
            pd.aspects = _find_aspects(name, pd.house, rasi)

        _apply_combustion(rasi, longitudes)

        # Build other divisional charts using standardized vargas
        self._snapshot = ChartSnapshot(
            person=self.person,
            rasi_chart=rasi,
            hora_chart=_build_varga_chart(charts.hora_chart(rasi_positions), retro_planets),
            drekkana_chart=_build_varga_chart(charts.drekkana_chart(rasi_positions), retro_planets),
            navamsa_chart=_build_varga_chart(charts.navamsa_chart(rasi_positions), retro_planets),
            decamsa_chart=_build_varga_chart(charts.dasamsa_chart(rasi_positions), retro_planets),
            dwadasamsa_chart=_build_varga_chart(charts.dwadasamsa_chart(rasi_positions), retro_planets),
            shodasamsa_chart=_build_varga_chart(charts.shodasamsa_chart(rasi_positions), retro_planets),
            chaturvimsa_chart=_build_varga_chart(charts.chaturvimsamsa_chart(rasi_positions), retro_planets),
            trimshamsa_chart=_build_varga_chart(charts.trimsamsa_chart(rasi_positions), retro_planets),
            saptamsa_chart=_build_varga_chart(charts.saptamsa_chart(rasi_positions), retro_planets),
            shashtyamsa_chart=_build_varga_chart(charts.shashtyamsa_chart(rasi_positions), retro_planets),
            lagna=lagna_sign,
            lagna_lord=utils.get_sign_lord(lagna_sign),
            ayanamsa_value=round(ayanamsa, 6),
            house_cusps=whole_sign_cusps,
            chalit_cusps=chalit_cusps,
            jd=jd_utc,
            house_system=_house_system_used,
        )

    def snapshot(self) -> ChartSnapshot:
        return self._snapshot
