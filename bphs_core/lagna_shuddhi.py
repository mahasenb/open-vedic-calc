"""
Lagna Shuddhi: minute-resolution electional muhurat.

Scans candidate windows to find the precise instant where the rising lagna
and its lord are well-disposed for the intended activity, clear of hard
inauspicious periods (Rahu Kala, Yamaganda, Gulika).

Does NOT compute or guess planetary positions — all astrology math uses
pyswisseph/pyjhora calls at the exact Julian Day of each candidate minute.

Family scan (scan_family_lagna_shuddhi): computes a single deterministic
joint instant that satisfies all members simultaneously — hard-gating each
member's Rahu/Yama/Gulika exclusions and Tara/Chandra Bala, then ranking by
the minimum individual score so no weak member can be averaged away.
"""
from datetime import datetime, date as date_type
from typing import Literal
from dataclasses import dataclass

import swisseph as swe
from jhora.panchanga import drik

from . import utils
from .muhurat import compute_muhurat_for_day, _TARA_BALA_LEVELS

# Chaldean descending-speed order (used for hora lord sequence)
_CHALDEAN = ["Saturn", "Jupiter", "Mars", "Sun", "Venus", "Mercury", "Moon"]
_WEEKDAY_LORDS = {
    "Monday": "Moon", "Tuesday": "Mars", "Wednesday": "Mercury",
    "Thursday": "Jupiter", "Friday": "Venus", "Saturday": "Saturn", "Sunday": "Sun",
}
# pyjhora planet IDs (matches utils.PLANETS order)
_PLANET_IDS = {p: i for i, p in enumerate(utils.PLANETS)}
_MALEFIC_IDS = [
    _PLANET_IDS["Sun"], _PLANET_IDS["Mars"], _PLANET_IDS["Saturn"],
    _PLANET_IDS["Rahu"], _PLANET_IDS["Ketu"],
]
_FAVORABLE_CHOGADIYA = {
    "Chara (Auspicious)", "Labh (Auspicious)",
    "Amrit (Highly Auspicious)", "Shubh (Auspicious)",
}
_FAVORABLE_HORA = {"Sun", "Moon", "Mercury", "Jupiter", "Venus"}

ActivityCategory = Literal[
    "generic",
    # exclusive
    "marriage",
    # special
    "griha_pravesh", "business", "shop_opening", "property",
    "namkaran", "mundan", "annaprashan", "upanayana", "surgery",
    # normal
    "travel", "vehicle", "new_job", "education",
]

# --- Panchanga suitability---------------------------------------------
# Rikta tithis (4th, 9th, 14th of either paksha) are classically inauspicious
# for new undertakings; get_tithi_name prefixes the paksha, so match by base name.
_RIKTA_TITHIS = ("Chaturthi", "Navami", "Chaturdashi")
# Classically avoided yogas (Vishkumbha … Vaidhriti).
_INAUSPICIOUS_YOGAS = frozenset({
    "Vishkumbha", "Atiganda", "Shula", "Ganda", "Vyaghata",
    "Vajra", "Vyatipata", "Parigha", "Vaidhriti",
})

# Event Navamsha (D9 of the rising sign at the candidate instant) — the
# minute-level electional signal. Its classical use is a *strength* check, NOT a
# per-activity sign whitelist: Vargottama (navamsa lagna sign == rasi lagna
# sign) is the strongest auspicious signal; the lagna's sign NATURE should suit
# the activity (movable for journeys, fixed for permanence/dwelling, dual for
# learning/exchange); a benefic-ruled navamsa lagna is mildly favourable.
# Sources: B.V. Raman, "Muhurtha" (Vargottama / Pushkaramsa); the classical
# chara/sthira/dvisvabhava muhurta rule (movable→yatra/travel, fixed→griha
# pravesh/marriage) — e.g. drikpanchang / standard muhurta texts.
_MOVABLE_SIGNS = frozenset({"Aries", "Cancer", "Libra", "Capricorn"})
_FIXED_SIGNS = frozenset({"Taurus", "Leo", "Scorpio", "Aquarius"})
_DUAL_SIGNS = frozenset({"Gemini", "Virgo", "Sagittarius", "Pisces"})
# Vara (weekday) classes. The benefic day-lords (Moon/Mercury/Jupiter/Venus)
# carry auspicious undertakings; Mangala (Tue) and Shani (Sat) are classically
# avoided for samskaras and beginnings. Commerce/learning lean to Budha/Guru/
# Shukra. (B.V. Raman, "Muhurtha"; standard panchanga vaara-shuddhi.)
_BENEFIC_VARAS = frozenset({"Monday", "Wednesday", "Thursday", "Friday"})
_COMMERCE_VARAS = frozenset({"Wednesday", "Thursday", "Friday"})
_MALEFIC_VARAS = frozenset({"Tuesday", "Saturday"})
# Vara bonus/penalty magnitudes (a tuning choice; the classical texts give a
# qualitative day-lord preference, not numbers).
_VARA_BONUS = 0.06
_VARA_PENALTY = 0.05
# Day/instant conditions that classically VETO an electional instant for the
# activities that name them (subset stored per-activity in ActivityRule).
_SAMSKARA_EXCLUDES = frozenset({"eclipse", "adhik_maasa"})


@dataclass(frozen=True)
class ActivityRule:
    """Per-activity electional preferences — pure DATA, no branching logic.

    Every field below is consumed by ``_score_instant`` / ``_event_navamsha_factor``
    so a new activity is added by appending ONE row to ``_ACTIVITY_RULES`` (Open/
    Closed), never by adding an ``if activity == ...`` branch.

    sign_nature        preferred nature of the lagna / event-navamsa sign
                       (movable→motion, fixed→permanence, dual→learning/exchange).
    prefer_varas       weekdays that earn a bonus (classical day-lords).
    avoid_varas        weekdays that earn a penalty.
    dignity_weight     multiplier on the lagna-lord dignity bonus (>1 amplifies).
    kendra_lord_bonus  extra credit when the lagna lord occupies a kendra.
    chogadiya_bonus    credit for a favourable chogadiya at the instant.
    hard_excludes      conditions that VETO the instant (score 0.0): a subset of
                       {"durm_varj", "eclipse", "adhik_maasa", "vishti"}.

    The values are BPHS-/muhurta-grounded defaults (B.V. Raman, "Muhurtha";
    standard drikpanchang vaara/tithi/yoga shuddhi) and are intended to be
    reviewed for domain accuracy; the *structure* is what keeps scoring uniform.
    """
    sign_nature: frozenset = frozenset()
    prefer_varas: frozenset = frozenset()
    avoid_varas: frozenset = frozenset()
    dignity_weight: float = 1.0
    kendra_lord_bonus: float = 0.0
    chogadiya_bonus: float = 0.08
    hard_excludes: frozenset = frozenset()


# The full taxonomy as data. 14 activities + a neutral ``generic`` fallback.
# Classes (metering lives in the application, not here): exclusive=marriage;
# special=griha_pravesh/business/shop_opening/property/namkaran/mundan/
# annaprashan/upanayana/surgery; normal=travel/vehicle/new_job/education.
_ACTIVITY_RULES: dict[str, ActivityRule] = {
    # Fallback: no fixed preferences — judged on Vargottama + a benefic navamsa
    # lagna alone. Preserves the original "generic" behaviour exactly.
    "generic": ActivityRule(),

    # --- exclusive -------------------------------------------------------
    # Marriage: permanence (fixed) lagna; benefic days, avoid Mangala/Shani;
    # HARD veto on eclipse, Adhika Maasa and Bhadra (Vishti karana). Vyatipata/
    # Vaidhriti are already in the always-avoided yoga set. Amplified lagna-lord
    # dignity weight (the lord's strength matters most for the union).
    "marriage": ActivityRule(
        sign_nature=_FIXED_SIGNS,
        prefer_varas=_BENEFIC_VARAS,
        avoid_varas=_MALEFIC_VARAS,
        dignity_weight=1.2,
        hard_excludes=frozenset({"eclipse", "adhik_maasa", "vishti"}),
    ),

    # --- special ---------------------------------------------------------
    # House entry: dwelling permanence (fixed); benefic days; no auspicious
    # entry during an eclipse or Adhika Maasa.
    "griha_pravesh": ActivityRule(
        sign_nature=_FIXED_SIGNS, prefer_varas=_BENEFIC_VARAS,
        avoid_varas=_MALEFIC_VARAS, hard_excludes=_SAMSKARA_EXCLUDES,
    ),
    # Commerce establishment: durability (fixed); Budha/Guru/Shukra days; a
    # strong lagna lord in a kendra is especially wanted.
    "business": ActivityRule(
        sign_nature=_FIXED_SIGNS, prefer_varas=_COMMERCE_VARAS,
        avoid_varas=_MALEFIC_VARAS, kendra_lord_bonus=0.05,
        hard_excludes=_SAMSKARA_EXCLUDES,
    ),
    # Shop opening: same commercial profile as business establishment.
    "shop_opening": ActivityRule(
        sign_nature=_FIXED_SIGNS, prefer_varas=_COMMERCE_VARAS,
        avoid_varas=_MALEFIC_VARAS, kendra_lord_bonus=0.05,
        hard_excludes=_SAMSKARA_EXCLUDES,
    ),
    # Property purchase: permanence (fixed); benefic days; avoid eclipse/Adhika.
    "property": ActivityRule(
        sign_nature=_FIXED_SIGNS, prefer_varas=_BENEFIC_VARAS,
        avoid_varas=_MALEFIC_VARAS, hard_excludes=_SAMSKARA_EXCLUDES,
    ),
    # Child samskaras (naming / tonsure / first-feeding / sacred-thread). Soft
    # benefic days; eclipse and Adhika Maasa are classically barred. Naming,
    # first-feeding and upanayana carry a learning (dual) flavour; tonsure has
    # no sign-nature preference.
    "namkaran": ActivityRule(
        sign_nature=_DUAL_SIGNS, prefer_varas=_BENEFIC_VARAS,
        avoid_varas=_MALEFIC_VARAS, hard_excludes=_SAMSKARA_EXCLUDES,
    ),
    "mundan": ActivityRule(
        prefer_varas=_BENEFIC_VARAS, avoid_varas=_MALEFIC_VARAS,
        hard_excludes=_SAMSKARA_EXCLUDES,
    ),
    "annaprashan": ActivityRule(
        sign_nature=_DUAL_SIGNS, prefer_varas=_BENEFIC_VARAS,
        avoid_varas=_MALEFIC_VARAS, hard_excludes=_SAMSKARA_EXCLUDES,
    ),
    "upanayana": ActivityRule(
        sign_nature=_DUAL_SIGNS, prefer_varas=_COMMERCE_VARAS,
        avoid_varas=_MALEFIC_VARAS, hard_excludes=_SAMSKARA_EXCLUDES,
    ),
    # Surgery: the one activity that keeps the Durmuhurtam/Varjyam veto; also
    # avoid eclipse. No sign-nature preference (parity with prior behaviour).
    "surgery": ActivityRule(
        hard_excludes=frozenset({"durm_varj", "eclipse"}),
    ),

    # --- normal ----------------------------------------------------------
    # Travel/yatra: motion (movable); a favourable chogadiya weighs more for
    # journeys; avoid eclipse.
    "travel": ActivityRule(
        sign_nature=_MOVABLE_SIGNS, prefer_varas=_BENEFIC_VARAS,
        chogadiya_bonus=0.15, hard_excludes=frozenset({"eclipse"}),
    ),
    # Vehicle purchase: a conveyance → motion (movable); chogadiya weighted
    # like travel.
    "vehicle": ActivityRule(
        sign_nature=_MOVABLE_SIGNS, prefer_varas=_BENEFIC_VARAS,
        chogadiya_bonus=0.15, hard_excludes=frozenset({"eclipse"}),
    ),
    # New job/role: stability (fixed) with a strong lagna lord in a kendra;
    # benefic days.
    "new_job": ActivityRule(
        sign_nature=_FIXED_SIGNS, prefer_varas=_BENEFIC_VARAS,
        avoid_varas=_MALEFIC_VARAS, kendra_lord_bonus=0.05,
        hard_excludes=frozenset({"eclipse"}),
    ),
    # Education/vidyarambha: learning & exchange (dual); Budha/Guru/Shukra days.
    "education": ActivityRule(
        sign_nature=_DUAL_SIGNS, prefer_varas=_COMMERCE_VARAS,
        hard_excludes=frozenset({"eclipse"}),
    ),
}

_GENERIC_RULE = _ACTIVITY_RULES["generic"]


def _rule_for(activity: str) -> ActivityRule:
    """Return the rule row for an activity, defaulting to the neutral generic
    rule for any unknown value (forward-compatible)."""
    return _ACTIVITY_RULES.get(activity, _GENERIC_RULE)
# Benefic-ruled signs (Moon, Mercury, Venus, Jupiter): a mildly favourable
# navamsa lagna when no stronger signal applies.
_BENEFIC_LORD_SIGNS = frozenset({
    "Cancer", "Gemini", "Virgo", "Taurus", "Libra", "Sagittarius", "Pisces",
})


def _navamsa_sign(longitude: float) -> str:
    """Classical continuous navamsa sign of a sidereal longitude: the zodiac is
    divided into 108 navamsas of 3°20'; navamsa N maps to sign (N mod 12).

    Computed as ``longitude * 9 / 30`` (multiply before divide) so that exact
    sign boundaries (30°, 60°, …) land on the correct navamsa rather than one
    early through floating-point error.
    """
    return utils.SIGNS[int(longitude * 9.0 / 30.0) % 12]


def _event_navamsha_factor(nav_sign: str, lagna_sign: str,
                           activity: ActivityCategory) -> tuple[bool, float]:
    """Score the event navamsa lagna. Returns (suitable, score_delta).

    Strongest signal first: Vargottama (navamsa lagna sign == rasi lagna sign),
    classically the most auspicious; else the sign NATURE matching the activity
    (movable/fixed/dual); else a benefic-ruled navamsa lagna. The magnitudes are
    a tuning choice (texts give a qualitative priority, not numbers); the
    *ordering* follows the classical priority.
    """
    if nav_sign == lagna_sign:                       # Vargottama
        return True, 0.08
    nature = _rule_for(activity).sign_nature
    if nature and nav_sign in nature:                # sign nature suits activity
        return True, 0.05
    if nav_sign in _BENEFIC_LORD_SIGNS:              # benefic navamsa lagna
        return True, 0.03
    return False, 0.0


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _hhmm_to_mins(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _mins_to_hhmm(mins: int) -> str:
    mins = mins % (24 * 60)
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _in_window(time_mins: int, start_hhmm: str, end_hhmm: str) -> bool:
    s = _hhmm_to_mins(start_hhmm)
    e = _hhmm_to_mins(end_hhmm)
    if e > s:
        return s <= time_mins < e
    # spans midnight
    return time_mins >= s or time_mins < e


def _label_at(time_mins: int, windows: list[dict]) -> str | None:
    for w in windows:
        if _in_window(time_mins, w["start"], w["end"]):
            return w.get("label")
    return None


def _jd_for_local(date_str: str, time_mins: int, tz_offset: float) -> float:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    local_h = time_mins / 60.0
    utc_h = local_h - tz_offset
    return swe.julday(d.year, d.month, d.day, utc_h)


# ---------------------------------------------------------------------------
# Hora lord
# ---------------------------------------------------------------------------

def compute_hora_lord(date_str: str, time_hhmm: str, sunrise_hhmm: str) -> str:
    """Return the planetary hora lord at the given local time on the given date."""
    date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    day_lord = _WEEKDAY_LORDS[date_obj.strftime("%A")]
    day_lord_idx = _CHALDEAN.index(day_lord)
    elapsed_mins = (_hhmm_to_mins(time_hhmm) - _hhmm_to_mins(sunrise_hhmm)) % (24 * 60)
    hora_num = elapsed_mins // 60
    return _CHALDEAN[(day_lord_idx + hora_num) % 7]


# ---------------------------------------------------------------------------
# Lagna at a specific JD
# ---------------------------------------------------------------------------

def compute_lagna_at_jd(jd: float, lat: float, lon: float) -> tuple[str, str, float]:
    """Return (lagna_sign, lagna_lord, sidereal_ascendant_longitude) for the JD (UT)."""
    drik.set_ayanamsa_mode('LAHIRI')
    ayanamsa = drik.get_ayanamsa_value(jd)
    try:
        _, ascmc = swe.houses(jd, lat, lon, b"P")
    except Exception:
        _, ascmc = swe.houses(jd, lat, lon, b"E")
    sid_asc = (ascmc[0] - ayanamsa) % 360
    sign = utils.SIGNS[int(sid_asc // 30)]
    return sign, utils.get_sign_lord(sign), sid_asc


# ---------------------------------------------------------------------------
# Per-minute scoring
# ---------------------------------------------------------------------------

def _score_instant(
    jd: float,
    lagna_sign: str,
    lagna_lord: str,
    day_data: dict,
    time_mins: int,
    activity: ActivityCategory,
    lagna_long: float | None = None,
    birth_nakshatra: str | None = None,
    birth_moon_sign: str | None = None,
) -> tuple[float, dict]:
    """
    Score a single candidate instant. Returns (score 0..1, detail_dict).
    score = 0.0 means hard-disqualified (Rahu Kala / Yamaganda / Gulika).
    """
    inauspicious = day_data.get("inauspicious_periods", [])
    in_rahu = any(
        "Rahu" in (w.get("label") or "") and _in_window(time_mins, w["start"], w["end"])
        for w in inauspicious
    )
    in_yama = any(
        "Yamagan" in (w.get("label") or "") and _in_window(time_mins, w["start"], w["end"])
        for w in inauspicious
    )
    in_guli = any(
        "Gulika" in (w.get("label") or "") and _in_window(time_mins, w["start"], w["end"])
        for w in inauspicious
    )
    in_durm = any(
        "Durmuhurt" in (w.get("label") or "") and _in_window(time_mins, w["start"], w["end"])
        for w in inauspicious
    )
    in_varj = any(
        "Varjyam" in (w.get("label") or "") and _in_window(time_mins, w["start"], w["end"])
        for w in inauspicious
    )

    detail = {
        "in_rahu_kala": in_rahu,
        "in_yamaganda": in_yama,
        "in_gulika": in_guli,
        "in_durmuhurtam": in_durm,
        "in_varjyam": in_varj,
        "in_auspicious_muhurta": None,
        "chogadiya_label": None,
        "hora_lord": "",
        "lagna_lord_house": 0,
        "lagna_lord_dignity": "unknown",
        "malefics_in_lagna": 0,
        "tithi": (day_data.get("panchanga") or {}).get("tithi"),
        "yoga": (day_data.get("panchanga") or {}).get("yogam"),
        "panchanga_suitable": True,
        "tara_bala": "Unknown",
        "chandra_bala": "Neutral",
        "event_navamsha": None,
        "event_navamsha_suitable": False,
        "hard_excluded": False,
    }

    if in_rahu or in_yama or in_guli:
        detail["hard_excluded"] = True
        return 0.0, detail

    # Per-activity hard vetoes (data-driven — see ActivityRule.hard_excludes).
    # Durmuhurtam/Varjyam (surgery), a locally-visible eclipse, Adhika Maasa, or
    # Bhadra (Vishti karana) disqualify the instant outright for the activities
    # that name them.
    rule = _rule_for(activity)
    he = rule.hard_excludes
    karana = (day_data.get("panchanga") or {}).get("karana") or ""
    if (("durm_varj" in he and (in_durm or in_varj))
            or ("eclipse" in he and day_data.get("is_eclipse_day"))
            or ("adhik_maasa" in he and day_data.get("is_adhik_maasa"))
            or ("vishti" in he and karana == "Vishti")):
        detail["hard_excluded"] = True
        return 0.0, detail

    in_auspicious = _label_at(time_mins, day_data.get("auspicious_muhurtas", []))
    chogadiya_label = _label_at(time_mins, day_data.get("chogadiya", []))
    hora_lord = compute_hora_lord(
        day_data["date"], _mins_to_hhmm(time_mins), day_data["sunrise"]
    )

    # Lagna lord transit position at this exact instant
    lagna_sign_idx = utils.SIGNS.index(lagna_sign)
    lord_id = _PLANET_IDS.get(lagna_lord, -1)
    lord_house = 0
    lord_dignity = "neutral"
    if lord_id >= 0:
        try:
            lord_lon = drik.sidereal_longitude(jd, lord_id)
            lord_sign_idx = int(lord_lon // 30) % 12
            lord_house = (lord_sign_idx - lagna_sign_idx) % 12 + 1
            lord_dignity = utils.get_planet_dignity(lagna_lord, utils.SIGNS[lord_sign_idx])
        except Exception:
            pass

    # Malefics in lagna sign at this instant
    malefics_in_lagna = 0
    for pid in _MALEFIC_IDS:
        try:
            m_lon = drik.sidereal_longitude(jd, pid)
            if int(m_lon // 30) % 12 == lagna_sign_idx:
                malefics_in_lagna += 1
        except Exception:
            pass

    detail.update({
        "in_auspicious_muhurta": in_auspicious,
        "chogadiya_label": chogadiya_label,
        "hora_lord": hora_lord,
        "lagna_lord_house": lord_house,
        "lagna_lord_dignity": lord_dignity,
        "malefics_in_lagna": malefics_in_lagna,
    })

    # --- Base score ---
    score = 0.4

    if in_durm:
        score -= 0.12
    if in_varj:
        score -= 0.12

    # Lagna lord dignity
    dignity_bonus = {
        "exalted": 0.20, "moolatrikona": 0.15, "own sign": 0.15,
        "friendly": 0.08, "neutral": 0.0, "enemy": -0.08, "debilitated": -0.15,
    }.get(lord_dignity, 0.0)
    score += dignity_bonus

    # Lagna lord house (whole-sign from lagna)
    if lord_house in (1, 4, 7, 10):
        score += 0.15
    elif lord_house in (5, 9):
        score += 0.10
    elif lord_house in (2, 3, 11):
        score += 0.05
    elif lord_house in (6, 8, 12):
        score -= 0.15

    # In auspicious muhurta
    if in_auspicious:
        score += 0.12 if in_auspicious == "Abhijit Muhurta" else 0.08

    # Favorable chogadiya (per-activity weight — travel/vehicle value it more)
    if chogadiya_label in _FAVORABLE_CHOGADIYA:
        score += rule.chogadiya_bonus

    # Benefic hora lord
    if hora_lord in _FAVORABLE_HORA:
        score += 0.05

    # Malefics in lagna
    score -= min(malefics_in_lagna * 0.08, 0.16)

    # Activity-specific weighting (data-driven — see ActivityRule):
    # amplify the lagna-lord dignity weight, and credit a kendra-placed lagna
    # lord for the activities that ask for it.
    score += dignity_bonus * (rule.dignity_weight - 1.0)
    if lord_house in (1, 4, 7, 10):
        score += rule.kendra_lord_bonus

    # --- Muhurta factor gates. Classical priority (B.V. Raman, "Muhurtha";
    # Panchanga Shuddhi): toxic periods are an absolute veto (applied above as a
    # hard 0.0); nakshatra-based strength (Tara / Chandra Bala) is the primary
    # limb; tithi/yoga suitability is secondary; the Event Navamsha is a final
    # refinement. The penalty/bonus magnitudes are a tuning choice; their
    # ordering (Tara/Chandra >= tithi/yoga > navamsa) follows that priority. ---

    # Panchanga suitability: Rikta tithis and the avoided yogas deprioritise.
    tithi_name = detail["tithi"] or ""
    yoga_name = detail["yoga"] or ""
    is_rikta = any(r in tithi_name for r in _RIKTA_TITHIS)
    is_bad_yoga = yoga_name in _INAUSPICIOUS_YOGAS
    detail["panchanga_suitable"] = not (is_rikta or is_bad_yoga)
    if is_rikta:
        score -= 0.10
    if is_bad_yoga:
        score -= 0.10

    # Vaara (weekday) suitability — classical day-lord preference for the activity.
    vaara = (day_data.get("panchanga") or {}).get("vaara") or ""
    if vaara in rule.prefer_varas:
        score += _VARA_BONUS
    elif vaara in rule.avoid_varas:
        score -= _VARA_PENALTY

    # Tara Bala / Chandra Bala at this exact instant (parity with the family scan).
    tara_label, chandra_str = compute_balam_at_jd(jd, birth_nakshatra, birth_moon_sign)
    detail["tara_bala"] = tara_label
    detail["chandra_bala"] = chandra_str
    if tara_label in _TARA_BAD:
        score -= 0.12
    if chandra_str == "Inauspicious (Avoid)":
        score -= 0.12
    elif chandra_str == "Good":
        score += 0.03

    # Event Navamsha — D9 of the rising sign at this instant; the minute-level
    # electional signal (Vargottama / sign-nature / benefic — see
    # _event_navamsha_factor).
    if lagna_long is not None:
        nav_sign = _navamsa_sign(lagna_long)
        suitable, nav_delta = _event_navamsha_factor(nav_sign, lagna_sign, activity)
        detail["event_navamsha"] = nav_sign
        detail["event_navamsha_suitable"] = suitable
        score += nav_delta

    return max(0.0, min(1.0, score)), detail


# ---------------------------------------------------------------------------
# Candidate window selection
# ---------------------------------------------------------------------------

def _candidate_minutes(day_data: dict) -> list[int]:
    """Return sorted list of minute-of-day values to scan for this day."""
    auspicious = day_data.get("auspicious_muhurtas", [])
    chogadiya = [
        w for w in day_data.get("chogadiya", [])
        if w.get("label") in _FAVORABLE_CHOGADIYA
    ]
    minutes: set[int] = set()
    for window_list in (auspicious, chogadiya):
        for w in window_list:
            s = _hhmm_to_mins(w["start"])
            e = _hhmm_to_mins(w["end"])
            if e <= s:
                e += 24 * 60
            for m in range(s, e):
                minutes.add(m % (24 * 60))
    return sorted(minutes)


# ---------------------------------------------------------------------------
# Clearance summary
# ---------------------------------------------------------------------------

def _ordinal(n: int) -> str:
    if not n:
        return "unknown house"
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _build_clearance_summary(sample: dict, activity: ActivityCategory) -> str:
    """Plain-English why-this-window summary built from a scored sample's factors."""
    pan = (
        "suitable" if sample.get("panchanga_suitable", True)
        else "inauspicious (Rikta tithi or avoided yoga)"
    )
    parts = [
        "Clear of Rahu Kala, Yamaganda and Gulika.",
        f"Panchanga: {sample.get('tithi')}, {sample.get('yoga')} yoga — {pan}.",
        f"Tara Bala: {sample.get('tara_bala')}; Chandra Bala: {sample.get('chandra_bala')}.",
        f"Rising {sample.get('lagna_sign')} (lord {sample.get('lagna_lord')}) in the "
        f"{_ordinal(sample.get('lagna_lord_house', 0))}, {sample.get('lagna_lord_dignity')}.",
    ]
    nav = sample.get("event_navamsha")
    if nav:
        fit = "suitable" if sample.get("event_navamsha_suitable") else "not specially indicated"
        parts.append(f"Event Navamsha lagna {nav} ({fit} for {activity}).")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Quality band + factor breakdown (additive presentation over the 0..1 score)
# ---------------------------------------------------------------------------

Band = Literal["Excellent", "Good", "Fair", "Avoid"]

# Tara levels that are classically favourable (bare label, no description).
# _TARA_BAD (Vipat/Pratyak/Naidhana) is defined further down for the family gate;
# both are resolved at call time, so order of definition does not matter.
_TARA_GOOD = frozenset({"Sampat", "Kshema", "Sadhana", "Mitra", "Paramitra"})
_BAND_ORDER = {"Avoid": 0, "Fair": 1, "Good": 2, "Excellent": 3}


def derive_band(score: float, sample: dict) -> Band:
    """Map a 0..1 score + its classical signals onto a four-level quality band.

    Hybrid rule — nakshatra strength dominates, then score granularity:
      * a hard inauspicious period (Rahu/Yama/Gulika) or a zero score -> Avoid
      * bad Tara Bala (Vipat/Pratyak/Naidhana) or Chandra "Avoid" -> at most Fair
      * else by score: >=0.85 Excellent, >=0.60 Good, otherwise Fair
    """
    if (score <= 0.0 or sample.get("in_rahu_kala")
            or sample.get("in_yamaganda") or sample.get("in_gulika")):
        return "Avoid"
    if (sample.get("tara_bala") in _TARA_BAD
            or sample.get("chandra_bala") == "Inauspicious (Avoid)"):
        return "Fair"
    if score >= 0.85:
        return "Excellent"
    if score >= 0.60:
        return "Good"
    return "Fair"


def _factor(name: str, impact: str, detail: str) -> dict:
    return {"name": name, "impact": impact, "detail": detail}


def build_factors(sample: dict) -> list[dict]:
    """Compact, ordered list of the salient classical contributors for a sample.

    Pure presentation: reads the signals already on the scored sample (no
    re-computation). Classical priority — toxic-period clearance, Tara/Chandra
    Bala, panchanga (tithi/yoga), lagna-lord dignity & house, then the finer
    muhurta/chogadiya/navamsa refinements. Only non-neutral signals are emitted.
    """
    factors: list[dict] = []

    # Toxic-period clearance (the hard gate the scan already enforces).
    if not (sample.get("in_rahu_kala") or sample.get("in_yamaganda")
            or sample.get("in_gulika")):
        factors.append(_factor("Inauspicious periods", "positive",
                               "Clear of Rahu Kala, Yamaganda and Gulika."))
    else:
        factors.append(_factor("Inauspicious periods", "negative",
                               "Falls in Rahu Kala, Yamaganda or Gulika."))
    if sample.get("in_durmuhurtam"):
        factors.append(_factor("Durmuhurtam", "negative", "Within Durmuhurtam."))
    if sample.get("in_varjyam"):
        factors.append(_factor("Varjyam", "negative", "Within Varjyam."))

    # Tara Bala.
    tara = sample.get("tara_bala")
    if tara in _TARA_GOOD:
        factors.append(_factor("Tara Bala", "positive", f"{tara} — favourable."))
    elif tara in _TARA_BAD:
        factors.append(_factor("Tara Bala", "negative", f"{tara} — unfavourable."))

    # Chandra Bala.
    chandra = sample.get("chandra_bala")
    if chandra == "Good":
        factors.append(_factor("Chandra Bala", "positive",
                               "Moon well placed from the birth sign."))
    elif chandra == "Inauspicious (Avoid)":
        factors.append(_factor("Chandra Bala", "negative",
                               "Moon poorly placed from the birth sign."))

    # Panchanga (tithi / yoga) suitability.
    if sample.get("panchanga_suitable", True):
        factors.append(_factor("Panchanga", "positive",
                               f"{sample.get('tithi')}, {sample.get('yoga')} yoga — suitable."))
    else:
        factors.append(_factor("Panchanga", "negative",
                               f"{sample.get('tithi')}, {sample.get('yoga')} yoga — "
                               "Rikta tithi or avoided yoga."))

    # Lagna-lord dignity.
    dignity = sample.get("lagna_lord_dignity")
    if dignity in ("exalted", "moolatrikona", "own sign", "friendly"):
        factors.append(_factor("Lagna lord dignity", "positive",
                               f"Lord {sample.get('lagna_lord')} {dignity}."))
    elif dignity in ("enemy", "debilitated"):
        factors.append(_factor("Lagna lord dignity", "negative",
                               f"Lord {sample.get('lagna_lord')} {dignity}."))

    # Lagna-lord house (whole-sign from lagna).
    house = sample.get("lagna_lord_house") or 0
    if house in (1, 4, 7, 10, 5, 9):
        factors.append(_factor("Lagna lord house", "positive",
                               f"Lord in the {_ordinal(house)} (kendra/trikona)."))
    elif house in (6, 8, 12):
        factors.append(_factor("Lagna lord house", "negative",
                               f"Lord in the {_ordinal(house)} (dusthana)."))

    # Auspicious muhurta window.
    musec = sample.get("in_auspicious_muhurta")
    if musec:
        factors.append(_factor("Muhurta", "positive", f"Within {musec}."))

    # Favourable chogadiya.
    if sample.get("chogadiya_label") in _FAVORABLE_CHOGADIYA:
        factors.append(_factor("Chogadiya", "positive", f"{sample.get('chogadiya_label')}."))

    # Event Navamsha refinement.
    if sample.get("event_navamsha_suitable"):
        factors.append(_factor("Event Navamsha", "positive",
                               f"Navamsa lagna {sample.get('event_navamsha')} suits the activity."))

    return factors


def _enrich_sample(sample: dict) -> dict:
    """Attach score_100 + band + factors to a scored sample dict, in place."""
    sample["score_100"] = round(sample.get("score", 0.0) * 100)
    sample["band"] = derive_band(sample.get("score", 0.0), sample)
    sample["factors"] = build_factors(sample)
    return sample


def _family_band(min_score: float, consensus_quality: str,
                 per_member: list[dict]) -> Band:
    """Joint band: the weakest member governs; a best-effort consensus caps at Fair."""
    if min_score <= 0.0:
        return "Avoid"
    worst: Band = "Excellent"
    for md in per_member:
        b = derive_band(md.get("score", 0.0), md)
        if _BAND_ORDER[b] < _BAND_ORDER[worst]:
            worst = b
    if consensus_quality == "best_effort" and _BAND_ORDER[worst] > _BAND_ORDER["Fair"]:
        return "Fair"
    return worst


# ---------------------------------------------------------------------------
# Main scan function
# ---------------------------------------------------------------------------

def scan_lagna_shuddhi(
    lat: float,
    lon: float,
    tz_offset: float,
    birth_nakshatra: str | None,
    birth_moon_sign: str | None,
    start_date: str,
    end_date: str,
    activity: ActivityCategory = "generic",
    step_seconds: int = 60,
) -> dict:
    """
    Scan all candidate auspicious windows across the date range at `step_seconds`
    resolution. Returns the best-scored instant + a tolerance band + top samples.

    Returns a dict with keys:
      best_instant, best_window, top_samples (list, up to 20 best)
    Each sample dict has all LagnaShuddhiSample fields.
    """
    place = utils.make_place("scan", lat, lon, tz_offset)
    step_mins = max(1, step_seconds // 60)

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    from datetime import timedelta
    curr = start_dt

    all_samples: list[dict] = []

    while curr <= end_dt:
        day_data = compute_muhurat_for_day(
            place, curr, birth_nakshatra, birth_moon_sign
        )
        candidates = _candidate_minutes(day_data)
        if step_mins > 1:
            candidates = [m for i, m in enumerate(candidates) if i % step_mins == 0]

        for time_mins in candidates:
            jd = _jd_for_local(day_data["date"], time_mins, tz_offset)
            lagna_sign, lagna_lord, lagna_long = compute_lagna_at_jd(jd, lat, lon)
            score, detail = _score_instant(
                jd, lagna_sign, lagna_lord, day_data, time_mins, activity,
                lagna_long=lagna_long,
                birth_nakshatra=birth_nakshatra,
                birth_moon_sign=birth_moon_sign,
            )
            all_samples.append({
                "instant": f"{day_data['date']} {_mins_to_hhmm(time_mins)}",
                "lagna_sign": lagna_sign,
                "lagna_lord": lagna_lord,
                "lagna_lord_house": detail["lagna_lord_house"],
                "lagna_lord_dignity": detail["lagna_lord_dignity"],
                "hora_lord": detail["hora_lord"],
                "chogadiya_label": detail["chogadiya_label"],
                "in_rahu_kala": detail["in_rahu_kala"],
                "in_yamaganda": detail["in_yamaganda"],
                "in_gulika": detail["in_gulika"],
                "in_durmuhurtam": detail["in_durmuhurtam"],
                "in_varjyam": detail["in_varjyam"],
                "in_auspicious_muhurta": detail["in_auspicious_muhurta"],
                "tara_bala": detail["tara_bala"],
                "chandra_bala": detail["chandra_bala"],
                "tithi": detail["tithi"],
                "yoga": detail["yoga"],
                "panchanga_suitable": detail["panchanga_suitable"],
                "event_navamsha": detail["event_navamsha"],
                "event_navamsha_suitable": detail["event_navamsha_suitable"],
                "score": round(score, 4),
            })

        curr += timedelta(days=1)

    if not all_samples:
        # Fallback: no auspicious windows found — return a zero-scored placeholder
        return {
            "best_instant": None,
            "best_window": None,
            "top_samples": [],
            "clearance_summary": None,
        }

    # Sort by score descending
    ranked = sorted(all_samples, key=lambda s: s["score"], reverse=True)
    best = ranked[0]

    # Tolerance band: contiguous run of samples around best_instant where
    # score >= 0.85 * best_score and within ±5 minutes of best_instant
    best_date, best_time = best["instant"].split(" ")
    best_mins = _hhmm_to_mins(best_time)
    best_score = best["score"]
    threshold = 0.85 * best_score

    band_start = best_mins
    band_end = best_mins

    # Walk backwards
    for sample in sorted(all_samples, key=lambda s: s["instant"], reverse=True):
        s_date, s_time = sample["instant"].split(" ")
        if s_date != best_date:
            continue
        s_mins = _hhmm_to_mins(s_time)
        if s_mins > best_mins:
            continue
        if best_mins - s_mins > 5:
            break
        if sample["score"] >= threshold:
            band_start = min(band_start, s_mins)

    # Walk forwards
    for sample in sorted(all_samples, key=lambda s: s["instant"]):
        s_date, s_time = sample["instant"].split(" ")
        if s_date != best_date:
            continue
        s_mins = _hhmm_to_mins(s_time)
        if s_mins < best_mins:
            continue
        if s_mins - best_mins > 5:
            break
        if sample["score"] >= threshold:
            band_end = max(band_end, s_mins)

    best_window = {
        "start": _mins_to_hhmm(band_start),
        # band_end is the last qualifying minute; +1 yields an EXCLUSIVE end
        # (the first non-qualifying minute), so the interval is [start, end).
        "end": _mins_to_hhmm(band_end + 1),
        "label": f"Best window for {activity}",
    }

    # Enrich only the samples we return (best + up to 20), never all candidates.
    returned = ranked[:20]
    for _s in returned:
        _enrich_sample(_s)

    return {
        "best_instant": best,
        "best_window": best_window,
        "top_samples": returned,
        "clearance_summary": _build_clearance_summary(best, activity),
    }


# ---------------------------------------------------------------------------
# Per-instant Tara / Chandra Bala helper (reuses muhurat.py constants)
# ---------------------------------------------------------------------------

def compute_balam_at_jd(
    jd: float,
    birth_nakshatra: str | None,
    birth_moon_sign: str | None,
) -> tuple[str, str]:
    """Return (tara_label, chandra_str) for the Moon's position at *jd*.

    Uses the same formula as muhurat.py lines 255-283.  tara_label is the
    bare label string (e.g. "Vipat") without the parenthesised description.
    chandra_str is one of "Good", "Neutral", or "Inauspicious (Avoid)".

    Returns ("Unknown", "Neutral") when inputs are missing or an error occurs.
    """
    if not birth_nakshatra or not birth_moon_sign:
        return "Unknown", "Neutral"

    # --- Tara Bala ---
    try:
        transit_moon_lon = drik.sidereal_longitude(jd, 1)  # Moon ID = 1
        transit_star = int(transit_moon_lon // (360 / 27)) % 27 + 1  # 1-27
        birth_star_idx = utils.NAKSHATRAS.index(birth_nakshatra) + 1
        tb_div = (((transit_star - birth_star_idx + 27) % 27) + 1) % 9
        tb_label, _ = _TARA_BALA_LEVELS.get(tb_div, ("Unknown", "Neutral"))
    except Exception:
        tb_label = "Unknown"

    # --- Chandra Bala ---
    try:
        transit_moon_lon = drik.sidereal_longitude(jd, 1)
        transit_moon_sign_idx = int(transit_moon_lon // 30) % 12
        birth_moon_sign_idx = utils.SIGNS.index(birth_moon_sign)
        diff = (transit_moon_sign_idx - birth_moon_sign_idx) % 12 + 1
        if diff in (1, 3, 6, 7, 10, 11):
            chandra_str = "Good"
        elif diff in (2, 5, 9):
            chandra_str = "Neutral"
        else:
            chandra_str = "Inauspicious (Avoid)"
    except Exception:
        chandra_str = "Neutral"

    return tb_label, chandra_str


# ---------------------------------------------------------------------------
# Hard-gate constants for family scan
# ---------------------------------------------------------------------------

_TARA_BAD = {"Vipat", "Pratyak", "Naidhana"}


# ---------------------------------------------------------------------------
# Family (multi-person) joint scan
# ---------------------------------------------------------------------------

def scan_family_lagna_shuddhi(
    members: list[dict],
    start_date: str,
    end_date: str,
    activity: ActivityCategory = "generic",
    step_seconds: int = 60,
) -> dict:
    """Deterministic joint muhurat scan for 2-6 family members.

    Each member dict must have keys:
      name, lat, lon, tz_offset, birth_nakshatra, birth_moon_sign

    Returns a dict with:
      instant: "YYYY-MM-DD HH:MM" | None
      best_window: {start, end, label} | None
      score: float
      per_member: list of per-member sample dicts
      consensus_quality: "strict" | "best_effort"
      compromised_members: list[str]
    """
    from datetime import timedelta

    step_mins = max(1, step_seconds // 60)
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

    # Pre-compute day_data for every member × every date.
    # Keyed by (member_index, date_str).
    member_day_data: dict[tuple[int, str], dict] = {}
    curr = start_dt
    while curr <= end_dt:
        for idx, m in enumerate(members):
            place = utils.make_place(m["name"], m["lat"], m["lon"], m["tz_offset"])
            dd = compute_muhurat_for_day(
                place, curr, m.get("birth_nakshatra"), m.get("birth_moon_sign")
            )
            member_day_data[(idx, curr.strftime("%Y-%m-%d"))] = dd
        curr += timedelta(days=1)

    # Collect candidate (date_str, time_mins) pairs = UNION across all members.
    candidate_set: set[tuple[str, int]] = set()
    curr = start_dt
    while curr <= end_dt:
        date_str = curr.strftime("%Y-%m-%d")
        for idx in range(len(members)):
            dd = member_day_data[(idx, date_str)]
            mins_list = _candidate_minutes(dd)
            if step_mins > 1:
                mins_list = [m for i, m in enumerate(mins_list) if i % step_mins == 0]
            for m in mins_list:
                candidate_set.add((date_str, m))
        curr += timedelta(days=1)

    # Sort candidates for determinism.
    candidates = sorted(candidate_set)

    def _score_candidate(date_str: str, time_mins: int) -> dict | None:
        """Score a single (date, minute) across all members.

        Returns None if the instant is hard-excluded for ANY member
        (Rahu/Yama/Gulika flags from _score_instant returning 0.0).
        Otherwise returns a scored record.
        """
        member_scores: list[float] = []
        member_details: list[dict] = []

        for idx, m in enumerate(members):
            dd = member_day_data[(idx, date_str)]
            jd = _jd_for_local(date_str, time_mins, m["tz_offset"])
            lagna_sign, lagna_lord, lagna_long = compute_lagna_at_jd(jd, m["lat"], m["lon"])
            score, detail = _score_instant(
                jd, lagna_sign, lagna_lord, dd, time_mins, activity,
                lagna_long=lagna_long,
                birth_nakshatra=m.get("birth_nakshatra"),
                birth_moon_sign=m.get("birth_moon_sign"),
            )
            # Hard-exclude if Rahu / Yama / Gulika (or surgery Durm/Varj) for this member.
            if detail.get("hard_excluded"):
                return None

            member_scores.append(score)
            member_details.append({
                "name": m["name"],
                "instant": f"{date_str} {_mins_to_hhmm(time_mins)}",
                "lagna_sign": lagna_sign,
                "lagna_lord": lagna_lord,
                "lagna_lord_house": detail["lagna_lord_house"],
                "lagna_lord_dignity": detail["lagna_lord_dignity"],
                "hora_lord": detail["hora_lord"],
                "chogadiya_label": detail["chogadiya_label"],
                "in_rahu_kala": detail["in_rahu_kala"],
                "in_yamaganda": detail["in_yamaganda"],
                "in_gulika": detail["in_gulika"],
                "in_durmuhurtam": detail["in_durmuhurtam"],
                "in_varjyam": detail["in_varjyam"],
                "in_auspicious_muhurta": detail["in_auspicious_muhurta"],
                "score": round(score, 4),
                "tara_bala": detail["tara_bala"],
                "chandra_bala": detail["chandra_bala"],
                "tithi": detail["tithi"],
                "yoga": detail["yoga"],
                "panchanga_suitable": detail["panchanga_suitable"],
                "event_navamsha": detail["event_navamsha"],
                "event_navamsha_suitable": detail["event_navamsha_suitable"],
            })

        min_score = min(member_scores)
        mean_score = sum(member_scores) / len(member_scores)
        instant_str = f"{date_str} {_mins_to_hhmm(time_mins)}"
        return {
            "instant": instant_str,
            "min_score": min_score,
            "mean_score": mean_score,
            "per_member": member_details,
        }

    def _passes_balam_gate(record: dict) -> tuple[bool, list[str]]:
        """Return (passes, compromised_names).

        Fails (returns False) if ANY member has Tara in _TARA_BAD or
        Chandra == "Inauspicious (Avoid)".
        """
        bad = []
        for md in record["per_member"]:
            if md["tara_bala"] in _TARA_BAD or md["chandra_bala"] == "Inauspicious (Avoid)":
                bad.append(md["name"])
        return (len(bad) == 0), bad

    # --- First pass: score all candidates, drop hard-excluded ones ---
    all_records: list[dict] = []
    for date_str, time_mins in candidates:
        rec = _score_candidate(date_str, time_mins)
        if rec is not None:
            all_records.append(rec)

    if not all_records:
        # Nothing survived even the hard gate.
        return {
            "instant": None,
            "best_window": None,
            "score": 0.0,
            "score_100": 0,
            "band": "Avoid",
            "per_member": [],
            "consensus_quality": "best_effort",
            "compromised_members": [],
            "clearance_summary": None,
        }

    # Stable sort key: (-min_score, -mean_score, instant_str)
    all_records.sort(key=lambda r: (-r["min_score"], -r["mean_score"], r["instant"]))

    # --- Second pass: apply balam gate ---
    strict_records = [r for r in all_records if _passes_balam_gate(r)[0]]

    if strict_records:
        # At least one instant passes all gates.
        best = strict_records[0]
        consensus_quality = "strict"
        compromised_members: list[str] = []
    else:
        # Best-effort: use top record from hard-gated list, note bad balam.
        best = all_records[0]
        consensus_quality = "best_effort"
        _, compromised_members = _passes_balam_gate(best)

    best_instant_str = best["instant"]
    best_min_score = best["min_score"]
    best_date = best_instant_str.split(" ")[0]
    best_time = best_instant_str.split(" ")[1]
    best_mins_val = _hhmm_to_mins(best_time)

    # --- Tolerance band (same ±5 min / 0.85 × best logic as single-person) ---
    # Only consider records from the same date as the best instant.
    same_day = [r for r in all_records if r["instant"].startswith(best_date + " ")]
    threshold = 0.85 * best_min_score

    band_start = best_mins_val
    band_end = best_mins_val

    for rec in sorted(same_day, key=lambda r: r["instant"], reverse=True):
        s_mins = _hhmm_to_mins(rec["instant"].split(" ")[1])
        if s_mins > best_mins_val:
            continue
        if best_mins_val - s_mins > 5:
            break
        if rec["min_score"] >= threshold:
            band_start = min(band_start, s_mins)

    for rec in sorted(same_day, key=lambda r: r["instant"]):
        s_mins = _hhmm_to_mins(rec["instant"].split(" ")[1])
        if s_mins < best_mins_val:
            continue
        if s_mins - best_mins_val > 5:
            break
        if rec["min_score"] >= threshold:
            band_end = max(band_end, s_mins)

    best_window = {
        "start": _mins_to_hhmm(band_start),
        # band_end is the last qualifying minute; +1 yields an EXCLUSIVE end
        # (the first non-qualifying minute), so the interval is [start, end).
        "end": _mins_to_hhmm(band_end + 1),
        "label": f"Best joint window for {activity}",
    }

    for _md in best["per_member"]:
        _enrich_sample(_md)

    return {
        "instant": best_instant_str,
        "best_window": best_window,
        "score": round(best_min_score, 4),
        "score_100": round(best_min_score * 100),
        "band": _family_band(best_min_score, consensus_quality, best["per_member"]),
        "per_member": best["per_member"],
        "consensus_quality": consensus_quality,
        "compromised_members": compromised_members,
        "clearance_summary": (
            f"Joint {consensus_quality} window for {len(members)} members. "
            + _build_clearance_summary(best["per_member"][0], activity)
            if best.get("per_member") else None
        ),
    }
