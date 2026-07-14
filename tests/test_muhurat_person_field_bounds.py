"""FR-MED-23 / FR-LOW-2 — MuhurtRequest and LagnaShuddhiRequest must enforce the
same person-field bounds as every other person-like model.

Both models previously redefined name / birth_date / birth_place from scratch
(class ...(BaseModel)) instead of inheriting BoundedPersonFields, and in doing so
silently dropped the name/birth_place length bounds and the birth_date
ephemeris-range guard. Confirmed on the unfixed models:
  - an oversized name ("x" * 100000) passed validation (200, not 422) -- a
    request/log-inflation vector;
  - birth_date=9999-01-01 reached swe.julday() unguarded and raised an uncaught
    swisseph.Error (a raw 500) rather than a clean 422, because 9999 is outside
    the shipped ephemeris data's supported range (EPHEMERIS_LICENSE.md: AD
    1800-2400).

Both models now inherit BoundedPersonFields (app/schemas.py), so these are
rejected as a 422 at the schema boundary before the ephemeris is reached. Mirrors
test_birth_date_ephemeris_range.py / test_family_member_string_bounds.py for the
/v1/muhurat and /v1/muhurat/lagna-shuddhi endpoints.
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("CALC_SERVICE_TOKEN", "test")
os.environ.setdefault("PUBLIC_SOURCE_URL", "https://example.com")

from app.main import app
from app.schemas import MAX_EPHEMERIS_YEAR, MIN_EPHEMERIS_YEAR
from tests.conftest import SAMPLE_A

client = TestClient(app, headers={"X-Calc-Service-Token": "test"})

# Known-good valid baselines (see test_endpoints.py::test_muhurat_endpoint and
# test_alternatives.py::_LAGNA_SHUDDHI_REQ). Kept to short scan windows so the
# boundary-year cases stay cheap.
_MUHURAT_BASE = {**SAMPLE_A, "start_date": "2026-05-26", "end_date": "2026-05-28"}
_LAGNA_SHUDDHI_BASE = {
    **SAMPLE_A,
    "start_date": "2026-06-15",
    "end_date": "2026-06-17",
    "activity_category": "generic",
    "step_seconds": 60,
}

# (endpoint, valid-baseline) pairs. The inherited person-field surface (name /
# birth_place / birth_date) is identical across both models, so every bound is
# asserted against both endpoints.
_ENDPOINTS = [
    ("/v1/muhurat", _MUHURAT_BASE),
    ("/v1/muhurat/lagna-shuddhi", _LAGNA_SHUDDHI_BASE),
]
_IDS = ["muhurat", "lagna_shuddhi"]


@pytest.mark.parametrize("endpoint,base", _ENDPOINTS, ids=_IDS)
class TestMuhuratPersonFieldBounds:
    # --- name length bound (FR-MED-23) ---
    def test_overlong_name_is_422(self, endpoint, base):
        r = client.post(endpoint, json={**base, "name": "x" * 121})
        assert r.status_code == 422

    def test_name_at_max_length_is_accepted(self, endpoint, base):
        r = client.post(endpoint, json={**base, "name": "x" * 120})
        assert r.status_code == 200, r.text

    def test_empty_name_is_422(self, endpoint, base):
        r = client.post(endpoint, json={**base, "name": ""})
        assert r.status_code == 422

    # --- birth_place length bound (FR-MED-23) ---
    def test_overlong_birth_place_is_422(self, endpoint, base):
        r = client.post(endpoint, json={**base, "birth_place": "p" * 201})
        assert r.status_code == 422

    def test_empty_birth_place_is_422(self, endpoint, base):
        r = client.post(endpoint, json={**base, "birth_place": ""})
        assert r.status_code == 422

    # --- birth_date ephemeris-range bound (FR-LOW-2) ---
    def test_birth_year_9999_is_422(self, endpoint, base):
        r = client.post(endpoint, json={**base, "birth_date": "9999-01-01"})
        assert r.status_code == 422

    def test_birth_year_1_is_422(self, endpoint, base):
        r = client.post(endpoint, json={**base, "birth_date": "0001-01-01"})
        assert r.status_code == 422

    def test_birth_year_just_below_min_is_422(self, endpoint, base):
        r = client.post(
            endpoint, json={**base, "birth_date": f"{MIN_EPHEMERIS_YEAR - 1}-01-01"}
        )
        assert r.status_code == 422

    def test_birth_year_just_above_max_is_422(self, endpoint, base):
        r = client.post(
            endpoint, json={**base, "birth_date": f"{MAX_EPHEMERIS_YEAR + 1}-01-01"}
        )
        assert r.status_code == 422

    def test_birth_year_at_min_boundary_is_accepted(self, endpoint, base):
        r = client.post(
            endpoint, json={**base, "birth_date": f"{MIN_EPHEMERIS_YEAR}-06-15"}
        )
        assert r.status_code != 422, r.text

    def test_birth_year_at_max_boundary_is_accepted(self, endpoint, base):
        r = client.post(
            endpoint, json={**base, "birth_date": f"{MAX_EPHEMERIS_YEAR}-06-15"}
        )
        assert r.status_code != 422, r.text

    # --- positive control: the golden-value sample must be unaffected ---
    def test_ordinary_request_returns_200(self, endpoint, base):
        r = client.post(endpoint, json=base)
        assert r.status_code == 200, r.text
