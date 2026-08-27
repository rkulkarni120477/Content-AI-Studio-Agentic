"""A stored Blueprint must say which prompt row produced it.

``generation_params`` recorded prompt_name="blueprint_generation" and
prompt_version="v1". Neither identifies anything: every blueprint prompt in the
library resolves under that one logical name — twelve of them — and each row
numbers its own versions from v1, so the pair collides across rows by design.

The cost was concrete. Working out which prompt produced blueprint_versions 396
meant full-text searching every prompt version in the database against the
stored document, and the answer (a DLU prompt that is not the course's default,
so it can only have arrived via an unrecorded dropdown choice) was still an
inference rather than a record.

PromptTemplate now carries the row it came from, and the router stores it.
"""

from __future__ import annotations

import json

import pytest

from promptops_app.services.llm_service import LLMResult


CANNED = "## Module Overview\n\nBody.\n\n## Lesson Structure\n\n1. Lesson 1.1\n"


@pytest.fixture()
def project(db):
    from promptops_app.database import Project

    p = Project(name="Provenance Project", created_by="test_admin")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def course(db, project):
    from promptops_app.database import Course

    c = Course(name="Provenance Course", project_id=project.id, created_by="test_admin")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture(autouse=True)
def stub_llm(monkeypatch):
    def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
        return LLMResult(text=CANNED, model="mock-model", prompt_tokens=10,
                         completion_tokens=20, status="success", stop_reason="stop")

    monkeypatch.setattr("promptops_app.services.llm_service.generate_with_metadata", fake)


def _make_pipeline_prompt(client, auth_headers, name, body="Design {{selected_module}}."):
    """A pipeline prompt reachable by id.

    The dropdown path in _from_db is deliberately NOT behind
    PROMPT_RESOLVE_BY_COMPONENT — only scope/default resolution is — so passing
    prompt_id exercises the DB tier without the flag.
    """
    resp = client.post("/api/v1/prompts", json={
        "name": name,
        "description": "provenance probe",
        "component_type": "blueprint",
        "system_prompt": "You are a blueprint author.",
        "user_prompt_template": body,
    }, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _generate(client, auth_headers, course, project, **overrides):
    body = {
        "course_id": course.id,
        "project_id": project.id,
        "selected_module": "Day 4: Exploded Views",
        "model_choice": "GPT-5.4",
    }
    body.update(overrides)
    resp = client.post("/api/v1/blueprints/generate", json=body, headers=auth_headers)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


def _params_of(db, blueprint_id):
    from promptops_app.database import BlueprintVersion

    ver = (db.query(BlueprintVersion)
             .filter_by(blueprint_id=blueprint_id, version="v1").one())
    return json.loads(ver.generation_params or "{}")


class TestTheRowIsRecorded:
    def test_the_prompt_row_id_and_title_are_stored(
        self, client, auth_headers, db, course, project
    ):
        p = _make_pipeline_prompt(client, auth_headers, "bp_provenance_one")

        bp = _generate(client, auth_headers, course, project, prompt_id=p["id"])
        params = _params_of(db, bp["blueprint_id"])

        assert params["prompt_row_id"] == p["id"]
        assert params["prompt_title"] == "bp_provenance_one"
        assert params["prompt_tier"] == "db"

    def test_two_prompts_sharing_the_logical_name_are_distinguishable(
        self, client, auth_headers, db, course, project
    ):
        """The whole point. Under name+version these two are identical rows."""
        a = _make_pipeline_prompt(client, auth_headers, "bp_prov_a", "A: {{selected_module}}")
        b = _make_pipeline_prompt(client, auth_headers, "bp_prov_b", "B: {{selected_module}}")

        bp_a = _generate(client, auth_headers, course, project, prompt_id=a["id"])
        bp_b = _generate(client, auth_headers, course, project, prompt_id=b["id"])
        pa = _params_of(db, bp_a["blueprint_id"])
        pb = _params_of(db, bp_b["blueprint_id"])

        # Indistinguishable before this change...
        assert pa["prompt_name"] == pb["prompt_name"]
        # ...and separable now.
        assert pa["prompt_row_id"] != pb["prompt_row_id"]
        assert {pa["prompt_title"], pb["prompt_title"]} == {"bp_prov_a", "bp_prov_b"}


class TestWhatTheRequesterAskedFor:
    def test_a_dropdown_choice_that_missed_is_still_recorded(
        self, client, auth_headers, db, course, project
    ):
        """A prompt_id that does not resolve falls through to the normal chain.

        That fallthrough is deliberate, but it used to be invisible: the stored
        row looked exactly like a request that never chose a prompt at all.
        """
        bp = _generate(client, auth_headers, course, project, prompt_id=999999)
        params = _params_of(db, bp["blueprint_id"])

        assert params["prompt_id_requested"] == 999999
        # It missed, so the row that actually ran is not that one.
        assert params["prompt_row_id"] != 999999

    def test_no_choice_records_none(self, client, auth_headers, db, course, project):
        bp = _generate(client, auth_headers, course, project)

        assert _params_of(db, bp["blueprint_id"])["prompt_id_requested"] is None


class TestTheFileTierStillWorks:
    def test_a_file_template_records_no_row_but_says_so(
        self, client, auth_headers, db, course, project
    ):
        """The file tier has no row. It must report that, not crash or invent one."""
        bp = _generate(client, auth_headers, course, project)
        params = _params_of(db, bp["blueprint_id"])

        assert params["prompt_tier"] == "file"
        assert params["prompt_row_id"] is None
        assert params["prompt_title"] == ""
        # The old fields stay, so anything already reading them keeps working.
        assert params["prompt_name"] == "blueprint_generation"
        assert params["prompt_version"]


class TestTheWrapperIsUnchanged:
    def test_build_prompt_still_returns_four_values(self):
        """24 call sites unpack this. build_prompt_resolved is additive."""
        from promptops_app.prompts.prompt_builder import build_prompt

        out = build_prompt("blueprint_generation", {
            "cdd_context": "x", "selected_module": "m", "extra_instructions": "",
            "teacher_mode": "No", "student_mode": "Yes", "style_guidelines": "",
            "block": "",
        })
        assert len(out) == 4
        system, user, name, version = out
        assert isinstance(system, str) and isinstance(user, str)
        assert name == "blueprint_generation"
        assert isinstance(version, str) and version
