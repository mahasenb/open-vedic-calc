"""
Synthetic test fixtures — no real personal data committed here.
Fill in coordinates/dates with any well-documented public birth data
(e.g., historical figures) to make the goldens verifiable.
"""
import os
import pytest

# Set the auth environment variables before any app import in the test suite.
# ENVIRONMENT=test keeps the calc service in insecure mode (auth disabled)
# without touching a real token, so endpoint-level tests using the
# X-Calc-Service-Token="test" header work correctly.
# These must be set before app.auth is first imported, which happens when
# any test module that imports app.main is collected.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("CALC_SERVICE_TOKEN", "test")
os.environ.setdefault("PUBLIC_SOURCE_URL", "https://example.com")

# Synthetic sample A: arbitrary date, Sri Lanka coordinates
SAMPLE_A = {
    "name": "sample_a",
    "birth_date": "1950-06-15",
    "birth_time": "06:00:00",
    "birth_place": "Sample City",
    "latitude": 7.0,
    "longitude": 80.0,
    "timezone_offset_hours": 5.5,
}

# Synthetic sample B
SAMPLE_B = {
    "name": "sample_b",
    "birth_date": "1975-12-01",
    "birth_time": "12:30:00",
    "birth_place": "Sample City",
    "latitude": 6.9,
    "longitude": 79.8,
    "timezone_offset_hours": 5.5,
}

# Synthetic sample C
SAMPLE_C = {
    "name": "sample_c",
    "birth_date": "2000-03-21",
    "birth_time": "09:00:00",
    "birth_place": "Sample City",
    "latitude": 6.9,
    "longitude": 79.8,
    "timezone_offset_hours": 5.5,
}
