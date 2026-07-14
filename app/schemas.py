from typing import Annotated, Literal
from pydantic import AfterValidator, BaseModel, Field
from datetime import datetime, date, time

from bphs_core.lagna_shuddhi import ActivityCategory


def _validate_iso_date(value: str) -> str:
    # Reject malformed dates at the schema boundary so bad input is a clean 422
    # (Pydantic validation error) rather than a 500 from datetime.strptime inside
    # an endpoint handler. Kept as a str (not coerced to date) so downstream
    # consumers that expect the original "%Y-%m-%d" string stay untouched.
    datetime.strptime(value, "%Y-%m-%d")
    return value


# An ISO "YYYY-MM-DD" date carried on the wire as a string, validated on input.
IsoDateStr = Annotated[str, AfterValidator(_validate_iso_date)]

# The dasha systems the timeline builder actually computes; see get_dasha_timeline.
DashaSystem = Literal["vimshottari", "yogini"]

# Ephemeris data files ship AD 1800-2400 (see EPHEMERIS_LICENSE.md: seas_18/
# semo_18/sepl_18 cover "AD 1800-2400"). A birth_date outside that span still
# reaches swe.julday() unguarded downstream (bphs_core/chart.py, lagna_shuddhi.py,
# muhurat.py, transits.py) — at best an inaccurate/undefined ephemeris result,
# at worst a native-level fault in the single-worker container. Reject it at
# the schema boundary as a clean 422 instead.
MIN_EPHEMERIS_YEAR = 1800
MAX_EPHEMERIS_YEAR = 2400


def _validate_ephemeris_year(value: date) -> date:
    if not (MIN_EPHEMERIS_YEAR <= value.year <= MAX_EPHEMERIS_YEAR):
        raise ValueError(
            f"birth_date year must be between {MIN_EPHEMERIS_YEAR} and "
            f"{MAX_EPHEMERIS_YEAR} (the supported ephemeris data range)"
        )
    return value


# A birth date carried on the wire as a date, bounded to the ephemeris range
# supported by the shipped Swiss Ephemeris data files.
BoundedBirthDate = Annotated[date, AfterValidator(_validate_ephemeris_year)]


class BoundedPersonFields(BaseModel):
    """Shared, bounded name / birth_place / birth_date fields for every
    person-like request model (PersonalDataIn, FamilyMember, ...).

    Theme-K (forensic review): FamilyMember previously redefined these three
    fields from scratch instead of inheriting them, and in doing so silently
    dropped PersonalDataIn's string-length bounds and picked up no birth_date
    guard at all -- a request/log-inflation vector plus an unguarded
    out-of-range ephemeris year. Centralizing the guards here means any new
    person-like model gets them by construction; a future field can't drop
    a guard by copy-paste omission.
    """
    # Bounded so an oversized free-text field can't be used to inflate request
    # size / log volume. name and place are display-only labels here; the
    # computation keys off the date/time/coordinates.
    name: str = Field(min_length=1, max_length=120)
    birth_date: BoundedBirthDate
    birth_place: str = Field(min_length=1, max_length=200)


class PersonalDataIn(BoundedPersonFields):
    birth_time: time
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    timezone_offset_hours: float = Field(ge=-12, le=14, allow_inf_nan=False)


class DashaRequest(PersonalDataIn):
    from_date: IsoDateStr
    to_date: IsoDateStr
    # Only these two dasha systems are computed (get_dasha_timeline). Restricting
    # the element type to a Literal rejects unknown values at the boundary instead
    # of silently dropping them (an unknown system would otherwise be ignored,
    # yielding a confusingly partial timeline). The length bound stops a huge list
    # forcing repeated scans. An empty list is still accepted (explicit "no
    # systems" → empty timeline) to preserve the existing contract.
    systems: list[DashaSystem] = Field(default_factory=lambda: ["vimshottari"], max_length=2)


class TransitRequest(PersonalDataIn):
    at_date: IsoDateStr


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


class RashiDrishtiPlanet(BaseModel):
    planet: str
    sign: str
    aspects_signs: list[str] = []
    aspects_planets: list[str] = []


class RashiDrishti(BaseModel):
    # Classical sign -> [aspected signs] map (full 12-sign matrix).
    sign_table: dict[str, list[str]] = {}
    # Per-planet view: which signs/planets each planet casts rashi drishti onto.
    per_planet: list[RashiDrishtiPlanet] = []


class ChartResponse(BaseModel):
    lagna: str
    lagna_lord: str
    # Lagna-derived Yoga Karaka planet (the single planet ruling both a kendra and
    # a trikona for this lagna). "" when the lagna has no single Yoga Karaka.
    yoga_karaka: str = ""
    ayanamsa_value: float
    # The cusp system used for the secondary Bhava-Chalit houses.
    # 'placidus' is normal; 'equatorial' means Placidus failed (e.g. at high
    # latitudes) and the equatorial fallback was used instead. Consumers that
    # display or interpret chalit_cusps should check this field — the cusp
    # values are only geometrically valid under the system that produced them.
    house_system: str = "placidus"
    bhava_chalit_cusps: list[float] = []   # 12 sidereal cusp longitudes (Bhava-Chalit)
    # Additive: Jaimini sign aspects (deterministic).
    rashi_drishti: RashiDrishti | None = None
    rasi: list[PlanetPlacement]
    hora: list[PlanetPlacement]          # D2 — wealth/resources
    drekkana: list[PlanetPlacement]      # D3 — siblings/vitality
    saptamsa: list[PlanetPlacement]      # D7 — children/creative output
    navamsa: list[PlanetPlacement]       # D9
    decamsa: list[PlanetPlacement]       # D10
    dwadasamsa: list[PlanetPlacement]    # D12 — parents/lineage
    shodasamsa: list[PlanetPlacement]    # D16 — vehicles/comforts (Kalamsa)
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


class VimshopakaItem(BaseModel):
    total: float                       # 0-20
    grade: str                         # very weak | weak | good | excellent
    contributions: dict[str, float]    # {varga_label (D1..D60): points}


class StrengthResponse(BaseModel):
    shadbala: list[ShadbalaItem]
    bhavabala: list[BhavabalaItem]
    ashtakavarga: dict
    # Additive: Vimshopaka Bala (Dashavarga, 0-20) keyed by planet name.
    vimshopaka: dict[str, VimshopakaItem] = {}


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


class InduLagnaOut(BaseModel):
    sign: str
    house_from_lagna: int
    occupants: list[str] = []
    lord: str
    lord_dignity: str
    lord_house: int


class SphutaOut(BaseModel):
    longitude: float
    sign: str
    navamsa_sign: str
    sign_parity: str               # odd | even
    navamsa_parity: str            # odd | even
    strength: str                  # strong | middling | weak
    sign_lord: str
    sign_lord_dignity: str


class SpecialPointsResponse(BaseModel):
    arudha_lagna: str
    upapada: str
    atmakaraka: str
    karakamsa: str
    jaimini_karakas: list[JaiminiKaraka] = []
    # Additive: wealth ascendant + fertility points.
    indu_lagna: InduLagnaOut | None = None
    beeja_sphuta: SphutaOut | None = None
    kshetra_sphuta: SphutaOut | None = None


# --- Profile (Phase 2) ---

class ProfileResponse(BaseModel):
    avkahada: dict               # Varna, Yoni, Gana, Vasya, Nadi
    kalsarp: dict                # present, name, partial, rahu_house
    sade_sati_lifetime: list     # [{phase, start, end}, ...]
    numerology: dict             # {radical, destiny, name}
    favourable: dict             # lucky_number, lucky_metal, lucky_stone, lucky_color, good_years
    janma_nakshatra: dict        # deity, symbol, ruling_planet, tattva, purushartha, pada
    mangal_dosha: dict           # present, severity, cancellation, from_moon, mars_house
    # The quarterly scan step used for sade_sati_lifetime: period boundaries
    # carry ±precision_days imprecision. Defaulted so clients that parsed this
    # response before the field was added still deserialise correctly.
    precision_days: int = 91


# --- Meta ---

class SourceInfo(BaseModel):
    license: str = "AGPL-3.0"
    source_url: str
    commit: str
    ephemeris_license: str = "Swiss Ephemeris AGPL-3.0 (data/ephe/)"


# --- Muhurat ---

class MuhurtRequest(BoundedPersonFields):
    # Inherits the bounded name / birth_place and the ephemeris-range-checked
    # birth_date from BoundedPersonFields (see PersonalDataIn / FamilyMember), so
    # this model can no longer accept an oversized name (request/log-inflation) or
    # an out-of-range birth year that would reach the ephemeris unguarded. The
    # fields below are muhurat-specific.
    birth_time: time
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    timezone_offset_hours: float = Field(ge=-12, le=14, allow_inf_nan=False)
    start_date: IsoDateStr
    end_date: IsoDateStr


class TimeWindow(BaseModel):
    start: str           # HH:MM (inclusive)
    # end is EXCLUSIVE: the first minute outside the qualifying window.
    # band_end is the last qualifying minute; +1 gives this exclusive boundary.
    end: str = Field(description="HH:MM exclusive end — first minute outside the window (band_end + 1)")
    label: str | None = None


class LagnaShuddhiAlternative(BaseModel):
    instant: str                         # "YYYY-MM-DD HH:MM"
    score: float
    score_100: int
    band: Literal["Excellent", "Good", "Fair", "Avoid"]
    window: TimeWindow | None = None     # present for solo; None for family


class PanchangaInfo(BaseModel):
    # tithi/karana names may be None when their pyjhora computation fails (e.g. a
    # ZeroDivisionError at an exact phase boundary); the day is marked degraded.
    tithi: str | None = None
    # End-times may be None: the end-time backstop yields None (and degrades the
    # day) when the pyjhora call raises or returns an out-of-range index.
    tithi_end: str | None = None
    # nakshatra/yogam are now computed DIRECTLY from sidereal longitudes, so in
    # practice always populated; kept nullable for the tithi/karana-failure paths.
    nakshatra: str | None = None
    nakshatra_end: str | None = None
    yogam: str | None = None
    yogam_end: str | None = None
    karana: str | None = None
    karana_end: str | None = None
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
    # None == 'panchaka status could not be computed' (fail closed; not a clean default).
    panchaka_free: bool | None = None
    personal_balam: PersonalBalam | None = None
    all_muhurtas: list[TimeWindow]
    # bool | None: None == 'status could not be computed' → the consumer vetoes (fail closed).
    is_eclipse_day: bool | None = None
    is_adhik_maasa: bool | None = None
    # True when the absolute-veto (Rahu/Yama/Gulika) computation failed → every
    # candidate instant for this day fails closed (the veto is unverifiable).
    hard_gate_failed: bool = False
    # True on any failure that corrupts the day: sunrise/sunset fallback,
    # tithi/nakshatra/yoga/karana name-or-end failure, or hard-gate failure.
    degraded: bool = False


class MuhurtResponse(BaseModel):
    days: list[DayMuhurat]


# --- Lagna Shuddhi (electional muhurat) ---

class LagnaShuddhiRequest(BoundedPersonFields):
    # Inherits the bounded name / birth_place / ephemeris-range birth_date from
    # BoundedPersonFields (see MuhurtRequest); the fields below are scan-specific.
    birth_time: time
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    timezone_offset_hours: float = Field(ge=-12, le=14, allow_inf_nan=False)
    start_date: IsoDateStr
    end_date: IsoDateStr
    activity_category: ActivityCategory = "generic"
    # Lower-bounded: a sub-minute step over a 365-day range is a denial-of-service
    # vector (~31M inner-loop iterations). 60s is the default scan granularity.
    step_seconds: int = Field(60, ge=60)


class ScoreFactor(BaseModel):
    """One salient classical contributor to a sample's quality (for display)."""
    name: str
    impact: Literal["positive", "negative"]
    detail: str


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
    # True when the day's absolute-veto (Rahu/Yama/Gulika) computation failed —
    # carried for the clearance prose (reported as 'could not be computed').
    hard_gate_failed: bool = False
    event_navamsha: str | None = None       # D9 sign of the rising lagna at the instant
    event_navamsha_suitable: bool = False
    # --- Quality band (additive; defaults keep older payloads parseable) ---
    score_100: int = 0                       # round(score * 100) — display scale
    band: Literal["Excellent", "Good", "Fair", "Avoid"] = "Fair"
    factors: list[ScoreFactor] = []          # salient classical contributors


class LagnaShuddhiResponse(BaseModel):
    best_instant: LagnaShuddhiSample | None
    best_window: TimeWindow | None          # tolerance band around best_instant
    top_samples: list[LagnaShuddhiSample]   # up to 20 best-scored samples
    clearance_summary: str | None = None    # plain-English why-this-window summary
    alternatives: list[LagnaShuddhiAlternative] = []


# --- Family (multi-person) Lagna Shuddhi ---

class FamilyMember(BoundedPersonFields):
    # latitude/longitude/timezone_offset_hours are intentionally redefined
    # here (not inherited from PersonalDataIn) -- see tests/test_coord_bounds.py
    # TestFamilyCoordBounds. name/birth_place/birth_date now come from
    # BoundedPersonFields so this model can no longer drop those bounds the
    # way it previously did (FR-MED-23 / FR-LOW-2).
    birth_time: time
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    timezone_offset_hours: float = Field(ge=-12, le=14, allow_inf_nan=False)


class FamilyLagnaShuddhiRequest(BaseModel):
    members: list[FamilyMember]
    start_date: IsoDateStr
    end_date: IsoDateStr
    activity_category: ActivityCategory = "generic"
    # See LagnaShuddhiRequest.step_seconds — lower bound guards against DoS.
    step_seconds: int = Field(60, ge=60)


class FamilyMemberSample(LagnaShuddhiSample):
    # tara_bala / chandra_bala and the other muhurta factors are inherited from
    # LagnaShuddhiSample now that single-chart scoring computes them too.
    name: str


class FamilyLagnaShuddhiResponse(BaseModel):
    instant: str | None                         # "YYYY-MM-DD HH:MM" (local, member[0] tz) | None
    best_window: TimeWindow | None              # tolerance band around instant
    score: float                                # joint min-score across members
    score_100: int = 0                          # round(score * 100) — display scale
    band: Literal["Excellent", "Good", "Fair", "Avoid"] = "Fair"   # weakest member governs
    per_member: list[FamilyMemberSample]        # per-member detail at the chosen instant
    consensus_quality: Literal["strict", "best_effort"]
    compromised_members: list[str]              # names of members with bad balam (best_effort only)
    clearance_summary: str | None = None        # plain-English why-this-window summary
    alternatives: list[LagnaShuddhiAlternative] = []


# --- Async scan jobs (CR-4: additive submit/poll variant for the scan-class
# endpoints above) ---

JobStatusLiteral = Literal["pending", "running", "done", "error"]


class JobSubmitted(BaseModel):
    """Returned immediately by a scan's async submit endpoint."""
    job_id: str
    status: JobStatusLiteral = "pending"


class LagnaShuddhiJobStatus(BaseModel):
    job_id: str
    status: JobStatusLiteral
    # Populated only once status == "done"; None while pending/running/error.
    result: LagnaShuddhiResponse | None = None
    # Populated only once status == "error"; the underlying exception's message.
    error: str | None = None


class FamilyLagnaShuddhiJobStatus(BaseModel):
    job_id: str
    status: JobStatusLiteral
    result: FamilyLagnaShuddhiResponse | None = None
    error: str | None = None


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

