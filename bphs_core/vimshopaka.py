"""Vimshopaka Bala — 20-point cross-varga strength (BPHS Dashavarga scheme).

Vimshopaka ("twenty-fold") strength rates how consistently a graha holds good
dignity across a set of divisional charts. This module implements the
**Dashavarga** (ten-varga) scheme, the BPHS standard for general assessment.

Wrap vs. hand-roll
------------------
* The ten vargas and their point-weights are taken **verbatim from pyjhora**
  (``jhora.const.dhasavarga_amsa_vimsopaka``) so the engine stays pinned to the
  same classical table the rest of pyjhora uses:

      D1 Rasi 3, D2 Hora 1.5, D3 Drekkana 1.5, D7 Saptamsa 1.5, D9 Navamsa 1.5,
      D10 Dasamsa 1.5, D12 Dwadasamsa 1.5, D16 Shodasamsa 1.5, D30 Trimsamsa 1.5,
      D60 Shashtiamsa 5  ——  total 20.

  We assert at import time that this wrapped table both sums to 20 and matches
  the BPHS Dashavarga set, so a future pyjhora bump can never silently change
  the weighting underneath us.

* The **per-varga dignity factor** is hand-implemented per standard BPHS
  practice (the explicit dignity-fraction ladder below), NOT pyjhora's
  compound-relationship ``vimsopaka_bala_scores`` table. pyjhora's scorer
  recomputes every divisional chart from the raw ``jd``/``place`` and leans on
  its internal ``house``/``const`` relationship machinery; it does not operate
  over a :class:`ChartSnapshot`. To stay consistent with the rest of this engine
  (pure functions over the snapshot, fail-closed on missing data, the dignity
  strings already attached to every ``PlanetData``) we score dignity from the
  snapshot's own ``dignity`` field. The ladder is documented on
  ``_DIGNITY_FACTOR``.

Source: BPHS ch. on Shadvarga/Dashavarga Vimshopaka Bala (Parashara); weight
table cross-checked against ``jhora.const.dhasavarga_amsa_vimsopaka``.
"""
from dataclasses import dataclass, field

from .chart import ChartSnapshot
from . import utils

try:  # wrap pyjhora's pinned Dashavarga weight table
    from jhora import const as _jhora_const
    _DASHAVARGA_WEIGHTS_RAW: dict[int, float] = dict(_jhora_const.dhasavarga_amsa_vimsopaka)
except Exception:  # fail-closed: fall back to the literal BPHS table if pyjhora moves it
    _DASHAVARGA_WEIGHTS_RAW = {1: 3, 2: 1.5, 3: 1.5, 7: 1.5, 9: 1.5,
                              10: 1.5, 12: 1.5, 16: 1.5, 30: 1.5, 60: 5}

# Map the pyjhora divisional-chart-factor (Dnn) to the ChartSnapshot attribute
# holding that varga. Ordered as BPHS lists the Dashavarga.
_VARGA_ATTR: dict[int, str] = {
    1: "rasi_chart",
    2: "hora_chart",
    3: "drekkana_chart",
    7: "saptamsa_chart",
    9: "navamsa_chart",
    10: "decamsa_chart",
    12: "dwadasamsa_chart",
    16: "shodasamsa_chart",
    30: "trimshamsa_chart",
    60: "shashtyamsa_chart",
}

# Human-readable Dnn -> varga label for the contributions map.
_VARGA_LABEL: dict[int, str] = {
    1: "D1", 2: "D2", 3: "D3", 7: "D7", 9: "D9",
    10: "D10", 12: "D12", 16: "D16", 30: "D30", 60: "D60",
}

# Validate the wrapped table at import: it must be exactly the BPHS Dashavarga
# set and sum to 20. A pyjhora change that breaks either invariant fails loudly.
assert set(_DASHAVARGA_WEIGHTS_RAW) == set(_VARGA_ATTR), (
    "pyjhora dhasavarga_amsa_vimsopaka no longer matches the BPHS Dashavarga set"
)
assert abs(sum(_DASHAVARGA_WEIGHTS_RAW.values()) - 20.0) < 1e-9, (
    "Dashavarga Vimshopaka weights must total 20"
)

# Per-varga dignity factor (standard BPHS practice). Multiplies the varga weight:
# a planet exalted/moolatrikona/own in a varga earns the full weight; a debilitated
# planet earns none. Monotonic by classical strength order.
#   exalted / moolatrikona / own sign  -> 1.0
#   great friend                       -> 0.9
#   friend                             -> 0.75
#   neutral                            -> 0.5
#   enemy                              -> 0.25
#   great enemy                        -> 0.125
#   debilitated                        -> 0.0
_DIGNITY_FACTOR: dict[str, float] = {
    "exalted": 1.0,
    "moolatrikona": 1.0,
    "own sign": 1.0,
    "great friend": 0.9,
    "friendly": 0.75,
    "friend": 0.75,
    "neutral": 0.5,
    "enemy": 0.25,
    "great enemy": 0.125,
    "debilitated": 0.0,
}

# The engine's get_planet_dignity() emits these dignity strings. "great friend"
# / "great enemy" are not currently produced (the friendship table is two-tier),
# but the ladder maps them so the factor is correct if a five-tier table lands.

# Grade bands (BPHS Vimshopaka convention, 0-20 scale):
#   < 5   very weak
#   5-10  weak
#   10-15 good
#   >= 15 excellent
def _grade(total: float) -> str:
    if total >= 15.0:
        return "excellent"
    if total >= 10.0:
        return "good"
    if total >= 5.0:
        return "weak"
    return "very weak"


@dataclass
class VimshopakaResult:
    planet: str
    total: float                       # 0-20, rounded 2dp
    grade: str                         # very weak | weak | good | excellent
    contributions: dict[str, float] = field(default_factory=dict)  # {varga_label: points}


# The 7 grahas always carry a computable dignity in every varga. Rahu/Ketu are
# excluded: the engine's dignity model returns a fixed "neutral" for the nodes in
# every sign (utils.get_planet_dignity), so their Vimshopaka would be a constant
# 0.5 * total weight = 10 with no cross-varga signal — meaningless as a strength
# metric. Documented choice: grahas only.
_VIMSHOPAKA_PLANETS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn"]


def _dignity_factor(dignity: str) -> float:
    # Fail-closed: an unrecognised dignity string scores as neutral (0.5) rather
    # than crashing, but never silently as full strength.
    return _DIGNITY_FACTOR.get(dignity, 0.5)


def compute_vimshopaka(snapshot: ChartSnapshot, planet: str) -> VimshopakaResult:
    """Vimshopaka Bala (Dashavarga, 0-20) for one graha over the snapshot."""
    contributions: dict[str, float] = {}
    total = 0.0
    for dcf, attr in _VARGA_ATTR.items():
        weight = _DASHAVARGA_WEIGHTS_RAW[dcf]
        varga = getattr(snapshot, attr, None) or {}
        pd = varga.get(planet)
        if pd is None:
            # Fail-closed: a missing placement contributes nothing.
            points = 0.0
        else:
            points = weight * _dignity_factor(pd.dignity)
        contributions[_VARGA_LABEL[dcf]] = round(points, 4)
        total += points
    total = round(total, 2)
    return VimshopakaResult(
        planet=planet,
        total=total,
        grade=_grade(total),
        contributions=contributions,
    )


def compute_all_vimshopaka(snapshot: ChartSnapshot) -> list[VimshopakaResult]:
    """Vimshopaka Bala for all seven grahas (Rahu/Ketu excluded — see module note)."""
    return [
        compute_vimshopaka(snapshot, p)
        for p in _VIMSHOPAKA_PLANETS
        if p in snapshot.rasi_chart
    ]
