"""
Synthetic test fixtures — no real personal data committed here.
Fill in coordinates/dates with any well-documented public birth data
(e.g., historical figures) to make the goldens verifiable.
"""
import pytest

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
