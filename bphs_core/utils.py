import functools
import inspect
import os
import threading

import swisseph as swe
from jhora import const as jhora_const
from jhora.panchanga import drik

# ---------------------------------------------------------------------------
# The lunar node model (Rahu / Ketu) is DECLARED here. It is never inherited.
#
# Rahu and Ketu can be computed from either the TRUE (osculating) lunar node --
# the actual instantaneous intersection of the Moon's orbital plane with the
# ecliptic -- or the MEAN node, a smoothed analytical fit to that intersection.
# Measured with the pinned dependency set (pyswisseph 2.10.3.2, pyjhora 4.8.6)
# against the real Swiss ephemeris data files, over the engine's supported
# birth-date span (1800-01-01..2400-12-31, app/schemas.py) at one-day steps and
# refined at one-hour steps around the worst day:
#
#     max |true - mean| = 1.981465100 deg = 118.8879 arcmin = 7133.2744 arcsec
#                         at 1905-10-10 22:00 UT
#
# and across those 219511 sampled days the two models put Rahu in a different
# nakshatra PADA on 28.85% of them, a different NAKSHATRA on 7.19%, and a
# different RASI on 3.20%. Ketu follows Rahu exactly (Rahu + 180 deg), so the
# choice moves both shadow grahas together. For scale, that is more than twice
# the ~0.883 deg Lahiri-vs-Fagan/Bradley ayanamsa error documented above.
#
# WHY TRUE. Every other graha this engine serves is a true/apparent position
# from a modern ephemeris; the true node is the same class of quantity, so
# serving a smoothed mean element for one graha alone would make a single chart
# internally inconsistent. The Siddhantic mean-motion treatment of Rahu is a
# computational method of its era, not a doctrinal requirement, and this engine
# already declines that era's methods for every visible graha. It is also what
# the engine already served, so declaring it moves no number that any caller has
# already been given.
#
# WHY IT IS DECLARED AT ALL, given it matches the library's current behaviour:
# an inherited default is not a choice, it is an accident that happens to be
# right. A library release that moved it would have moved every Rahu and Ketu by
# up to 1.98 deg with no change in this repository at all. The committed goldens
# would have failed -- but a golden failure with no declared model anywhere reads
# as a regression to chase rather than a deliberate choice to honour. The
# dependency is additionally pinned to an exact version in pyproject.toml so the
# default cannot move underneath this declaration in the first place; this block
# is what makes the choice legible when it does move.
#
# THREE INDEPENDENT LEVERS, and the third is the one that actually serves.
# Verified by measurement, not by reading the config (flip only the first and the
# served chart does not move at all):
#
#   1. ``const._RAHU`` / ``const._use_true_nodes_for_rahu_ketu`` -- read at call
#      time by every ``drik.sidereal_longitude(jd, const._RAHU)`` site.
#   2. ``drik.planet_list`` -- a MUTABLE module global, rebuilt by
#      ``drik.set_planet_list()``; body id -> planet id for the position loops.
#   3. ``drik.dhasavarga``'s own parameter default. This is the lever the chart
#      path uses: ``charts.rasi_chart`` -> ``drik.dhasavarga(jd, place, 1)``,
#      and dhasavarga declares ``set_rahu_ketu_as_true_nodes=True`` as a LITERAL
#      default (not ``None``), so it re-derives planet_list from that literal and
#      never consults lever 1 at all. Setting only the constants therefore leaves
#      every served Rahu unchanged -- a declaration that looked applied and was
#      not. ``apply_lunar_node_model`` below pins 1 and 2 and VERIFIES 3.
#
# Because lever 3 is a library literal this repo cannot set, declaring
# LUNAR_NODE_MODEL = "mean" hard-fails at import rather than silently serving
# true-node charts under a mean-node banner. That is deliberate: switching model
# is real work (routing the chart path through
# ``drik.dhasavarga(..., set_rahu_ketu_as_true_nodes=False)`` instead of
# ``charts.rasi_chart``) plus a deliberate re-recording of the Swiss goldens, and
# a one-word edit here must not be able to look like it did that work.
#
# Changing LUNAR_NODE_MODEL is an astrological model decision, not an
# implementation detail: it invalidates every previously served chart.
# tests/test_lunar_node_model.py is the tripwire that enforces all of the above.
# ---------------------------------------------------------------------------
LUNAR_NODE_MODEL = "true"

# Derived, never independently declared, so the two can never disagree; an
# unknown model name raises KeyError at import rather than serving something
# unintended.
LUNAR_NODE_BODY = {"true": swe.TRUE_NODE, "mean": swe.MEAN_NODE}[LUNAR_NODE_MODEL]


_SERVING_PATH_NODE_PARAMETER = "set_rahu_ketu_as_true_nodes"


def _serving_path_node_default() -> bool | None:
    """The node model ``drik.dhasavarga`` applies when this repo does not pass one.

    ``charts.rasi_chart`` calls ``dhasavarga`` without that argument, so this
    literal -- not ``const._RAHU`` -- decides which node every served chart
    carries. ``None`` means the library defers to the constants, which
    ``apply_lunar_node_model`` has already pinned, and is therefore acceptable.
    """
    parameters = inspect.signature(drik.dhasavarga).parameters
    parameter = parameters.get(_SERVING_PATH_NODE_PARAMETER)
    if parameter is None:
        raise RuntimeError(
            f"drik.dhasavarga no longer takes a {_SERVING_PATH_NODE_PARAMETER!r} "
            "parameter, so this engine can no longer tell which lunar node model "
            "the chart path serves. Refusing to continue: re-establish how the "
            "serving path selects Rahu and Ketu before moving the dependency pin."
        )
    return parameter.default


def apply_lunar_node_model() -> None:
    """Pin the astronomy library to LUNAR_NODE_MODEL, or refuse to run.

    Fail-closed throughout. Every step below is read back after it is applied,
    because calling a setter is not evidence it took effect, and the cost of
    being wrong is up to 1.98 deg on both shadow grahas.

    Pins the two levers this repo can set, and verifies the third it cannot:

    1. ``const.set_node_mode`` rather than assigning ``const._RAHU`` directly,
       because two library-internal names must stay in agreement -- most call
       sites read ``const._RAHU``, while ``set_planet_list`` and others read the
       boolean ``_use_true_nodes_for_rahu_ketu``. A direct assignment would
       desynchronise them and leave different limbs of one chart describing
       different nodes.
    2. ``drik.set_planet_list``, because ``drik.planet_list`` is a mutable
       module global that was built from the constants at drik's own import
       time -- i.e. before this ran.
    3. ``drik.dhasavarga``'s parameter default, which is a library literal this
       repo cannot set and which is what the chart path actually uses. It is
       verified, never assumed: if it ever stops agreeing with the declaration
       the engine refuses to start instead of serving the other model.

    ``const._KETU`` is deliberately NOT verified: the library resolves it to -10
    under the true node and to ``-swe.MEAN_NODE`` -- which is also -10 -- under
    the mean node, so it carries no information about which model is active. It
    is a sentinel that makes the library compute Ketu as Rahu + 180 deg, which
    is why Ketu always follows whichever model Rahu is on.

    Idempotent, and cheap: it sets and inspects module attributes and does not
    touch the ephemeris.
    """
    want_true_node = LUNAR_NODE_MODEL == "true"

    # (1) the library's module-level constants
    setter = getattr(jhora_const, "set_node_mode", None)
    if setter is None:
        raise RuntimeError(
            "the astronomy library no longer exposes jhora.const.set_node_mode, so "
            f"this engine cannot pin its declared lunar node model ({LUNAR_NODE_MODEL} "
            "node). Refusing to continue rather than inheriting whatever the library "
            "defaults to: the true and mean nodes differ by up to 1.98 degrees, which "
            "moves Rahu's and Ketu's nakshatra pada on roughly a third of dates. "
            "Re-establish the declaration against the new library API in the same "
            "change that moves the pin."
        )
    setter(want_true_node)

    applied_body = getattr(jhora_const, "_RAHU", None)
    applied_flag = getattr(jhora_const, "_use_true_nodes_for_rahu_ketu", None)
    if applied_body != LUNAR_NODE_BODY or applied_flag != want_true_node:
        raise RuntimeError(
            f"pinning the declared lunar node model did not take effect: asked the "
            f"astronomy library for the {LUNAR_NODE_MODEL} node (swisseph body "
            f"{LUNAR_NODE_BODY}), but jhora.const now reports _RAHU={applied_body!r} "
            f"and _use_true_nodes_for_rahu_ketu={applied_flag!r}. Refusing to serve "
            "charts whose Rahu and Ketu are on an undeclared model."
        )

    # (2) drik's mutable planet_list global, built before this ran
    drik.set_planet_list(set_rahu_ketu_as_true_nodes=want_true_node)
    listed_body = next(
        (body for body, planet_id in drik.planet_list.items()
         if planet_id == jhora_const.RAHU_ID),
        None,
    )
    if listed_body != LUNAR_NODE_BODY:
        raise RuntimeError(
            f"pinning the declared lunar node model did not take effect in "
            f"drik.planet_list: Rahu is mapped to swisseph body {listed_body!r}, "
            f"expected {LUNAR_NODE_BODY} for the {LUNAR_NODE_MODEL} node."
        )

    # (3) the serving path's own literal, which this repo cannot set
    serving_default = _serving_path_node_default()
    if serving_default is not None and serving_default != want_true_node:
        raise RuntimeError(
            f"this engine declares the {LUNAR_NODE_MODEL} lunar node, but the chart "
            f"path (charts.rasi_chart -> drik.dhasavarga) applies "
            f"{_SERVING_PATH_NODE_PARAMETER}={serving_default!r} of its own accord, "
            "so every served Rahu and Ketu would be on the other model -- up to "
            "1.98 degrees away, a different nakshatra pada on roughly a third of "
            "dates. Refusing to serve charts that contradict the declaration. Either "
            "the dependency changed that default (revert the bump, or route the "
            "chart path through drik.dhasavarga explicitly and re-record the Swiss "
            "goldens), or LUNAR_NODE_MODEL was changed without that routing work."
        )


apply_lunar_node_model()

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

# ---------------------------------------------------------------------------
# The graha-name -> swisseph-body BOUNDARY.
#
# Two integer id spaces coexist around this engine and they are NOT
# interchangeable:
#
#   * pyjhora's planet indices — the order of PLANETS above (Sun 0, Moon 1,
#     Mars 2, Mercury 3, Jupiter 4, Venus 5, Saturn 6, Rahu 7, Ketu 8). This
#     is the space pyjhora's chart OUTPUTS use (``charts.rasi_chart``,
#     ``drik.dhasavarga``, ``drik.planets_in_retrograde`` return these).
#   * swisseph body ids — what ``swe.calc_ut`` and ``drik.sidereal_longitude``
#     CONSUME (Sun 0, Moon 1, Mercury 2, Venus 3, Mars 4, Jupiter 5, Saturn 6,
#     Uranus 7, Neptune 8, the lunar node 10/11). pyjhora's own docstring on
#     ``sidereal_longitude`` is explicit: "The sequence number of 0 to 8 for
#     planets is not followed by swiss ephemeris".
#
# Sun, Moon and Saturn share the same value in both spaces, which is what
# makes the conflation survivable: a spot check of those three looks right
# while every other graha silently becomes a DIFFERENT CELESTIAL BODY —
# index 2 ("Mars") computes Mercury, 3 ("Mercury") computes Venus, 4
# ("Jupiter") computes Mars, 5 ("Venus") computes Jupiter, 7 ("Rahu")
# computes Uranus and 8 ("Ketu") computes Neptune. Measured with the pinned
# dependency set at 2026-07-31 00:00 UT: index 7 returned 40.7495 deg
# (Uranus) where the true node stood at 305.5916 deg.
#
# The rule this boundary enforces: raw integer body ids never cross a module
# edge. ``graha_sidereal_longitude`` below is the ONLY sanctioned route from
# a graha to ``drik.sidereal_longitude``, and it is keyed by graha NAME — a
# key that exists in neither integer space, so neither kind of int can be
# passed to it at all (either raises KeyError immediately). The table is
# written out longhand from explicit ``swe.*`` constants; never rebuild it
# from ``enumerate(PLANETS)``. tests/test_graha_body_ids.py enforces both
# halves: the served longitudes are asserted per-graha against independent
# computations, and an AST scan fails any new ``sidereal_longitude`` /
# ``calc_ut`` call site outside this module.
# ---------------------------------------------------------------------------
_GRAHA_SWE_BODY: dict[str, int] = {
    "Sun": swe.SUN,
    "Moon": swe.MOON,
    "Mars": swe.MARS,
    "Mercury": swe.MERCURY,
    "Jupiter": swe.JUPITER,
    "Venus": swe.VENUS,
    "Saturn": swe.SATURN,
    # The declared lunar node model (see LUNAR_NODE_MODEL above), never a
    # hardcoded 10/11 — the node body is a model DECISION, not a constant.
    "Rahu": LUNAR_NODE_BODY,
}


@serialized_ephemeris
def graha_sidereal_longitude(jd_utc: float, graha: str) -> float:
    """Sidereal (Lahiri) longitude of *graha* at a UTC Julian Day, in [0, 360).

    The single sanctioned boundary between graha names and swisseph body ids
    (rationale above). *jd_utc* must be a true UTC Julian Day —
    ``drik.sidereal_longitude`` requires UTC, unlike pyjhora's place-aware
    functions which take a local-clock JD and subtract the timezone
    themselves (see the Julian Day discipline in CLAUDE.md).

    Ketu is served as exactly Rahu + 180 deg on the declared lunar node
    model. Raises KeyError for anything that is not one of the nine graha
    names — deliberately including every integer, from either id space.
    """
    if graha == "Ketu":
        return (graha_sidereal_longitude(jd_utc, "Rahu") + 180.0) % 360.0
    body = _GRAHA_SWE_BODY.get(graha)
    if body is None:
        raise KeyError(
            f"unknown graha {graha!r}: expected one of "
            f"{[*_GRAHA_SWE_BODY, 'Ketu']}. Integer ids are deliberately "
            "rejected — utils.PLANETS indices and swisseph body ids are "
            "different id spaces (index 2 is Mars in one and Mercury in the "
            "other), and a raw int cannot say which space it belongs to."
        )
    return drik.sidereal_longitude(jd_utc, body)


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


