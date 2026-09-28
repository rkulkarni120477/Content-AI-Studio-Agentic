"""P4.1 — generation-job idempotency under Celery at-least-once retry.

Celery is configured with ``task_acks_late`` + ``task_reject_on_worker_lost``,
so a worker crash re-runs a task. These tests pin the guarantee that a retry
never duplicates content or (once P2 lands) double-charges usage:

  * a job already marked completed is a no-op on re-run — no second LLM call,
    no second Generation/Block set;
  * a job that crashed *after* persisting its Generation (job_id stamped) but
    *before* being marked completed is detected on re-run and adopted, not
    regenerated — again no second LLM call.

The dispatch-layer routing/fallback (Celery vs. in-process threadpool) is
covered in tests/unit/test_job_dispatch.py.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.conftest import _TestSessionLocal


@pytest.fixture()
def job_env(monkeypatch):
    """Point the job's self-managed session at the test DB, neutralise CE
    validation, and stub the post-persist plagiarism enqueue (needs Redis)."""
    monkeypatch.setattr(
        "promptops_app.jobs.generation_jobs.SessionLocal", _TestSessionLocal
    )
    monkeypatch.setattr(
        "promptops_app.services.ce_validation_service.run_ce_validation",
        lambda out, db, **kwargs: out,
    )

    class _FakeTask:
        id = "fake-task-id"

    class _FakeCeleryTask:
        @staticmethod
        def delay(*args, **kwargs):
            return _FakeTask()

    monkeypatch.setattr(
        "promptops_app.jobs.plagiarism_jobs.run_plagiarism_scan",
        _FakeCeleryTask(),
    )


def _make_job(db, **overrides):
    from promptops_app.database import GenerationJob

    params = {
        "topic": "Infection Control",
        "b_type": "Lesson",
        "model_choice": "GPT-5.4",
        "target_audience": "Nursing Students Year 2",
        "expert_domain": "Clinical Nursing",
        "expert_exp": 20,
        "aud_cat": "Professional/Corporate",
        "user_name": "test_admin",
    }
    params.update(overrides)
    job = GenerationJob(
        id=str(uuid.uuid4()),
        job_type="generation",
        status="queued",
        input_payload_json=json.dumps(params),
    )
    db.add(job)
    db.commit()
    return job


def _gen_count(db):
    from promptops_app.database import Generation

    return db.query(Generation).count()


def _block_count(db):
    from promptops_app.database import Block

    return db.query(Block).count()


class TestGenerationRetryIdempotency:
    def test_completed_job_rerun_is_noop(self, db, job_env, capture_llm):
        """Re-running a completed job makes no second LLM call and creates no
        duplicate Generation/Block rows."""
        from promptops_app.database import Generation
        from promptops_app.jobs.generation_jobs import run_generation_job

        job = _make_job(db)
        run_generation_job(job.id)

        db.expire_all()
        # 2 calls: the primary generation, then the continuity review pass
        # (CANNED_LLM_TEXT has 3 headings, meeting its review threshold).
        assert len(capture_llm) == 2
        gens_after_first = _gen_count(db)
        blocks_after_first = _block_count(db)
        gen = db.query(Generation).order_by(Generation.id.desc()).first()
        assert gen.job_id == job.id          # stamped with the idempotency key

        # Simulate Celery re-delivering the same task after completion.
        run_generation_job(job.id)

        db.expire_all()
        assert len(capture_llm) == 2, "retry must not call the LLM again"
        assert _gen_count(db) == gens_after_first, "retry must not add a Generation"
        assert _block_count(db) == blocks_after_first, "retry must not add Blocks"

    def test_crashed_before_completion_is_adopted_not_regenerated(
        self, db, job_env, capture_llm
    ):
        """A Generation persisted by a prior attempt (job_id stamped) but whose
        job never reached 'completed' is adopted on retry — no regeneration."""
        from promptops_app.database import Generation, GenerationJob
        from promptops_app.jobs.generation_jobs import run_generation_job
        from promptops_app.jobs.job_status import JobStatus

        job = _make_job(db)
        # Prior attempt: a Generation exists keyed to this job, but the worker
        # crashed before set_completed, so the job is still 'running'.
        prior = Generation(
            prompt_name="",
            prompt_version="",
            block_type="Lesson",
            topic="Infection Control",
            output_text="prior output",
            created_by="test_admin",
            job_id=job.id,
        )
        db.add(prior)
        job.status = JobStatus.RUNNING
        db.commit()
        prior_id = prior.id

        run_generation_job(job.id)

        db.expire_all()
        assert len(capture_llm) == 0, "must adopt the prior generation, not call the LLM"
        assert _gen_count(db) == 1, "must not create a second Generation"
        refreshed = db.query(GenerationJob).filter_by(id=job.id).first()
        assert refreshed.status == JobStatus.COMPLETED
        assert refreshed.result_entity_id == prior_id
