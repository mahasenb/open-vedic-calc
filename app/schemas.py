from typing import Literal
from pydantic import BaseModel, Field
from datetime import datetime, date, time


class PersonalDataIn(BaseModel):
    name: str
    birth_date: date
    birth_time: time
    birth_place: str
    latitude: float
    longitude: float
    timezone_offset_hours: float


class DashaRequest(PersonalDataIn):
    from_date: str             # ISO date
    to_date: str               # ISO date
    systems: list[str] = ["vimshottari"]


class TransitRequest(PersonalDataIn):
    at_date: str               # ISO date


# --- Chart ---

class PlanetPlacement(BaseModel):
    planet: str
    sign: str
    degrees: float
    nakshatra: str
    dignity: str
    house: int
    conjunctions: list[str]
    aspects: list[str]
    is_retrograde: bool
    is_gandanta: bool = False
    gandanta_proximity_degrees: float | None = None
    is_combust: bool = False
    combust_proximity_degrees: float | None = None
    chalit_house: int | None = None      # secondary Bhava-Chalit (Placidus cusp) house
    pada_lord: str | None = None


class ChartResponse(BaseModel):
    lagna: str
    lagna_lord: str
    ayanamsa_value: float
    bhava_chalit_cusps: list[float] = []   # 12 sidereal Placidus cusp longitudes (Bhava-Chalit)
    rasi: list[PlanetPlacement]
    hora: list[PlanetPlacement]          # D2 — wealth/resources
    drekkana: list[PlanetPlacement]      # D3 — siblings/vitality
    saptamsa: list[PlanetPlacement]      # D7 — children/creative output
    navamsa: list[PlanetPlacement]       # D9
    decamsa: list[PlanetPlacement]       # D10
    dwadasamsa: list[PlanetPlacement]    # D12 — parents/lineage
    chaturvimsa: list[PlanetPlacement]   # D24
    trimshamsa: list[PlanetPlacement]    # D30
    shashtyamsa: list[PlanetPlacement]   # D60


# --- Strength ---

class ShadbalaItem(BaseModel):
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


class BhavabalaItem(BaseModel):
    house_number: int
    bala_total: float
    bhava_adhipathi_bala: float
    bhava_drik: float
    rank: str


class StrengthResponse(BaseModel):
    shadbala: list[ShadbalaItem]
    bhavabala: list[BhavabalaItem]
    ashtakavarga: dict


# --- Dashas ---

class DashaPeriodOut(BaseModel):
    lord: str
    level: str
    system: str
    start_date: datetime
    end_date: datetime
    duration_years: float


# --- Yogas ---

class YogaOut(BaseModel):
    name: str
    description: str
    planets_involved: list[str]
    houses_involved: list[int]
    strength: str
    is_viparita_raja: bool = False
    activating_lords: list[str] = []


# --- Transits ---

class TransitPlanetPlacement(BaseModel):
    planet: str
    sign: str
    degrees: float
    nakshatra: str
    house_from_lagna: int | None = None     # house from natal lagna (all 9 planets)
    # Gochara signals — present for the seven grahas, null for Rahu/Ketu.
    house_from_moon: int | None = None
    favourable: bool | None = None
    bindu_score: int | None = None


class GocharaVedha(BaseModel):
    blocked_planet: str
    blocking_planet: str
    blocked_house: int       # favourable house from the Moon (1-12)
    vedha_house: int         # obstructing house from the Moon (1-12)
    neutralised: bool        # True: favourable result obstructed; False: exempt pair, result stands


class TransitResponse(BaseModel):
    planets: list[TransitPlanetPlacement]
    sade_sati_active: bool
    sade_sati_phase: str | None = None
    saturn_vedha_blocked: bool
    jupiter_vedha_blocked: bool
    gochara_vedha: list[GocharaVedha] = []
    chandrashtama: bool = False              # transit Moon 8th from natal Moon
    dhaiya_active: bool = False              # transit Saturn 4th/8th from natal Moon
    dhaiya_phase: str | None = None


# --- Special points ---

class JaiminiKaraka(BaseModel):
    abbr: str
    name: str
    planet: str
    degree: float
    domain: str


class SpecialPointsResponse(BaseModel):
    arudha_lagna: str
    upapada: str
    atmakaraka: str
    karakamsa: str
    jaimini_karakas: list[JaiminiKaraka] = []


# --- Profile (Phase 2) ---

class ProfileResponse(BaseModel):
    avkahada: dict               # Varna, Yoni, Gana, Vasya, Nadi
    kalsarp: dict                # present, name, partial, rahu_house
    sade_sati_lifetime: list     # [{phase, start, end}, ...]
    numerology: dict             # {radical, destiny, name}
    favourable: dict             # lucky_number, lucky_metal, lucky_stone, lucky_color, good_years
    janma_nakshatra: dict        # deity, symbol, ruling_planet, tattva, purushartha, pada
    mangal_dosha: dict           # present, severity, cancellation, from_moon, mars_house


# --- Meta ---

class SourceInfo(BaseModel):
    license: str = "AGPL-3.0"
    source_url: str
    commit: str
    ephemeris_license: str = "Swiss Ephemeris AGPL-3.0 (data/ephe/)"


# --- Muhurat ---

class MuhurtRequest(BaseModel):
    name: str
    birth_date: date
    birth_time: time
    birth_place: str
    latitude: float
    longitude: float
    timezone_offset_hours: float
    start_date: str      # YYYY-MM-DD
    end_date: str        # YYYY-MM-DD


class TimeWindow(BaseModel):
    start: str           # HH:MM (inclusive)
    # end is EXCLUSIVE: the first minute outside the qualifying window.
    # band_end is the last qualifying minute; +1 gives this exclusive boundary.
    end: str = Field(description="HH:MM exclusive end — first minute outside the window (band_end + 1)")
    label: str | None = None


class PanchangaInfo(BaseModel):
    tithi: str
    tithi_end: str
    # name may be None when pyjhora returns an out-of-range (0) index — the
    # bphs_core guard reports None rather than silently wrapping to the last entry.
    nakshatra: str | None = None
    nakshatra_end: str
    yogam: str | None = None
    yogam_end: str
    karana: str
    karana_end: str
    vaara: str


class PersonalBalam(BaseModel):
    tara_bala: str
    chandra_bala: str


class DayMuhurat(BaseModel):
    date: str            # YYYY-MM-DD
    sunrise: str
    sunset: str
    moonrise: str | None = None
    moonset: str | None = None
    panchanga: PanchangaInfo
    auspicious_muhurtas: list[TimeWindow]
    chogadiya: list[TimeWindow]
    inauspicious_periods: list[TimeWindow]
    amrita_periods: list[TimeWindow]
    panchaka_free: bool
    personal_balam: PersonalBalam | None = None
    all_muhurtas: list[TimeWindow]
    degraded: bool = False  # True when sunrise/sunset computation failed (fallback values corrupt day-length math)


class MuhurtResponse(BaseModel):
    days: list[DayMuhurat]


# --- Lagna Shuddhi (electional muhurat) ---

class LagnaShuddhiRequest(BaseModel):
    name: str
    birth_date: date
    birth_time: time
    birth_place: str
    latitude: float
    longitude: float
    timezone_offset_hours: float
    start_date: str     # YYYY-MM-DD
    end_date: str       # YYYY-MM-DD
    activity_category: Literal["generic", "business", "marriage", "travel", "surgery"] = "generic"
    step_seconds: int = 60


class LagnaShuddhiSample(BaseModel):
    instant: str                    # YYYY-MM-DD HH:MM (local time)
    lagna_sign: str
    lagna_lord: str
    lagna_lord_house: int           # whole-sign house from lagna (0 = unknown)
    lagna_lord_dignity: str
    hora_lord: str
    chogadiya_label: str | None
    in_rahu_kala: bool
    in_yamaganda: bool
    in_gulika: bool
    in_durmuhurtam: bool
    in_varjyam: bool
    in_auspicious_muhurta: str | None   # name of muhurta if inside one
    score: float                    # 0..1
    # --- Muhurta factors (defaults keep older payloads parseable) ---
    tara_bala: str = "Unknown"
    chandra_bala: str = "Neutral"
    tithi: str | None = None
    yoga: str | None = None
    panchanga_suitable: bool = True
    event_navamsha: str | None = None       # D9 sign of the rising lagna at the instant
    event_navamsha_suitable: bool = False


class LagnaShuddhiResponse(BaseModel):
    best_instant: LagnaShuddhiSample | None
    best_window: TimeWindow | None          # tolerance band around best_instant
    top_samples: list[LagnaShuddhiSample]   # up to 20 best-scored samples
    clearance_summary: str | None = None    # plain-English why-this-window summary


# --- Family (multi-person) Lagna Shuddhi ---

class FamilyMember(BaseModel):
    name: str
    birth_date: date
    birth_time: time
    birth_place: str
    latitude: float
    longitude: float
    timezone_offset_hours: float


class FamilyLagnaShuddhiRequest(BaseModel):
    members: list[FamilyMember]
    start_date: str      # YYYY-MM-DD
    end_date: str        # YYYY-MM-DD
    activity_category: Literal["generic", "business", "marriage", "travel", "surgery"] = "generic"
    step_seconds: int = 60


class FamilyMemberSample(LagnaShuddhiSample):
    # tara_bala / chandra_bala and the other muhurta factors are inherited from
    # LagnaShuddhiSample now that single-chart scoring computes them too.
    name: str


class FamilyLagnaShuddhiResponse(BaseModel):
    instant: str | None                         # "YYYY-MM-DD HH:MM" (local, member[0] tz) | None
    best_window: TimeWindow | None              # tolerance band around instant
    score: float                                # joint min-score across members
    per_member: list[FamilyMemberSample]        # per-member detail at the chosen instant
    consensus_quality: Literal["strict", "best_effort"]
    compromised_members: list[str]              # names of members with bad balam (best_effort only)
    clearance_summary: str | None = None        # plain-English why-this-window summary


# --- Compatibility ---

class CompatRequest(BaseModel):
    person_a: PersonalDataIn
    person_b: PersonalDataIn
    reference_date: date | None = None


class KutaScore(BaseModel):
    name: str
    score: float
    max_score: float
    interpretation: str


class MangalDoshaResult(BaseModel):
    has_dosha: bool
    severity: Literal["none", "mild", "strong"]
    cancellation: str


class DashaOverlap(BaseModel):
    start_date: str   # YYYY-MM-DD
    end_date: str     # YYYY-MM-DD
    person_a_lord: str
    person_b_lord: str
    quality: Literal["favorable", "neutral", "challenging"]


class CompatResponse(BaseModel):
    total_score: float
    max_score: float
    kutas: list[KutaScore]
    mangal_dosha_a: MangalDoshaResult
    mangal_dosha_b: MangalDoshaResult
    nakshatra_compatibility: str
    dasha_overlaps: list[DashaOverlap]
    composite_strength_notes: str

