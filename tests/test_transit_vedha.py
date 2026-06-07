"""Classical gochara-vedha veto logic.

These exercise the pure table/exemption logic with synthetic placements (Moon in
Aries, so house-from-Moon = sign index + 1). The bindu-magnitude signal and full
endpoint wiring are covered by the /v1/transits endpoint test, which needs the
real chart engine.
"""
from types import SimpleNamespace

from bphs_core.transits import compute_gochara_vedha


def _snapshot(moon_sign: str):
    return SimpleNamespace(rasi_chart={"Moon": SimpleNamespace(sign=moon_sign)})


def _transits(**by_graha: str):
    return {g: SimpleNamespace(planet=g, sign=s) for g, s in by_graha.items()}


def test_vedha_blocks_favourable_transit():
    # Mars favourable in the 3rd (Gemini); its vedha house 12 (Pisces) is occupied
    # by Jupiter — not an exempt pair — so the favourable result is neutralised.
    result = compute_gochara_vedha(_snapshot("Aries"), _transits(Mars="Gemini", Jupiter="Pisces"))
    assert len(result) == 1
    v = result[0]
    assert (v.blocked_planet, v.blocking_planet) == ("Mars", "Jupiter")
    assert (v.blocked_house, v.vedha_house) == (3, 12)
    assert v.neutralised is True


def test_no_vedha_when_obstructing_house_empty():
    # Mars favourable in the 3rd, but nothing occupies the 12th → no obstruction.
    assert compute_gochara_vedha(_snapshot("Aries"), _transits(Mars="Gemini")) == []


def test_exempt_pair_records_but_does_not_neutralise():
    # Sun favourable in the 11th (Aquarius); vedha house 5 (Leo) holds Saturn.
    # Sun-Saturn are mutually exempt → recorded with neutralised=False.
    result = compute_gochara_vedha(_snapshot("Aries"), _transits(Sun="Aquarius", Saturn="Leo"))
    assert len(result) == 1
    v = result[0]
    assert (v.blocked_planet, v.blocking_planet) == ("Sun", "Saturn")
    assert v.neutralised is False


def test_moon_mercury_pair_is_exempt():
    # Transit Moon favourable in the 1st; its vedha house 5 (Leo) holds Mercury.
    # Moon-Mercury is the other classical exempt pair → neutralised=False.
    result = compute_gochara_vedha(_snapshot("Aries"), _transits(Moon="Aries", Mercury="Leo"))
    assert len(result) == 1
    v = result[0]
    assert {v.blocked_planet, v.blocking_planet} == {"Moon", "Mercury"}
    assert v.neutralised is False


def test_unfavourable_house_is_not_evaluated():
    # Mars in the 2nd (Taurus) is not a favourable house for Mars → nothing to veto.
    assert compute_gochara_vedha(_snapshot("Aries"), _transits(Mars="Taurus", Jupiter="Aries")) == []
