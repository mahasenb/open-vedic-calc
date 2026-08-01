"""Package init — deliberately not empty.

Importing ANY module of this package must first pin the engine's two declared
astronomical choices: the lunar node model (``utils.LUNAR_NODE_MODEL``) and the
swisseph position-flag word (``utils.POSITION_FLAGS``). ``jhora.panchanga.drik``
builds BOTH of the mutable globals those live in — ``planet_list`` and
``PLANET_FLAGS`` — from the library's own constants at ITS import time, i.e.
before anything here has declared anything, and several sibling modules import
drik at their own top level. Without this line the declarations would be applied
only if ``bphs_core.utils`` happened to be the first of them to load, and a
correctness property resting on import order is not a property at all.

``bphs_core.utils`` applies both declarations at its module level and fails
closed if the library will not carry them, so importing it first is the whole
mechanism.
"""
from . import utils  # noqa: F401
