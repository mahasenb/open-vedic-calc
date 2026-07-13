"""FR-MED-22 — the auto-generated docs/schema endpoints must not be reachable
without the service token.

Every business route is gated by the ``require_token`` dependency, but that
dependency is attached per-route (``dependencies=AUTH``), not app-wide. FastAPI
wires ``/docs``, ``/redoc``, and ``/openapi.json`` directly on the ``FastAPI``
app object, bypassing that per-route gate entirely -- so leaving them enabled
would let anyone with network reach pull the full API schema (field names,
bounds, response shapes) without presenting a token. The fix disables them at
construction time (``docs_url=None, redoc_url=None, openapi_url=None``);
``/healthz`` must remain reachable (it is intentionally public).
"""
import os

from fastapi.testclient import TestClient

os.environ.setdefault("CALC_SERVICE_TOKEN", "test")
os.environ.setdefault("PUBLIC_SOURCE_URL", "https://example.com")

from app.main import app

# No X-Calc-Service-Token header — these endpoints must be unreachable even
# to an anonymous caller, which is the whole point of the finding.
anon_client = TestClient(app)


def test_openapi_json_is_not_found():
    r = anon_client.get("/openapi.json")
    assert r.status_code == 404


def test_docs_is_not_found():
    r = anon_client.get("/docs")
    assert r.status_code == 404


def test_redoc_is_not_found():
    r = anon_client.get("/redoc")
    assert r.status_code == 404


def test_healthz_still_reachable_without_token():
    # /healthz is intentionally public (liveness probe) -- disabling the docs
    # endpoints must not collaterally affect it.
    r = anon_client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
