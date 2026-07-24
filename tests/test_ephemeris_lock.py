"""Process-wide serialization of Swiss-Ephemeris / pyjhora C-library access.

pyswisseph and pyjhora carry process-global mutable state (the ephemeris
path set at import in ``bphs_core/utils.py`` and the sidereal/ayanamsa mode
that ``Chart._compute`` re-asserts on every call), and the underlying Swiss
Ephemeris C library is not documented as safe for concurrent
``swe_calc``/``swe_houses`` calls from multiple threads. Two compute paths
now run concurrently in one process: Starlette's per-request thread pool for
the synchronous ``/v1/*`` handlers, and the background scan-job pool in
``app/jobs.py``. Without synchronization the failure mode is not a crash but
a silently wrong chart for one of two concurrent requests — invisible, and
the worst possible defect for a determinism-first engine.

These tests pin the fix: a single process-wide re-entrant lock
(``bphs_core.utils.EPHEMERIS_LOCK``) that every C-library entry point holds
for the duration of its computation.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time

from bphs_core import lagna_shuddhi, muhurat, transits, utils
from bphs_core.chart import Chart, PersonalData

# Synthetic sample data (mirrors tests/conftest.py — no real personal data).
_PERSON_A = PersonalData(
    name="sample_a",
    birth_date=datetime(1950, 6, 15),
    birth_time=time(6, 0),
    birth_place="Sample City",
    latitude=7.0,
    longitude=80.0,
    timezone_offset_hours=5.5,
)

_PERSON_B = PersonalData(
    name="sample_b",
    birth_date=datetime(1975, 12, 1),
    birth_time=time(12, 30),
    birth_place="Sample City",
    latitude=6.9,
    longitude=79.8,
    timezone_offset_hours=5.5,
)


def test_ephemeris_lock_exists_and_is_reentrant():
    """The lock must be re-entrant: an outer entry point (e.g. a family scan)
    may build member Charts, each of which re-acquires the same lock on the
    same thread."""
    lock = utils.EPHEMERIS_LOCK
    acquired_outer = lock.acquire(timeout=1)
    assert acquired_outer
    try:
        acquired_inner = lock.acquire(timeout=1)
        assert acquired_inner, "EPHEMERIS_LOCK must be an RLock (re-entrant)"
        lock.release()
    finally:
        lock.release()


def test_every_c_library_entry_point_is_serialized():
    """Each public compute path that enters pyswisseph/pyjhora carries the
    ``_holds_ephemeris_lock`` marker set by the ``serialized_ephemeris``
    decorator — a new entry point added without the lock fails this list."""
    entry_points = [
        Chart._compute,
        muhurat.compute_muhurat_for_day,
        lagna_shuddhi.scan_lagna_shuddhi,
        lagna_shuddhi.scan_family_lagna_shuddhi,
        transits.get_current_transits,
        transits.get_sade_sati_info,
    ]
    for fn in entry_points:
        assert getattr(fn, "_holds_ephemeris_lock", False), (
            f"{fn.__qualname__} enters the C library but does not hold "
            "EPHEMERIS_LOCK (missing @serialized_ephemeris)"
        )


def test_chart_compute_actually_blocks_on_the_lock():
    """Behavioural proof the marker means something: while another thread
    holds EPHEMERIS_LOCK, constructing a Chart must block until release."""
    # Warm-up so imports/ephemeris init don't inflate the timing below.
    Chart(_PERSON_A)

    entered = threading.Event()
    finished = threading.Event()

    def _compute_in_thread() -> None:
        entered.set()
        Chart(_PERSON_B)
        finished.set()

    with utils.EPHEMERIS_LOCK:
        t = threading.Thread(target=_compute_in_thread, daemon=True)
        t.start()
        assert entered.wait(5)
        # A warm Chart computes in well under this window; if the thread
        # finishes while we still hold the lock, _compute never acquired it.
        assert not finished.wait(2.0), (
            "Chart._compute completed while another thread held "
            "EPHEMERIS_LOCK — the compute path is not serialized"
        )
    assert finished.wait(30), "Chart._compute never completed after release"
    t.join(timeout=30)


def test_concurrent_charts_match_serial_reference():
    """Determinism regression: N concurrent chart computations for two
    different people must each equal their single-threaded reference."""
    ref_a = Chart(_PERSON_A).snapshot()
    ref_b = Chart(_PERSON_B).snapshot()

    def _one(person: PersonalData):
        return Chart(person).snapshot()

    people = [_PERSON_A, _PERSON_B] * 4
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_one, people))

    for person, snap in zip(people, results):
        ref = ref_a if person is _PERSON_A else ref_b
        assert snap.rasi_chart == ref.rasi_chart, (
            f"concurrent chart for {person.name} diverged from its serial reference"
        )
