"""Generate's own version of test_blueprint_source_grounding_reported.py /
test_cdd_source_grounding_reported.py -- an unreachable Source Library was the
one outcome Generate never recorded anywhere, on either the request-time
retrieval (generations.py) or the job that actually runs (generation_jobs.py).

Generate has no CDDVersion/BlueprintVersion-equivalent row of its own at
request time -- the eventual Generation row is written later, inside the job,
via the params generations.py hands it. So the fix has two halves: the
retrieval call records why there is nothing (generations.py, same as the CDD
and Blueprint routes), and the job reads that back onto the content.generated
audit event it already writes (generation_jobs.py) -- otherwise it would be
recorded somewhere nobody reads, which is the same failure in a new spot.
"""

from __future__ import annotations

import json
import uuid

import pytest


@pytest.fixture()
def project(db):
    from promptops_app.database import Project

    p = Project(name="Generate Grounding Project", created_by="tester")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def course(db, project):
    from promptops_app.database import Course

    c = Course(project_id=project.id, name="Generate Grounding Course", created_by="tester")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def dis(monkeypatch):
    state = {"mode": "empty"}

    def fake(purpose, payload, current_user=None, client_id="", timeout=None):
        if state["mode"] == "raise":
            raise RuntimeError("connect to dis_backend:8000 failed: token abc123")
        if state["mode"] == "context":
            return {"combined_context": "SOURCE MATERIAL", "source_units": ["unit-1"]}
        return {"combined_context": "", "source_units": []}

    monkeypatch.setattr(
        "app.api.v1.routers.generations.dis_client.retrieve_context_sync", fake)
    return state


def _launch(client, headers, course, project, **overrides):
    body = {
        "course_id": course.id, "project_id": project.id,
        "component_value": "lesson_1", "component_label": "Lesson 1",
        "component_type": "lesson", "model_choice": "GPT-5.4",
    }
    body.update(overrides)
    return client.post("/api/v1/generations/launch", json=body, headers=headers)


class TestTheLaunchRequestRecordsUnavailability:
    """generations.py's own half: the retrieval call must say why there is
    nothing, in the params the job later reads -- checked before the job even
    runs, since that is where the fix actually sits."""

    def test_an_unreachable_library_is_recorded_in_the_job_params(
        self, client, auth_headers, db, course, project, dis
    ):
        from promptops_app.database import GenerationJob

        dis["mode"] = "raise"

        resp = _launch(client, auth_headers, course, project)
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]

        job = db.query(GenerationJob).filter_by(id=job_id).first()
        params = json.loads(job.request_json)
        assert params["source_context_unavailable"] == "RuntimeError"

    def test_an_answering_library_records_nothing(
        self, client, auth_headers, db, course, project, dis
    ):
        from promptops_app.database import GenerationJob

        dis["mode"] = "empty"

        resp = _launch(client, auth_headers, course, project)
        job_id = resp.json()["job_id"]

        job = db.query(GenerationJob).filter_by(id=job_id).first()
        params = json.loads(job.request_json)
        assert params["source_context_unavailable"] == ""


class TestTheGenerationsAuditRowRecordsUnavailability:
    """generation_jobs.py's own half: whatever generations.py recorded must
    actually reach the row an auditor would look at (content.generated), not
    just sit unread in request_json."""

    @pytest.fixture(autouse=True)
    def job_env(self, monkeypatch):
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

    @pytest.fixture(autouse=True)
    def stub_llm(self, monkeypatch):
        from promptops_app.services.llm_service import LLMResult

        def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
            return LLMResult(text="## Section\n\nBody.", model=model_choice,
                             prompt_tokens=10, completion_tokens=20,
                             status="success", stop_reason="stop")

        monkeypatch.setattr("promptops_app.jobs.generation_jobs._llm_call", fake)

    def _run_job_from_params(self, db, **param_overrides):
        from promptops_app.database import GenerationJob
        from promptops_app.jobs.generation_jobs import run_generation_job

        params = {
            "topic": "Test Topic", "b_type": "Lesson", "model_choice": "GPT-5.4",
            "user_name": "tester",
        }
        params.update(param_overrides)
        job = GenerationJob(id=str(uuid.uuid4()), job_type="generation", status="queued",
                           input_payload_json=json.dumps(params))
        db.add(job)
        db.commit()
        run_generation_job(job.id)
        return job.id

    def _audit_metadata_for(self, db, job_id):
        from promptops_app.database import AuditLog, Generation

        gen = db.query(Generation).filter_by(job_id=job_id).first()
        assert gen is not None, "job never produced a Generation row"
        entry = (
            db.query(AuditLog)
            .filter_by(action="content.generated", entity_type="generation", entity_id=str(gen.id))
            .first()
        )
        assert entry is not None, "content.generated audit event was never written"
        return json.loads(entry.metadata_json or "{}")

    def test_an_unreachable_library_reaches_the_audit_row(self, db):
        job_id = self._run_job_from_params(
            db, source_context_unavailable="RuntimeError", dis_source_units=[],
        )

        metadata = self._audit_metadata_for(db, job_id)
        assert metadata["source_context_unavailable"] == "RuntimeError"

    def test_an_answering_library_reports_nothing(self, db):
        job_id = self._run_job_from_params(
            db, source_context_unavailable="", dis_source_units=["unit-1"],
        )

        metadata = self._audit_metadata_for(db, job_id)
        assert metadata["source_context_unavailable"] is None
        assert metadata["dis_source_units_count"] == 1
