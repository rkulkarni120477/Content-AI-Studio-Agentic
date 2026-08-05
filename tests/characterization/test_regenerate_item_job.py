"""P4.5 — single-item block regeneration as a background job.

The endpoint now queues this job instead of calling the LLM inline. These tests
pin the job's behaviour: it regenerates the targeted item, saves a version,
marks the job completed, and is idempotent under Celery at-least-once retry.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.conftest import _TestSessionLocal


@pytest.fixture()
def regen_env(monkeypatch):
    """Point the job's session at the test DB and stub the LLM regen call."""
    monkeypatch.setattr(
        "promptops_app.jobs.regen_jobs.SessionLocal", _TestSessionLocal
    )
    calls = {"n": 0}

    def _fake_regen(*args, **kwargs):
        calls["n"] += 1
        return "REGENERATED ITEM TEXT"

    monkeypatch.setattr(
        "promptops_app.parsers.blueprint_parser.regen_single_item", _fake_regen
    )
    return calls


def _make_block(db):
    from promptops_app.database import Block, Generation

    gen = Generation(
        prompt_name="", prompt_version="", block_type="Lesson",
        topic="Hygiene", output_text="x", created_by="tester",
    )
    db.add(gen)
    db.commit()
    db.refresh(gen)
    block = Block(
        generation_id=gen.id,
        block_type="Lesson",
        block_label="Hygiene - Body",
        content="- First item about hygiene\n- Second item about safety",
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _make_job(db, block_id, item_index=0, **overrides):
    from promptops_app.database import GenerationJob

    params = {
        "block_id": block_id,
        "item_index": item_index,
        "section_key": "Body",
        "feedback": "",
        "model_choice": "GPT-5.4",
        "user_name": "tester",
    }
    params.update(overrides)
    job = GenerationJob(
        id=str(uuid.uuid4()),
        job_type="regenerate_item",
        status="queued",
        input_payload_json=json.dumps(params),
    )
    db.add(job)
    db.commit()
    return job


class TestRegenerateItemJob:
    def test_regenerates_item_and_completes(self, db, regen_env):
        from promptops_app.database import Block, GenerationJob
        from promptops_app.jobs.regen_jobs import run_regenerate_item_job

        block = _make_block(db)
        job = _make_job(db, block.id, item_index=0)

        run_regenerate_item_job(job.id)

        db.expire_all()
        assert regen_env["n"] == 1
        refreshed_block = db.query(Block).filter_by(id=block.id).first()
        assert "REGENERATED ITEM TEXT" in refreshed_block.content
        assert "Second item about safety" in refreshed_block.content  # others preserved
        refreshed_job = db.query(GenerationJob).filter_by(id=job.id).first()
        assert refreshed_job.status == "completed"
        assert refreshed_job.result_entity_id == block.id
        result = json.loads(refreshed_job.result_json)
        assert result["block_id"] == block.id
        assert result["patched_item"] == "REGENERATED ITEM TEXT"

    def test_completed_job_rerun_is_noop(self, db, regen_env):
        from promptops_app.database import BlockVersion
        from promptops_app.jobs.regen_jobs import run_regenerate_item_job

        block = _make_block(db)
        job = _make_job(db, block.id, item_index=1)

        run_regenerate_item_job(job.id)
        db.expire_all()
        versions_after_first = db.query(BlockVersion).filter_by(block_id=block.id).count()

        # Celery re-delivers the same completed task.
        run_regenerate_item_job(job.id)

        db.expire_all()
        assert regen_env["n"] == 1, "retry must not call the LLM again"
        assert (
            db.query(BlockVersion).filter_by(block_id=block.id).count()
            == versions_after_first
        ), "retry must not add another version"

    def test_bad_item_index_fails_without_calling_llm(self, db, regen_env):
        from promptops_app.database import GenerationJob
        from promptops_app.jobs.regen_jobs import run_regenerate_item_job

        block = _make_block(db)
        job = _make_job(db, block.id, item_index=99)  # out of range

        run_regenerate_item_job(job.id)

        db.expire_all()
        assert regen_env["n"] == 0
        refreshed_job = db.query(GenerationJob).filter_by(id=job.id).first()
        assert refreshed_job.status == "failed"
        assert "not found" in (refreshed_job.error_message or "").lower()
