"""Route-level contract for a limb that cannot be computed.

``bphs_core.muhurat`` RAISES :class:`~bphs_core.muhurat.MuhurtaLimbError` for
every limb that decides WHICH time can be recommended (project decision
2026-08-17; see ``tests/test_muhurat_limb_failure_modes.py`` for the engine-side
contract). This file pins what the *synchronous* HTTP route does with that
exception: it must surface a STRUCTURED JSON error naming the failed limb and a
stable machine-readable code — never an opaque 500, and never a 200 masking the
failure behind an empty/partial muhurat list.

The computation is monkeypatched to RAISE (a legitimate failure simulation, per
this repo's testing rule — never to invent a success shape), so the assertion is
about the route's error envelope and is independent of the ephemeris runtime.
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.main import app
from bphs_core import muhurat as muhurat_mod
from tests.conftest import SAMPLE_A

# raise_server_exceptions=False so the CURRENT (pre-fix) behaviour is observable
# as the opaque 500 it is, rather than being re-raised into the test body. Once
# the handler exists it converts the exception into a real response, which this
# same client then returns.
client = TestClient(
    app,
    headers={"X-Calc-Service-Token": "test"},
    raise_server_exceptions=False,
)

_BASE_REQUEST = {
    **SAMPLE_A,
    "start_date": "2026-05-26",
    "end_date": "2026-05-28",
}

# The stable machine-readable code the wire contract promises the caller.
_EXPECTED_CODE = "muhurat_limb_error"


@pytest.mark.parametrize("limb", ["sunrise", "tithi"])
def test_muhurat_sync_route_surfaces_limb_error_as_structured_json(monkeypatch, limb):
    """A raised ``MuhurtaLimbError`` becomes a 422 structured error on ``/v1/muhurat``.

    Parametrised across two distinct limbs so a handler that hard-codes a single
    limb name (or returns a generic body) fails for at least one case: the limb
    NAME the engine raised must appear in the served body.
    """
    target = dt.date(2026, 5, 26)

    def _raise_limb(*args, **kwargs):
        raise muhurat_mod.MuhurtaLimbError(limb, target, "could not be computed")

    monkeypatch.setattr(muhurat_mod, "compute_muhurat_for_day", _raise_limb)

    r = client.post("/v1/muhurat", json=_BASE_REQUEST)

    # Not an opaque 500, and not a 200 masking the failure.
    assert r.status_code == 422, r.text

    body = r.json()
    detail = body["detail"]
    # Stable, machine-readable discriminator the caller can branch on.
    assert detail["code"] == _EXPECTED_CODE
    # The failed limb is named — this is the whole point of the change.
    assert detail["limb"] == limb
    assert detail["target_date"] == target.strftime("%Y-%m-%d")
    # The limb name must be present in the served payload, not swallowed behind a
    # generic error string.
    assert limb in r.text
