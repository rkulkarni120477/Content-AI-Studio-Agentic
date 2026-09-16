"""Content generation left max_tokens unset on every LLM call, so every
provider fell through to llm_client.py's flat DEFAULT_MAX_OUTPUT_TOKENS
(16384) -- including Bedrock models whose catalog entry (models.py) already
advertises a real 64000-token ceiling. Long generations (and the CE
validation "fix" pass, which regenerates the whole document) silently cut
off mid-sentence once the flat cap was hit, with only a backend log line as
evidence.

Fixed by resolving the model's own max_output_tokens and passing it through,
matching the pattern already used by block_wide_generator.py, cdd_import_
service.py and outline_import_service.py.
"""
from __future__ import annotations

import json
import uuid

import pytest

from promptops_app.services.llm_service import LLMResult


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
    calls: list[dict] = []

    def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
        calls.append({"model": model_choice, "max_tokens": kwargs.get("max_tokens")})
        return LLMResult(text="## Section\n\nBody.", model=model_choice,
                         prompt_tokens=10, completion_tokens=20,
                         status="success", stop_reason="stop")

    monkeypatch.setattr("promptops_app.jobs.generation_jobs._llm_call", fake)
    return calls


@pytest.fixture()
def course(db):
    from promptops_app.database import Course, Project

    project = Project(name="Token Headroom Project", created_by="tester")
    db.add(project)
    db.commit()
    db.refresh(project)

    course = Course(project_id=project.id, name="Token Headroom Course", created_by="tester")
    db.add(course)
    db.commit()
    db.refresh(course)
    return {"project": project, "course": course}


def _make_job(db, course, project, **overrides):
    from promptops_app.database import GenerationJob

    params = {
        "topic": "Test Topic", "b_type": "Lesson", "model_choice": "GPT-5.4",
        "target_audience": "Learners", "expert_domain": "Testing",
        "user_name": "tester", "project_id": project.id, "course_id": course.id,
    }
    params.update(overrides)
    job = GenerationJob(id=str(uuid.uuid4()), job_type="generation", status="queued",
                       input_payload_json=json.dumps(params))
    db.add(job)
    db.commit()
    return job


class TestGenerationRequestsModelAwareHeadroom:
    def test_openai_model_gets_its_catalog_cap(self, db, job_env, capture_llm, course):
        from promptops_app.core.models import resolve_model
        from promptops_app.jobs.generation_jobs import run_generation_job

        job = _make_job(db, course["course"], course["project"], model_choice="GPT-5.4")
        run_generation_job(job.id)

        assert capture_llm, "the job never reached the LLM"
        assert capture_llm[0]["max_tokens"] == resolve_model("GPT-5.4").max_output_tokens

    def test_bedrock_model_gets_its_larger_catalog_cap_not_the_flat_default(
        self, db, job_env, capture_llm, course
    ):
        """The regression this test guards against: a Bedrock model advertising
        64000 tokens must not be silently capped to the flat 16384 default."""
        from promptops_app.jobs.generation_jobs import run_generation_job

        job = _make_job(
            db, course["course"], course["project"],
            model_choice="Claude Sonnet 4.5 (Bedrock)",
        )
        run_generation_job(job.id)

        assert capture_llm
        assert capture_llm[0]["max_tokens"] == 64000


class TestCeValidationFixPassRequestsModelAwareHeadroom:
    def test_the_fix_call_is_given_the_models_real_ceiling(self):
        from promptops_app.services.ce_validation_service import run_ce_validation

        calls: list[dict] = []

        def fake_llm(model_choice, system, user, *args, **kwargs):
            calls.append({"user": user, "max_tokens": kwargs.get("max_tokens")})
            if len(calls) == 1:
                return LLMResult(text='{"passed": false, "issues": ["too informal"]}',
                                 model=model_choice, prompt_tokens=1, completion_tokens=1,
                                 status="success", stop_reason="stop")
            return LLMResult(text="Fixed content.", model=model_choice,
                             prompt_tokens=1, completion_tokens=1,
                             status="success", stop_reason="stop")

        class _Style:
            style_documents = []
            custom_instructions = "Be formal."
            generated_summary = ""

        out = run_ce_validation(
            "Original content.", db=None, active_style=_Style(),
            model_choice="Claude Sonnet 4.5 (Bedrock)", llm_call_fn=fake_llm,
        )

        assert out == "Fixed content."
        fix_call = calls[1]
        assert fix_call["max_tokens"] == 64000
