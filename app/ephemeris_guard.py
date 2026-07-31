"""Fail closed at startup when a real deployment is not on the Swiss data.

THE DEFECT THIS PREVENTS
------------------------
swisseph does not raise when its data files are missing. A request that asks
for ``FLG_SWIEPH`` still succeeds, still returns a full, plausible position, and
reports ``FLG_MOSEPH`` in its retflag — the built-in analytical fallback. So a
deployment can compute every chart it ever serves on the wrong engine and look
completely healthy doing it. Measured 2026-07-31 inside the image digest
staging was serving: ``data/ephe`` empty, no ``*.se1`` anywhere on the
filesystem, ``retflag=65604``, and ``/healthz`` answering ``200 {"status":
"ok"}``.

Baking the data into the image (see the ``ephemeris`` stage in ``Dockerfile``)
removes the runtime dependency that allowed this. This guard is the second
layer: it makes the failure impossible to serve THROUGH, whatever a future
bind-mount, volume, or edited base image does to the baked files at runtime. A
container that cannot compute correctly must not answer requests at all.

Same shape, and deliberately so, as ``app.auth``'s ``CALC_SERVICE_TOKEN``
check: a pure policy helper that is unit-testable without process-env
gymnastics, invoked once at import so a misconfiguration fails at startup
rather than on the first chart request. Both read their environment policy from
``app.deployment`` so the two cannot drift.
"""
from __future__ import annotations

import logging
from typing import Callable

import swisseph as swe

from bphs_core import utils

from .deployment import LENIENT_ENVS, environment

logger = logging.getLogger(__name__)

# Checking the Sun alone is not enough. The data set is split across files:
# ``sepl_18.se1`` carries the planets and ``semo_18.se1`` the Moon. An image
# that shipped only the first would report FLG_SWIEPH for the Sun while every
# Moon-derived value — the nakshatra, its pada, and every dasha in the system,
# which is the backbone of the whole engine — came from the fallback. The
# checksum-pinned fetch makes a partial data set unlikely; this makes it
# non-serving.
_REQUIRED_BODIES: tuple[tuple[str, int], ...] = (("Sun", swe.SUN), ("Moon", swe.MOON))

Probe = Callable[..., tuple[bool, int]]


def fallback_bodies(probe: Probe) -> list[tuple[str, int]]:
    """(name, retflag) for every required body NOT served by the Swiss data."""
    fallen_back: list[tuple[str, int]] = []
    for name, body in _REQUIRED_BODIES:
        swiss_active, retflag = probe(body=body)
        if not swiss_active:
            fallen_back.append((name, retflag))
    return fallen_back


def ephemeris_failure_reason(
    *, env: str, probe: Probe = utils.probe_ephemeris_source
) -> str | None:
    """Why this process must refuse to serve, or ``None`` if it may.

    Pure: takes the environment name and the detector, returns a message. The
    ``probe`` seam is what lets the partial-data-set case be tested at all —
    a real Moon-only outage cannot be arranged from a test.
    """
    if env in LENIENT_ENVS:
        return None
    fallen_back = fallback_bodies(probe)
    if not fallen_back:
        return None
    detail = ", ".join(f"{name} (retflag={retflag})" for name, retflag in fallen_back)
    return (
        f"Swiss ephemeris data is NOT in use in ENVIRONMENT={env!r}: swisseph fell "
        f"back to the built-in Moshier analytical engine for {detail}. "
        "The calc-service refuses to serve astrological results computed on the "
        f"fallback outside {sorted(LENIENT_ENVS)}. The data files are baked into "
        "the image (see the `ephemeris` stage in Dockerfile) — this almost always "
        f"means something is shadowing {utils.EPHE_PATH} at runtime, e.g. a volume "
        "or bind-mount over /app/data/ephe. Remove the mount; the image already "
        "carries the data."
    )


# Resolved at import (container start), never on the first request: a
# deployment computing on the fallback is a misconfiguration, not a runtime
# condition that might clear itself.
_FAILURE_REASON: str | None = ephemeris_failure_reason(env=environment())

if _FAILURE_REASON:
    raise RuntimeError(_FAILURE_REASON)

# Not a real deployment AND on the fallback: legitimate (the test suite and the
# default local loop genuinely have no data files), but never silent — a
# developer comparing numbers against a reference deserves to know which engine
# produced them. Emitted only when the fallback is actually in use, so a
# correctly-provisioned local stack stays quiet.
if environment() in LENIENT_ENVS:
    _fallen_back = fallback_bodies(utils.probe_ephemeris_source)
    if _fallen_back:
        logger.critical(
            "Swiss ephemeris data is NOT in use: swisseph is on the built-in "
            "Moshier fallback for %s. Tolerated because ENVIRONMENT=%r is in %r, "
            "but every position this process computes comes from the analytical "
            "approximation, not the Swiss data files. Run "
            "`python ci/fetch_swiss_ephemeris.py` to populate %s.",
            [name for name, _ in _fallen_back],
            environment(),
            sorted(LENIENT_ENVS),
            utils.EPHE_PATH,
        )
