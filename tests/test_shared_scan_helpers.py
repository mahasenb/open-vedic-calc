"""Sync and async scan endpoints share ONE validation + assembly pipeline.

Previously each async submit handler duplicated ~90 lines of date-range /
member-count validation and raw-dict -> response-model assembly from its
synchronous twin (an explicit textually-untouched choice at the time). The
parity tests in tests/test_async_scan_jobs.py reduce but cannot structurally
prevent one-sided drift: a guard added to only one of the four handlers has
no mechanism forcing a matching change in its twin.

These tests pin the extraction: both the sync endpoint and the async submit
route go through the same ``_prepare_lagna_shuddhi`` /
``_prepare_family_lagna_shuddhi`` helper, so a validation or assembly change
lands in one place and applies to both by construction.
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("CALC_SERVICE_TOKEN", "test")
os.environ.setdefault("PUBLIC_SOURCE_URL", "https://example.com")

import app.main as main_mod
from app.main import app
from app.schemas import LagnaShuddhiResponse, FamilyLagnaShuddhiResponse
from tests.conftest import SAMPLE_A, SAMPLE_B

client = TestClient(app, headers={"X-Calc-Service-Token": "test"})

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


def _fake_solo_response() -> LagnaShuddhiResponse:
    return LagnaShuddhiResponse(
        best_instant=None, best_window=None, top_samples=[],
        clearance_summary=None, alternatives=[],
    )


def _fake_family_response() -> FamilyLagnaShuddhiResponse:
    return FamilyLagnaShuddhiResponse(
        instant=None, best_window=None, score=0.0, score_100=0,
        band="Avoid", per_member=[], consensus_quality="strict",
        compromised_members=[], clearance_summary=None, alternatives=[],
    )


def test_sync_solo_endpoint_goes_through_shared_helper(monkeypatch):
    calls: list[object] = []

    def _fake_prepare(req):
        calls.append(req)
        return _fake_solo_response

    monkeypatch.setattr(main_mod, "_prepare_lagna_shuddhi", _fake_prepare)
    r = client.post("/v1/muhurat/lagna-shuddhi", json=SOLO_REQ)
    assert r.status_code == 200
    assert len(calls) == 1, "sync endpoint must delegate to _prepare_lagna_shuddhi"


def test_async_solo_submit_goes_through_shared_helper(monkeypatch):
    calls: list[object] = []

    def _fake_prepare(req):
        calls.append(req)
        return _fake_solo_response

    monkeypatch.setattr(main_mod, "_prepare_lagna_shuddhi", _fake_prepare)
    r = client.post("/v1/muhurat/lagna-shuddhi/async", json=SOLO_REQ)
    assert r.status_code == 202
    assert len(calls) == 1, "async submit must delegate to _prepare_lagna_shuddhi"


def test_sync_family_endpoint_goes_through_shared_helper(monkeypatch):
    calls: list[object] = []

    def _fake_prepare(req):
        calls.append(req)
        return _fake_family_response

    monkeypatch.setattr(main_mod, "_prepare_family_lagna_shuddhi", _fake_prepare)
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=FAMILY_REQ)
    assert r.status_code == 200
    assert len(calls) == 1, (
        "sync family endpoint must delegate to _prepare_family_lagna_shuddhi"
    )


def test_async_family_submit_goes_through_shared_helper(monkeypatch):
    calls: list[object] = []

    def _fake_prepare(req):
        calls.append(req)
        return _fake_family_response

    monkeypatch.setattr(main_mod, "_prepare_family_lagna_shuddhi", _fake_prepare)
    r = client.post("/v1/muhurat/family-lagna-shuddhi/async", json=FAMILY_REQ)
    assert r.status_code == 202
    assert len(calls) == 1, (
        "async family submit must delegate to _prepare_family_lagna_shuddhi"
    )


def test_validation_rejections_still_happen_at_submit_time():
    """Validation must run when the request arrives (422 to the caller), not
    inside the background job — for BOTH members of each pair."""
    bad_range = {**SOLO_REQ, "start_date": "2026-06-02", "end_date": "2026-06-01"}
    assert client.post("/v1/muhurat/lagna-shuddhi", json=bad_range).status_code == 422
    assert client.post("/v1/muhurat/lagna-shuddhi/async", json=bad_range).status_code == 422

    one_member = {**FAMILY_REQ, "members": [SAMPLE_A]}
    assert client.post("/v1/muhurat/family-lagna-shuddhi", json=one_member).status_code == 422
    assert client.post("/v1/muhurat/family-lagna-shuddhi/async", json=one_member).status_code == 422
