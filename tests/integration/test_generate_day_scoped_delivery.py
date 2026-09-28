"""G1 (AIM_PIPELINE_REPAIR_WORKFLOW.txt, step 6): Generate never used the
structured day bundle.

app/api/v1/routers/generations.py:131 already gated the structured retrieval
on `request_body.block and request_body.day and digest_pipeline_on_for(...)`,
and the server side kept its source units correctly (unlike the Blueprint
day path -- see the two remaining xfail tests in test_aim_outline_smoke.py,
which are NOT part of this fix). The gap was entirely client-side:

  - generateService.js's mapLaunchPayload() is an explicit key whitelist that
    did not include `block` or `day`, so even a caller that passed them had
    them stripped before the request left the browser.
  - GeneratePage.jsx never populated `day` from the component's own
    metadata.day_number (parse_blueprint_components already attaches it to
    every DLU day component).

So the complete day bundle (day units, digest, ACS codes, bounded kNN
supplement) was dead code for every generation launched from the product;
AIM DLU days always fell through to the free-text blob query.

This file proves the SERVER side of the fix (day is derived to a block label
when the client sends day but no block, and the day-scoped retrieval fires and
is actually delivered into the job's stored request_params). The frontend
change (generateService.js, GeneratePage.jsx) is what makes the browser send
`day` at all -- not independently testable from here without a browser
harness, so it is covered by manual verification instead.
"""

from __future__ import annotations

import json

import pytest

SOURCE_MARKER = "SOURCE_MARKER_hydraulic_actuator_torque_spec"


@pytest.fixture()
def aim_project(db):
    from promptops_app.database import Project

    p = Project(name="AIM", client_name="AIM", created_by="test_admin")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def aim_course(db, aim_project):
    """Course name carries the block label -- infer_block_label parses it from
    the title, same signal blueprints.py's day-scoped grounding already uses."""
    from promptops_app.database import Course

    c = Course(name="Block 2 - Aircraft Drawings", project_id=aim_project.id,
              created_by="test_admin")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def digest_on(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "digest_pipeline_enabled", True)
    monkeypatch.setattr(settings, "digest_pipeline_clients", "aim")


@pytest.fixture()
def dis(monkeypatch):
    calls = {"day": []}

    def fake_day(block, day, audience="instructor", current_user=None, client_id="", **kw):
        calls["day"].append({"block": block, "day": day, "client_id": client_id})
        return {
            "block": block, "day_number": day, "topic": "Hydraulic Actuators",
            "acs_codes": ["AC.II.A.1"],
            "digest": {"derived_objective": "Identify actuator components."},
            "units": [{"unit_type": "handbook", "title": "Actuators",
                       "content_unit_id": "u-1", "text": SOURCE_MARKER}],
            "supplement": [],
        }

    monkeypatch.setattr("app.core.dis_day_context.dis_client.get_day_context_sync", fake_day)
    return calls


@pytest.fixture()
def synchronous_dispatch(monkeypatch):
    """Run the job inline instead of on the ThreadPoolExecutor so this test can
    assert against it deterministically, matching the existing job_env pattern
    (tests/characterization/test_run_generation_job.py) but applied at the
    router's own dispatch.submit call site."""
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

    def synchronous_submit(fn, job_id):
        fn(job_id)

    monkeypatch.setattr("promptops_app.jobs.dispatch.submit", synchronous_submit)


def _launch(client, headers, course, project, **overrides):
    body = {
        "course_id": course.id, "project_id": project.id,
        "component_value": "lesson_4", "component_label": "Day 4 Lesson",
        "component_type": "lesson", "model_choice": "GPT-5.4",
    }
    body.update(overrides)
    return client.post("/api/v1/generations/launch", json=body, headers=headers)


def _job_request_params(db, job_id):
    from promptops_app.database import GenerationJob

    job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
    return json.loads(job.request_json)


class TestDayScopedRetrievalFiresWhenDayIsSent:
    def test_block_is_derived_server_side_from_the_course_title(
        self, client, auth_headers, db, aim_course, aim_project, digest_on, dis
    ):
        """The frontend change sends `day` alone (no block) -- the server must
        derive the block, not require the browser to parse it."""
        resp = _launch(client, auth_headers, aim_course, aim_project, day=4)
        assert resp.status_code == 202, resp.text

        assert dis["day"], "day-scoped retrieval was never called"
        assert dis["day"][0]["block"] == "Block 2"
        assert dis["day"][0]["day"] == 4
        assert dis["day"][0]["client_id"] == "aim"

    def test_an_explicit_block_from_the_caller_still_wins(
        self, client, auth_headers, db, aim_course, aim_project, digest_on, dis
    ):
        resp = _launch(client, auth_headers, aim_course, aim_project,
                       day=4, block="Block 9")
        assert resp.status_code == 202, resp.text
        assert dis["day"][0]["block"] == "Block 9"

    def test_no_day_means_no_day_scoped_call_at_all(
        self, client, auth_headers, db, aim_course, aim_project, digest_on, dis
    ):
        """Regression guard: omitting day must not accidentally start deriving
        a block and firing day-scoped retrieval anyway."""
        resp = _launch(client, auth_headers, aim_course, aim_project)
        assert resp.status_code == 202, resp.text
        assert dis["day"] == []

    def test_an_unparseable_title_logs_instead_of_failing_silently(
        self, client, auth_headers, db, aim_project, digest_on, dis, caplog
    ):
        """B5: no stored course-to-block linkage exists, so a course/CDD title
        that happens to carry no "Block N" text silently turns off day-scoped
        grounding for it -- e.g. after a routine rename. Cannot be fixed
        without a schema migration (out of scope); made observable instead."""
        import logging

        from promptops_app.database import Course

        unlabeled_course = Course(name="Aircraft Drawings", project_id=aim_project.id,
                                  created_by="test_admin")
        db.add(unlabeled_course)
        db.commit()
        db.refresh(unlabeled_course)

        with caplog.at_level(logging.WARNING, logger="app.api.v1.routers.generations"):
            resp = _launch(client, auth_headers, unlabeled_course, aim_project, day=4)

        assert resp.status_code == 202, resp.text
        assert dis["day"] == []
        assert any("day_scoped_grounding_skipped_no_block_label" in r.message
                  for r in caplog.records)


class TestTheSourceMaterialIsActuallyDelivered:
    def test_the_source_marker_reaches_the_stored_job_params(
        self, client, auth_headers, db, aim_course, aim_project, digest_on, dis
    ):
        """Resolving is not enough -- it has to reach the params the
        generation job actually reads from (dis_context_block, kept separate
        from extra_instructions per the Phase 4 PR review -- retrieved
        context must not be wrapped as if the user typed it), which is what
        generation_jobs.py folds into the final prompt."""
        resp = _launch(client, auth_headers, aim_course, aim_project, day=4)
        job_id = resp.json()["job_id"]

        params = _job_request_params(db, job_id)
        assert SOURCE_MARKER in params["dis_context_block"]
        assert params["dis_source_units"], "day units resolved but not recorded on the job"

    def test_the_source_marker_reaches_the_model(
        self, client, auth_headers, db, aim_course, aim_project, digest_on, dis,
        synchronous_dispatch, monkeypatch
    ):
        """End-to-end: the day-scoped source material must survive all the way
        to the prompt the model is actually called with."""
        from promptops_app.services.llm_service import LLMResult

        captured = {"system": "", "user": ""}

        def fake_llm(model_choice, system_prompt, user_prompt, *args, **kwargs):
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return LLMResult(text="## Section\n\nBody.", model=model_choice,
                             prompt_tokens=10, completion_tokens=20,
                             status="success", stop_reason="stop")

        monkeypatch.setattr("promptops_app.jobs.generation_jobs._llm_call", fake_llm)

        resp = _launch(client, auth_headers, aim_course, aim_project, day=4)
        assert resp.status_code == 202, resp.text

        assert SOURCE_MARKER in captured["system"] + captured["user"]
