"""Apply-feedback as a background job.

``POST /feedback/apply`` used to regenerate a module's blocks inline in the
request — one LLM call per block — which blew past the browser's 120s timeout on
larger modules, cancelling the request while the server kept working. The
endpoint now validates + enqueues, and this worker does the regeneration off the
request thread. These tests pin the worker's behaviour: it regenerates the
module's blocks against the compiled feedback, records the summary the result
endpoint serves, marks the job completed, and is idempotent under retry.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import _TestSessionLocal


@pytest.fixture()
def apply_env(monkeypatch):
    """Point the job's session at the test DB and stub the LLM regen call."""
    monkeypatch.setattr(
        "promptops_app.jobs.regen_jobs.SessionLocal", _TestSessionLocal
    )
    result = MagicMock()
    result.status = "ok"
    result.is_error = False
    result.text = "Improved content with local examples"
    llm = patch(
        "promptops_app.services.llm_service.generate_with_metadata",
        return_value=result,
    )
    mock = llm.start()
    yield mock
    llm.stop()


def _make_graph(db):
    """Course + blueprint + one lesson block + one active feedback item."""
    from promptops_app.database import (
        Block,
        Cluster,
        Course,
        CourseDesignDocument,
        FeedbackDocument,
        FeedbackItem,
        Generation,
        ModuleBlueprint,
        Project,
    )

    project = Project(name="Apply Proj", is_active=True)
    db.add(project)
    db.flush()
    cluster = Cluster(name="Apply Cluster", project_id=project.id, is_active=True)
    db.add(cluster)
    db.flush()
    course = Course(
        name="Apply Course",
        project_id=project.id,
        cluster_id=cluster.id,
        is_active=True,
        config_model_choice="test-model",
    )
    db.add(course)
    db.flush()
    cdd = CourseDesignDocument(
        title="CDD", course_title="Apply Course", course_id=course.id,
        project_id=project.id, created_by="tester",
    )
    db.add(cdd)
    db.flush()
    bp = ModuleBlueprint(
        cdd_id=cdd.id, title="BP1", module_title="Intro", module_number=1,
        project_id=project.id, course_id=course.id, created_by="tester",
    )
    db.add(bp)
    db.flush()
    gen = Generation(
        topic="Lesson A", prompt_name="lesson", prompt_version="v1",
        block_type="lesson", output_text="body", project_id=project.id,
        course_id=course.id, cdd_id=cdd.id, blueprint_id=bp.id, created_by="tester",
    )
    db.add(gen)
    db.flush()
    block = Block(
        generation_id=gen.id, block_type="lesson", block_label="Lesson A",
        content="Original content", workflow_state="draft", version_num=1,
    )
    db.add(block)
    db.flush()
    doc = FeedbackDocument(
        project_id=project.id, course_id=course.id, blueprint_id=None,
        filename="review.pptx", file_type="pptx", content="text",
        item_count=1, status="active", created_by="tester",
    )
    db.add(doc)
    db.flush()
    item = FeedbackItem(
        document_id=doc.id, project_id=project.id, course_id=course.id,
        blueprint_id=bp.id, feedback_text="Add local examples", theme="Content",
        sentiment="suggestion", priority="high", status="active", created_by="tester",
    )
    db.add(item)
    db.commit()
    return {"course": course, "bp": bp, "block": block, "item": item}


def _make_job(db, *, item_ids, blueprint_id, course_id, status="queued"):
    from promptops_app.database import GenerationJob

    job = GenerationJob(
        id=str(uuid.uuid4()),
        job_type="apply_feedback",
        status=status,
        input_payload_json=json.dumps({
            "item_ids": item_ids,
            "blueprint_id": blueprint_id,
            "course_id": course_id,
            "user_name": "tester",
        }),
        course_id=course_id,
    )
    db.add(job)
    db.commit()
    return job


class TestApplyFeedbackJob:
    def test_regenerates_block_and_completes(self, db, apply_env):
        from promptops_app.database import Block, GenerationJob
        from promptops_app.jobs.regen_jobs import run_apply_feedback_job

        graph = _make_graph(db)
        job = _make_job(
            db, item_ids=[graph["item"].id], blueprint_id=graph["bp"].id,
            course_id=graph["course"].id,
        )

        run_apply_feedback_job(job.id)

        db.expire_all()
        assert apply_env.called
        block = db.query(Block).filter_by(id=graph["block"].id).first()
        assert block.content == "Improved content with local examples"

        refreshed = db.query(GenerationJob).filter_by(id=job.id).first()
        assert refreshed.status == "completed"
        summary = json.loads(refreshed.result_json)
        assert summary["blueprint_id"] == graph["bp"].id
        assert summary["module_label"] == "Module 1 — Intro"
        assert summary["skipped"] == 0
        assert summary["regenerated"][0]["block_id"] == graph["block"].id
        assert "Add local examples" in summary["instruction"]

    def test_completed_job_rerun_is_noop(self, db, apply_env):
        from promptops_app.database import BlockVersion
        from promptops_app.jobs.regen_jobs import run_apply_feedback_job

        graph = _make_graph(db)
        job = _make_job(
            db, item_ids=[graph["item"].id], blueprint_id=graph["bp"].id,
            course_id=graph["course"].id,
        )

        run_apply_feedback_job(job.id)
        db.expire_all()
        versions_after_first = (
            db.query(BlockVersion).filter_by(block_id=graph["block"].id).count()
        )
        calls_after_first = apply_env.call_count

        # Celery re-delivers the same completed task.
        run_apply_feedback_job(job.id)

        db.expire_all()
        assert apply_env.call_count == calls_after_first, "retry must not call the LLM again"
        assert (
            db.query(BlockVersion).filter_by(block_id=graph["block"].id).count()
            == versions_after_first
        ), "retry must not add another version"

    def test_missing_blueprint_fails_cleanly(self, db, apply_env):
        from promptops_app.database import GenerationJob
        from promptops_app.jobs.regen_jobs import run_apply_feedback_job

        graph = _make_graph(db)
        job = _make_job(
            db, item_ids=[graph["item"].id], blueprint_id=999999,
            course_id=graph["course"].id,
        )

        run_apply_feedback_job(job.id)

        db.expire_all()
        assert not apply_env.called
        refreshed = db.query(GenerationJob).filter_by(id=job.id).first()
        assert refreshed.status == "failed"
        assert "module" in (refreshed.error_message or "").lower()
