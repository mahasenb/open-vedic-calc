"""Package init — deliberately not empty.

Importing ANY module of this package must first pin the engine's declared lunar
node model (``utils.LUNAR_NODE_MODEL``). ``jhora.panchanga.drik`` builds its
mutable ``planet_list`` global from the library's own constants at ITS import
time — i.e. before anything here has declared anything — and several sibling
modules import drik at their own top level. Without this line the model would be
pinned only if ``bphs_core.utils`` happened to be the first of them to load, and
a correctness property resting on import order is not a property at all.

``bphs_core.utils`` applies the declaration at its module level and fails closed
if the library will not carry it, so importing it first is the whole mechanism.
"""
from . import utils  # noqa: F401
