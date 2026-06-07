import os
import swisseph as swe
from jhora.panchanga import drik

EPHE_PATH = os.path.join(os.path.dirname(__file__), "../data/ephe")
swe.set_ephe_path(EPHE_PATH)

# Initialize pyjhora ayanamsa mode
drik.set_ayanamsa_mode('LAHIRI')

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
    "Moon": ["None"],
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


