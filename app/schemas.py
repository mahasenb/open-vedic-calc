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

# The supported birth-date span, MEASURED against the shipped Swiss Ephemeris
# data files rather than taken from the label on them.
#
# Upstream describes seas_18/semo_18/sepl_18 as covering "AD 1800-2400"
# (EPHEMERIS_LICENSE.md), and this bound used to be a YEAR range that took that
# literally: 1800..2400, i.e. everything through 2400-12-31. The files stop
# earlier. Measured with bphs_core.utils.probe_ephemeris_source() at one-day
# steps, the last day the seven visible grahas answer from the data files is
# 2400-01-10; on 2400-01-11 every one of them returns FLG_MOSEPH.
#
# That does not fail — it ANSWERS. swisseph silently substitutes its built-in
# Moshier analytical ephemeris when the data runs out and still returns a full,
# plausible result, so the whole tail of the advertised range was being served
# at HTTP 200 off the fallback engine with nothing saying so. Measured on
# /v1/chart, spying on every swe.calc_ut a served request makes: 2400-01-10 was
# 15/15 calls on Swiss data, 2400-01-11 was 12/15 on Moshier, and 2400-06-01
# through 2400-12-31 were 15/15 on Moshier. For a determinism-first engine that
# is the wrong thing to advertise, so the bound is the measured one.
#
# THE ONE-DAY MARGIN AT EACH END IS DELIBERATE. These bound a local DATE, while
# the ephemeris is queried at a UTC INSTANT, and timezone_offset_hours spans
# [-12, +14] — so a single local date covers UTC instants from (D-1 10:00) to
# (D+1 11:59:59). A date bound sitting exactly on the file boundary therefore
# still admits requests that land past it: measured, 2400-01-10 23:59:59 at
# tz=-12 was 12/15 Moshier, and the old lower bound leaked the same way
# (1800-01-01 00:00:00 at tz=+14 was 12/15 Moshier). Pulling in one day at each
# end makes every admissible (birth_time, timezone_offset_hours) combination
# inside the range Swiss-backed; tests/test_birth_date_ephemeris_range.py
# re-measures that property, and pins the file boundary as tight, on every run
# against real data.
#
# Outside this span a birth_date would otherwise reach swe.julday() unguarded
# downstream (bphs_core/chart.py, lagna_shuddhi.py, muhurat.py, transits.py) —
# at best an ephemeris result computed on the fallback and reported as if it
# were not, at worst a native-level fault in the single-worker container (year
# 9999 raises an uncaught swisseph.Error deep in the pyjhora call chain).
# Reject it at the schema boundary as a clean 422 instead.
MIN_EPHEMERIS_DATE = date(1800, 1, 2)
MAX_EPHEMERIS_DATE = date(2400, 1, 9)


def _out_of_span_message(field_name: str) -> str:
    """The 422 body for any date outside the span.

    Named per field rather than hardcoded to "birth_date", because every date
    the caller can send is bounded by the same span and a message naming the
    wrong field is worse than a vague one.
    """
    return (
        f"{field_name} must be between {MIN_EPHEMERIS_DATE.isoformat()} and "
        f"{MAX_EPHEMERIS_DATE.isoformat()} (the span the shipped Swiss "
        "Ephemeris data files actually cover for every supported timezone "
        "offset; outside it the answer would come from the Moshier fallback)"
    )


def _openapi_description(what: str) -> str:
    """The served contract as /openapi.json advertises it.

    Advertising a range wider than the data covers is what these bounds exist
    to stop, so the description is generated from the constants and can never
    drift from the validator.
    """
    return (
        f"{what}, {MIN_EPHEMERIS_DATE.isoformat()} to "
        f"{MAX_EPHEMERIS_DATE.isoformat()} inclusive — the span covered by the "
        "shipped Swiss Ephemeris data files for every supported timezone offset."
    )


def _validate_ephemeris_date(value: date) -> date:
    if not (MIN_EPHEMERIS_DATE <= value <= MAX_EPHEMERIS_DATE):
        raise ValueError(_out_of_span_message("birth_date"))
    return value


def _bounded_date(field_name: str, what: str):
    """``Annotated[date, ...]`` bounded to the ephemeris span."""
    def _check(value: date) -> date:
        if not (MIN_EPHEMERIS_DATE <= value <= MAX_EPHEMERIS_DATE):
            raise ValueError(_out_of_span_message(field_name))
        return value

    return Annotated[
        date, AfterValidator(_check), Field(description=_openapi_description(what))
    ]


def _bounded_iso_date_str(field_name: str, what: str):
    """``Annotated[str, ...]``: an ISO date string INSIDE the ephemeris span.

    Layered on IsoDateStr rather than replacing it, so a malformed value still
    fails the shape check first and the field stays a ``str`` on the wire —
    every one of these is handed straight to ``datetime.strptime`` downstream
    (app/main.py) and coercing it to a ``date`` here would be a silent
    request-shape change, not a bound.
    """
    def _check(value: str) -> str:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
        if not (MIN_EPHEMERIS_DATE <= parsed <= MAX_EPHEMERIS_DATE):
            raise ValueError(_out_of_span_message(field_name))
        return value

    return Annotated[
        IsoDateStr, AfterValidator(_check), Field(description=_openapi_description(what))
    ]


# A birth date carried on the wire as a date, bounded to the ephemeris range
# supported by the shipped Swiss Ephemeris data files. The description is part
# of the served contract: it is what /openapi.json advertises, and advertising
# a range wider than the data covers is what this bound exists to stop.
BoundedBirthDate = Annotated[
    date,
    AfterValidator(_validate_ephemeris_date),
    Field(description=_openapi_description("Birth date")),
]

# ---------------------------------------------------------------------------
# The NON-birth date inputs, bounded to the SAME measured span.
#
# birth_date was bounded first (see above) because it was the one that faulted
# loudest. It was never the only date on the wire: eight further IsoDateStr
# fields and one optional `date` carried no range check at all. Measured on the
# commit that introduced these constants, with the real Swiss files present and
# a spy on every swe.calc_ut a served request makes:
#
#   /v1/transits at_date=9999-01-01   ->  500, UNCAUGHT swisseph.Error
#   /v1/transits at_date=0001-01-01   ->  500, UNCAUGHT swisseph.Error
#   /v1/transits at_date=2400-06-01   ->  200, 10/25 calls on MOSHIER
#   /v1/transits at_date=2500-01-01   ->  200, 71/86 calls on MOSHIER
#   /v1/muhurat  2400-06-01           ->  200, 1450/6353 calls on MOSHIER
#   lagna-shuddhi 2400-06-01          ->  200, 1650/6553 calls on MOSHIER
#   family        2400-06-01          ->  200, 3300/13106 calls on MOSHIER
#   /v1/compat   reference_date=9999  ->  500, UNCAUGHT OverflowError
#
# The 500s are the fault birth_date's guard was written to stop, reached through
# a different field. The 200s are the silent-Moshier tail, reached through a
# different field. Same span, same reason, same refusal.
#
# NOT every one of these fields consults the ephemeris at the bounded date, and
# the bound is not claimed to be an accuracy fix for the ones that do not:
# measured, /v1/dashas answers from arithmetic projected off the natal Moon (its
# swe.calc_ut count is 15 — the natal chart — whether to_date is 2026-12-31 or
# 2999-12-31), and /v1/compat's reference_date drives no calls either. Those two
# are bounded to close the OverflowError and to keep one span across the served
# contract, not because a Moshier answer was measured behind them. The cost is
# real and deliberate: a late-born chart can no longer request a dasha timeline
# past MAX_EPHEMERIS_DATE, which it could before (measured: birth 2390-06-15,
# to_date 2500-01-01 was a 200). Serving a period dated past the span this
# service declares it covers is the inconsistency being removed.
# ---------------------------------------------------------------------------
BoundedAtDate = _bounded_iso_date_str("at_date", "Transit date")
BoundedFromDate = _bounded_iso_date_str("from_date", "Timeline start date")
BoundedToDate = _bounded_iso_date_str("to_date", "Timeline end date")
BoundedScanStartDate = _bounded_iso_date_str("start_date", "Scan range start date")
BoundedScanEndDate = _bounded_iso_date_str("end_date", "Scan range end date")
BoundedReferenceDate = _bounded_date("reference_date", "Reference date")


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
    from_date: BoundedFromDate
    to_date: BoundedToDate
    # Only these two dasha systems are computed (get_dasha_timeline). Restricting
    # the element type to a Literal rejects unknown values at the boundary instead
    # of silently dropping them (an unknown system would otherwise be ignored,
    # yielding a confusingly partial timeline). The length bound stops a huge list
    # forcing repeated scans. An empty list is still accepted (explicit "no
    # systems" → empty timeline) to preserve the existing contract.
    systems: list[DashaSystem] = Field(default_factory=lambda: ["vimshottari"], max_length=2)


class TransitRequest(PersonalDataIn):
    at_date: BoundedAtDate


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
    # Which engine actually produced this deployment's numbers. swisseph does
    # not raise when its data files are absent -- it substitutes its built-in
    # Moshier analytical ephemeris and returns a plausible result -- so a
    # consumer cannot infer the engine from a successful call. These two fields
    # carry the retflag-derived answer (bphs_core.utils.probe_ephemeris_source)
    # to a caller that can act on it. No default: an omitted value would be
    # indistinguishable from a genuine "yes", which is the whole class of bug
    # this exists to close.
    ephe_loaded: bool
    ephemeris_source: str  # "swiss" | "moshier"


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
    start_date: BoundedScanStartDate
    end_date: BoundedScanEndDate


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
    # The tithi and karana NAMES are recommendation-affecting (the scorer reads
    # the tithi for rikta/Amavasya and the karana for the Bhadra/Vishti veto), so
    # since 2026-08-17 this service raises rather than serving them as None. They
    # stay nullable for wire compatibility, not because null is produced here.
    tithi: str | None = None
    # End-times may be None: they are display-only refinements (zero reads in the
    # scan pipeline), so they degrade — the day is marked degraded and the limb
    # names itself in DayMuhurat.degraded_limbs — when the pyjhora call raises or
    # returns an out-of-range index.
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
    # The three veto flags below stay nullable on the wire, but are always
    # answered by this service: since the failure-mode decision of 2026-08-17 an
    # unverifiable veto raises rather than resolving to null (a null that the
    # consumer must remember to read as 'veto' is the same silent-drop shape as
    # an omitted window). ``None`` remains legal so a payload from any other
    # producer still parses, and the consumer-side fail-closed gate that treats
    # it as a veto is unchanged.
    panchaka_free: bool | None = None
    personal_balam: PersonalBalam | None = None
    all_muhurtas: list[TimeWindow]
    is_eclipse_day: bool | None = None
    is_adhik_maasa: bool | None = None
    # True when the absolute-veto (Rahu/Yama/Gulika) computation failed → every
    # candidate instant for this day fails closed (the veto is unverifiable).
    # Always False from this service — those three limbs raise instead — but
    # retained on the wire, and still honoured by the scorer, for the same
    # reason as the nullable flags above.
    hard_gate_failed: bool = False
    # True when any SUPPLEMENTARY limb degraded. Derived from degraded_limbs, so
    # it can never disagree with the detail. Recommendation-affecting limbs do
    # not appear here at all: they raise, and the request fails.
    degraded: bool = False
    # Which supplementary limbs degraded, by served-field name (e.g.
    # "moonrise", "panchanga.tithi_end", "all_muhurtas",
    # "personal_balam.tara_bala"). Additive: a consumer seeing degraded=True can
    # tell WHAT it lost instead of having to diff the payload.
    degraded_limbs: list[str] = []


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
    start_date: BoundedScanStartDate
    end_date: BoundedScanEndDate
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
    start_date: BoundedScanStartDate
    end_date: BoundedScanEndDate
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
    # The ninth date input, and the one the IsoDateStr sweep missed: it is a
    # `date`, so it never matched that grep. Unbounded it reached
    # compute_dasha_overlaps, where `window_start + timedelta(days=25*365.25)`
    # runs past year 9999 and raised an uncaught OverflowError (a raw 500).
    # Optional, and stays optional — `None` still means "today".
    reference_date: BoundedReferenceDate | None = None


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

