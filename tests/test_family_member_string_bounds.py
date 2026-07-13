"""FR-MED-23 — FamilyMember.name / .birth_place must be length-bounded, the
same as PersonalDataIn (exercised via /v1/dashas in test_endpoint_limits.py).

FamilyMember previously redefined name/birth_date/birth_place from scratch
instead of inheriting PersonalDataIn's fields, and in doing so silently
dropped the string-length bounds -- an oversized name/birth_place in a
family-lagna-shuddhi request passed validation and was echoed into the
stored async job result (compounding FR-MED-21's memory-growth concern).
FamilyMember now derives name/birth_place from the shared
BoundedPersonFields base (see app/schemas.py), so it can no longer drop
these bounds by copy-paste omission.
"""
import os

from fastapi.testclient import TestClient

os.environ.setdefault("CALC_SERVICE_TOKEN", "test")
os.environ.setdefault("PUBLIC_SOURCE_URL", "https://example.com")

from app.main import app
from tests.conftest import SAMPLE_A, SAMPLE_B

client = TestClient(app, headers={"X-Calc-Service-Token": "test"})

_FAMILY_BASE = {
    "start_date": "2026-05-26",
    "end_date": "2026-05-27",
    "activity_category": "generic",
    "step_seconds": 60,
}


def _family_req(member_overrides: dict) -> dict:
    return {
        **_FAMILY_BASE,
        "members": [{**SAMPLE_A, **member_overrides}, SAMPLE_B],
    }


def test_family_member_overlong_name_is_422():
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=_family_req({"name": "x" * 121}))
    assert r.status_code == 422


def test_family_member_name_at_max_length_is_accepted():
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=_family_req({"name": "x" * 120}))
    assert r.status_code == 200, r.text


def test_family_member_empty_name_is_422():
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=_family_req({"name": ""}))
    assert r.status_code == 422


def test_family_member_overlong_birth_place_is_422():
    r = client.post(
        "/v1/muhurat/family-lagna-shuddhi", json=_family_req({"birth_place": "p" * 201})
    )
    assert r.status_code == 422


def test_family_member_birth_place_at_max_length_is_accepted():
    r = client.post(
        "/v1/muhurat/family-lagna-shuddhi", json=_family_req({"birth_place": "p" * 200})
    )
    assert r.status_code == 200, r.text


def test_family_member_empty_birth_place_is_422():
    r = client.post("/v1/muhurat/family-lagna-shuddhi", json=_family_req({"birth_place": ""}))
    assert r.status_code == 422


def test_family_member_ordinary_fields_return_200():
    # Positive control: the existing golden-value sample must be unaffected.
    r = client.post(
        "/v1/muhurat/family-lagna-shuddhi",
        json={**_FAMILY_BASE, "members": [SAMPLE_A, SAMPLE_B]},
    )
    assert r.status_code == 200
