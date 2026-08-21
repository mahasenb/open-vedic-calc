"""Every date field the caller can send must sit inside the served ephemeris span.

PR #68 bounded ``birth_date`` to the MEASURED Swiss data span
(``MIN_EPHEMERIS_DATE``..``MAX_EPHEMERIS_DATE``, app/schemas.py) after finding
that the tail of the advertised range answered at HTTP 200 off the Moshier
fallback. ``birth_date`` was not the only date on the wire. NINE further
``IsoDateStr`` inputs carried no range check at all — ``at_date``, ``from_date``,
``to_date``, and THREE ``start_date``/``end_date`` pairs — and a TENTH,
``reference_date``, was missed by the field sweep entirely because it is a
``date``, not an ``IsoDateStr``, so it never matched that grep. TEN in all, and
``_SINGLE_DATE_CASES`` below carries one entry per field.

MEASURED BEFORE THIS CHANGE (base 5e63404, Swiss data present, spying on every
``swe.calc_ut`` a served request makes):

    field                        input            base behaviour
    ---------------------------- ---------------- --------------------------------
    at_date          /v1/transits 9999-01-01      500 — UNCAUGHT swisseph.Error
    at_date          /v1/transits 0001-01-01      500 — UNCAUGHT swisseph.Error
    at_date          /v1/transits 2400-06-01      200, 10/25 calls on MOSHIER
    at_date          /v1/transits 2500-01-01      200, 71/86 calls on MOSHIER
    start/end        /v1/muhurat  2400-06-01      200, 1450/6353 calls on MOSHIER
    start/end        lagna-shuddhi 2400-06-01     200, 1650/6553 calls on MOSHIER
    start/end        family        2400-06-01     200, 3300/13106 calls on MOSHIER
    reference_date   /v1/compat   9999-01-01      500 — UNCAUGHT OverflowError
    from/to          /v1/dashas   9999-12-31      422 (the MAX_DASHA_DAYS cap)

The two 500s are the same class of defect PR #68's docstring described for
``birth_date=9999-01-01``: a well-formed request reaching the engine unguarded
and faulting there instead of being refused at the schema boundary. The four
silent-Moshier rows are the same class as the ``birth_date`` tail — a 200 whose
numbers came from the fallback engine with nothing in the response saying so.

WHAT THESE TESTS DO AND DO NOT CLAIM
------------------------------------
``at_date`` is asserted Swiss-backed at both bounds across every timezone
corner, because it is a single instant lookup and the spy can attribute the
calls to it.

``reference_date``, ``from_date`` and ``to_date`` are NOT asserted Swiss-backed,
and that omission is deliberate rather than an oversight. Measured, they drive
ZERO ephemeris calls.

For /v1/dashas the demonstration has to run on a pair the service will actually
SERVE, which a normal birth cannot provide: birth 1950-06-15 with to_date
2999-12-31 is refused 422 by the birth-relative MAX_DASHA_DAYS cap having made
ZERO ephemeris calls, so it shows nothing either way. A late birth keeps the cap
satisfied — measured at base with birth 2390-06-15 and from_date 2395-01-01,
to_date at 2400-01-09, 2450-01-01 and 2500-01-01 all returned 200 with the
swe.calc_ut count pinned at exactly 15 (the natal chart), the last of them
running ~100 years past the ephemeris span. /v1/compat needs no such care, having
no cap: 30 calls (its two natal charts) at reference_date 2026-05-26,
2400-01-09, 2500-01-01 and 3000-01-01 alike.

A "Swiss-backed" spy assertion on those fields would pass by construction while
measuring nothing, which is worse than no test. They are bounded for the crash
and for a uniform served contract; what is asserted here is the crash closing.

The muhurat/lagna-shuddhi scanned-day fields are asserted to be REFUSED outside
the span and ACCEPTED at the bounds, but are likewise not asserted Swiss-backed
at the bounds — because measured, they are not. See
``test_scanned_day_edges_still_reach_outside_the_files`` at the end of this
module, which pins that separately-tracked engine defect so it cannot widen
silently.
"""
import datetime
import os
import threading
from types import UnionType
from typing import Annotated, Union, get_args, get_origin

import pytest
from pydantic import AfterValidator, BaseModel

os.environ.setdefault("CALC_SERVICE_TOKEN", "test")
os.environ.setdefault("PUBLIC_SOURCE_URL", "https://example.com")

import swisseph as swe
from fastapi.testclient import TestClient

from bphs_core import utils
from app import schemas as app_schemas
from app.main import app
from app.schemas import MAX_EPHEMERIS_DATE, MIN_EPHEMERIS_DATE
from tests.conftest import SAMPLE_A, SAMPLE_B

_ONE_DAY = datetime.timedelta(days=1)
MIN_S = MIN_EPHEMERIS_DATE.isoformat()
MAX_S = MAX_EPHEMERIS_DATE.isoformat()
BELOW_MIN = (MIN_EPHEMERIS_DATE - _ONE_DAY).isoformat()
ABOVE_MAX = (MAX_EPHEMERIS_DATE + _ONE_DAY).isoformat()

client = TestClient(app, headers={"X-Calc-Service-Token": "test"})

# A birth date that leaves plenty of MAX_DASHA_DAYS headroom, so a /v1/dashas
# assertion about the ephemeris bound is never actually answered by the cap.
_DASHA_PERSON = {**SAMPLE_A, "birth_date": "1950-06-15"}

_SCAN_EXTRA = {"activity_category": "generic", "step_seconds": 3600}


def _transits(at_date, **over):
    return {**SAMPLE_A, "at_date": at_date, **over}


def _dashas(from_date, to_date, **over):
    return {**_DASHA_PERSON, "from_date": from_date, "to_date": to_date,
            "systems": ["vimshottari"], **over}


def _dashas_valid_at(d):
    """A /v1/dashas body that is valid in EVERY respect except the span check.

    The ephemeris span is ~600 years wide while MAX_DASHA_DAYS caps a timeline
    at ~128 years FROM BIRTH, so no single anchor birth date can pair with both
    ends of the span: to_date=2400-01-09 against a 1950 birth trips the cap, and
    from_date=2400-01-09 against a 1970 to_date trips the ordering guard. Both
    would answer 422 for a reason that has nothing to do with the bound under
    test. Collapsing birth, from and to onto the same day makes the cap zero and
    the ordering trivially satisfied, so a 422 here can only be the span bound.
    """
    return {**SAMPLE_A, "birth_date": d, "from_date": d, "to_date": d,
            "systems": ["vimshottari"]}


def _muhurat(start, end, **over):
    return {**SAMPLE_A, "start_date": start, "end_date": end, **over}


def _lagna(start, end, **over):
    return {**SAMPLE_A, "start_date": start, "end_date": end, **_SCAN_EXTRA, **over}


def _family(start, end, **over):
    return {"members": [SAMPLE_A, SAMPLE_B], "start_date": start, "end_date": end,
            **_SCAN_EXTRA, **over}


def _compat(d):
    return {"person_a": SAMPLE_A, "person_b": SAMPLE_B, "reference_date": d}


# (label, endpoint, field name, builder for an OUT-OF-SPAN probe, builder for an
# otherwise-entirely-valid probe). The two builders differ only for /v1/dashas,
# where the birth-relative cap and the ordering guard would otherwise answer
# instead of the bound — see _dashas_valid_at.
_SINGLE_DATE_CASES = [
    ("at_date", "/v1/transits", "at_date",
     lambda d: _transits(d), lambda d: _transits(d)),
    ("from_date", "/v1/dashas", "from_date",
     lambda d: _dashas(d, "1970-01-01"), _dashas_valid_at),
    ("to_date", "/v1/dashas", "to_date",
     lambda d: _dashas("1960-01-01", d), _dashas_valid_at),
    ("muhurat.start_date", "/v1/muhurat", "start_date",
     lambda d: _muhurat(d, d), lambda d: _muhurat(d, d)),
    ("muhurat.end_date", "/v1/muhurat", "end_date",
     lambda d: _muhurat(d, d), lambda d: _muhurat(d, d)),
    ("lagna.start_date", "/v1/muhurat/lagna-shuddhi", "start_date",
     lambda d: _lagna(d, d), lambda d: _lagna(d, d)),
    ("lagna.end_date", "/v1/muhurat/lagna-shuddhi", "end_date",
     lambda d: _lagna(d, d), lambda d: _lagna(d, d)),
    ("family.start_date", "/v1/muhurat/family-lagna-shuddhi", "start_date",
     lambda d: _family(d, d), lambda d: _family(d, d)),
    ("family.end_date", "/v1/muhurat/family-lagna-shuddhi", "end_date",
     lambda d: _family(d, d), lambda d: _family(d, d)),
    ("reference_date", "/v1/compat", "reference_date", _compat, _compat),
]

_IDS = [c[0] for c in _SINGLE_DATE_CASES]


# ---------------------------------------------------------------------------
# The COUNT is derived here, never typed into prose
# ---------------------------------------------------------------------------
#
# The first draft of this module said "eight" because a grep for `IsoDateStr`
# was eyeballed and the three start_date/end_date pairs were read as two. The
# number was wrong in five places before anyone re-counted. So the enumeration
# is computed from the schema module itself and asserted, and any future date
# input either appears here or reddens this file.


_OUT_OF_SPAN_PROBES = ("2500-01-01", datetime.date(2500, 1, 1))


def _after_validators(annotation) -> list:
    """Every AfterValidator function reachable inside this annotation.

    Recursive because a naive ``field.metadata`` check gets ``reference_date``
    WRONG: it is declared ``BoundedReferenceDate | None``, and for a union
    Pydantic leaves ``FieldInfo.metadata`` EMPTY — the validator lives on the
    union's Annotated member, not on the field.
    """
    out: list = []
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        out += [m.func for m in args[1:] if isinstance(m, AfterValidator)]
        out += _after_validators(args[0])
        return out
    if get_origin(annotation) in (Union, UnionType):
        for arg in get_args(annotation):
            out += _after_validators(arg)
    return out


def _rejects_out_of_span(funcs) -> bool:
    """Does any of these validators actually REFUSE a date outside the span?

    Presence of an AfterValidator is NOT the question, and testing for it was a
    real bug in the first draft of this checker. ``IsoDateStr`` carries its own
    AfterValidator — the ISO *shape* check — so a presence test calls every bare
    ``IsoDateStr`` field "bounded". Measured: with that version, reverting
    ``at_date: BoundedAtDate`` to ``at_date: IsoDateStr`` left this file GREEN,
    which is exactly the regression the checker exists to catch.

    So the validators are CALLED with an out-of-span probe and must raise. A
    shape validator accepts 2500-01-01 happily; only a range validator refuses.
    """
    for fn in funcs:
        for probe in _OUT_OF_SPAN_PROBES:
            try:
                fn(probe)
            except ValueError:
                return True
            except Exception:
                continue  # wrong probe type for this validator; try the other
    return False


def _request_model_date_fields() -> dict[tuple[str, str], bool]:
    """{(model, field): is_bounded} for every date-ish field on a request model."""
    found: dict[tuple[str, str], bool] = {}
    for name in dir(app_schemas):
        obj = getattr(app_schemas, name)
        if not (isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel):
            continue
        # Request models only: response models are free to carry unbounded dates.
        if not any(tag in name for tag in ("Request", "DataIn", "Member", "PersonFields")):
            continue
        for field, info in obj.model_fields.items():
            if "date" in field:
                # BOTH places must be gathered, and each catches what the other
                # misses. For a plain `x: BoundedAtDate` Pydantic HOISTS the
                # metadata onto FieldInfo.metadata and leaves `annotation` as the
                # bare `str`/`date` — so walking the annotation alone finds
                # nothing. For `x: BoundedReferenceDate | None` it does the
                # opposite: metadata is EMPTY and the validator stays inside the
                # union member. Measured both ways; a checker that looks in one
                # place is wrong about half the fields here.
                funcs = [m.func for m in info.metadata if isinstance(m, AfterValidator)]
                funcs += _after_validators(info.annotation)
                found[(name, field)] = _rejects_out_of_span(funcs)
    return found


def test_every_request_model_date_field_is_bounded():
    """No date a caller can send may reach the engine unbounded."""
    fields = _request_model_date_fields()
    unbounded = sorted(k for k, bounded in fields.items() if not bounded)
    assert not unbounded, (
        "these request-model date fields carry no range validator:\n"
        + "\n".join(f"  - {m}.{f}" for m, f in unbounded)
    )


def test_the_non_birth_date_field_count_is_ten():
    """Pins the number this module's docstring states.

    Ten distinct declaration sites, not ten models: birth_date is declared once
    on BoundedPersonFields and inherited, while start_date/end_date are declared
    separately on each of the three scan request models.
    """
    non_birth = sorted(k for k in _request_model_date_fields() if k[1] != "birth_date")
    assert len(non_birth) == 10, (
        f"expected 10 non-birth date fields on request models, found "
        f"{len(non_birth)}:\n" + "\n".join(f"  - {m}.{f}" for m, f in non_birth)
        + "\n\nIf a date input was added or removed, update the count in this "
        "module's docstring, app/schemas.py and CLAUDE.md together — and add a "
        "_SINGLE_DATE_CASES entry for it."
    )


def test_the_case_table_covers_every_non_birth_date_field():
    """_SINGLE_DATE_CASES must not silently stop covering a field."""
    declared = {f for (_m, f) in _request_model_date_fields() if f != "birth_date"}
    covered = {field for (_l, _e, field, _bad, _ok) in _SINGLE_DATE_CASES}
    assert declared == covered, (
        f"case table covers {sorted(covered)} but the request models declare "
        f"{sorted(declared)}; missing {sorted(declared - covered)}"
    )
    assert len(_SINGLE_DATE_CASES) == 10, (
        f"expected one case per non-birth date field (10), got {len(_SINGLE_DATE_CASES)}"
    )


# ---------------------------------------------------------------------------
# Out of span -> 422, and the 422 names the real bound
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", _SINGLE_DATE_CASES, ids=_IDS)
@pytest.mark.parametrize("bad", ["9999-01-01", "0001-01-01", "2400-06-01", "2500-01-01"])
def test_out_of_span_date_is_422(case, bad):
    """Every date input refuses a value outside the served ephemeris span.

    2400-06-01 and 2500-01-01 are the important rows: they are not absurd
    inputs, they are dates the service used to ANSWER at 200 with numbers from
    the Moshier fallback. 9999-01-01 and 0001-01-01 are the two that faulted.
    """
    label, endpoint, _field, build_bad, _build_ok = case
    r = client.post(endpoint, json=build_bad(bad))
    assert r.status_code == 422, (
        f"{label}={bad} was not refused at the schema boundary. "
        f"Got {r.status_code}: {r.text[:300]}"
    )


@pytest.mark.parametrize("case", _SINGLE_DATE_CASES, ids=_IDS)
def test_the_422_names_the_field_and_the_real_bound(case):
    """A caller must be told which field and which span, in dates not years."""
    label, endpoint, field, build_bad, _build_ok = case
    r = client.post(endpoint, json=build_bad("2500-01-01"))
    assert r.status_code == 422
    body = r.text
    assert field in body, f"{label}: the 422 does not name the field. Got: {body[:400]}"
    assert MIN_S in body, f"{label}: the 422 does not name the lower bound. Got: {body[:400]}"
    assert MAX_S in body, f"{label}: the 422 does not name the upper bound. Got: {body[:400]}"


@pytest.mark.parametrize("case", _SINGLE_DATE_CASES, ids=_IDS)
@pytest.mark.parametrize("edge", [BELOW_MIN, ABOVE_MAX])
def test_one_day_outside_each_bound_is_422(case, edge):
    label, endpoint, _field, build_bad, _build_ok = case
    r = client.post(endpoint, json=build_bad(edge))
    assert r.status_code == 422, (
        f"{label}={edge} is one day outside the span and must be refused. "
        f"Got {r.status_code}: {r.text[:200]}"
    )


@pytest.mark.parametrize("case", _SINGLE_DATE_CASES, ids=_IDS)
@pytest.mark.parametrize("edge", [MIN_S, MAX_S])
def test_the_bounds_themselves_are_accepted(case, edge):
    """The bound is inclusive: neither end may be refused."""
    label, endpoint, _field, _build_bad, build_ok = case
    r = client.post(endpoint, json=build_ok(edge))
    assert r.status_code != 422, (
        f"{label}={edge} is ON the bound and must be accepted. Got 422: {r.text[:300]}"
    )


# ---------------------------------------------------------------------------
# The two uncaught engine faults this closes
# ---------------------------------------------------------------------------

def test_transits_year_9999_no_longer_reaches_the_engine():
    """Base: 500, uncaught ``swisseph.Error`` from drik.sidereal_longitude.

    Measured on 5e63404 — ``swisseph.Error: swisseph.calc_ut: SwissEph file
    'sepl_96.se1' not found`` escaping to the caller as a raw 500. The schema
    must refuse it before the engine is reached.
    """
    r = client.post("/v1/transits", json=_transits("9999-01-01"))
    assert r.status_code == 422, f"expected a clean 422, got {r.status_code}"


def test_compat_year_9999_no_longer_overflows():
    """Base: 500, uncaught ``OverflowError: date value out of range``.

    Measured on 5e63404 at bphs_core/compat.py:374 —
    ``window_start + timedelta(days=25 * 365.25)`` runs past year 9999 and
    raises. This is the TENTH date field, and the one the IsoDateStr sweep
    missed: reference_date is a ``date``, so it never matched that grep.
    """
    r = client.post("/v1/compat", json={
        "person_a": SAMPLE_A, "person_b": SAMPLE_B, "reference_date": "9999-01-01"})
    assert r.status_code == 422, f"expected a clean 422, got {r.status_code}"


def test_compat_reference_date_stays_optional():
    """The bound must not make an optional field required."""
    r = client.post("/v1/compat", json={"person_a": SAMPLE_A, "person_b": SAMPLE_B})
    assert r.status_code == 200, r.text[:300]


# ---------------------------------------------------------------------------
# Reversed ranges — pinned. These already 422 on base; the bound must not
# convert any of them into a 200, a crash, or an empty iteration.
# ---------------------------------------------------------------------------

_REVERSED = [
    ("/v1/dashas", _dashas("2030-01-01", "2020-01-01"), ("from_date", "to_date")),
    ("/v1/muhurat", _muhurat("2026-05-27", "2026-05-26"), ("start_date", "end_date")),
    ("/v1/muhurat/lagna-shuddhi", _lagna("2026-05-27", "2026-05-26"), ("start_date", "end_date")),
    ("/v1/muhurat/family-lagna-shuddhi", _family("2026-05-27", "2026-05-26"), ("start_date", "end_date")),
]


@pytest.mark.parametrize("endpoint,body,names", _REVERSED,
                         ids=[e for e, _b, _n in _REVERSED])
def test_reversed_in_bounds_range_is_422_naming_the_order(endpoint, body, names):
    """Measured on base: all four already 422 with an ordering message.

    Both ends are inside the span here, so the new bound cannot be what
    refuses them — this pins that the ORDERING guard is still the thing that
    fires, and that a reversed range never degrades to an empty 200.
    """
    r = client.post(endpoint, json=body)
    assert r.status_code == 422, f"{endpoint}: got {r.status_code}: {r.text[:200]}"
    low = r.text.lower()
    assert any(n in low for n in names), (
        f"{endpoint}: the 422 does not name the ordering. Got: {r.text[:300]}"
    )


@pytest.mark.parametrize("endpoint,body", [
    ("/v1/muhurat", _muhurat(MAX_S, MIN_S)),
    ("/v1/muhurat/lagna-shuddhi", _lagna(MAX_S, MIN_S)),
    ("/v1/muhurat/family-lagna-shuddhi", _family(MAX_S, MIN_S)),
])
def test_reversed_range_exactly_on_both_bounds_is_still_422(endpoint, body):
    """The widest legal-but-reversed range. Both ends pass the span check, so
    the ordering guard is the only thing standing between this and a scan that
    iterates nothing."""
    r = client.post(endpoint, json=body)
    assert r.status_code == 422, f"{endpoint}: got {r.status_code}: {r.text[:200]}"


# ---------------------------------------------------------------------------
# Composition with the pre-existing range CAPS
# ---------------------------------------------------------------------------

def test_muhurat_day_cap_still_fires_for_an_in_bounds_range():
    """An over-cap range with BOTH ends inside the span must still hit the cap.

    If the new bound shadowed the cap, this would come back with the span
    message instead — the cap would be dead code and a 400-day scan would be
    refused for the wrong reason (or, once someone "fixed" the message, not at
    all).
    """
    import app.main as main_mod
    start = datetime.date(2026, 1, 1)
    end = start + datetime.timedelta(days=main_mod.MAX_MUHURAT_DAYS + 1)
    assert MIN_EPHEMERIS_DATE <= start and end <= MAX_EPHEMERIS_DATE
    r = client.post("/v1/muhurat", json=_muhurat(start.isoformat(), end.isoformat()))
    assert r.status_code == 422
    assert "exceeds" in r.text.lower(), (
        f"the day cap no longer fires — the span bound shadowed it. Got: {r.text[:300]}"
    )


def test_lagna_shuddhi_day_cap_still_fires_for_an_in_bounds_range():
    import app.main as main_mod
    start = datetime.date(2026, 1, 1)
    end = start + datetime.timedelta(days=main_mod.MAX_LAGNA_SHUDDHI_DAYS + 1)
    assert MIN_EPHEMERIS_DATE <= start and end <= MAX_EPHEMERIS_DATE
    r = client.post("/v1/muhurat/lagna-shuddhi", json=_lagna(start.isoformat(), end.isoformat()))
    assert r.status_code == 422
    assert "exceeds" in r.text.lower(), r.text[:300]


def test_dasha_cap_still_fires_for_an_in_bounds_range():
    """MAX_DASHA_DAYS is measured from BIRTH, so it can still fire on a
    to_date well inside the ephemeris span. SAMPLE_A is born 1950-06-15;
    birth + 47001 days is 2079-ish — comfortably inside the bound."""
    import app.main as main_mod
    birth = datetime.date.fromisoformat(_DASHA_PERSON["birth_date"])
    to_dt = birth + datetime.timedelta(days=main_mod.MAX_DASHA_DAYS + 1)
    assert to_dt <= MAX_EPHEMERIS_DATE, (
        "this test must exercise the CAP, not the span bound — "
        f"{to_dt} is outside {MAX_S}"
    )
    r = client.post("/v1/dashas", json=_dashas(birth.isoformat(), to_dt.isoformat()))
    assert r.status_code == 422
    assert "exceeds" in r.text.lower(), (
        f"the dasha cap no longer fires. Got: {r.text[:300]}"
    )


def test_full_life_dasha_span_still_returns_200():
    """Positive control: the legitimate 120-year full-cycle request must not
    become collateral damage of the new bound."""
    birth = datetime.date.fromisoformat(_DASHA_PERSON["birth_date"])
    to_dt = birth + datetime.timedelta(days=44000)
    r = client.post("/v1/dashas", json=_dashas(birth.isoformat(), to_dt.isoformat()))
    assert r.status_code == 200, r.text[:300]
    lords = {p["lord"] for p in r.json() if p["level"] == "mahadasha"}
    assert len(lords) >= 9, f"expected all 9 mahadasha lords, got {lords}"


# ---------------------------------------------------------------------------
# The async submit paths share these models — the refusal must land at SUBMIT,
# not inside a job that reports failure later.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint,body", [
    ("/v1/muhurat/lagna-shuddhi/async", _lagna("2400-06-01", "2400-06-02")),
    ("/v1/muhurat/family-lagna-shuddhi/async", _family("2400-06-01", "2400-06-02")),
])
def test_async_submit_refuses_an_out_of_span_range(endpoint, body):
    r = client.post(endpoint, json=body)
    assert r.status_code == 422, (
        f"{endpoint} accepted an out-of-span range for asynchronous execution "
        f"({r.status_code}) — the caller would get a job id and a late failure "
        "instead of a refusal."
    )


# ---------------------------------------------------------------------------
# at_date is Swiss-backed at both bounds — re-measured, not restated
# ---------------------------------------------------------------------------

_TZ_CORNERS = (14.0, -12.0, 5.5)


def _moshier_calls_during(endpoint: str, body: dict) -> tuple[int, int]:
    """(moshier_calls, total_calls) over every swe.calc_ut the request makes."""
    records: list[int] = []
    lock = threading.Lock()
    real_calc_ut = swe.calc_ut

    def spy(jd, planet, flags, *args, **kwargs):
        values, retflag = real_calc_ut(jd, planet, flags, *args, **kwargs)
        with lock:
            records.append(retflag)
        return values, retflag

    swe.calc_ut = spy
    try:
        response = client.post(endpoint, json=body)
    finally:
        swe.calc_ut = real_calc_ut

    assert response.status_code == 200, (
        f"the probe request itself failed: {response.status_code} {response.text[:300]}"
    )
    with lock:
        flags = list(records)
    assert flags, "no swe.calc_ut calls were observed — the spy did not take effect"
    return sum(1 for f in flags if f & swe.FLG_MOSEPH), len(flags)


def test_at_date_at_both_bounds_is_swiss_backed(swiss_ephemeris: int) -> None:
    """No admissible /v1/transits request inside the span may answer on Moshier.

    at_date is a single instant lookup, so — unlike the scanned-day fields —
    the spy attributes these calls to the bounded field itself. Sweeping the
    timezone extremes is what makes reusing birth_date's one-day margin a
    measurement rather than an assumption: tz=+14 pulls the UTC instant a day
    earlier than the local date and tz=-12 pushes it a day later.
    """
    failures: list[str] = []
    for bound in (MIN_S, MAX_S):
        for tz in _TZ_CORNERS:
            for birth_time in ("00:00:00", "23:59:59"):
                body = _transits(
                    bound,
                    birth_date=MIN_S if bound == MIN_S else "1990-01-01",
                    birth_time=birth_time,
                    timezone_offset_hours=tz,
                )
                moshier, total = _moshier_calls_during("/v1/transits", body)
                if moshier:
                    failures.append(
                        f"at_date={bound} tz={tz:+g} birth_time={birth_time}: "
                        f"{moshier}/{total} swe.calc_ut calls answered on MOSHIER"
                    )
    assert not failures, (
        "an admissible /v1/transits request inside the served span was computed "
        "on the Moshier fallback:\n"
        + "\n".join(f"  - {f}" for f in failures)
        + "\n\nEither the bound is too wide for the shipped files or the timezone "
        "margin is too thin. Re-measure before widening anything."
    )


def test_an_at_date_just_past_the_bound_would_have_leaked(swiss_ephemeris: int) -> None:
    """The bound is TIGHT, not merely safe.

    Without this, narrowing at_date to any small interval would satisfy the
    test above. Asks the engine's own detector whether the day past the bound
    (plus the timezone margin) really is where the data stops.
    """
    past = MAX_EPHEMERIS_DATE + datetime.timedelta(days=2)
    swiss_active, _ = utils.probe_ephemeris_source(
        swe.julday(past.year, past.month, past.day, 12.0), swe.SUN
    )
    assert not swiss_active, (
        f"{past} still answers from Swiss data, so the shipped files reach further "
        "than when this bound was measured. The bound is now needlessly narrow — "
        "re-measure and widen it deliberately."
    )


# ---------------------------------------------------------------------------
# KNOWN, SEPARATELY-TRACKED: the scanned-day endpoints still consult data
# outside the files at the very edges of the span.
# ---------------------------------------------------------------------------

def test_scanned_day_edges_still_reach_outside_the_files(swiss_ephemeris: int) -> None:
    """This bound does NOT make every muhurat scan Swiss-backed, and says so.

    ``bphs_core/muhurat.py::_is_eclipse_day`` calls ``drik.next_solar_eclipse``
    / ``next_lunar_eclipse``, which search for an eclipse OUTSIDE the scanned
    day. Measured on base and unchanged by this PR: a day scanned at
    2400-01-09 drives Sun/Moon probes out to 2400-01-29, and one scanned at
    1800-01-02 drives them back to 1798-07-10 — both outside sepl_18/semo_18,
    so they answer on Moshier. The leak window is roughly 1800-01-02..1801-06-30
    at the low end and 2399-12-28..2400-01-09 at the high end; the interior of
    the span is clean (measured 0/344 at 2026-05-26, 0/2728 at 2399-12-01).

    That is an ENGINE defect at the data edges, not a schema one — this PR
    shrinks the exposure enormously (2400-06-01 went from 200 with 1450/6353
    Moshier to a 422) but does not close it. Reported upward as a follow-up.

    This test exists so the residual cannot widen unnoticed: it asserts the
    INTERIOR is clean. It deliberately does not assert the edges are clean,
    because they are not, and a test claiming otherwise would be false.
    """
    interior = "2399-12-01"
    moshier, total = _moshier_calls_during("/v1/muhurat", _muhurat(interior, interior))
    assert moshier == 0, (
        f"a muhurat scan at {interior} — well inside the span — now answers "
        f"{moshier}/{total} calls on MOSHIER. The eclipse-finder edge leak has "
        "widened into the interior of the served range; re-measure it."
    )
