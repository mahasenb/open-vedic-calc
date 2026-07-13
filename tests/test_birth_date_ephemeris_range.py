"""FR-LOW-2 — birth_date must fall within the supported ephemeris range.

PersonalDataIn.birth_date / FamilyMember.birth_date previously accepted any
year 1-9999 and reached swe.julday() unguarded. An extreme year outside the
loaded ephemeris data's range (see EPHEMERIS_LICENSE.md: the shipped
seas_18/semo_18/sepl_18 files cover AD 1800-2400) is at best a 500 and
possibly a native-level fault -- confirmed locally: birth_date=9999-01-01
raises an uncaught swisseph.Error deep in the pyjhora call chain rather than
a clean validation error.

Both PersonalDataIn (exercised via /v1/chart) and FamilyMember (exercised
via /v1/muhurat/family-lagna-shuddhi) now share the same BoundedBirthDate
constraint (app/schemas.py), so an out-of-range year is rejected as a 422 at
the schema boundary before it ever reaches the ephemeris.
"""
import os

from fastapi.testclient import TestClient

os.environ.setdefault("CALC_SERVICE_TOKEN", "test")
os.environ.setdefault("PUBLIC_SOURCE_URL", "https://example.com")

from app.main import app
from app.schemas import MAX_EPHEMERIS_YEAR, MIN_EPHEMERIS_YEAR
from tests.conftest import SAMPLE_A, SAMPLE_B

client = TestClient(app, headers={"X-Calc-Service-Token": "test"})

_FAMILY_BASE = {
    "start_date": "2026-05-26",
    "end_date": "2026-05-27",
    "activity_category": "generic",
    "step_seconds": 60,
}


class TestPersonalDataInBirthYearRange:
    def test_year_9999_is_422(self):
        r = client.post("/v1/chart", json={**SAMPLE_A, "birth_date": "9999-01-01"})
        assert r.status_code == 422

    def test_year_1_is_422(self):
        r = client.post("/v1/chart", json={**SAMPLE_A, "birth_date": "0001-01-01"})
        assert r.status_code == 422

    def test_year_just_below_min_is_422(self):
        r = client.post(
            "/v1/chart", json={**SAMPLE_A, "birth_date": f"{MIN_EPHEMERIS_YEAR - 1}-01-01"}
        )
        assert r.status_code == 422

    def test_year_just_above_max_is_422(self):
        r = client.post(
            "/v1/chart", json={**SAMPLE_A, "birth_date": f"{MAX_EPHEMERIS_YEAR + 1}-01-01"}
        )
        assert r.status_code == 422

    def test_year_at_min_boundary_is_accepted(self):
        r = client.post(
            "/v1/chart", json={**SAMPLE_A, "birth_date": f"{MIN_EPHEMERIS_YEAR}-06-15"}
        )
        assert r.status_code != 422, r.text

    def test_year_at_max_boundary_is_accepted(self):
        r = client.post(
            "/v1/chart", json={**SAMPLE_A, "birth_date": f"{MAX_EPHEMERIS_YEAR}-06-15"}
        )
        assert r.status_code != 422, r.text

    def test_ordinary_year_within_range_returns_200(self):
        # Positive control: the existing golden-value sample must be unaffected.
        r = client.post("/v1/chart", json=SAMPLE_A)
        assert r.status_code == 200


class TestFamilyMemberBirthYearRange:
    def _family_req(self, member_overrides: dict) -> dict:
        return {
            **_FAMILY_BASE,
            "members": [{**SAMPLE_A, **member_overrides}, SAMPLE_B],
        }

    def test_year_9999_is_422(self):
        r = client.post(
            "/v1/muhurat/family-lagna-shuddhi",
            json=self._family_req({"birth_date": "9999-01-01"}),
        )
        assert r.status_code == 422

    def test_year_1_is_422(self):
        r = client.post(
            "/v1/muhurat/family-lagna-shuddhi",
            json=self._family_req({"birth_date": "0001-01-01"}),
        )
        assert r.status_code == 422

    def test_ordinary_year_within_range_returns_200(self):
        r = client.post(
            "/v1/muhurat/family-lagna-shuddhi",
            json={**_FAMILY_BASE, "members": [SAMPLE_A, SAMPLE_B]},
        )
        assert r.status_code == 200
