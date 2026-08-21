"""The sunrise/sunset convention word is a DECLARED choice, not an inherited default.

WHY THIS MODULE EXISTS
----------------------
Every panchanga and electional limb this engine serves is anchored to the day
boundary. ``drik.sunrise``/``drik.sunset``/``drik.moonrise``/``drik.moonset`` each
call ``swe.rise_trans(jd, body, geopos=..., rsmi=<word>, flags=PLANET_FLAGS)``.
That ``rsmi`` argument -- a bit word DISTINCT from the ephemeris flag word
``POSITION_FLAGS`` pins -- decides *what counts as sunrise*: whether the Sun's
disc CENTRE or its lower limb touches the horizon, whether that horizon is the
true (geometric) one or the apparent (refracted) one, and whether the rise or the
set instant is wanted. It is not a tuning knob; it is part of what "sunrise"
means, and a day whose start moves drags the tithi, nakshatra, yoga, karana, the
thirty muhurtas and the whole muhurat/lagna-shuddhi scan with it.

Until this module existed the engine stated that word nowhere. It inherited
``jhora.panchanga.drik.RISE_FLAGS`` / ``SET_FLAGS``, which the astronomy library
builds at ITS import time from ``jhora.utils.set_flags_for_rise_set()``, which in
turn reads three ``jhora.const.RISE_SET_*`` module globals. Measured on pyjhora
4.8.6 and re-measured unchanged on the now-pinned 4.8.7 (pyswisseph 2.10.3.2
throughout):

    RISE_FLAGS = 897    SET_FLAGS = 898

and -- unlike the position-flag word, which moved 66386 -> 65810 on the 4.8.6 ->
4.8.7 bump -- these are IDENTICAL in 4.8.6 and 4.8.7, so they have not moved. But
"has not moved" is not "declared": the three ``const`` globals that build them are
exactly the shape of default that 4.8.7 flipped for the position word, and a
release that flips ``RISE_SET_USE_REFRACTION`` or
``RISE_SET_USE_DISC_CENTER_FOR_RISING`` would move every served sunrise with no
change in this repository and nothing here to notice it.

WHAT THE CONVENTION IS, AND WHAT IT IS WORTH
--------------------------------------------
The declared convention is swisseph's ``BIT_HINDU_RISING`` (896): the Sun's disc
CENTRE at the TRUE, unrefracted horizon (geocentric, no ecliptic-latitude
correction) -- the "madhya-bimba" sunrise of the classical Vedic (BPHS /
Surya-Siddhanta) panchanga. It is what ``drik.sunrise``'s docstring states
("centre of disc at horizon") and what drik's own source comment calls
"geometric, i.e. true sunrise/set, so refraction is not considered".

It is a deliberate choice, not the only defensible one, and the difference is
observable. Measured at Colombo (6.9271 N, 79.8612 E), 2024-03-20: clearing
``BIT_NO_REFRACTION`` from the declared word -- i.e. computing the APPARENT,
refracted horizon a secular ephemeris would report -- moves sunrise by

    06:18:01 (declared, geometric) -> 06:15:33 (refracted) = 147.99 seconds

So the convention is load-bearing: a quietly-refracted engine would serve a
sunrise nearly two and a half minutes early, and every limb measured from it
would inherit that shift.

THE LEVERS
----------
TWO, not three -- there is no runtime bypass here analogous to the position
word's ``_TROPICAL_MODE``. Measured, not read from documentation:

  1. ``jhora.const.RISE_SET_HINDU_RISING`` / ``_USE_REFRACTION`` /
     ``_USE_DISC_CENTER_FOR_RISING`` -- three module globals read by
     ``jhora.utils.set_flags_for_rise_set()`` every time it is called. This is
     the INHERITED DEFAULT. Flipping each one moves the word to a distinct value:
     hindu-rising off -> 769/770, refraction on -> 385/386, disc-bottom ->
     9089/9090.
  2. ``drik.RISE_FLAGS`` / ``drik.SET_FLAGS`` -- two MUTABLE module globals, built
     from (1) at drik's own import time, which is before anything in this package
     has run. These are the words that actually serve: ``drik.sunrise`` passes
     ``rsmi=RISE_FLAGS``, ``drik.sunset`` ``rsmi=SET_FLAGS``, and the two lunar
     equivalents likewise.

``bphs_core.utils.apply_rise_set_flags`` compares (1) against the declaration and
refuses to start on any disagreement, then pins (2) and reads both back. It is
called at ``bphs_core.utils`` import, after the position word, and
``bphs_core/__init__.py`` imports utils first, so the pins land before any
sibling module uses drik.

WHAT THIS DECLARATION DOES *NOT* COVER
--------------------------------------
Stated rather than left to be discovered. This is the sunrise/sunset ``rsmi``
convention word only. The ephemeris frame the same ``rise_trans`` call uses for
the Sun's position (its ``flags`` argument) is ``drik.PLANET_FLAGS``, already
pinned by ``apply_position_flags`` -- see ``tests/test_position_flags.py``. drik's
unrelated module global ``_rise_flags`` (an ephemeris word, ``BIT_HINDU_RISING |
FLG_TRUEPOS | FLG_SPEED``) is NOT on the served sunrise/sunset path and is out of
scope here.

WHAT IS ASSERTED HERE
---------------------
The positive signal, never the absence of an error:

  * the two words are stated as literals in this repo and decompose into named
    swisseph constants, so a reader learns the convention without running
    anything;
  * each governing flag is asserted present, and the flag deliberately NOT taken
    (BIT_DISC_BOTTOM, the lower-limb alternative) is asserted absent; rise and set
    are asserted to differ in the direction bit ALONE;
  * the astronomy library's own inherited words equal the declaration -- the
    dependency-bump tripwire, the thing that would go red on a 4.8.7-shaped move
    that no golden could see;
  * the serving globals carry the declaration, and importing the package is what
    puts them there;
  * a directly-computed sunrise under the served word matches the declared word
    and is 148 seconds away from the refracted convention -- so this cannot pass
    if the served convention were quietly something else;
  * the applier fails CLOSED on every lever: an inherited default moved, the
    builder gone, the pin refused, the declaration inconsistent with its own
    decomposition.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import swisseph as swe
from jhora import utils as jhora_utils
from jhora.panchanga import drik
from jhora.panchanga.drik import Place

from bphs_core import utils

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The words measured in the frozen resolve (pyjhora 4.8.7, pyswisseph 2.10.3.2);
# identical on the preceding 4.8.6, so the bump did not move them.
# Repeated here rather than imported so that a change to the declaration has to be
# made in two places, one of which is a test -- the same friction the position
# word and the lunar node model use.
_MEASURED_RISE_FLAGS = 897
_MEASURED_SET_FLAGS = 898

# The word each of the three inherited-default levers produces when flipped, and
# the bit fragment the applier's error message must surface for it. Measured, not
# hand-derived: flipping the const global and reading the library builder's output
# back. hindu-rising off drops the geocentric-no-ecliptic-latitude bit (128),
# which this swisseph build does not export a name for, so the message reports it
# as an explicit unnamed-bit hex rather than dropping it.
_LEVER_MOVES = [
    ("RISE_SET_HINDU_RISING", False, 769, 770, "0x80"),
    ("RISE_SET_USE_REFRACTION", True, 385, 386, "BIT_NO_REFRACTION"),
    ("RISE_SET_USE_DISC_CENTER_FOR_RISING", False, 9089, 9090, "BIT_DISC_BOTTOM"),
]

# A fixture spread for the behaviour-preservation check: equatorial, mid-latitude
# both hemispheres, and a high latitude. Synthetic, non-personal.
# Explicit elevation=0 so the fixtures never reach drik's elevation lookup and are
# fully deterministic offline.
_PLACES = [
    Place("Colombo", 6.9271, 79.8612, 5.5, 0.0),
    Place("London", 51.5074, -0.1278, 0.0, 0.0),
    Place("Sydney", -33.8688, 151.2093, 11.0, 0.0),
    Place("NewYork", 40.7128, -74.0060, -5.0, 0.0),
]
_PLACE_IDS = [pl.name for pl in _PLACES]
_FIXTURE_UT = (2024, 3, 20)

# rise_trans under the same rsmi word is the same C call with the same arguments,
# so it agrees exactly; a 1e-6 day (~0.086 s) tolerance allows for float noise
# only. drik.sunrise round-trips the instant through whole-second dms formatting,
# so the served string is compared at a looser 2 s.
_SAME_WORD_TOLERANCE_DAYS = 1e-6
_SERVED_ROUNDTRIP_TOLERANCE_SEC = 2.0

# Measured separation between the declared (geometric) and refracted conventions
# is 147.99 s at the Colombo fixture. A 60 s floor keeps a 2.4x margin below it
# while staying far above the same-word tolerance, so a declared-vs-refracted pair
# can never satisfy both assertions.
_CONVENTION_SEPARATION_FLOOR_SEC = 60.0


def _fixture_jd() -> tuple[float, float]:
    """The fixture instant as (local-clock jd, UT jd of that calendar day's start).

    These are exactly the two Julian days ``drik.sunrise`` derives internally, so a
    direct computation and the served path can be compared with matching references.
    """
    y, m, d = _FIXTURE_UT
    jd = jhora_utils.julian_day_number((y, m, d), (6, 0, 0))
    yy, mm, dd, _ = drik.jd_to_gregorian(jd)
    jd_utc = jhora_utils.gregorian_to_jd(drik.Date(yy, mm, dd))
    return jd, jd_utc


def _direct_rise_jd(place: Place, rsmi: int, *, body: int = swe.SUN) -> float:
    """The UT Julian day of the body's rise under an explicit rsmi word.

    Reproduces exactly what ``drik.sunrise`` hands to swisseph -- same geopos,
    same ``flags=drik.PLANET_FLAGS`` -- but with the rsmi word named by the caller
    instead of read from the module global, so a test can compare the served
    convention against a chosen alternative.
    """
    _, jd_utc = _fixture_jd()
    _, lat, lon, tz = place
    ele = place.elevation
    result = swe.rise_trans(
        jd_utc - tz / 24, body, geopos=(lon, lat, ele), rsmi=rsmi, flags=drik.PLANET_FLAGS
    )
    return result[1][0]


def _direct_rise_local_hours(place: Place, rsmi: int) -> float:
    """The direct rise instant as local clock hours, the same quantity
    ``drik.sunrise(...)[0]`` returns (``(rise_jd_ut - jd_utc) * 24 + tz``)."""
    _, jd_utc = _fixture_jd()
    _, _, _, tz = place
    return (_direct_rise_jd(place, rsmi) - jd_utc) * 24 + tz


@pytest.fixture
def restore_rise_set_flags():
    """Re-apply the declaration after a test deliberately corrupts a lever.

    The levers are process-global module attributes, not thread-local state, so a
    test that left one corrupted would silently move every served day boundary for
    the rest of the session. Teardown rather than an in-test ``finally`` so it also
    runs when a test fails part-way through.
    """
    yield
    utils.apply_rise_set_flags()
    assert drik.RISE_FLAGS == utils.RISE_FLAGS
    assert drik.SET_FLAGS == utils.SET_FLAGS


# ---------------------------------------------------------------------------
# The declaration itself
# ---------------------------------------------------------------------------


def test_the_rise_set_words_are_declared_in_this_repo() -> None:
    """The engine states its sunrise convention as two literals.

    This assertion is the tripwire. Changing the convention is an astronomical
    decision that moves every served day boundary (147.99 s to the refracted
    horizon at the Colombo fixture); it must be impossible to make by editing one
    expression, and must instead require re-recording these literals and the
    electional goldens in the same reviewed change.
    """
    assert utils.RISE_FLAGS == _MEASURED_RISE_FLAGS, (
        f"the engine's declared sunrise word changed from {_MEASURED_RISE_FLAGS} to "
        f"{utils.RISE_FLAGS}. That is the day boundary every panchanga limb is "
        "measured from, not an implementation detail: re-record the electional "
        "goldens and the rationale in CLAUDE.md in the same change."
    )
    assert utils.SET_FLAGS == _MEASURED_SET_FLAGS, (
        f"the engine's declared sunset word changed from {_MEASURED_SET_FLAGS} to "
        f"{utils.SET_FLAGS}."
    )


def test_the_declared_words_decompose_into_named_swisseph_constants() -> None:
    """Two magic integers would state numbers without stating a convention.

    The named decomposition is what makes the declaration readable, and checking
    it against the literals is what would catch a swisseph release that renumbered
    a constant -- which would silently change the convention while every name in
    the source still looked right.
    """
    convention = 0
    for bit in utils.RISE_SET_CONVENTION_COMPONENTS.values():
        convention |= bit
    assert convention | swe.CALC_RISE == utils.RISE_FLAGS, (
        f"the convention components OR to {convention}, and with CALC_RISE that is "
        f"{convention | swe.CALC_RISE}, but the declared rise word is {utils.RISE_FLAGS}"
    )
    assert convention | swe.CALC_SET == utils.SET_FLAGS, (
        f"the convention components OR to {convention}, and with CALC_SET that is "
        f"{convention | swe.CALC_SET}, but the declared set word is {utils.SET_FLAGS}"
    )
    assert utils.RISE_SET_CONVENTION_COMPONENTS == {
        "BIT_HINDU_RISING": swe.BIT_HINDU_RISING,
        "BIT_DISC_CENTER": swe.BIT_DISC_CENTER,
        "BIT_NO_REFRACTION": swe.BIT_NO_REFRACTION,
    }
    assert utils.RISE_SET_DIRECTION_COMPONENTS == {
        "CALC_RISE": swe.CALC_RISE,
        "CALC_SET": swe.CALC_SET,
    }


def test_each_governing_flag_is_declared_present_or_absent_by_name() -> None:
    """Name the flags that govern the convention, and the one deliberately not taken.

    Asserting the absent one matters as much as the present ones: BIT_DISC_BOTTOM
    is the lower-limb convention a secular almanac uses, and taking it would move
    the served day boundary the engine measures every muhurta from.
    """
    for word in (utils.RISE_FLAGS, utils.SET_FLAGS):
        assert word & swe.BIT_HINDU_RISING == swe.BIT_HINDU_RISING, (
            "BIT_HINDU_RISING is not fully set: the engine would not be serving the "
            "geometric disc-centre convention it declares"
        )
        assert word & swe.BIT_DISC_CENTER, (
            "BIT_DISC_CENTER is not set: the engine would not measure sunrise from "
            "the Sun's disc centre"
        )
        assert word & swe.BIT_NO_REFRACTION, (
            "BIT_NO_REFRACTION is not set: the engine would serve the APPARENT, "
            "refracted horizon -- 147.99 s from the geometric sunrise it serves today"
        )
        assert not word & swe.BIT_DISC_BOTTOM, (
            "BIT_DISC_BOTTOM is set: the engine would measure from the Sun's lower "
            "limb, not the disc centre it declares"
        )
        for absent in ("CALC_MTRANSIT", "CALC_ITRANSIT"):
            assert not word & getattr(swe, absent), (
                f"{absent} is set: the rise/set words request a transit, not a "
                "horizon crossing"
            )

    assert utils.RISE_FLAGS & swe.CALC_RISE, "the rise word must request the rising instant"
    assert not utils.RISE_FLAGS & swe.CALC_SET, "the rise word must not request the setting instant"
    assert utils.SET_FLAGS & swe.CALC_SET, "the set word must request the setting instant"
    assert not utils.SET_FLAGS & swe.CALC_RISE, "the set word must not request the rising instant"


def test_rise_and_set_differ_only_in_the_direction_bit() -> None:
    """Rise and set are the same convention, opposite horizon crossings.

    If they diverged in any bit other than CALC_RISE/CALC_SET the engine would be
    measuring the start and end of the day under different definitions of the
    horizon -- a day whose two ends do not agree on what the horizon is.
    """
    convention_bits = ~(swe.CALC_RISE | swe.CALC_SET)
    assert utils.RISE_FLAGS & convention_bits == utils.SET_FLAGS & convention_bits, (
        f"rise {utils.describe_rise_set_flags(utils.RISE_FLAGS)} and set "
        f"{utils.describe_rise_set_flags(utils.SET_FLAGS)} disagree outside the "
        "direction bit"
    )
    assert utils.RISE_FLAGS ^ utils.SET_FLAGS == swe.CALC_RISE ^ swe.CALC_SET


def test_the_words_render_themselves_by_name_for_a_human() -> None:
    """Error messages must name the bits, not just print two integers.

    A reviewer told "expected 897, got 385" has to decompose a bit word by hand;
    told "the library dropped BIT_NO_REFRACTION" they know immediately that the
    horizon model moved from geometric to refracted.
    """
    for word, direction in ((utils.RISE_FLAGS, "CALC_RISE"), (utils.SET_FLAGS, "CALC_SET")):
        rendered = utils.describe_rise_set_flags(word)
        assert str(word) in rendered
        assert "BIT_HINDU_RISING" in rendered, f"BIT_HINDU_RISING missing from {rendered!r}"
        assert direction in rendered, f"{direction} missing from {rendered!r}"

    # A bit swisseph does not name must still be reported, never dropped.
    unknown_bit = 1 << 30
    rendered_unknown = utils.describe_rise_set_flags(utils.RISE_FLAGS | unknown_bit)
    assert hex(unknown_bit) in rendered_unknown, (
        f"an unnamed bit was silently dropped from {rendered_unknown!r}"
    )


# ---------------------------------------------------------------------------
# The dependency-bump tripwire and the serving globals
# ---------------------------------------------------------------------------


def test_the_library_inherited_words_equal_the_declaration() -> None:
    """The astronomy library's own words match what this engine declares.

    This is the dependency-bump tripwire: it is what would go red on a
    4.8.7-shaped move that flips a RISE_SET_* default, which no electional golden
    could see because it moves a time by a couple of minutes without failing any
    fixed-value assertion tuned looser than that.
    """
    assert jhora_utils.set_flags_for_rise_set(flags_for_rise=True) == utils.RISE_FLAGS
    assert jhora_utils.set_flags_for_rise_set(flags_for_rise=False) == utils.SET_FLAGS


def test_the_serving_globals_carry_the_declared_words() -> None:
    """drik's mutable rsmi globals hold the declaration, not whatever it built."""
    assert drik.RISE_FLAGS == utils.RISE_FLAGS
    assert drik.SET_FLAGS == utils.SET_FLAGS


@pytest.mark.parametrize(
    "constant, value, expected_rise, expected_set, expected_fragment",
    _LEVER_MOVES,
)
def test_applier_fails_closed_when_an_inherited_default_moves(
    restore_rise_set_flags, constant, value, expected_rise, expected_set, expected_fragment
) -> None:
    """The shape of the real threat: a dependency bump moves a library default.

    Reproduced by moving the very ``const`` globals a release would move, so this
    is a measured regression rather than an invented one. The engine must refuse
    to start and must name both words -- a message that says only "flags changed"
    sends the next reader back to a bit-word decomposition by hand.
    """
    original = getattr(utils.jhora_const, constant)
    setattr(utils.jhora_const, constant, value)
    try:
        assert jhora_utils.set_flags_for_rise_set(flags_for_rise=True) == expected_rise, (
            "precondition not established: the library did not produce the rise word "
            "this regression is about"
        )
        assert jhora_utils.set_flags_for_rise_set(flags_for_rise=False) == expected_set
        with pytest.raises(RuntimeError) as excinfo:
            utils.apply_rise_set_flags()
    finally:
        setattr(utils.jhora_const, constant, original)

    message = str(excinfo.value)
    assert str(utils.RISE_FLAGS) in message, (
        f"the failure does not name the declared word: {message}"
    )
    assert str(expected_rise) in message, (
        f"the failure does not name the word the library produced: {message}"
    )
    assert expected_fragment in message, (
        f"the failure does not surface the flag that moved ({expected_fragment}): {message}"
    )


def test_applier_fails_closed_when_the_library_builder_vanishes(
    restore_rise_set_flags,
) -> None:
    """A library that no longer offers the builder means the default is unknowable.

    Carrying on with whatever ``drik.RISE_FLAGS`` happened to hold is the exact
    silent inheritance this module exists to remove. Patched by hand rather than
    with ``monkeypatch`` so the restore order is unambiguous.
    """
    original = jhora_utils.set_flags_for_rise_set
    del jhora_utils.set_flags_for_rise_set
    try:
        with pytest.raises(RuntimeError, match="set_flags_for_rise_set"):
            utils.apply_rise_set_flags()
    finally:
        jhora_utils.set_flags_for_rise_set = original


def test_applier_repins_the_serving_globals(restore_rise_set_flags) -> None:
    """The applier ACTS; it does not merely agree with what it finds.

    Corrupting the globals first is what makes this meaningful -- an applier that
    had silently become a no-op would leave the corrupted words in place here.
    """
    drik.RISE_FLAGS = utils.RISE_FLAGS & ~swe.BIT_NO_REFRACTION
    drik.SET_FLAGS = utils.SET_FLAGS & ~swe.BIT_NO_REFRACTION
    assert drik.RISE_FLAGS != utils.RISE_FLAGS, "precondition not established"

    utils.apply_rise_set_flags()

    assert drik.RISE_FLAGS == utils.RISE_FLAGS
    assert drik.SET_FLAGS == utils.SET_FLAGS


def test_applier_fails_closed_when_the_pin_will_not_take(
    restore_rise_set_flags,
) -> None:
    """Assigning is not evidence the assignment stuck -- read it back.

    A future library could make ``RISE_FLAGS`` a read-only property, or a lazily
    recomputed one. Substituting a stand-in that swallows writes proves the applier
    notices instead of reporting success.
    """
    class _SwallowsWrites:
        def __init__(self) -> None:
            object.__setattr__(self, "RISE_FLAGS", utils.RISE_FLAGS & ~swe.BIT_NO_REFRACTION)
            object.__setattr__(self, "SET_FLAGS", utils.SET_FLAGS & ~swe.BIT_NO_REFRACTION)

        def __setattr__(self, name, value):  # noqa: D105 - deliberately inert
            pass

    original_drik = utils.drik
    utils.drik = _SwallowsWrites()
    try:
        with pytest.raises(RuntimeError, match="did not take effect"):
            utils.apply_rise_set_flags()
    finally:
        utils.drik = original_drik


def test_applier_fails_closed_when_the_declaration_contradicts_its_components(
    restore_rise_set_flags,
) -> None:
    """A swisseph release that renumbered a constant, simulated.

    Every name in this repo's source would still read correctly while the word
    they compose changed underneath. The applier must not accept a declaration
    that no longer equals its own decomposition.
    """
    original = utils.RISE_SET_CONVENTION_COMPONENTS["BIT_NO_REFRACTION"]
    utils.RISE_SET_CONVENTION_COMPONENTS["BIT_NO_REFRACTION"] = 1 << 29
    try:
        with pytest.raises(RuntimeError, match="do not equal"):
            utils.apply_rise_set_flags()
    finally:
        utils.RISE_SET_CONVENTION_COMPONENTS["BIT_NO_REFRACTION"] = original


def test_importing_the_package_is_what_pins_the_serving_globals() -> None:
    """Import ``bphs_core`` into a fresh interpreter whose drik is already wrong.

    A subprocess, deliberately. In-process the words are already pinned by the time
    any test runs, so nothing in this session can distinguish "the package pins
    them" from "the library happened to build the right words". The child corrupts
    ``drik.RISE_FLAGS`` BEFORE importing the package; if the import-time application
    were dropped, the child would still report the corrupted word.
    """
    probe = (
        "import swisseph as swe\n"
        "from jhora.panchanga import drik\n"
        "drik.RISE_FLAGS = drik.RISE_FLAGS & ~swe.BIT_NO_REFRACTION\n"
        "drik.SET_FLAGS = drik.SET_FLAGS & ~swe.BIT_NO_REFRACTION\n"
        "assert not (drik.RISE_FLAGS & swe.BIT_NO_REFRACTION), 'child failed to corrupt'\n"
        "import bphs_core  # noqa: F401  -- the import under test\n"
        "print(f'RESULT {drik.RISE_FLAGS} {drik.SET_FLAGS}')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300, encoding="utf-8", errors="replace",
    )
    assert completed.returncode == 0, (
        f"probe interpreter failed:\nstdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    result = [line for line in completed.stdout.splitlines() if line.startswith("RESULT ")]
    assert result, f"probe produced no RESULT line:\nstdout:\n{completed.stdout}"
    _, pinned_rise, pinned_set = result[-1].split()
    assert int(pinned_rise) == utils.RISE_FLAGS and int(pinned_set) == utils.SET_FLAGS, (
        f"a fresh interpreter whose drik rsmi globals were corrupted still had "
        f"{pinned_rise}/{pinned_set} after importing bphs_core -- importing the "
        "package no longer pins the declared sunrise/sunset words, so the engine is "
        "back to inheriting whatever the library builds"
    )


# ---------------------------------------------------------------------------
# Behaviour preservation: the served convention is the declared one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("place", _PLACES, ids=_PLACE_IDS)
def test_served_sunrise_uses_the_declared_convention_not_the_refracted_one(place) -> None:
    """A directly-computed sunrise under the SERVED word matches the declaration,
    and is a measurable margin away from the refracted convention.

    Pinning is behaviour-preserving: the served word IS the declared word, so a
    rise computed under ``drik.RISE_FLAGS`` equals one computed under the declared
    literal to float noise. The second assertion is what makes the first non-
    tautological -- the same computation on the refracted horizon (declared word
    minus BIT_NO_REFRACTION) lands 148 s earlier, so these two could not both hold
    if the served convention were quietly the refracted one.
    """
    served_word = drik.RISE_FLAGS
    assert served_word == utils.RISE_FLAGS, "the serving global is not the declared word"

    rise_served = _direct_rise_jd(place, served_word)
    rise_declared = _direct_rise_jd(place, utils.RISE_FLAGS)
    assert abs(rise_served - rise_declared) < _SAME_WORD_TOLERANCE_DAYS

    refracted_word = utils.RISE_FLAGS & ~swe.BIT_NO_REFRACTION
    rise_refracted = _direct_rise_jd(place, refracted_word)
    separation_sec = abs(rise_served - rise_refracted) * 86400.0
    assert separation_sec > _CONVENTION_SEPARATION_FLOOR_SEC, (
        f"served sunrise at {place.name} is only {separation_sec:.2f} s from the "
        "refracted convention -- too close to prove the served word is the "
        "geometric disc-centre one this engine declares"
    )


@pytest.mark.parametrize("place", _PLACES, ids=_PLACE_IDS)
def test_the_served_sunrise_path_reads_the_pinned_word(place) -> None:
    """``drik.sunrise`` -- the function muhurat.py actually calls -- serves the
    instant the declared word produces.

    ``drik.sunrise`` reads ``drik.RISE_FLAGS`` at call time; its local-time result
    ``[0]`` is compared to the direct computation at a 2 s tolerance (float noise
    only -- both derive from the same raw rise instant). This closes the loop from
    the served API down to the pinned rsmi word.
    """
    jd, _ = _fixture_jd()
    served_local_hours = drik.sunrise(jd, place)[0]
    direct_local_hours = _direct_rise_local_hours(place, utils.RISE_FLAGS)
    delta_sec = abs(served_local_hours - direct_local_hours) * 3600.0
    assert delta_sec < _SERVED_ROUNDTRIP_TOLERANCE_SEC, (
        f"drik.sunrise at {place.name} served an instant {delta_sec:.2f} s from the "
        "direct computation under the declared word"
    )
