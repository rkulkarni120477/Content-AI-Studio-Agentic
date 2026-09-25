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
        from promptops_app.services.user_directives import ADDITIONAL_INSTRUCTIONS_HEADING

        job = _make_job(db, extra_instructions="Use UK spelling.")
        run_generation_job(job.id)

        assert capture_llm[0]["user"].rstrip().endswith(
            f"{ADDITIONAL_INSTRUCTIONS_HEADING}\nUse UK spelling."
        )
        # CAS AIM findings, Phase 4: the append must state precedence over
        # standing/built-in guidance, not just relabel the text -- a bare
        # "Additional Instructions:" heading with no precedence stated is
        # exactly what let the built-in prompt win every conflict.
        assert "preference to any standing guidance" in capture_llm[0]["user"]


class TestGenerateWiring:
    """Phase 8: with PROMPT_RESOLVE_BY_COMPONENT on, the lesson path resolves
    `content_generation` and quiz components `quiz_generation` via the
    registry; persona prefix + citation stay code-injected; the resolved
    template name/version is recorded on the Generation row."""

    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "1")

    def test_lesson_resolves_generate_default_row(self, db, job_env, capture_llm):
        from promptops_app.database import Generation
        from promptops_app.jobs.generation_jobs import run_generation_job

        from .conftest import make_db_prompt

        make_db_prompt(
            db, "console-generate", system="DB GENERATE SYSTEM",
            user="topic={{topic}} format={{output_format}}",
            component_type="generate", is_default=True,
        )
        job = _make_job(db)
        run_generation_job(job.id)

        call = capture_llm[0]
        # Persona prefix and citation instruction still wrap the DB template.
        assert call["system"].startswith(
            "Act as 20 yr Domain expert in Clinical Nursing."
        )
        assert "DB GENERATE SYSTEM" in call["system"]
        assert "cite it as [Source: filename]" in call["system"]
        assert call["user"] == "topic=Infection Control format=Lesson"

        db.expire_all()
        gen = db.query(Generation).order_by(Generation.id.desc()).first()
        assert gen.prompt_name == "content_generation"
        assert gen.prompt_version == "v1"

    def test_quiz_component_resolves_quiz_stem(self, db, job_env, capture_llm):
        from promptops_app.database import Generation
        from promptops_app.jobs.generation_jobs import run_generation_job

        from .conftest import make_db_prompt

        # A quiz default must NOT collide with the generate/lesson default.
        make_db_prompt(
            db, "console-generate", system="LESSON SYS", user="lesson body",
            component_type="generate", is_default=True,
        )
        make_db_prompt(
            db, "console-quiz", system="DB QUIZ SYSTEM",
            user="quiz on {{topic}}",
            component_type="quiz", is_default=True,
        )
        job = _make_job(
            db, selected_component={"type": "assessment", "label": "Module Quiz"},
        )
        run_generation_job(job.id)

        call = capture_llm[0]
        assert "DB QUIZ SYSTEM" in call["system"]
        assert "LESSON SYS" not in call["system"]
        assert call["user"] == "quiz on Infection Control"

        db.expire_all()
        gen = db.query(Generation).order_by(Generation.id.desc()).first()
        assert gen.prompt_name == "quiz_generation"

    def test_interactive_component_resolves_exact_variant_row(
        self, db, job_env, capture_llm
    ):
        from promptops_app.database import Generation
        from promptops_app.jobs.generation_jobs import run_generation_job

        from .conftest import make_db_prompt

        # The NULL-variant lesson default must NOT be what interactive gets.
        make_db_prompt(
            db, "console-generate", system="LESSON SYS", user="lesson body",
            component_type="generate", is_default=True,
        )
        make_db_prompt(
            db, "console-interactive", system="DB INTERACTIVE SYSTEM",
            user="component={{component_label}} topic={{topic}}",
            component_type="generate", is_default=True, variant="interactive",
        )
        job = _make_job(
            db, selected_component={"type": "component", "label": "Reflection"},
        )
        run_generation_job(job.id)

        call = capture_llm[0]
        assert "DB INTERACTIVE SYSTEM" in call["system"]
        assert "LESSON SYS" not in call["system"]
        assert call["user"] == "component=Reflection topic=Infection Control"

        db.expire_all()
        gen = db.query(Generation).order_by(Generation.id.desc()).first()
        assert gen.prompt_name == "content_generation"
        assert gen.prompt_version == "v1"

    def test_interactive_never_falls_back_to_lesson_default(
        self, db, job_env, capture_llm
    ):
        # No interactive-variant row authored → the NULL-variant generate
        # default (a lesson template) must NOT hijack the component; the
        # legacy bespoke path keeps control (here: the no-context lesson
        # constants, with no template recorded).
        from promptops_app.database import Generation
        from promptops_app.jobs.generation_jobs import run_generation_job

        from .conftest import make_db_prompt

        make_db_prompt(
            db, "console-generate", system="LESSON DEFAULT SYS", user="lesson body",
            component_type="generate", is_default=True,
        )
        job = _make_job(
            db, selected_component={"type": "component", "label": "Reflection"},
        )
        run_generation_job(job.id)

        assert "LESSON DEFAULT SYS" not in capture_llm[0]["system"]
        db.expire_all()
        gen = db.query(Generation).order_by(Generation.id.desc()).first()
        assert gen.prompt_name == "" and gen.prompt_version == ""

    def test_declared_variable_violation_fails_the_job(
        self, db, job_env, capture_llm
    ):
        # Strict-variable enforcement: a misconfigured declaration must fail
        # the job visibly, not silently regenerate with the legacy constants.
        from promptops_app.database import GenerationJob, PromptVariable
        from promptops_app.jobs.generation_jobs import run_generation_job

        from .conftest import make_db_prompt

        row = make_db_prompt(
            db, "console-generate", system="s", user="{{no_such_var}}",
            component_type="generate", is_default=True,
        )
        db.add(PromptVariable(prompt_id=row.id, name="no_such_var"))
        db.commit()

        job = _make_job(db)
        run_generation_job(job.id)

        assert capture_llm == []  # never reached the LLM
        db.expire_all()
        refreshed = db.query(GenerationJob).filter_by(id=job.id).first()
        assert refreshed.status == "failed"
        assert "no_such_var" in (refreshed.error_message or "")

    def test_flag_off_keeps_legacy_constants(self, db, job_env, capture_llm,
                                             monkeypatch):
        from promptops_app.database import Generation
        from promptops_app.jobs.generation_jobs import run_generation_job

        from .conftest import make_db_prompt

        monkeypatch.delenv("PROMPT_RESOLVE_BY_COMPONENT", raising=False)
        make_db_prompt(
            db, "console-generate", system="DB GENERATE SYSTEM", user="db body",
            component_type="generate", is_default=True,
        )
        job = _make_job(db)
        run_generation_job(job.id)

        assert "DB GENERATE SYSTEM" not in capture_llm[0]["system"]
        db.expire_all()
        gen = db.query(Generation).order_by(Generation.id.desc()).first()
        assert gen.prompt_name == "" and gen.prompt_version == ""
