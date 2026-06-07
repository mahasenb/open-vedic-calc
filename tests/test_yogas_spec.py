from bphs_core.chart import ChartSnapshot, PlanetData, PersonalData
from bphs_core.yogas import _compute_yoga_strength, detect_all_yogas
import datetime

def mock_chart(planets_data: dict[str, dict]) -> ChartSnapshot:
    # A simple mock for ChartSnapshot that only populates rasi_chart
    # and minimal lagna/house cusps for yoga detection tests.
    
    person = PersonalData(
        name="Test", birth_date=datetime.date(2000, 1, 1),
        birth_time=datetime.time(12, 0), birth_place="Test",
        latitude=0.0, longitude=0.0, timezone_offset_hours=0.0
    )
    rasi = {}
    for p, d in planets_data.items():
        rasi[p] = PlanetData(
            planet=p,
            degrees=d.get("degrees", 0.0),
            sign=d.get("sign", "Aries"),
            nakshatra="Ashwini",
            house=d.get("house", 1),
            dignity=d.get("dignity", "neutral"),
            conjunctions=[],
            aspects=[],
            is_retrograde=d.get("is_retrograde", False),
        )
    
    return ChartSnapshot(
        person=person,
        lagna="Aries",
        lagna_lord="Mars",
        ayanamsa_value=0.0,
        house_cusps=[0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0, 210.0, 240.0, 270.0, 300.0, 330.0],
        rasi_chart=rasi,
        hora_chart={},
        drekkana_chart={},
        navamsa_chart={},
        decamsa_chart={},
        dwadasamsa_chart={},
        chaturvimsa_chart={},
        trimshamsa_chart={},
        saptamsa_chart={},
        shashtyamsa_chart={}
    )

def test_yoga_strength_strong():
    chart = mock_chart({
        "Jupiter": {"dignity": "exalted"},
        "Moon": {"dignity": "own sign"}
    })
    # Both planets are strong
    assert _compute_yoga_strength(chart, ["Jupiter", "Moon"]) == "strong"

def test_yoga_strength_mild():
    chart = mock_chart({
        "Jupiter": {"dignity": "exalted"},
        "Moon": {"dignity": "debilitated"}
    })
    # One planet is debilitated, should downgrade the entire yoga to mild
    assert _compute_yoga_strength(chart, ["Jupiter", "Moon"]) == "mild"

def test_yoga_strength_moderate():
    chart = mock_chart({
        "Jupiter": {"dignity": "neutral"},
        "Moon": {"dignity": "own sign"}
    })
    # Mixed neutral and strong, should be moderate
    assert _compute_yoga_strength(chart, ["Jupiter", "Moon"]) == "moderate"

def test_viparita_raja_yoga():
    # 6th lord (Mercury) in 8th house (Scorpio)
    chart = mock_chart({
        "Mercury": {"sign": "Scorpio", "house": 8, "dignity": "neutral"}
    })
    yogas = detect_all_yogas(chart)
    harsha = [y for y in yogas if y.name == "Harsha Yoga"]
    assert len(harsha) == 1
    assert harsha[0].is_viparita_raja is True
    assert "Mercury" in harsha[0].activating_lords
    assert harsha[0].strength == "moderate"

def test_panchamahapurusha_yoga():
    # Mars in Aries (1st house/kendra)
    chart = mock_chart({
        "Mars": {"sign": "Aries", "house": 1, "dignity": "own sign"}
    })
    yogas = detect_all_yogas(chart)
    ruchaka = [y for y in yogas if y.name == "Ruchaka Yoga"]
    assert len(ruchaka) == 1
    assert "Mars" in ruchaka[0].activating_lords
    assert ruchaka[0].strength == "strong"

def test_dhana_yoga():
    # Aries lagna. 2nd lord (Venus) and 11th lord (Saturn) conjunct in 2nd house (Taurus)
    chart = mock_chart({
        "Venus": {"sign": "Taurus", "house": 2, "dignity": "own sign"},
        "Saturn": {"sign": "Taurus", "house": 2, "dignity": "friend"}
    })
    yogas = detect_all_yogas(chart)
    dhana = [y for y in yogas if y.name == "Dhana Yoga"]
    assert len(dhana) == 1
    assert set(dhana[0].activating_lords) == {"Venus", "Saturn"}
    assert dhana[0].strength == "moderate" # friend + own sign = moderate

def test_parivartana_yoga():
    # Aries lagna. Mars (lord of Aries) in Taurus, Venus (lord of Taurus) in Aries
    chart = mock_chart({
        "Mars": {"sign": "Taurus", "house": 2, "dignity": "neutral"},
        "Venus": {"sign": "Aries", "house": 1, "dignity": "neutral"}
    })
    yogas = detect_all_yogas(chart)
    parivartana = [y for y in yogas if y.name == "Parivartana Yoga"]
    assert len(parivartana) == 1
    assert set(parivartana[0].activating_lords) == {"Mars", "Venus"}
    assert parivartana[0].strength == "moderate"
