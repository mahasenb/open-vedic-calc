import functools
import os
import threading

import swisseph as swe
from jhora.panchanga import drik

EPHE_PATH = os.path.join(os.path.dirname(__file__), "../data/ephe")

# ---------------------------------------------------------------------------
# pyswisseph's state is THREAD-LOCAL, so it must be applied per thread.
#
# pyswisseph 2.10.3.2 ships swisseph's thread-safe (thread-local ``swed``)
# configuration: the ephemeris path and the sidereal/ayanamsa mode are
# per-THREAD, not per-process. Setting them once at import therefore configures
# only the importing thread. Every other thread starts with:
#
#   * no ephemeris path -> swe_calc silently falls back to the built-in Moshier
#     analytical ephemeris. It does NOT raise: it returns a plausible result
#     with SEFLG_MOSEPH set in retflag instead of SEFLG_SWIEPH.
#   * no sidereal mode  -> swisseph's default Fagan/Bradley ayanamsa, which
#     differs from Lahiri by ~0.88 degrees (53 arcmin) — enough to move a
#     nakshatra pada, and a sign near a boundary.
#
# That matters because this service does nearly all of its computing on
# non-importing threads: Starlette's threadpool for the synchronous ``def``
# ``/v1/*`` handlers, and the background scan-job pool in ``app/jobs.py``. The
# mounted Swiss ephemeris data was being bypassed on exactly the paths that
# serve real requests, while ``/healthz``'s ``ephe_loaded`` — a directory
# existence check — reported fine. (Now fixed: ``/healthz`` calls
# ``probe_ephemeris_source()`` below, the same retflag-based detector as the
# accuracy gate, instead of checking whether the directory exists.)
#
# Measured on a CI runner with the data files present (the ``swiss-ephemeris``
# job in .github/workflows/test.yml, which is what surfaced this):
#   main thread                      -> retflag 65602, SEFLG_SWIEPH set
#   worker thread, path not applied  -> retflag 65604, SEFLG_MOSEPH set
#   worker thread, path applied      -> retflag 65602, SEFLG_SWIEPH set
#
# ``_ensure_thread_ephemeris_state()`` applies both settings once per thread and
# ``serialized_ephemeris`` calls it, so every C-library entry point in this
# package is covered by construction rather than by remembering — and
# tests/test_ephemeris_lock.py already pins that every entry point carries that
# decorator.
# ---------------------------------------------------------------------------
_THREAD_EPHEMERIS_STATE = threading.local()


def _ensure_thread_ephemeris_state() -> None:
    """Apply the ephemeris path + Lahiri ayanamsa to the CALLING thread, once.

    Idempotent, and after the first call per thread it costs one attribute
    lookup — so it is safe on every serialized entry point.
    """
    if getattr(_THREAD_EPHEMERIS_STATE, "initialised", False):
        return
    swe.set_ephe_path(EPHE_PATH)
    # Initialize pyjhora ayanamsa mode
    drik.set_ayanamsa_mode('LAHIRI')
    _THREAD_EPHEMERIS_STATE.initialised = True


# Configure the importing thread eagerly, so a direct ``swe.*`` call made
# outside a @serialized_ephemeris entry point (a test, a REPL) behaves as before.
_ensure_thread_ephemeris_state()

# ---------------------------------------------------------------------------
# Process-wide serialization of Swiss-Ephemeris / pyjhora access.
#
# EPHEMERIS_LOCK is a process-wide RLock that every C-library entry point
# holds for the duration of its computation. The path/ayanamsa state it
# guards is THREAD-local (see above) -- pyswisseph's own thread-local
# ``swed`` struct already protects that from a cross-thread race -- so this
# lock is not protecting a shared process-global value. It exists for a
# separate reason: the underlying Swiss Ephemeris C library is not
# documented as safe for concurrent swe_calc/swe_houses calls from multiple
# threads, even once each thread's own state is correctly configured. Two
# thread pools can enter it concurrently in one process: Starlette's
# per-request pool for the synchronous ``def`` route handlers, and the
# background scan-job pool (app/jobs.py). Without serialization the failure
# mode is not necessarily a crash but a silently wrong chart for one of two
# concurrent requests — invisible, and unacceptable for a determinism-first
# engine.
#
# serialized_ephemeris also calls _ensure_thread_ephemeris_state() on EVERY
# invocation, not just once: a thread pool reuses worker threads across
# unrelated requests, so "this thread's first call" recurs for the life of
# the process, not only at startup. Doing that inside the decorator, at
# every entry point, makes the per-thread guarantee structural rather than a
# convention someone has to remember at each new call site.
#
# Every entry point that calls into swisseph/pyjhora must hold this lock
# (apply @serialized_ephemeris) at INNER, millisecond-bounded granularity —
# one chart, one day, one instant, one JD conversion. Never decorate a
# whole scan: a whole-scan hold (seconds to minutes at the API range caps)
# would block every interactive route and the async submit path for the
# scan's remaining duration, reversing the async-scan design's
# "submissions never wait on scan compute" property (see CLAUDE.md). It is
# an RLock so nested entry points — e.g. a per-instant scorer that builds
# a Chart — re-acquire it on the same thread without deadlock. The
# throughput cost of serializing is accepted: these are CPU-bound
# computations on a GIL-bound process, so true parallelism was never
# available; the lock only removes the correctness risk.
# ---------------------------------------------------------------------------
EPHEMERIS_LOCK = threading.RLock()


def serialized_ephemeris(fn):
    """Decorator: run ``fn`` while holding the process-wide ephemeris lock.

    Sets ``_holds_ephemeris_lock`` on the wrapper so the test suite can
    assert every C-library entry point is covered.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with EPHEMERIS_LOCK:
            # Inside the lock: pyswisseph's state is thread-local, so a worker
            # thread must be configured before it computes anything, or it
            # silently runs on the Moshier fallback with a Fagan/Bradley
            # ayanamsa. See _ensure_thread_ephemeris_state's rationale above.
            _ensure_thread_ephemeris_state()
            return fn(*args, **kwargs)

    wrapper._holds_ephemeris_lock = True
    return wrapper


@serialized_ephemeris
def probe_ephemeris_source(
    jd: float | None = None, body: int | None = None
) -> tuple[bool, int]:
    """(swiss_active, retflag) for a sidereal position requesting Swiss data.

    The single canonical Swiss-vs-Moshier detector in this package. A
    directory's existence says nothing about whether swisseph is actually
    reading it: ``os.path.isdir(EPHE_PATH)`` is True for an empty directory
    (exactly what ``mkdir -p data/ephe`` in the Dockerfile leaves behind
    before any volume mount), so a directory-existence check can report
    healthy while every worker thread silently computes on the Moshier
    fallback (CALC-1's original ``/healthz`` defect). This asks swisseph
    directly and reads the FLG_SWIEPH/FLG_MOSEPH bit in its retflag instead
    -- the only reliable detector, because a Swiss request with the data
    files absent still succeeds and returns a plausible result.

    ``body`` defaults to the Sun, which is what every historical caller
    asked for. It is a parameter because the data set is SPLIT across files:
    ``sepl_18.se1`` carries the planets and ``semo_18.se1`` the Moon. A
    Sun-only probe therefore reports FLG_SWIEPH on a data set that is
    missing the Moon file, while the nakshatra, its pada and every dasha in
    the engine quietly come from the fallback. ``app.ephemeris_guard`` asks
    about both.

    ``/healthz`` and ``/source`` (``app/main.py``), the boot guard
    (``app/ephemeris_guard.py``) and the accuracy gate
    (``tests/test_swiss_ephemeris.py``) all call this exact function, so
    there is exactly one classification implementation to keep correct --
    never re-derive ``swiss_active`` from a retflag by hand at a second
    call site.
    """
    if jd is None:
        jd = swe.julday(2000, 1, 1, 12.0)
    if body is None:
        body = swe.SUN
    _values, retflag = swe.calc_ut(jd, body, swe.FLG_SWIEPH | swe.FLG_SIDEREAL)
    swiss_active = bool(retflag & swe.FLG_SWIEPH) and not bool(retflag & swe.FLG_MOSEPH)
    return swiss_active, retflag


SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]

PLANETS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu", "Ketu"]

NAKSHATRAS = [
    "Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra",
    "Punarvasu", "Pushya", "Ashlesha", "Magha", "Purva Phalguni", "Uttara Phalguni",
    "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha", "Jyeshtha",
    "Mula", "Purva Ashadha", "Uttara Ashadha", "Shravana", "Dhanishta", "Shatabhisha",
    "Purva Bhadrapada", "Uttara Bhadrapada", "Revati",
]

SIGN_LORDS: dict[str, str] = {
    "Aries": "Mars", "Taurus": "Venus", "Gemini": "Mercury",
    "Cancer": "Moon", "Leo": "Sun", "Virgo": "Mercury",
    "Libra": "Venus", "Scorpio": "Mars", "Sagittarius": "Jupiter",
    "Capricorn": "Saturn", "Aquarius": "Saturn", "Pisces": "Jupiter",
}

# Classical BPHS dignity tables
_EXALTATION: dict[str, str] = {
    "Sun": "Aries", "Moon": "Taurus", "Mars": "Capricorn",
    "Mercury": "Virgo", "Jupiter": "Cancer", "Venus": "Pisces", "Saturn": "Libra",
}
_DEBILITATION: dict[str, str] = {
    "Sun": "Libra", "Moon": "Scorpio", "Mars": "Cancer",
    "Mercury": "Pisces", "Jupiter": "Capricorn", "Venus": "Virgo", "Saturn": "Aries",
}
_OWN_SIGNS: dict[str, list[str]] = {
    "Sun": ["Leo"], "Moon": ["Cancer"], "Mars": ["Aries", "Scorpio"],
    "Mercury": ["Gemini", "Virgo"], "Jupiter": ["Sagittarius", "Pisces"],
    "Venus": ["Taurus", "Libra"], "Saturn": ["Capricorn", "Aquarius"],
}
_MOOLATRIKONA: dict[str, str] = {
    "Sun": "Leo", "Moon": "Taurus", "Mars": "Aries",
    "Mercury": "Virgo", "Jupiter": "Sagittarius", "Venus": "Libra", "Saturn": "Aquarius",
}
_FRIENDLY: dict[str, list[str]] = {
    "Sun": ["Moon", "Mars", "Jupiter"],
    "Moon": ["Sun", "Mercury"],
    "Mars": ["Sun", "Moon", "Jupiter"],
    "Mercury": ["Sun", "Venus"],
    "Jupiter": ["Sun", "Moon", "Mars"],
    "Venus": ["Mercury", "Saturn"],
    "Saturn": ["Mercury", "Venus"],
}
_ENEMY: dict[str, list[str]] = {
    "Sun": ["Venus", "Saturn"],
    "Moon": [],  # Moon has no natural enemies in the BPHS friendship table
    "Mars": ["Mercury"],
    "Mercury": ["Moon"],
    "Jupiter": ["Mercury", "Venus"],
    "Venus": ["Sun", "Moon"],
    "Saturn": ["Sun", "Moon", "Mars"],
}


def longitude_to_sign_and_degree(longitude: float) -> tuple[str, float]:
    longitude = longitude % 360
    return SIGNS[int(longitude // 30)], longitude % 30


def longitude_to_nakshatra(longitude: float) -> str:
    longitude = longitude % 360
    return NAKSHATRAS[int(longitude / (360 / 27))]


def get_sign_lord(sign: str) -> str:
    return SIGN_LORDS.get(sign, "Unknown")


def get_planet_dignity(planet: str, sign: str) -> str:
    if planet in ("Rahu", "Ketu"):
        return "neutral"
    if _EXALTATION.get(planet) == sign:
        return "exalted"
    if _DEBILITATION.get(planet) == sign:
        return "debilitated"
    if sign == _MOOLATRIKONA.get(planet):
        return "moolatrikona"
    if sign in _OWN_SIGNS.get(planet, []):
        return "own sign"
    sign_lord = get_sign_lord(sign)
    if sign_lord in _FRIENDLY.get(planet, []):
        return "friendly"
    if sign_lord in _ENEMY.get(planet, []):
        return "enemy"
    return "neutral"


_WATER_SIGNS = {"Cancer", "Scorpio", "Pisces"}
_FIRE_SIGNS = {"Leo", "Sagittarius", "Aries"}
_GANDANTA_PADA = 360 / 27 / 4  # one nakshatra pada = 3°20' = 3.3333...°


def check_gandanta(sign: str, degrees: float) -> tuple[bool, float]:
    """Return (is_gandanta, proximity_degrees) for a planet at sign/degrees.

    Gandanta zones are the last nakshatra pada of each water sign and
    first pada of the adjacent fire sign (the water→fire junction points
    in the nakshatra wheel: Cancer/Leo, Scorpio/Sagittarius, Pisces/Aries).

    proximity_degrees is the distance to the exact boundary (0 = exactly on cusp).
    """
    if sign in _WATER_SIGNS:
        proximity = 30.0 - degrees  # distance to end of water sign
        return proximity <= _GANDANTA_PADA, round(proximity, 4)
    if sign in _FIRE_SIGNS:
        proximity = degrees  # distance from start of fire sign
        return proximity <= _GANDANTA_PADA, round(proximity, 4)
    return False, round(min(degrees, 30.0 - degrees), 4)


def make_place(name: str, lat: float, lon: float, tz_offset: float) -> drik.Place:
    return drik.Place(name, lat, lon, tz_offset)


def nakshatra_pada_lord(longitude: float) -> str:
    """Return the Vimshottari Nakshatra Pada Lord for a sidereal longitude.

    In pure BPHS, a Nakshatra (13°20') is divided into 4 equal padas (quarters) 
    of 3°20' each. There are 108 padas in total (27 nakshatras * 4).
    These 108 padas map sequentially to the 12 signs from Aries to Pisces (9 full cycles).
    The Pada Lord is the lord of the Navamsha sign for that pada.
    """
    longitude = longitude % 360
    
    # Each pada is exactly 3°20' (200 minutes = 3.33333333333 degrees)
    # Total padas = 108. 360 / 108 = 10/3
    pada_size = 10.0 / 3.0
    
    # Calculate absolute pada index (0 to 107)
    absolute_pada = int(longitude / pada_size)
    
    # The signs cycle Aries to Pisces (0 to 11) repeatedly.
    # Pada 0 (Ashwini 1) is Aries.
    sign_index = absolute_pada % 12
    
    navamsha_sign = SIGNS[sign_index]
    return get_sign_lord(navamsha_sign)


