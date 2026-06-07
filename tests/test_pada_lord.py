import pytest
from bphs_core import utils

def test_nakshatra_pada_lord_ashwini():
    # Ashwini starts at 0°00' Aries (0.0° absolute)
    # The first pada of Ashwini (0 to 3°20' or 0 to 3.333°) is Aries (Mars)
    assert utils.nakshatra_pada_lord(0.0) == "Mars"
    assert utils.nakshatra_pada_lord(1.0) == "Mars"
    assert utils.nakshatra_pada_lord(3.33) == "Mars"
    
    # Second pada of Ashwini (3°20' to 6°40') is Taurus (Venus)
    assert utils.nakshatra_pada_lord(3.34) == "Venus"
    assert utils.nakshatra_pada_lord(6.66) == "Venus"

    # Third pada of Ashwini (6°40' to 10°00') is Gemini (Mercury)
    assert utils.nakshatra_pada_lord(6.67) == "Mercury"
    assert utils.nakshatra_pada_lord(9.99) == "Mercury"

    # Fourth pada of Ashwini (10°00' to 13°20') is Cancer (Moon)
    assert utils.nakshatra_pada_lord(10.0) == "Moon"
    assert utils.nakshatra_pada_lord(13.33) == "Moon"

def test_nakshatra_pada_lord_overflow():
    # Check absolute longitude wrap-around (360° + x)
    assert utils.nakshatra_pada_lord(360.0) == "Mars"
    assert utils.nakshatra_pada_lord(361.0) == "Mars"
    assert utils.nakshatra_pada_lord(363.34) == "Venus"
