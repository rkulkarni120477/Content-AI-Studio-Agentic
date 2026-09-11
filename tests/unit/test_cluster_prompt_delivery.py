"""Y2 (AIM_PIPELINE_REPAIR_WORKFLOW.txt, step 3): cluster prompts vanish at
Generate and on regeneration.

build_style_context's step 1 -- auto-injected cluster prompts -- only runs
when cluster_id is passed. blueprints.py and cdd.py both pass it
(course.cluster_id); the two Generate-side callers did not:

    promptops_app/jobs/generation_jobs.py       (run_generation_job)
    promptops_app/core/content_utils.py         (build_regeneration_context)

So a cluster prompt -- whose entire purpose is auto-injection across every
course in a cluster -- applied at CDD/Blueprint generation time and then
silently disappeared for the actual content generation and every
regeneration of that content.
"""

from __future__ import annotations

import json
import uuid

import pytest

CLUSTER_PROMPT_MARKER = "CLUSTER_MARKER_always_use_metric_units"


@pytest.fixture()
def clustered_course(db):
    """A Project -> Cluster -> Course, with an active ClusterPrompt on the
    cluster and an active Style on the course (Style is required for
    build_style_context to be called at all -- a course with no active style
    never reaches the cluster-prompt injection regardless of cluster_id)."""
    from promptops_app.database import (
        Cluster, ClusterPrompt, Course, Project, Style,
    )

    project = Project(name="Cluster Project", created_by="tester")
    db.add(project)
    db.commit()
    db.refresh(project)

    cluster = Cluster(project_id=project.id, name="Cluster A", created_by="tester")
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    db.add(ClusterPrompt(
        cluster_id=cluster.id, name="Metric Units Rule",
        system_prompt=CLUSTER_PROMPT_MARKER, is_active=True, created_by="tester",
    ))

    course = Course(project_id=project.id, cluster_id=cluster.id,
                    name="Clustered Course", created_by="tester")
    db.add(course)
    db.commit()
    db.refresh(course)

    style = Style(style_id="cluster-course-style", name="Course Style",
                 custom_instructions="Write clearly.", is_active=True)
    db.add(style)
    db.commit()
    db.refresh(style)
    course.active_style_id = style.id
    db.commit()

    return {"project": project, "cluster": cluster, "course": course, "style": style}


@pytest.fixture()
def unclustered_course(db):
    """Same shape, but the course belongs to no cluster -- the non-AIM proof:
    must produce a byte-identical prompt to before this fix."""
    from promptops_app.database import Course, Project, Style

    project = Project(name="No Cluster Project", created_by="tester")
    db.add(project)
    db.commit()
    db.refresh(project)

    course = Course(project_id=project.id, cluster_id=None,
                    name="Unclustered Course", created_by="tester")
    db.add(course)
    db.commit()
    db.refresh(course)

    style = Style(style_id="unclustered-course-style", name="Course Style",
                 custom_instructions="Write clearly.", is_active=True)
    db.add(style)
    db.commit()
    db.refresh(style)
    course.active_style_id = style.id
    db.commit()

    return {"project": project, "course": course, "style": style}


# ── run_generation_job (promptops_app/jobs/generation_jobs.py) ─────────────

@pytest.fixture()
def job_env(monkeypatch):
    """Same shape as tests/characterization/test_run_generation_job.py's
    fixture of the same name -- point the job's self-managed session at the
    test DB, neutralise CE validation and the plagiarism-scan Celery task."""
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
    """Same shape as tests/characterization/conftest.py's fixture of the same
    name -- patch the generation job's bound LLM entry point, record prompts."""
    from promptops_app.services.llm_service import LLMResult

    calls: list[dict] = []

    def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
        calls.append({"model": model_choice, "system": system_prompt, "user": user_prompt})
        return LLMResult(text="## Section\n\nBody.", model=model_choice,
                         prompt_tokens=10, completion_tokens=20,
                         status="success", stop_reason="stop")

    monkeypatch.setattr("promptops_app.jobs.generation_jobs._llm_call", fake)
    return calls


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


class TestGenerateDeliversClusterPrompts:
    def test_a_clustered_courses_generation_includes_the_cluster_prompt(
        self, db, job_env, capture_llm, clustered_course
    ):
        from promptops_app.jobs.generation_jobs import run_generation_job

        job = _make_job(db, clustered_course["course"], clustered_course["project"])
        run_generation_job(job.id)

        assert capture_llm, "the job never reached the LLM"
        both = capture_llm[0]["system"] + capture_llm[0]["user"]
        assert CLUSTER_PROMPT_MARKER in both, (
            "cluster prompt did not reach Generate -- it was applied at "
            "CDD/Blueprint time and silently dropped here"
        )

    def test_an_unclustered_courses_prompt_is_unaffected(
        self, db, job_env, capture_llm, unclustered_course
    ):
        """Non-AIM proof: a course with no cluster must not gain any new text."""
        from promptops_app.jobs.generation_jobs import run_generation_job

        job = _make_job(db, unclustered_course["course"], unclustered_course["project"])
        run_generation_job(job.id)

        assert capture_llm
        both = capture_llm[0]["system"] + capture_llm[0]["user"]
        assert CLUSTER_PROMPT_MARKER not in both


# ── build_regeneration_context (promptops_app/core/content_utils.py) ───────

class TestRegenerationDeliversClusterPrompts:
    def test_regeneration_context_includes_the_cluster_prompt(self, db, clustered_course):
        from promptops_app.core.content_utils import build_regeneration_context
        from promptops_app.database import Generation

        gen = Generation(
            topic="Test Topic", project_id=clustered_course["project"].id,
            course_id=clustered_course["course"].id, created_by="tester",
            prompt_name="", prompt_version="", block_type="Lesson", output_text="",
        )
        db.add(gen)
        db.commit()
        db.refresh(gen)

        preamble = build_regeneration_context(db, gen)
        assert CLUSTER_PROMPT_MARKER in preamble

    def test_regeneration_for_an_unclustered_course_is_unaffected(self, db, unclustered_course):
        from promptops_app.core.content_utils import build_regeneration_context
        from promptops_app.database import Generation

        gen = Generation(
            topic="Test Topic", project_id=unclustered_course["project"].id,
            course_id=unclustered_course["course"].id, created_by="tester",
            prompt_name="", prompt_version="", block_type="Lesson", output_text="",
        )
        db.add(gen)
        db.commit()
        db.refresh(gen)

        preamble = build_regeneration_context(db, gen)
        assert CLUSTER_PROMPT_MARKER not in preamble
