"""In-process async job store for the scan-class electional endpoints (CR-4).

The service runs as a single, GIL-bound worker; a wide date-range scan (up to
``MAX_LAGNA_SHUDDHI_DAYS`` days, both solo and multi-member) can occupy a
request's thread -- and the caller's open HTTP connection -- for the full
scan duration, which starves concurrent interactive chart requests behind
it and risks the caller's own connection/proxy timeout. This module gives
those scan endpoints an ADDITIVE async submit/poll variant: submit hands
back a job id immediately and the scan itself runs on a small dedicated
background thread pool, decoupled from the submitting request's lifecycle,
so the HTTP call that kicks it off returns right away.

This is intentionally the simplest mechanism available that needs no new
infrastructure: an in-memory dict guarded by a lock, plus a stdlib
``ThreadPoolExecutor``. No queue, no Redis, no second process. Job state is
process-local and does not survive a restart -- acceptable here because the
existing synchronous ``/v1/*`` endpoints remain fully intact as the
canonical, durable path; this store only backs the additive async variant.
"""
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

JobStatus = Literal["pending", "running", "done", "error"]


@dataclass
class Job:
    id: str
    status: JobStatus = "pending"
    result: Any = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class JobStore:
    """Thread-safe in-memory job registry with its own small worker pool.

    The pool is deliberately separate from FastAPI/Starlette's own request
    thread pool: a burst of scan submissions must not be able to exhaust the
    threads that serve ordinary request handling. ``max_workers`` is small
    on purpose -- these are CPU-bound scans on a GIL-bound process, so
    running many of them "concurrently" would not add real throughput, only
    contention; a small bound simply lets a handful of scans queue and run
    to completion without blocking submission of new jobs or polling of
    existing ones (both of which only touch the lock-protected dict).
    """

    def __init__(self, max_workers: int = 4) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="calc-scan-job"
        )

    def submit(self, fn: Callable[[], Any]) -> str:
        """Register a new job and hand its execution to the background pool.

        Returns the job id immediately; ``fn`` has not necessarily started
        running yet by the time this call returns (it may still be queued
        behind other jobs in the pool) -- that is the point: submission is
        O(1) and never waits on the scan itself.
        """
        job_id = uuid.uuid4().hex
        job = Job(id=job_id)
        with self._lock:
            self._jobs[job_id] = job

        def _run() -> None:
            with self._lock:
                job.status = "running"
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 - reported via poll, never re-raised
                with self._lock:
                    job.status = "error"
                    job.error = str(exc)
                return
            with self._lock:
                job.status = "done"
                job.result = result

        self._executor.submit(_run)
        return job_id

    def get(self, job_id: str) -> Job | None:
        """Return a point-in-time snapshot of the job, or None if unknown.

        A copy is returned (rather than the live object) so a caller can
        never observe or mutate a torn/partial write from the worker thread.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return Job(
                id=job.id,
                status=job.status,
                result=job.result,
                error=job.error,
                created_at=job.created_at,
            )


# Process-wide singleton. Job state is intentionally process-local (see the
# module docstring); this is acceptable because the synchronous /v1/*
# endpoints remain the durable, canonical computation path -- this store
# only ever backs the additive async convenience variant.
job_store = JobStore()
