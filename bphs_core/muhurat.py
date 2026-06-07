import logging
from datetime import datetime, date as date_type
import swisseph as swe
from jhora.panchanga import drik
from . import utils

logger = logging.getLogger(__name__)

TITHIS = [
    "Prathama", "Dwitiya", "Tritiya", "Chaturthi", "Panchami", "Shashthi",
    "Saptami", "Ashtami", "Navami", "Dashami", "Ekadashi", "Dwadashi",
    "Trayodashi", "Chaturdashi", "Purnima",
    "Prathama", "Dwitiya", "Tritiya", "Chaturthi", "Panchami", "Shashthi",
    "Saptami", "Ashtami", "Navami", "Dashami", "Ekadashi", "Dwadashi",
    "Trayodashi", "Chaturdashi", "Amavasya"
]

YOGAS = [
    "Vishkumbha", "Priti", "Ayushman", "Saubhagya", "Shobhana", "Atiganda",
    "Sukarma", "Dhriti", "Shula", "Ganda", "Vridhi", "Dhruva", "Vyaghata",
    "Harshana", "Vajra", "Siddhi", "Vyatipata", "Variyan", "Parigha",
    "Shiva", "Siddha", "Sadhya", "Shubha", "Shukla", "Brahma", "Indra", "Vaidhriti"
]

KARANAS = [
    "Bava", "Balava", "Kaulava", "Taitila", "Gara", "Vanija", "Vishti"
]

FIXED_KARANAS = {
    1: "Kimstughna",
    58: "Shakuni",
    59: "Chatushpada",
    60: "Naga"
}

_TARA_BALA_LEVELS = {
    1: ("Janma", "Not Good"),
    2: ("Sampat", "Very Good"),
    3: ("Vipat", "Bad"),
    4: ("Kshema", "Good"),
    5: ("Pratyak", "Not Good"),
    6: ("Sadhana", "Very Good"),
    7: ("Naidhana", "Totally Bad"),
    8: ("Mitra", "Good"),
    0: ("Paramitra", "Good"),
}

_CHOGHADIYA_TYPES = {
    0: "Udveg (Inauspicious)",
    1: "Chara (Auspicious)",
    2: "Labh (Auspicious)",
    3: "Amrit (Highly Auspicious)",
    4: "Kaal (Inauspicious)",
    5: "Shubh (Auspicious)",
    6: "Rog (Inauspicious)",
}

# The 30 standard Muhurtas of the day/night
_MUHURTA_NAMES = [
    "Rudra", "Ahi", "Mitra", "Pitri", "Vasu", "Vara", "Vishwadeva", "Vidhi",
    "Sathamukhi", "Puruhuta", "Vahni", "Naktanchara", "Varuna", "Aryaman", "Bhaga",
    "Girish", "Ajapad", "Ahirbudhnya", "Pusa", "Ashwini", "Yama", "Agni", "Vidhatri",
    "Chanda", "Aditi", "Jiva", "Visnu", "Dyumani", "Brahma", "Samudra"
]


def float_hours_to_hhmm(fh: float) -> str:
    fh = fh % 24
    h = int(fh)
    m = int(round((fh - h) * 60))
    if m == 60:
        h = (h + 1) % 24
        m = 0
    return f"{h:02d}:{m:02d}"


def get_tithi_name(idx: int) -> str:
    if idx <= 15:
        phase = "Shukla"
        name = TITHIS[idx - 1]
    else:
        phase = "Krishna"
        name = TITHIS[idx - 16]
    return f"{phase} {name}"


def get_karana_name(idx: int) -> str:
    if idx in FIXED_KARANAS:
        return FIXED_KARANAS[idx]
    return KARANAS[(idx - 2) % 7]


def _is_eclipse_day(target_date: date_type, place: drik.Place) -> bool:
    """True if a solar OR lunar eclipse is VISIBLE at *place* on this local day.

    An eclipse not visible at the location carries no grahana dosha, so the
    location-aware drik.next_*_eclipse finders are used — they skip eclipses not
    seen from `place` (verified: India sees 2026-03-03 lunar & 2027-08-02 solar,
    but not the 2026-02-17 / 2026-08-12 solar eclipses). The next-eclipse JD is
    in ``tret[0]``; we accept it when it falls inside this local calendar day.
    """
    tz = place.timezone
    day_start = swe.julday(
        target_date.year, target_date.month, target_date.day, 0.0 - tz
    )
    day_end = day_start + 1.0
    for finder in (drik.next_solar_eclipse, drik.next_lunar_eclipse):
        try:
            res = finder(day_start - 2.0, place)
            t_max = res[1][0]
            guard = 0
            # Advance past any eclipse that falls before this day.
            while t_max < day_start and guard < 60:
                res = finder(t_max + 0.05, place)
                t_max = res[1][0]
                guard += 1
            if day_start <= t_max < day_end:
                return True
        except Exception:
            logger.warning("muhurat_eclipse_check_failed", exc_info=True)
    return False


def _is_adhik_maasa(jd: float, place: drik.Place) -> bool:
    """True if the lunar month containing *jd* is an Adhika (intercalary) Maasa.

    ``drik.lunar_month`` returns ``[maasa_number, is_leap_month, is_nija_month]``;
    element 1 (``is_leap_month``) is the adhika flag per pyjhora's own docstring —
    an adhika maasa is the amanta month whose bracketing new moons fall in the
    same solar month (no sankranti). The service always runs in LAHIRI ayanamsa
    (set in utils import), under which this is verified against Adhika Shravana
    2023. No auspicious samskara is begun in an Adhika Maasa.
    """
    try:
        return bool(drik.lunar_month(jd, place)[1])
    except Exception:
        logger.warning("muhurat_adhika_maasa_check_failed", exc_info=True)
        return False


def compute_muhurat_for_day(
    place: drik.Place,
    target_date: date_type,
    birth_nakshatra: str | None = None,
    birth_moon_sign: str | None = None
) -> dict:
    # 1. Convert target date to Julian Day at Noon local time
    jd = swe.julday(target_date.year, target_date.month, target_date.day, 12.0)
    drik.set_ayanamsa_mode('LAHIRI')

    # 2. Get Sunrise, Sunset, Moonrise, Moonset
    # degraded=True when sunrise or sunset fails: the "06:00"/"18:00" fallbacks
    # corrupt downstream day-length calculations (chogadiya widths etc).
    degraded = False
    try:
        sr = drik.sunrise(jd, place)[1][:5]
    except Exception:
        logger.warning("muhurat_sunrise_failed", exc_info=True)
        sr = "06:00"
        degraded = True
    try:
        ss = drik.sunset(jd, place)[1][:5]
    except Exception:
        logger.warning("muhurat_sunset_failed", exc_info=True)
        ss = "18:00"
        degraded = True
    try:
        mr = drik.moonrise(jd, place)[1][:5]
    except Exception:
        logger.warning("muhurat_moonrise_failed", exc_info=True)
        mr = None
    try:
        ms = drik.moonset(jd, place)[1][:5]
    except Exception:
        logger.warning("muhurat_moonset_failed", exc_info=True)
        ms = None

    # 3. Panchanga limbs
    t_res = drik.tithi(jd, place)
    t_name = get_tithi_name(t_res[0])
    t_end = float_hours_to_hhmm(t_res[2])

    n_res = drik.nakshatra(jd, place)
    n_idx = n_res[0]
    if 1 <= n_idx <= len(utils.NAKSHATRAS):
        n_name = utils.NAKSHATRAS[n_idx - 1]
    else:
        logger.warning("muhurat_nakshatra_index_out_of_range", extra={"n_idx": n_idx})
        n_name = None
    n_end = float_hours_to_hhmm(n_res[3])  # index 3 contains the end float hour

    y_res = drik.yogam(jd, place)
    y_idx = y_res[0]
    if 1 <= y_idx <= len(YOGAS):
        y_name = YOGAS[y_idx - 1]
    else:
        logger.warning("muhurat_yoga_index_out_of_range", extra={"y_idx": y_idx})
        y_name = None
    y_end = float_hours_to_hhmm(y_res[2])

    k_res = drik.karana(jd, place)
    k_name = get_karana_name(k_res[0])
    k_end = float_hours_to_hhmm(k_res[2])

    # Derived weekday
    weekday = target_date.strftime("%A")

    panchanga = {
        "tithi": t_name,
        "tithi_end": t_end,
        "nakshatra": n_name,
        "nakshatra_end": n_end,
        "yogam": y_name,
        "yogam_end": y_end,
        "karana": k_name,
        "karana_end": k_end,
        "vaara": weekday
    }

    # 4. Auspicious windows (Abhijit, Brahma, Vijaya, Godhuli, Nishita)
    auspicious = []
    try:
        ab = drik.abhijit_muhurta(jd, place)
        auspicious.append({"start": ab[0][:5], "end": ab[1][:5], "label": "Abhijit Muhurta"})
    except Exception:
        logger.warning("muhurat_abhijit_failed", exc_info=True)

    try:
        bm = drik.brahma_muhurtha(jd, place)
        auspicious.append({"start": float_hours_to_hhmm(bm[0]), "end": float_hours_to_hhmm(bm[1]), "label": "Brahma Muhurtha"})
    except Exception:
        logger.warning("muhurat_brahma_failed", exc_info=True)

    try:
        vm = drik.vijaya_muhurtha(jd, place)
        # vm is double tuple: ((day_start, day_end), (night_start, night_end))
        auspicious.append({"start": float_hours_to_hhmm(vm[0][0]), "end": float_hours_to_hhmm(vm[0][1]), "label": "Vijaya Muhurtha (Day)"})
        auspicious.append({"start": float_hours_to_hhmm(vm[1][0]), "end": float_hours_to_hhmm(vm[1][1]), "label": "Vijaya Muhurtha (Night)"})
    except Exception:
        logger.warning("muhurat_vijaya_failed", exc_info=True)

    try:
        gm = drik.godhuli_muhurtha(jd, place)
        auspicious.append({"start": float_hours_to_hhmm(gm[0]), "end": float_hours_to_hhmm(gm[1]), "label": "Godhuli Muhurtha"})
    except Exception:
        logger.warning("muhurat_godhuli_failed", exc_info=True)

    try:
        nm = drik.nishita_muhurtha(jd, place)
        auspicious.append({"start": float_hours_to_hhmm(nm[0]), "end": float_hours_to_hhmm(nm[1]), "label": "Nishita Muhurtha"})
    except Exception:
        logger.warning("muhurat_nishita_failed", exc_info=True)

    # 5. Chogadiya windows
    chogadiya_list = []
    try:
        gc = drik.gauri_choghadiya(jd, place)
        for g_type, g_start, g_end in gc:
            label = _CHOGHADIYA_TYPES.get(g_type, "Unknown")
            chogadiya_list.append({"start": g_start[:5], "end": g_end[:5], "label": label})
    except Exception:
        logger.warning("muhurat_chogadiya_failed", exc_info=True)

    # 6. Inauspicious periods (Rahu Kala, Yamagandam, Gulikai, Durmuhurtam, Varjyam)
    inauspicious = []
    try:
        rk = drik.raahu_kaalam(jd, place)
        inauspicious.append({"start": rk[0][:5], "end": rk[1][:5], "label": "Rahu Kala"})
    except Exception:
        logger.warning("muhurat_rahu_kaalam_failed", exc_info=True)

    try:
        yg = drik.yamaganda_kaalam(jd, place)
        inauspicious.append({"start": yg[0][:5], "end": yg[1][:5], "label": "Yamagandam"})
    except Exception:
        logger.warning("muhurat_yamaganda_failed", exc_info=True)

    try:
        gk = drik.gulikai_kaalam(jd, place)
        inauspicious.append({"start": gk[0][:5], "end": gk[1][:5], "label": "Gulika"})
    except Exception:
        logger.warning("muhurat_gulikai_failed", exc_info=True)

    try:
        dm = drik.durmuhurtam(jd, place)
        # dm list of strings in pairs
        if len(dm) >= 2:
            inauspicious.append({"start": dm[0][:5], "end": dm[1][:5], "label": "Durmuhurtam Period 1"})
        if len(dm) >= 4:
            inauspicious.append({"start": dm[2][:5], "end": dm[3][:5], "label": "Durmuhurtam Period 2"})
    except Exception:
        logger.warning("muhurat_durmuhurtam_failed", exc_info=True)

    try:
        vj = drik.varjyam(jd, place)
        # float hours, can span sunrise
        inauspicious.append({"start": float_hours_to_hhmm(vj[0]), "end": float_hours_to_hhmm(vj[1]), "label": "Varjyam"})
    except Exception:
        logger.warning("muhurat_varjyam_failed", exc_info=True)

    # 7. Amrita periods
    amrita = []
    try:
        ag = drik.amrita_gadiya(jd, place)
        amrita.append({"start": float_hours_to_hhmm(ag[0]), "end": float_hours_to_hhmm(ag[1]), "label": "Amrita Gadiya"})
    except Exception:
        logger.warning("muhurat_amrita_failed", exc_info=True)

    # 8. Panchaka free periods
    panchaka_free = True
    try:
        pk = drik.panchaka_rahitha(jd, place)
        # pk is list of tuples: (dosha_idx, start_h, end_h)
        # if there are any non-zero doshas spanning noon, we can mark as not panchaka free
        for dosha, s_h, e_h in pk:
            if s_h <= 12.0 <= e_h and dosha != 0:
                panchaka_free = False
                break
    except Exception:
        logger.warning("muhurat_panchaka_failed", exc_info=True)

    # 9. Personalized Balam
    personal = None
    if birth_nakshatra and birth_moon_sign:
        # Tara Bala
        try:
            transit_star = n_res[0]  # todays star index (1-27)
            birth_star_idx = utils.NAKSHATRAS.index(birth_nakshatra) + 1
            tb_div = (((transit_star - birth_star_idx + 27) % 27) + 1) % 9
            tb_label, tb_desc = _TARA_BALA_LEVELS.get(tb_div, ("Unknown", "Neutral"))
            tara_str = f"{tb_label} ({tb_desc})"
        except Exception:
            tara_str = "Neutral"

        # Chandra Bala
        try:
            transit_moon_lon = drik.sidereal_longitude(jd, 1)  # 1 is Moon ID
            transit_moon_sign_idx = int(transit_moon_lon // 30) % 12
            birth_moon_sign_idx = utils.SIGNS.index(birth_moon_sign)
            diff = (transit_moon_sign_idx - birth_moon_sign_idx) % 12 + 1
            if diff in [1, 3, 6, 7, 10, 11]:
                chandra_str = "Good"
            elif diff in [2, 5, 9]:
                chandra_str = "Neutral"
            else:
                chandra_str = "Inauspicious (Avoid)"
        except Exception:
            chandra_str = "Neutral"

        personal = {
            "tara_bala": tara_str,
            "chandra_bala": chandra_str
        }

    # 10. All 30 muhurtas
    all_muhur = []
    try:
        m30 = drik.muhurthas(jd, place)
        # m30 returns list of 30 tuples: (name, start_h, end_h) or float hours
        for idx, m_time in enumerate(m30):
            # sometimes m30 is list of tuples or float values
            name = _MUHURTA_NAMES[idx % 30]
            if isinstance(m_time, tuple) and len(m_time) >= 2:
                all_muhur.append({"start": float_hours_to_hhmm(m_time[0]), "end": float_hours_to_hhmm(m_time[1]), "label": name})
    except Exception:
        logger.warning("muhurat_muhurthas_failed", exc_info=True)

    return {
        "date": target_date.strftime("%Y-%m-%d"),
        "sunrise": sr,
        "sunset": ss,
        "moonrise": mr,
        "moonset": ms,
        "panchanga": panchanga,
        "auspicious_muhurtas": auspicious,
        "chogadiya": chogadiya_list,
        "inauspicious_periods": inauspicious,
        "amrita_periods": amrita,
        "panchaka_free": panchaka_free,
        "personal_balam": personal,
        "all_muhurtas": all_muhur,
        # Day-level electional gates referenced by per-activity rule tables
        # (lagna_shuddhi._ACTIVITY_RULES.hard_excludes).
        "is_eclipse_day": _is_eclipse_day(target_date, place),
        "is_adhik_maasa": _is_adhik_maasa(jd, place),
        "degraded": degraded,
    }
