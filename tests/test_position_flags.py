"""The swisseph position-flag word is a DECLARED choice, not an inherited default.

WHY THIS MODULE EXISTS
----------------------
Every planetary longitude this engine serves comes from ``swe.calc_ut(jd, body,
flags)``. That third argument — a bit word — decides the *reference frame* the
number is expressed in: geometric or apparent position, with or without
gravitational deflection, with or without nutation, sidereal or tropical. It is
not a tuning knob; it is part of what the answer means.

Until this module existed the engine stated that word nowhere. It inherited
``jhora.panchanga.drik.PLANET_FLAGS``, which the astronomy library builds at ITS
import time from ``jhora.const.PLANET_POSITIONS_*`` module globals. Measured
2026-07-31, the same repository on two dependency resolves:

    frozen resolve (pyjhora 4.8.6, pyswisseph 2.10.3.2)   drik.PLANET_FLAGS = 66386
    a resolve one patch release later (pyjhora 4.8.7)     drik.PLANET_FLAGS = 65810

a difference of 576 = FLG_NOGDEFL (512) | FLG_NONUT (64), because 4.8.7 flipped
``const.PLANET_POSITIONS_USE_DEFLECTION`` and ``const.PLANET_POSITIONS_USE_NUTATION``
from ``False`` to ``True``. No file in this repository changed. Nothing went red.

WHAT THE THREE GOVERNING FLAGS ARE ACTUALLY WORTH
-------------------------------------------------
Measured with the frozen resolve against the real Swiss ephemeris data files,
scanning 1800-01-01..2400-12-31 (the supported birth-date span, app/schemas.py)
one day at a time — 219511 days x 9 grahas:

  FLG_TRUEPOS (geometric vs apparent position)
      max |declared - apparent| = 0.016817256 deg = 60.5421 arcsec
      (Mercury, JD 2580641.5 = 2353-06-16 00:00 UT)
      per graha: Mercury 60.5421", Venus 44.7711", Mars 38.9693",
      Jupiter 30.0595", Saturn 27.2519", Sun 20.8489", Moon 0.7632",
      Rahu/Ketu 0.0146"

  FLG_NOGDEFL + FLG_NONUT (the 576 that moved between the two resolves)
      max |with - without| = 0.000000000 deg = 0.000000 arcsec, every graha,
      every one of those 219511 days

The second result is the whole point, and it cuts both ways. The dependency's
flag word moved and **no committed golden could ever have caught it**, because
under FLG_TRUEPOS swisseph ignores deflection and aberration entirely, and for a
sidereal request it applies FLG_NONUT of its own accord (visible in the retflag:
a request of FLG_SWIEPH|FLG_SIDEREAL returns 65602, which carries FLG_NONUT that
nobody asked for). So the 576 is inert — but inert *conditionally*, masked by a
different flag that is inherited from the same undeclared source. Clear
FLG_TRUEPOS and the mask lifts: measured at century steps across the same span,
FLG_NOGDEFL is then worth 0.0711 arcsec and FLG_NOABERR would be worth 20.8557
arcsec. Three inherited defaults, coupled, with one of them deciding whether the
other two matter, is not a frame anyone chose.

THE LEVERS
----------
Measured, not read from documentation:

  1. ``jhora.const.PLANET_POSITIONS_GEOCENTRIC / _TRUE / _USE_ABERRATION /
     _USE_DEFLECTION / _USE_NUTATION`` — five module globals read by
     ``jhora.utils.set_flags_for_planet_positions()`` every time it is called.
     This is the *inherited default*, and it is what moved between 4.8.6 and
     4.8.7.
  2. ``drik.PLANET_FLAGS`` — a MUTABLE module global, built from (1) at drik's
     own import time, which is before anything in this package has run. This is
     the word that actually serves: ``drik.sidereal_longitude``,
     ``drik.dhasavarga`` (and therefore ``charts.rasi_chart``) and
     ``drik.planets_in_retrograde`` all read it at call time.
  3. ``jhora.const._TROPICAL_MODE`` — when true, ``drik.sidereal_longitude``
     ignores ``PLANET_FLAGS`` completely and computes with
     ``FLG_SWIEPH + FLG_SPEED``, i.e. tropical. A pinned word that is bypassed
     is not a pin, so the applier verifies this too.

``bphs_core.utils.apply_position_flags`` compares (1) against the declaration and
refuses to start on any disagreement, pins (2) and reads it back, and verifies
(3). It is called at ``bphs_core.utils`` import, and ``bphs_core/__init__.py``
imports utils first, so the pin lands before any sibling module uses drik.

WHAT THIS DECLARATION DOES *NOT* COVER
--------------------------------------
Stated rather than left to be discovered. ``POSITION_FLAGS`` is the *planetary
position* word only:

  * the ascendant and the Bhava-Chalit cusps come from
    ``swe.houses(jd_utc, lat, lon, b"P")`` in ``bphs_core/chart.py``, which takes
    no flag word at all (tropical cusps, with the ayanamsa subtracted by this
    repo — see the Julian Day discipline in CLAUDE.md);
  * sunrise/sunset use pyjhora's separate ``drik.RISE_FLAGS`` / ``drik.SET_FLAGS``
    words, measured 897 / 898 and IDENTICAL in 4.8.6 and 4.8.7, so they did not
    move — but they are equally undeclared, and a separate declaration would be
    needed to pin them.

WHAT IS ASSERTED HERE
---------------------
The positive signal, never the absence of an error:

  * the word is stated as a literal in this repo and decomposes into named
    swisseph constants, so a reader learns the reference frame without running
    anything;
  * each of the three governing flags is individually asserted present, and the
    flags deliberately NOT taken (FLG_NOABERR, FLG_TOPOCTR, FLG_J2000) are
    asserted absent;
  * the astronomy library's own inherited word equals the declaration — this is
    the dependency-bump tripwire, and it is what would have gone red on the
    4.8.6 -> 4.8.7 move that no golden could see;
  * the serving global carries the declaration, and importing the package is
    what puts it there;
  * a REAL served graha and a REAL served Chart equal a direct computation under
    the declared word to float noise, and are 60.54 arcsec away from the same
    computation under an apparent-position word — so these cannot pass if the
    served frame were quietly something else;
  * the applier fails CLOSED on every lever: inherited default moved, builder
    gone, pin refused, tropical bypass live, declaration inconsistent with its
    own decomposition.

Everything here holds under BOTH ephemeris runtimes. Measured at the epoch used
below: the served value matches the direct declared-word computation to
0.000000 arcsec on Swiss data and on the Moshier fallback alike, and the
declared-vs-apparent separation for Mercury is 60.5421 arcsec on Swiss data and
60.5422 arcsec on Moshier.
"""
from __future__ import annotations

import datetime
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import swisseph as swe
from jhora.panchanga import drik

from bphs_core import utils
from bphs_core.chart import Chart, PersonalData

_REPO_ROOT = Path(__file__).resolve().parent.parent

# The word measured in the frozen resolve (pyjhora 4.8.6, pyswisseph 2.10.3.2).
# Repeated here rather than imported so that a change to the declaration has to
# be made in two places, one of which is a test — the same friction the lunar
# node model uses.
_MEASURED_FROZEN_WORD = 66386

# The word the next patch release of the astronomy library produced (pyjhora
# 4.8.7), used below to reproduce the exact regression this module exists to
# catch. 66386 - 65810 = 576 = FLG_NOGDEFL | FLG_NONUT.
_MEASURED_NEXT_RELEASE_WORD = 65810

# Epoch of maximum geometric-vs-apparent separation in the supported span, from
# the scan quoted in the module docstring. Synthetic and deliberately chosen: at
# an arbitrary instant Jupiter's separation is 1.34 arcsec, small enough that a
# sloppy tolerance could not tell the two frames apart. Mercury here is 60.54.
_MAX_TRUEPOS_SEPARATION_UT = (2353, 6, 16, 0, 0)
_SEPARATION_GRAHA = "Mercury"

# The served value and a direct swe.calc_ut under the declared word are the same
# C call with the same arguments, so they agree exactly; measured 0.000000 arcsec
# on both runtimes. 1e-9 deg (3.6 microarcsec) allows for float noise only.
_SAME_FRAME_TOLERANCE_DEG = 1e-9

# Measured separation at the epoch above is 0.016817 deg (60.54 arcsec) on both
# runtimes. A 0.002 deg (7.2 arcsec) floor keeps a 8x margin below it while
# staying 2,000,000x above the match tolerance, so the two assertions can never
# both be satisfied by the same pair of numbers.
_FRAME_SEPARATION_FLOOR_DEG = 0.002


def _separation(a: float, b: float) -> float:
    """Absolute angular separation in degrees, wrap-safe across 0/360."""
    return abs(((a - b + 180.0) % 360.0) - 180.0)


@pytest.fixture
def restore_position_flags():
    """Re-apply the declaration after a test deliberately corrupts a lever.

    The levers are process-global module attributes, not thread-local state, so a
    test that left one corrupted would silently move every planet for the rest of
    the session — including the committed Swiss goldens. Teardown rather than an
    in-test ``finally`` so it also runs when a test fails part-way through.
    """
    yield
    utils.jhora_const._TROPICAL_MODE = False
    utils.apply_position_flags()
    assert drik.PLANET_FLAGS == utils.POSITION_FLAGS


# ---------------------------------------------------------------------------
# The declaration itself
# ---------------------------------------------------------------------------


def test_the_position_flag_word_is_declared_in_this_repo() -> None:
    """The engine states its reference frame as a literal.

    This assertion is the tripwire. Changing the frame is an astronomical
    decision that moves every served longitude by up to 60.54 arcsec; it must be
    impossible to make it by editing one expression, and must instead require
    re-recording this literal and the Swiss goldens in the same reviewed change.
    """
    assert utils.POSITION_FLAGS == _MEASURED_FROZEN_WORD, (
        f"the engine's declared position-flag word changed from "
        f"{_MEASURED_FROZEN_WORD} to {utils.POSITION_FLAGS}. That is the reference "
        "frame every served planetary longitude is expressed in, not an "
        "implementation detail: re-record the Swiss goldens and the rationale in "
        "CLAUDE.md in the same change."
    )


def test_the_declared_word_decomposes_into_the_named_swisseph_constants() -> None:
    """A magic integer would state a number without stating a frame.

    The named decomposition is what makes the declaration readable, and checking
    it against the literal is what would catch a swisseph release that renumbered
    a constant — which would silently change the frame while every name in the
    source still looked right.
    """
    composed = 0
    for bit in utils.POSITION_FLAG_COMPONENTS.values():
        composed |= bit
    assert composed == utils.POSITION_FLAGS, (
        f"the named components OR to {composed}, but the declaration is "
        f"{utils.POSITION_FLAGS}"
    )
    assert utils.POSITION_FLAG_COMPONENTS == {
        "FLG_SWIEPH": swe.FLG_SWIEPH,
        "FLG_SIDEREAL": swe.FLG_SIDEREAL,
        "FLG_TRUEPOS": swe.FLG_TRUEPOS,
        "FLG_NONUT": swe.FLG_NONUT,
        "FLG_NOGDEFL": swe.FLG_NOGDEFL,
        "FLG_SPEED": swe.FLG_SPEED,
    }


def test_each_governing_flag_is_declared_present_or_absent_by_name() -> None:
    """Name the three flags that govern the frame, and the ones deliberately not taken.

    Asserting the absent ones matters as much as the present ones: FLG_NOABERR
    is worth 20.86 arcsec the moment FLG_TRUEPOS is cleared, and FLG_TOPOCTR
    would make every position depend on an observer altitude this service never
    receives.
    """
    assert utils.POSITION_FLAGS & swe.FLG_TRUEPOS, (
        "FLG_TRUEPOS is not declared: the engine would serve APPARENT positions, "
        "up to 60.54 arcsec from the geometric positions it serves today"
    )
    assert utils.POSITION_FLAGS & swe.FLG_NOGDEFL
    assert utils.POSITION_FLAGS & swe.FLG_NONUT
    assert utils.POSITION_FLAGS & swe.FLG_SIDEREAL, (
        "FLG_SIDEREAL is not declared: the engine would serve TROPICAL "
        "longitudes, ~24 deg from the sidereal ones it serves today"
    )
    assert utils.POSITION_FLAGS & swe.FLG_SPEED, (
        "FLG_SPEED is not declared: swisseph returns no daily motion, and "
        "drik.planets_in_retrograde decides retrogression from its sign"
    )
    for absent in ("FLG_NOABERR", "FLG_TOPOCTR", "FLG_J2000", "FLG_HELCTR",
                   "FLG_BARYCTR", "FLG_EQUATORIAL", "FLG_XYZ", "FLG_RADIANS",
                   "FLG_MOSEPH", "FLG_JPLEPH"):
        assert not utils.POSITION_FLAGS & getattr(swe, absent), (
            f"{absent} is set in the declared word — it is not part of the "
            "declared frame"
        )


def test_the_word_renders_itself_by_name_for_a_human() -> None:
    """Error messages must name the bits, not just print two integers.

    A reviewer told "expected 66386, got 65810" has to decompose a bit word by
    hand under time pressure. Told "the library dropped FLG_NOGDEFL|FLG_NONUT",
    they know immediately what moved.
    """
    rendered = utils.describe_position_flags(utils.POSITION_FLAGS)
    assert str(utils.POSITION_FLAGS) in rendered
    for name in utils.POSITION_FLAG_COMPONENTS:
        assert name in rendered, f"{name} missing from {rendered!r}"

    # A bit swisseph does not name must still be reported, never dropped: a word
    # carrying an unknown bit is exactly the case where a silent renderer would
    # make a real difference invisible.
    unknown_bit = 1 << 30
    assert not any(
        getattr(swe, n) == unknown_bit
        for n in dir(swe)
        if n.startswith("FLG_")
    ), "1<<30 is now a named swisseph flag — pick a different unnamed bit"
    rendered_unknown = utils.describe_position_flags(utils.POSITION_FLAGS | unknown_bit)
    assert hex(unknown_bit) in rendered_unknown or str(unknown_bit) in rendered_unknown

    # An empty word must still render as prose. Reached in practice when a
    # library builds 0 (or FLG_TROPICAL, which IS 0) and the applier has to
    # report what it found.
    assert "no named flags" in utils.describe_position_flags(0)


# ---------------------------------------------------------------------------
# The library agrees — the dependency-bump tripwire
# ---------------------------------------------------------------------------


def test_the_astronomy_library_derives_the_same_word_of_its_own_accord() -> None:
    """The inherited default equals the declaration.

    This is the assertion that would have gone red on the 4.8.6 -> 4.8.7 move,
    and the only one that could have: the 576 difference changes no served
    longitude at all (measured 0.000000000 deg over 219511 days x 9 grahas), so
    every golden in this repo stays green through it.
    """
    inherited = utils.jhora_utils.set_flags_for_planet_positions()
    assert inherited == utils.POSITION_FLAGS, (
        f"the astronomy library now derives {utils.describe_position_flags(inherited)} "
        f"of its own accord, but this engine declares "
        f"{utils.describe_position_flags(utils.POSITION_FLAGS)}"
    )


def test_the_serving_global_carries_the_declaration() -> None:
    """``drik.PLANET_FLAGS`` is the word every served position is computed with.

    ``drik.sidereal_longitude``, ``drik.dhasavarga`` (hence ``charts.rasi_chart``)
    and ``drik.planets_in_retrograde`` all read this module global at call time.
    """
    assert drik.PLANET_FLAGS == utils.POSITION_FLAGS, (
        f"the serving global is {utils.describe_position_flags(drik.PLANET_FLAGS)}, "
        f"declared {utils.describe_position_flags(utils.POSITION_FLAGS)}"
    )


def test_the_sidereal_branch_is_the_one_taken() -> None:
    """A pinned word that gets bypassed is not a pin.

    ``drik.sidereal_longitude`` starts ``if const._TROPICAL_MODE: flags =
    swe.FLG_SWIEPH + swe.FLG_SPEED`` — ignoring ``PLANET_FLAGS`` entirely and
    dropping FLG_SIDEREAL, i.e. serving tropical longitudes ~24 deg away.
    """
    assert not getattr(utils.jhora_const, "_TROPICAL_MODE", False)


# ---------------------------------------------------------------------------
# The served value — the assertions that cannot be satisfied by another frame
# ---------------------------------------------------------------------------


def _direct(jd_ut: float, body: int, flags: int) -> float:
    with utils.EPHEMERIS_LOCK:
        values, _retflag = swe.calc_ut(jd_ut, body, flags)
    return values[0] % 360.0


def test_a_served_graha_is_computed_in_the_declared_frame() -> None:
    """The number the caller is given, not the configuration behind it.

    Load-bearing: every other check here is about state, this one is about
    output, so it still holds if the configuration were bypassed. The second
    assertion is what makes it non-vacuous — the served value must be FAR from
    the same body computed with FLG_TRUEPOS cleared.
    """
    year, month, day, hour, minute = _MAX_TRUEPOS_SEPARATION_UT
    jd_ut = swe.julday(year, month, day, hour + minute / 60.0)
    body = utils._GRAHA_SWE_BODY[_SEPARATION_GRAHA]

    served = utils.graha_sidereal_longitude(jd_ut, _SEPARATION_GRAHA)
    declared_frame = _direct(jd_ut, body, utils.POSITION_FLAGS)
    apparent_frame = _direct(jd_ut, body, utils.POSITION_FLAGS & ~swe.FLG_TRUEPOS)

    assert _separation(served, declared_frame) < _SAME_FRAME_TOLERANCE_DEG, (
        f"the served {_SEPARATION_GRAHA} ({served}) is "
        f"{_separation(served, declared_frame):.9f} deg from a direct computation "
        f"under the declared word {utils.POSITION_FLAGS} ({declared_frame}) — the "
        "engine is not serving its declared frame"
    )
    assert _separation(declared_frame, apparent_frame) > _FRAME_SEPARATION_FLOOR_DEG, (
        "the declared (geometric) and apparent frames are not separated at this "
        f"epoch — separation {_separation(declared_frame, apparent_frame):.9f} deg. "
        "This test proves nothing until the epoch is changed to one where they are."
    )
    assert _separation(served, apparent_frame) > _FRAME_SEPARATION_FLOOR_DEG, (
        f"the served {_SEPARATION_GRAHA} matches an APPARENT-position computation "
        "— FLG_TRUEPOS is not reaching the served path"
    )


def test_every_graha_is_served_in_the_declared_frame() -> None:
    """All nine, because a per-body flag divergence would hide behind one spot check."""
    year, month, day, hour, minute = _MAX_TRUEPOS_SEPARATION_UT
    jd_ut = swe.julday(year, month, day, hour + minute / 60.0)
    for graha in (*utils._GRAHA_SWE_BODY, "Ketu"):
        if graha == "Ketu":
            expected = (
                _direct(jd_ut, utils.LUNAR_NODE_BODY, utils.POSITION_FLAGS) + 180.0
            ) % 360.0
        else:
            expected = _direct(
                jd_ut, utils._GRAHA_SWE_BODY[graha], utils.POSITION_FLAGS
            )
        served = utils.graha_sidereal_longitude(jd_ut, graha)
        assert _separation(served, expected) < _SAME_FRAME_TOLERANCE_DEG, (
            f"{graha}: served {served}, declared-frame computation {expected}"
        )


def test_a_served_chart_carries_the_declared_frame() -> None:
    """The whole chart path, not just the single-body boundary function.

    ``Chart`` reaches swisseph through ``charts.rasi_chart -> drik.dhasavarga``,
    a completely different call path from ``utils.graha_sidereal_longitude``. The
    lunar node model proved these paths can disagree: dhasavarga ignores
    ``const._RAHU`` and uses its own literal. Assert the served chart directly.
    """
    year, month, day, hour, minute = _MAX_TRUEPOS_SEPARATION_UT
    person = PersonalData(
        name="synthetic",
        birth_date=datetime.date(year, month, day),
        birth_time=datetime.time(hour, minute, 0),
        birth_place="Synthetic",
        latitude=7.0,
        longitude=80.0,
        timezone_offset_hours=0.0,  # so the local JD below IS the UT JD
    )
    snapshot = Chart(person).snapshot()
    served = snapshot.rasi_chart[_SEPARATION_GRAHA].longitude_abs

    jd_ut = swe.julday(year, month, day, hour + minute / 60.0)
    body = utils._GRAHA_SWE_BODY[_SEPARATION_GRAHA]
    declared_frame = _direct(jd_ut, body, utils.POSITION_FLAGS)
    apparent_frame = _direct(jd_ut, body, utils.POSITION_FLAGS & ~swe.FLG_TRUEPOS)

    assert _separation(served, declared_frame) < _SAME_FRAME_TOLERANCE_DEG, (
        f"a served chart's {_SEPARATION_GRAHA} ({served}) is not the declared "
        f"frame ({declared_frame})"
    )
    assert _separation(served, apparent_frame) > _FRAME_SEPARATION_FLOOR_DEG


# ---------------------------------------------------------------------------
# The applier — mechanism, and fail-closed on every lever
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("constant", "value", "expected_word", "expected_bit"),
    [
        # The exact 4.8.6 -> 4.8.7 change, one flag at a time and then together.
        ("PLANET_POSITIONS_USE_NUTATION", True, 66322, "FLG_NONUT"),
        ("PLANET_POSITIONS_USE_DEFLECTION", True, 65874, "FLG_NOGDEFL"),
        # The one that actually moves a served number.
        ("PLANET_POSITIONS_TRUE", False, 66370, "FLG_TRUEPOS"),
    ],
)
def test_applier_fails_closed_when_an_inherited_default_moves(
    restore_position_flags, constant, value, expected_word, expected_bit
) -> None:
    """The shape of the real threat: a dependency bump moves a library default.

    Reproduced by moving the very constants pyjhora 4.8.7 moved, so this is the
    measured regression rather than an invented one. The engine must refuse to
    start and must name both words — a message that says only "flags changed"
    sends the next reader back to a bit-word decomposition by hand.
    """
    original = getattr(utils.jhora_const, constant)
    setattr(utils.jhora_const, constant, value)
    try:
        assert utils.jhora_utils.set_flags_for_planet_positions() == expected_word, (
            "precondition not established: the library did not produce the word "
            "this regression is about"
        )
        with pytest.raises(RuntimeError) as excinfo:
            utils.apply_position_flags()
    finally:
        setattr(utils.jhora_const, constant, original)

    message = str(excinfo.value)
    assert str(utils.POSITION_FLAGS) in message, (
        f"the failure does not name the declared word: {message}"
    )
    assert str(expected_word) in message, (
        f"the failure does not name the word the library produced: {message}"
    )
    assert expected_bit in message, (
        f"the failure does not name the flag that moved ({expected_bit}): {message}"
    )


def test_applier_fails_closed_on_the_exact_next_release_word(
    restore_position_flags,
) -> None:
    """The measured 4.8.7 word, 65810, reached by moving both constants together.

    Kept separate from the parametrised cases above because this is the one that
    really happened, and because 65810 changes NO served longitude — so this is
    the only mechanism in the repository that can notice it at all.
    """
    originals = {
        name: getattr(utils.jhora_const, name)
        for name in ("PLANET_POSITIONS_USE_NUTATION", "PLANET_POSITIONS_USE_DEFLECTION")
    }
    for name in originals:
        setattr(utils.jhora_const, name, True)
    try:
        assert (
            utils.jhora_utils.set_flags_for_planet_positions()
            == _MEASURED_NEXT_RELEASE_WORD
        ), "precondition not established"
        with pytest.raises(RuntimeError, match=str(_MEASURED_NEXT_RELEASE_WORD)):
            utils.apply_position_flags()
    finally:
        for name, value in originals.items():
            setattr(utils.jhora_const, name, value)


def test_applier_fails_closed_when_the_library_builder_vanishes(
    restore_position_flags,
) -> None:
    """A library that no longer offers the builder means the default is unknowable.

    Carrying on with whatever ``drik.PLANET_FLAGS`` happened to hold is the exact
    silent inheritance this module exists to remove.

    Patched by hand rather than with ``monkeypatch``: pytest undoes ``monkeypatch``
    AFTER the fixtures declared before it, so the ``restore_position_flags``
    teardown would run while the builder was still missing and fail there instead
    of restoring.
    """
    original = utils.jhora_utils.set_flags_for_planet_positions
    del utils.jhora_utils.set_flags_for_planet_positions
    try:
        with pytest.raises(RuntimeError, match="set_flags_for_planet_positions"):
            utils.apply_position_flags()
    finally:
        utils.jhora_utils.set_flags_for_planet_positions = original


def test_applier_repins_the_serving_global(restore_position_flags) -> None:
    """The applier ACTS; it does not merely agree with what it finds.

    Corrupting the global first is what makes this meaningful — an applier that
    had silently become a no-op would leave the corrupted word in place here.
    """
    drik.PLANET_FLAGS = utils.POSITION_FLAGS & ~swe.FLG_TRUEPOS
    assert drik.PLANET_FLAGS != utils.POSITION_FLAGS, "precondition not established"

    utils.apply_position_flags()

    assert drik.PLANET_FLAGS == utils.POSITION_FLAGS


def test_applier_fails_closed_when_the_pin_will_not_take(
    restore_position_flags,
) -> None:
    """Assigning is not evidence the assignment stuck — read it back.

    A future library could make ``PLANET_FLAGS`` a read-only property, or a
    lazily recomputed one. Substituting a stand-in that swallows writes proves
    the applier notices instead of reporting success.
    """
    class _SwallowsWrites:
        def __init__(self, word: int) -> None:
            object.__setattr__(self, "PLANET_FLAGS", word)

        def __setattr__(self, name, value):  # noqa: D105 - deliberately inert
            pass

    original_drik = utils.drik
    utils.drik = _SwallowsWrites(utils.POSITION_FLAGS & ~swe.FLG_TRUEPOS)
    try:
        with pytest.raises(RuntimeError, match="did not take effect"):
            utils.apply_position_flags()
    finally:
        utils.drik = original_drik


def test_applier_fails_closed_in_tropical_mode(restore_position_flags) -> None:
    """The bypass lever: tropical mode ignores the pinned word altogether."""
    utils.jhora_const._TROPICAL_MODE = True
    try:
        with pytest.raises(RuntimeError, match="_TROPICAL_MODE"):
            utils.apply_position_flags()
    finally:
        utils.jhora_const._TROPICAL_MODE = False


def test_applier_fails_closed_when_the_declaration_contradicts_its_components(
    restore_position_flags,
) -> None:
    """A swisseph release that renumbered a constant, simulated.

    Every name in this repo's source would still read correctly while the word
    they compose changed underneath. The applier must not accept a declaration
    that no longer equals its own decomposition.

    Patched by hand rather than with ``monkeypatch`` for the same reason as the
    builder test above: an explicit ``finally`` makes the restore order
    unambiguous instead of a property of fixture argument order, and this is a
    corruption whose teardown the ``restore_position_flags`` fixture would
    otherwise trip over.
    """
    original = utils.POSITION_FLAG_COMPONENTS["FLG_TRUEPOS"]
    utils.POSITION_FLAG_COMPONENTS["FLG_TRUEPOS"] = 1 << 29
    try:
        with pytest.raises(RuntimeError, match="does not equal"):
            utils.apply_position_flags()
    finally:
        utils.POSITION_FLAG_COMPONENTS["FLG_TRUEPOS"] = original


def test_importing_the_package_is_what_pins_the_serving_global() -> None:
    """Import ``bphs_core`` into a fresh interpreter whose drik is already wrong.

    A subprocess, deliberately. In-process the word is already pinned by the time
    any test runs, so nothing in this session can distinguish "the package pins
    it" from "the library happened to build the right word". The child corrupts
    ``drik.PLANET_FLAGS`` BEFORE importing the package; if the import-time
    application were dropped, the child would still report the corrupted word.
    """
    probe = textwrap.dedent(
        """
        import swisseph as swe
        from jhora.panchanga import drik

        drik.PLANET_FLAGS = drik.PLANET_FLAGS & ~swe.FLG_TRUEPOS
        assert not (drik.PLANET_FLAGS & swe.FLG_TRUEPOS), "child failed to corrupt"

        import bphs_core  # noqa: F401  -- the import under test

        print(f"RESULT {drik.PLANET_FLAGS}")
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, (
        f"probe interpreter failed:\nstdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    result = [line for line in completed.stdout.splitlines() if line.startswith("RESULT ")]
    assert result, f"probe produced no RESULT line:\nstdout:\n{completed.stdout}"
    pinned = int(result[-1].split()[1])
    assert pinned == utils.POSITION_FLAGS, (
        f"a fresh interpreter whose drik.PLANET_FLAGS was corrupted to "
        f"{utils.POSITION_FLAGS & ~swe.FLG_TRUEPOS} still had {pinned} after "
        "importing bphs_core — importing the package no longer pins the declared "
        "position-flag word, so the engine is back to inheriting whatever the "
        "library builds"
    )
