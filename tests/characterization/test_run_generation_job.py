"""
Characterization: promptops_app/jobs/generation_jobs.run_generation_job

Generate is the ONLY stage whose prompts are fully hard-coded — it never
consults build_prompt/load_template. These tests pin down that behavior
(plan: "Pipeline prompt-resolution state") so Phase 8's move onto the
DB-backed path is a deliberate, visible change:

  * system prompt = PERSONA_PREFIX_TEMPLATE({single}-brace .format) +
    LESSON_WITH_CONTEXT_SYSTEM + citation instruction
  * Generation rows are written with prompt_name="" / prompt_version=""
  * "storyboard" is scrubbed from user-facing output
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.conftest import _TestSessionLocal


@pytest.fixture()
def job_env(monkeypatch):
    """Point the job's self-managed session at the test DB and neutralise
    the (LLM-driven, non-deterministic with a mock) CE validation pass."""
    monkeypatch.setattr(
        "promptops_app.jobs.generation_jobs.SessionLocal", _TestSessionLocal
    )
    monkeypatch.setattr(
        "promptops_app.services.ce_validation_service.run_ce_validation",
        lambda out, db, **kwargs: out,
    )

    # The job enqueues a plagiarism scan via Celery after persisting; with no
    # Redis in the test env the client blocks in a long retry loop before the
    # surrounding try/except catches it. Stub the task out.
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


def _make_job(db, **param_overrides):
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
    params.update(param_overrides)
    job = GenerationJob(
        id=str(uuid.uuid4()),
        job_type="generation",
        status="queued",
        input_payload_json=json.dumps(params),
    )
    db.add(job)
    db.commit()
    return job


class TestRunGenerationJob:
    def test_prompts_are_hardcoded_not_registry(
        self, db, job_env, capture_llm
    ):
        from promptops_app.jobs.generation_jobs import run_generation_job

        job = _make_job(db)
        run_generation_job(job.id)

        assert len(capture_llm) == 1
        call = capture_llm[0]
        # PERSONA_PREFIX_TEMPLATE rendered via {single}-brace .format().
        assert call["system"].startswith(
            "Act as 20 yr Domain expert in Clinical Nursing."
        )
        assert "Professional/Corporate level content" in call["system"]
        # Hard-coded citation instruction is appended to SYSTEM.
        assert "cite it as [Source: filename]" in call["system"]
        # USER prompt is the .format()-rendered LESSON_WITH_CONTEXT_USER.
        assert "Infection Control" in call["user"]

    def test_generation_row_ignores_prompt_registry(
        self, db, job_env, capture_llm
    ):
        from promptops_app.database import Generation
        from promptops_app.jobs.generation_jobs import run_generation_job

        job = _make_job(db)
        run_generation_job(job.id)

        db.expire_all()
        gen = db.query(Generation).order_by(Generation.id.desc()).first()
        assert gen is not None
        # The registry is never consulted: no template name/version recorded.
        assert gen.prompt_name == ""
        assert gen.prompt_version == ""
        assert gen.block_type == "Lesson"
        assert gen.created_by == "test_admin"

    def test_job_completes_and_links_generation(
        self, db, job_env, capture_llm
    ):
        from promptops_app.database import GenerationJob
        from promptops_app.jobs.generation_jobs import run_generation_job

        job = _make_job(db)
        run_generation_job(job.id)

        db.expire_all()
        refreshed = db.query(GenerationJob).filter_by(id=job.id).first()
        assert refreshed.status == "completed"
        assert refreshed.result_entity_id is not None

    def test_storyboard_scrubbed_from_output(self, db, job_env, monkeypatch):
        from promptops_app.database import Generation
        from promptops_app.jobs import generation_jobs
        from promptops_app.services.llm_service import LLMResult

        monkeypatch.setattr(
            generation_jobs,
            "_llm_call",
            lambda *a, **kw: LLMResult(
                text="## Lesson\n\nSee the Storyboard for details. storyboard!",
                model="mock",
            ),
        )
        job = _make_job(db)
        generation_jobs.run_generation_job(job.id)

        db.expire_all()
        gen = db.query(Generation).order_by(Generation.id.desc()).first()
        assert "storyboard" not in gen.output_text.lower()

    def test_extra_instructions_appended_to_user_prompt(
        self, db, job_env, capture_llm
    ):
        from promptops_app.jobs.generation_jobs import run_generation_job

        job = _make_job(db, extra_instructions="Use UK spelling.")
        run_generation_job(job.id)

        assert capture_llm[0]["user"].rstrip().endswith(
            "**Additional Instructions:**\nUse UK spelling."
        )
