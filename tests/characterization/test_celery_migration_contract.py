"""P4.7 — automated tests for the Celery migration.

Complements the idempotency tests in ``test_generation_idempotency.py`` and the
dispatch routing/fallback tests in ``tests/unit/test_job_dispatch.py``. Here we
pin:

  * the GenerationJob / job_status polling contract the frontend reads is
    unchanged after the execution backend swap (completed AND failed paths);
  * P4.5's queued regenerate-item endpoint processes a burst of jobs correctly
    and independently (each block gets its own item, no cross-contamination).
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.conftest import _TestSessionLocal


# ── Generation job env (mirrors test_run_generation_job.job_env) ──────────────
@pytest.fixture()
def gen_job_env(monkeypatch):
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
        "promptops_app.jobs.plagiarism_jobs.run_plagiarism_scan", _FakeCeleryTask()
    )


def _make_generation_job(db):
    from promptops_app.database import GenerationJob

    job = GenerationJob(
        id=uuid.uuid4().hex,
        job_type="generation",
        status="queued",
        input_payload_json=json.dumps({
            "topic": "Infection Control", "b_type": "Lesson",
            "model_choice": "GPT-5.4", "user_name": "tester",
        }),
    )
    db.add(job)
    db.commit()
    return job


class TestPollingContract:
    def test_completed_job_exposes_the_fields_the_poller_reads(
        self, db, gen_job_env, capture_llm
    ):
        from promptops_app.database import GenerationJob
        from promptops_app.jobs.generation_jobs import run_generation_job

        job = _make_generation_job(db)
        run_generation_job(job.id)

        db.expire_all()
        j = db.query(GenerationJob).filter_by(id=job.id).first()
        # These are exactly the fields app/api/v1/routers/jobs.py returns.
        assert j.status == "completed"
        assert j.progress == 100
        assert j.current_step == "Completed"
        assert j.result_entity_id is not None   # -> generation_id in the response
        assert j.completed_at is not None
        assert j.error_message is None

    def test_failed_job_exposes_error_contract(self, db, gen_job_env, monkeypatch):
        from promptops_app.database import GenerationJob
        from promptops_app.jobs import generation_jobs
        from promptops_app.services.llm_service import LLMResult

        # LLM returns an error result → the job must fail cleanly.
        monkeypatch.setattr(
            generation_jobs, "_llm_call",
            lambda *a, **kw: LLMResult(text="boom", model="mock", status="error",
                                       error_type="provider"),
        )
        job = _make_generation_job(db)
        generation_jobs.run_generation_job(job.id)

        db.expire_all()
        j = db.query(GenerationJob).filter_by(id=job.id).first()
        assert j.status == "failed"
        assert j.error_message  # user-safe message present
        assert j.result_entity_id is None
        assert j.completed_at is not None


# ── Regenerate-item burst (P4.5 queued endpoint) ──────────────────────────────
@pytest.fixture()
def regen_env(monkeypatch):
    monkeypatch.setattr(
        "promptops_app.jobs.regen_jobs.SessionLocal", _TestSessionLocal
    )
    monkeypatch.setattr(
        "promptops_app.parsers.blueprint_parser.regen_single_item",
        lambda *a, **kw: "REGEN",
    )


def _make_block(db, first="First", second="Second"):
    from promptops_app.database import Block, Generation

    gen = Generation(prompt_name="", prompt_version="", block_type="Lesson",
                     topic="t", output_text="x", created_by="tester")
    db.add(gen)
    db.commit()
    db.refresh(gen)
    block = Block(generation_id=gen.id, block_type="Lesson", block_label="B",
                  content=f"- {first}\n- {second}")
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _make_regen_job(db, block_id, item_index):
    from promptops_app.database import GenerationJob

    job = GenerationJob(
        id=uuid.uuid4().hex,
        job_type="regenerate_item",
        status="queued",
        input_payload_json=json.dumps({
            "block_id": block_id, "item_index": item_index,
            "section_key": "B", "feedback": "", "model_choice": "GPT-5.4",
            "user_name": "tester",
        }),
    )
    db.add(job)
    db.commit()
    return job


class TestRegenerateItemBurst:
    def test_burst_of_queued_jobs_all_complete_independently(self, db, regen_env):
        """A burst of queued regenerate-item jobs (as the queue drains) each
        regenerate their own block's targeted item with no cross-contamination."""
        from promptops_app.database import Block, GenerationJob
        from promptops_app.jobs.regen_jobs import run_regenerate_item_job

        # 5 blocks; regenerate item 0 in each.
        blocks = [_make_block(db, first=f"A{i}", second=f"B{i}") for i in range(5)]
        jobs = [_make_regen_job(db, b.id, 0) for b in blocks]

        for j in jobs:
            run_regenerate_item_job(j.id)

        db.expire_all()
        for i, (b, j) in enumerate(zip(blocks, jobs)):
            rb = db.query(Block).filter_by(id=b.id).first()
            rj = db.query(GenerationJob).filter_by(id=j.id).first()
            assert rj.status == "completed"
            assert "REGEN" in rb.content            # its own item regenerated
            assert f"B{i}" in rb.content            # its own second item preserved
