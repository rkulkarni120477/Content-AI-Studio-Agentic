"""P4.6 — job-queue position visibility (F19).

`count_active_jobs_ahead` returns how many still-active (queued/running) jobs were
created before a given job — a backend-agnostic queue position surfaced to the
user instead of a bare spinner.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from promptops_app.repositories import job_repository


def _job(db, *, status, minutes):
    from promptops_app.database import GenerationJob

    base = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
    job = GenerationJob(
        id=f"job-{status}-{minutes}",
        job_type="generation",
        status=status,
        input_payload_json="{}",
        created_at=base + timedelta(minutes=minutes),
    )
    db.add(job)
    db.commit()
    return job


def test_counts_only_active_jobs_created_earlier(db):
    # Earlier + active → counted.
    _job(db, status="queued", minutes=0)
    _job(db, status="running", minutes=1)
    # Earlier but terminal → NOT counted.
    _job(db, status="completed", minutes=2)
    _job(db, status="failed", minutes=3)
    # The job under test.
    target = _job(db, status="queued", minutes=4)
    # Later + active → NOT counted (it's behind, not ahead).
    _job(db, status="queued", minutes=5)

    assert job_repository.count_active_jobs_ahead(db, target) == 2


def test_front_of_queue_has_zero_ahead(db):
    first = _job(db, status="queued", minutes=0)
    _job(db, status="queued", minutes=1)
    _job(db, status="queued", minutes=2)

    assert job_repository.count_active_jobs_ahead(db, first) == 0
