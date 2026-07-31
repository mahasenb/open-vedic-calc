"""What kind of deployment this process is — the single source of truth.

Two independent fail-closed guards need the same answer to "is this a real
deployment?": ``app.auth`` (refuse to start without a strong
``CALC_SERVICE_TOKEN``) and ``app.ephemeris_guard`` (refuse to start on the
Moshier fallback). Encoding the environment policy twice would let the two
drift — someone adding an environment name to one list and not the other would
produce a container that enforces its token but silently tolerates a fallback
ephemeris, or the reverse. One definition, two readers.

The default is deliberately ``production``: a container with ``ENVIRONMENT``
unset is a misconfiguration, and every guard built on this must treat it as the
strictest case rather than the most convenient one.
"""
from __future__ import annotations

import os

# Environments where a guard may legitimately stand down: a developer's box, a
# local compose stack, the test suite. Anything else — production, staging, or
# a value nobody recognises — is a deployment that serves real answers.
LENIENT_ENVS = frozenset({"development", "local", "test"})


def environment() -> str:
    """The configured environment name, defaulting fail-secure to production.

    Read at call time rather than cached here, so a caller may decide for
    itself whether to resolve once at import (as both current guards do, to
    stop a mid-process ``os.environ`` mutation flipping a control off) or to
    re-read.
    """
    return os.environ.get("ENVIRONMENT", "production")


def is_real_deployment() -> bool:
    """True when this process serves real answers and every guard must hold."""
    return environment() not in LENIENT_ENVS
