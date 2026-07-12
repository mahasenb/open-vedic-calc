"""Async job variant for the scan-class electional endpoints (CR-4).

CR-4 (design-fitness review): the calc-service is a single, GIL-bound worker
and the scan-class endpoints (`/v1/muhurat/lagna-shuddhi`,
`/v1/muhurat/family-lagna-shuddhi`) are synchronous -- a wide date-range scan
occupies a request thread (and the connection) for the full compute, starving
concurrent interactive chart requests. This module adds and exercises the new
ADDITIVE async submit/poll variant end-to-end, and separately pins the
existing synchronous endpoints' output so this change cannot regress them.

Three things this file proves:
  1. The synchronous endpoints are byte-identical to their pre-existing
     behaviour (golden snapshot captured from the untouched code at the base
     commit, before this change -- see tests/fixtures/lagna_shuddhi_*_golden.json).
  2. Submitting a scan asynchronously returns a job id immediately (the HTTP
     call does not block for the scan's duration).
  3. Polling a job observes a non-terminal state (pending/running) before it
     transitions to done, and the completed result matches the synchronous
     endpoint's output for the identical input (the async path is a thin
     wrapper around the same computation, not a reimplementation).
"""
import json
import os
import threading
import time as time_mod
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("CALC_SERVICE_TOKEN", "test")
os.environ.setdefault("PUBLIC_SOURCE_URL", "https://example.com")

import app.main as main_mod
from app.main import app
from tests.conftest import SAMPLE_A, SAMPLE_B

client = TestClient(app, headers={"X-Calc-Service-Token": "test"})

_FIXTURES = Path(__file__).parent / "fixtures"
_SOLO_GOLDEN = json.loads((_FIXTURES / "lagna_shuddhi_solo_golden.json").read_text(encoding="utf-8"))
_FAMILY_GOLDEN = json.loads((_FIXTURES / "lagna_shuddhi_family_golden.json").read_text(encoding="utf-8"))

SOLO_REQ = {
    **SAMPLE_A,
    "start_date": "2026-05-26",
    "end_date": "2026-05-26",
    "activity_category": "generic",
    "step_seconds": 3600,
}

FAMILY_REQ = {
    "members": [SAMPLE_A, SAMPLE_B],
    "start_date": "2026-05-26",
    "end_date": "2026-05-26",
    "activity_category": "generic",
    "step_seconds": 3600,
}


# ---------------------------------------------------------------------------
# 1. Existing synchronous endpoints must stay byte-identical.
# ---------------------------------------------------------------------------

def test_lagna_shuddhi_sync_endpoint_byte_identical_to_before_change():
    r = client.post("/v1/muhurat/lagna-shuddhi", json=SOLO_REQ)
    assert r.status_code == 200
    assert r.json() == _SOLO_GOLDEN


def test_family_lagna_shuddhi_sync_endpoint_byte_identical_to_before_change():
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=FAMILY_REQ)
    assert r.status_code == 200
    assert r.json() == _FAMILY_GOLDEN


# ---------------------------------------------------------------------------
# 2. Async submit returns a job id immediately (202, no compute inline).
# ---------------------------------------------------------------------------

def test_lagna_shuddhi_async_submit_returns_job_id_immediately():
    r = client.post("/v1/muhurat/lagna-shuddhi/async", json=SOLO_REQ)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"]
    assert body["status"] == "pending"


def test_family_lagna_shuddhi_async_submit_returns_job_id_immediately():
    r = client.post("/v1/muhurat/family-lagna-shuddhi/async", json=FAMILY_REQ)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"]
    assert body["status"] == "pending"


def test_lagna_shuddhi_async_submit_rejects_same_range_limit_as_sync():
    """The async submit endpoint enforces the same DoS-bounding date-range and
    step_seconds guards as the synchronous endpoint -- the job store must
    never be handed an unbounded scan."""
    req = {**SOLO_REQ, "start_date": "2026-01-01", "end_date": "2027-06-01"}
    r = client.post("/v1/muhurat/lagna-shuddhi/async", json=req)
    assert r.status_code == 422


def test_lagna_shuddhi_async_submit_rejects_reversed_range():
    req = {**SOLO_REQ, "start_date": "2026-05-27", "end_date": "2026-05-26"}
    r = client.post("/v1/muhurat/lagna-shuddhi/async", json=req)
    assert r.status_code == 422


def test_family_lagna_shuddhi_async_submit_rejects_same_range_limit_as_sync():
    req = {**FAMILY_REQ, "start_date": "2026-01-01", "end_date": "2027-06-01"}
    r = client.post("/v1/muhurat/family-lagna-shuddhi/async", json=req)
    assert r.status_code == 422


def test_family_lagna_shuddhi_async_submit_rejects_reversed_range():
    req = {**FAMILY_REQ, "start_date": "2026-05-27", "end_date": "2026-05-26"}
    r = client.post("/v1/muhurat/family-lagna-shuddhi/async", json=req)
    assert r.status_code == 422


def test_family_lagna_shuddhi_async_submit_rejects_too_few_members():
    req = {**FAMILY_REQ, "members": [SAMPLE_A]}
    r = client.post("/v1/muhurat/family-lagna-shuddhi/async", json=req)
    assert r.status_code == 422


def test_family_lagna_shuddhi_async_submit_rejects_too_many_members():
    req = {**FAMILY_REQ, "members": [SAMPLE_A, SAMPLE_B] * 4}  # 8 > MAX_FAMILY_MEMBERS(6)
    r = client.post("/v1/muhurat/family-lagna-shuddhi/async", json=req)
    assert r.status_code == 422


def test_unknown_job_id_is_404():
    r = client.get("/v1/muhurat/lagna-shuddhi/jobs/does-not-exist")
    assert r.status_code == 404
    r2 = client.get("/v1/muhurat/family-lagna-shuddhi/jobs/does-not-exist")
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# 3. Poll transitions pending -> done, and the result matches the sync path.
# ---------------------------------------------------------------------------

def _poll_until_terminal(url: str, timeout_s: float = 30, interval_s: float = 0.05):
    deadline = time_mod.time() + timeout_s
    last = None
    while time_mod.time() < deadline:
        r = client.get(url)
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] in ("done", "error"):
            return last
        time_mod.sleep(interval_s)
    raise AssertionError(f"job did not reach a terminal state within {timeout_s}s: {last}")


def test_lagna_shuddhi_async_poll_observes_pending_before_done(monkeypatch):
    """Deterministic pending -> done transition: the underlying scan is
    monkeypatched to block until released, so a poll taken while it is
    blocked must observe a non-terminal status regardless of how fast the
    real computation would otherwise finish."""
    release = threading.Event()
    started = threading.Event()
    real_scan = main_mod.lagna_shuddhi_mod.scan_lagna_shuddhi

    def _blocking_scan(**kwargs):
        started.set()
        release.wait(timeout=5)
        return real_scan(**kwargs)

    monkeypatch.setattr(main_mod.lagna_shuddhi_mod, "scan_lagna_shuddhi", _blocking_scan)

    submit = client.post("/v1/muhurat/lagna-shuddhi/async", json=SOLO_REQ)
    assert submit.status_code == 202
    job_id = submit.json()["job_id"]

    assert started.wait(timeout=5), "background job never started running"
    r = client.get(f"/v1/muhurat/lagna-shuddhi/jobs/{job_id}")
    assert r.status_code == 200
    assert r.json()["status"] in ("pending", "running"), r.json()

    release.set()
    body = _poll_until_terminal(f"/v1/muhurat/lagna-shuddhi/jobs/{job_id}")
    assert body["status"] == "done", body
    assert body["result"] == _SOLO_GOLDEN


def test_lagna_shuddhi_async_completed_result_matches_sync_endpoint():
    submit = client.post("/v1/muhurat/lagna-shuddhi/async", json=SOLO_REQ)
    job_id = submit.json()["job_id"]
    body = _poll_until_terminal(f"/v1/muhurat/lagna-shuddhi/jobs/{job_id}")
    assert body["status"] == "done"
    assert body["result"] == _SOLO_GOLDEN


def test_family_lagna_shuddhi_async_poll_observes_pending_before_done(monkeypatch):
    release = threading.Event()
    started = threading.Event()
    real_scan = main_mod.lagna_shuddhi_mod.scan_family_lagna_shuddhi

    def _blocking_scan(**kwargs):
        started.set()
        release.wait(timeout=5)
        return real_scan(**kwargs)

    monkeypatch.setattr(main_mod.lagna_shuddhi_mod, "scan_family_lagna_shuddhi", _blocking_scan)

    submit = client.post("/v1/muhurat/family-lagna-shuddhi/async", json=FAMILY_REQ)
    assert submit.status_code == 202
    job_id = submit.json()["job_id"]

    assert started.wait(timeout=5), "background job never started running"
    r = client.get(f"/v1/muhurat/family-lagna-shuddhi/jobs/{job_id}")
    assert r.status_code == 200
    assert r.json()["status"] in ("pending", "running"), r.json()

    release.set()
    body = _poll_until_terminal(f"/v1/muhurat/family-lagna-shuddhi/jobs/{job_id}")
    assert body["status"] == "done", body
    assert body["result"] == _FAMILY_GOLDEN


def test_family_lagna_shuddhi_async_completed_result_matches_sync_endpoint():
    submit = client.post("/v1/muhurat/family-lagna-shuddhi/async", json=FAMILY_REQ)
    job_id = submit.json()["job_id"]
    body = _poll_until_terminal(f"/v1/muhurat/family-lagna-shuddhi/jobs/{job_id}")
    assert body["status"] == "done"
    assert body["result"] == _FAMILY_GOLDEN


# ---------------------------------------------------------------------------
# A job that raises is reported as an error, not a silently-dropped 200/none.
# ---------------------------------------------------------------------------

def test_lagna_shuddhi_async_job_error_is_reported_not_swallowed(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("synthetic scan failure")

    monkeypatch.setattr(main_mod.lagna_shuddhi_mod, "scan_lagna_shuddhi", _boom)

    submit = client.post("/v1/muhurat/lagna-shuddhi/async", json=SOLO_REQ)
    assert submit.status_code == 202
    job_id = submit.json()["job_id"]
    body = _poll_until_terminal(f"/v1/muhurat/lagna-shuddhi/jobs/{job_id}")
    assert body["status"] == "error", body
    assert "synthetic scan failure" in body["error"]
    assert body["result"] is None
