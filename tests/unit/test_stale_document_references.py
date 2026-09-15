"""Ticket: "Fix Outline Generation Error & Stale Source References", Issue 2.

Deleting a document from the (legacy, non-DIS) Source Library is a SOFT delete
-- Document.status flips "active" -> "archived", the row stays. Three read
paths that feed generation context never checked that status, so an archived
document kept contributing content forever:

  1. document_repository.get_documents_by_filenames() -- resolves
     context_document_names ("Additional Source Materials" picker) into the
     generation prompt.
  2. generation_jobs.py's CDD generation_params["source_document_ids"] replay
     -- a snapshot taken at CDD-creation time, replayed on every later
     regeneration from that CDD with no re-validation.
  3. build_style_context() / _build_unified_style_docs() (database.py), and
     ce_validation_service._fetch_ce_checklist()'s style-documents branch --
     any document ever linked to a Style kept injecting its content into every
     generation using that style.

Fixed by filtering on Document.status == "active" at each of these read
sites (not inside the shared get_document_by_id(), which is also used to
fetch/restore a document by id regardless of status -- that lookup must stay
unfiltered).
"""

from __future__ import annotations

import json
import uuid

import pytest


# ── get_documents_by_filenames: context_document_names resolution ─────────

class TestGetDocumentsByFilenamesExcludesArchived:
    def test_an_active_document_is_returned(self, db):
        from promptops_app.database import Document
        from promptops_app.repositories import document_repository

        db.add(Document(filename="active.pdf", content="ACTIVE_MARKER",
                        status="active", uploaded_by="tester"))
        db.commit()

        docs = document_repository.get_documents_by_filenames(db, ["active.pdf"])
        assert [d.filename for d in docs] == ["active.pdf"]

    def test_an_archived_document_is_excluded(self, db):
        from promptops_app.database import Document
        from promptops_app.repositories import document_repository

        db.add(Document(filename="deleted.pdf", content="ARCHIVED_MARKER",
                        status="archived", uploaded_by="tester"))
        db.commit()

        docs = document_repository.get_documents_by_filenames(db, ["deleted.pdf"])
        assert docs == [], (
            "an archived document's filename still resolved to its content -- "
            "deleting it from the Source Library did not stop it being injected"
        )


# ── build_style_context / _build_unified_style_docs: Style-linked docs ────

@pytest.fixture()
def style_with_active_and_archived_docs(db):
    from promptops_app.database import Document, Style, StyleDocument

    active_doc = Document(filename="active_ref.pdf", content="ACTIVE_STYLE_DOC_MARKER",
                          status="active", uploaded_by="tester")
    archived_doc = Document(filename="deleted_ref.pdf", content="ARCHIVED_STYLE_DOC_MARKER",
                            status="archived", uploaded_by="tester")
    db.add_all([active_doc, archived_doc])
    db.commit()
    db.refresh(active_doc)
    db.refresh(archived_doc)

    style = Style(style_id="style-mixed-docs", name="Mixed Docs Style")
    db.add(style)
    db.commit()
    db.refresh(style)
    db.add_all([
        StyleDocument(style_id=style.id, document_id=active_doc.id),
        StyleDocument(style_id=style.id, document_id=archived_doc.id),
    ])
    db.commit()
    db.refresh(style)
    return style


class TestStyleContextExcludesArchivedDocuments:
    def test_build_style_context_keeps_the_active_document(
        self, db, style_with_active_and_archived_docs
    ):
        from promptops_app.database import build_style_context

        ctx = build_style_context(db, style_with_active_and_archived_docs)
        assert "ACTIVE_STYLE_DOC_MARKER" in ctx

    def test_build_style_context_drops_the_archived_document(
        self, db, style_with_active_and_archived_docs
    ):
        from promptops_app.database import build_style_context

        ctx = build_style_context(db, style_with_active_and_archived_docs)
        assert "ARCHIVED_STYLE_DOC_MARKER" not in ctx, (
            "a document archived from the Source Library still reached the "
            "prompt because it was once linked to this Style"
        )

    def test_unified_style_docs_drops_the_archived_document(
        self, db, style_with_active_and_archived_docs
    ):
        from promptops_app.database import _build_unified_style_docs

        ctx = _build_unified_style_docs(db, style_with_active_and_archived_docs)
        assert "ACTIVE_STYLE_DOC_MARKER" in ctx
        assert "ARCHIVED_STYLE_DOC_MARKER" not in ctx


# ── ce_validation_service._fetch_ce_checklist: style-documents branch ──────

class TestCeChecklistSkipsArchivedStyleDocument:
    def test_an_archived_ce_checklist_document_is_not_used(self, db):
        from promptops_app.database import Document, Style, StyleDocument
        from promptops_app.services.ce_validation_service import _fetch_ce_checklist

        doc = Document(filename="CE_Checklist.docx", content="ARCHIVED_CHECKLIST_MARKER",
                       status="archived", uploaded_by="tester")
        db.add(doc)
        db.commit()
        db.refresh(doc)

        style = Style(style_id="style-archived-checklist", name="Archived Checklist Style",
                     custom_instructions="Be formal.")
        db.add(style)
        db.commit()
        db.refresh(style)
        db.add(StyleDocument(style_id=style.id, document_id=doc.id))
        db.commit()
        db.refresh(style)

        result = _fetch_ce_checklist(db, active_style=style)
        assert result != "ARCHIVED_CHECKLIST_MARKER"


# ── generation_jobs.py: CDD generation_params source_document_ids replay ──

@pytest.fixture()
def job_env(monkeypatch):
    from tests.conftest import _TestSessionLocal

    monkeypatch.setattr("promptops_app.jobs.generation_jobs.SessionLocal", _TestSessionLocal)
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
        "promptops_app.jobs.plagiarism_jobs.run_plagiarism_scan", _FakeCeleryTask(),
    )


@pytest.fixture()
def capture_llm(monkeypatch):
    from promptops_app.services.llm_service import LLMResult

    calls: list[dict] = []

    def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
        calls.append({"system": system_prompt, "user": user_prompt})
        return LLMResult(text="## Section\n\nBody.", model=model_choice,
                         prompt_tokens=10, completion_tokens=20,
                         status="success", stop_reason="stop")

    monkeypatch.setattr("promptops_app.jobs.generation_jobs._llm_call", fake)
    return calls


@pytest.fixture()
def course_with_cdd_snapshot_docs(db):
    """A course whose active CDD's generation_params snapshot names one active
    and one archived source document -- the shape generation_jobs.py replays
    on every regeneration."""
    from promptops_app.database import (
        Course, CourseDesignDocument, CDDVersion, Document, Project,
    )

    active_doc = Document(filename="active_source.pdf", content="ACTIVE_SNAPSHOT_DOC_MARKER",
                          status="active", uploaded_by="tester")
    archived_doc = Document(filename="deleted_source.pdf", content="ARCHIVED_SNAPSHOT_DOC_MARKER",
                            status="archived", uploaded_by="tester")
    db.add_all([active_doc, archived_doc])
    db.commit()
    db.refresh(active_doc)
    db.refresh(archived_doc)

    project = Project(name="Stale Doc Project", created_by="tester")
    db.add(project)
    db.commit()
    db.refresh(project)

    course = Course(project_id=project.id, name="Stale Doc Course", created_by="tester")
    db.add(course)
    db.commit()
    db.refresh(course)

    cdd = CourseDesignDocument(
        title="Stale Doc CDD", course_title="Stale Doc Course",
        active_version="v1", project_id=project.id, course_id=course.id,
        created_by="tester",
    )
    db.add(cdd)
    db.commit()
    db.refresh(cdd)

    version = CDDVersion(
        cdd_id=cdd.id, version="v1", full_content="CDD body.",
        generation_params=json.dumps({
            "source_document_ids": [active_doc.id, archived_doc.id],
        }),
        is_active=True, created_by="tester",
    )
    db.add(version)
    db.commit()

    return {"project": project, "course": course, "cdd": cdd}


def _make_job(db, course, project, cdd_id, **overrides):
    from promptops_app.database import GenerationJob

    params = {
        "topic": "Test Topic", "b_type": "Lesson", "model_choice": "GPT-5.4",
        "target_audience": "Learners", "expert_domain": "Testing",
        "user_name": "tester", "project_id": project.id, "course_id": course.id,
        "eff_cdd_id": cdd_id,
    }
    params.update(overrides)
    job = GenerationJob(id=str(uuid.uuid4()), job_type="generation", status="queued",
                       input_payload_json=json.dumps(params))
    db.add(job)
    db.commit()
    return job


class TestCddSourceDocumentSnapshotExcludesArchived:
    def test_the_active_snapshot_document_still_reaches_the_prompt(
        self, db, job_env, capture_llm, course_with_cdd_snapshot_docs
    ):
        from promptops_app.jobs.generation_jobs import run_generation_job

        job = _make_job(
            db, course_with_cdd_snapshot_docs["course"], course_with_cdd_snapshot_docs["project"],
            course_with_cdd_snapshot_docs["cdd"].id,
        )
        run_generation_job(job.id)

        assert capture_llm, "the job never reached the LLM"
        both = capture_llm[0]["system"] + capture_llm[0]["user"]
        assert "ACTIVE_SNAPSHOT_DOC_MARKER" in both

    def test_the_archived_snapshot_document_no_longer_reaches_the_prompt(
        self, db, job_env, capture_llm, course_with_cdd_snapshot_docs
    ):
        from promptops_app.jobs.generation_jobs import run_generation_job

        job = _make_job(
            db, course_with_cdd_snapshot_docs["course"], course_with_cdd_snapshot_docs["project"],
            course_with_cdd_snapshot_docs["cdd"].id,
        )
        run_generation_job(job.id)

        assert capture_llm
        both = capture_llm[0]["system"] + capture_llm[0]["user"]
        assert "ARCHIVED_SNAPSHOT_DOC_MARKER" not in both, (
            "a document archived from the Source Library was still replayed "
            "into the prompt from the CDD's stale generation_params snapshot"
        )
