"""Regeneration must revise under the conventions the document was written with.

Section regeneration renders BLUEPRINT_SECTION_REGENERATE_PROMPT under
BLUEPRINT_SYSTEM_PROMPT — a generic module-authoring constant — whatever prompt
authored the document. The revision template itself is correct; it is a fixed
contract ("revise this text, keep its facts"). What went missing was the
document's own conventions: a DLU day outline written under an AIM prompt with
day-type rules and an ACS disposition table was revised by a model never told any
of that existed. That is how blueprint_versions 396 became 397, a Day 20 exam
outline replaced by generic Lesson 1/2/3 filler.

The fix distils rather than substitutes. prompt_guidance extracts only a prompt's
judgment and emphasis instructions — never its structure — so appending its
output refines how the fixed contract is filled without redefining it. These
tests pin that: the guidance arrives, the contract survives it, and nothing
breaks when there is no prompt to distil or the distillation fails.
"""

from __future__ import annotations

import json

import pytest

from promptops_app.core.llm_client import LLMResult


CURRENT = "| Day 20 | Final Exam | AM.I.B | Full-block coverage |"


@pytest.fixture(autouse=True)
def _fresh_guidance_memo():
    """The distillation memo is process-wide and hashed on prompt text, so a
    leftover entry from another test would skip the call under test."""
    from promptops_app.services import prompt_guidance

    prompt_guidance.reset_cache()
    yield
    prompt_guidance.reset_cache()


@pytest.fixture()
def project(db):
    from promptops_app.database import Project

    p = Project(name="Conventions Project", created_by="test_admin")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def course(db, project):
    from promptops_app.database import Course

    c = Course(name="Conventions Course", project_id=project.id, created_by="test_admin")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def blueprint(db, course, project):
    from promptops_app.database import ModuleBlueprint

    bp = ModuleBlueprint(
        title="Day 20: Final Exam", module_title="Module 20", module_number=20,
        active_version="v1", project_id=project.id, course_id=course.id,
        created_by="test_admin",
    )
    db.add(bp)
    db.commit()
    db.refresh(bp)
    return bp


def _add_version(db, bp, *, version, params, active=True):
    from promptops_app.database import BlueprintVersion

    ver = BlueprintVersion(
        blueprint_id=bp.id, version=version,
        full_content="## Day Table\n\n" + CURRENT,
        sections=json.dumps({"Day Table": CURRENT}),
        generation_params=json.dumps(params) if params is not None else None,
        change_reason="test", is_active=active, created_by="test_admin",
    )
    db.add(ver)
    db.commit()
    return ver


@pytest.fixture()
def llm(monkeypatch):
    """Record every call, and answer the distillation distinctly from the revision.

    Both go through generate_with_metadata, so they are told apart by whether the
    prompt carries the revision contract.
    """
    calls: list[dict] = []
    state = {"distil": "1. Keep the ACS disposition table intact.", "fail": False}

    def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
        calls.append({"system": system_prompt, "user": user_prompt})
        is_revision = "Revise the current content above" in (user_prompt or "")
        if is_revision:
            return LLMResult(text="## Revised\n\nRevised.", model="m",
                             prompt_tokens=1, completion_tokens=1, stop_reason="end_turn")
        if state["fail"]:
            raise RuntimeError("distiller exploded")
        return LLMResult(text=state["distil"], model="m",
                         prompt_tokens=1, completion_tokens=1, stop_reason="end_turn")

    monkeypatch.setattr("promptops_app.services.llm_service.generate_with_metadata", fake)
    return calls, state


def _make_prompt(client, auth_headers, name, body):
    resp = client.post("/api/v1/prompts", json={
        "name": name, "description": "conventions probe",
        "component_type": "blueprint",
        "system_prompt": "Author day outlines. Every day row needs an ACS disposition.",
        "user_prompt_template": body,
    }, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _regen(client, auth_headers, bp_id, **overrides):
    body = {
        "section_key": "Day Table",
        "section_content": CURRENT,
        "feedback": "Why are the ACS cells N/A?",
        "model_choice": "GPT-5.4",
    }
    body.update(overrides)
    resp = client.post(f"/api/v1/blueprints/{bp_id}/regenerate-section",
                       json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _revision(calls):
    hits = [c for c in calls if "Revise the current content above" in (c["user"] or "")]
    assert hits, "no revision call was made"
    return hits[-1]


class TestTheAuthoringPromptsConventionsArrive:
    def test_the_distilled_guidance_is_appended(
        self, client, auth_headers, db, blueprint, llm
    ):
        calls, _ = llm
        p = _make_prompt(client, auth_headers, "conv_one", "Write {{selected_module}}.")
        _add_version(db, blueprint, version="v1", params={
            "prompt_row_id": p["id"], "prompt_title": "conv_one",
        })

        _regen(client, auth_headers, blueprint.id)

        assert "Keep the ACS disposition table intact." in _revision(calls)["user"]

    def test_the_revision_contract_survives_it(
        self, client, auth_headers, db, blueprint, llm
    ):
        """Appended, not substituted — the guidance must not displace the
        instructions that keep the current content authoritative."""
        calls, _ = llm
        p = _make_prompt(client, auth_headers, "conv_two", "Write {{selected_module}}.")
        _add_version(db, blueprint, version="v1", params={"prompt_row_id": p["id"]})

        _regen(client, auth_headers, blueprint.id)
        prompt = _revision(calls)["user"]

        assert "Revise the current content above" in prompt
        assert CURRENT in prompt, "the section being revised was displaced"
        # And the guidance is explicitly subordinate.
        assert "never override the instructions above" in prompt

    def test_the_prompt_comes_from_the_version_that_recorded_one(
        self, client, auth_headers, db, blueprint, llm
    ):
        """Regenerated versions record no generation_params, so the newest
        version that names a row has to win rather than the newest overall."""
        calls, _ = llm
        p = _make_prompt(client, auth_headers, "conv_three", "Write {{selected_module}}.")
        _add_version(db, blueprint, version="v1",
                     params={"prompt_row_id": p["id"]}, active=False)
        _add_version(db, blueprint, version="regen-v2", params=None, active=True)

        _regen(client, auth_headers, blueprint.id)

        assert "Keep the ACS disposition table intact." in _revision(calls)["user"]


class TestItDegradesInsteadOfBreaking:
    def test_no_recorded_prompt_leaves_the_prompt_unchanged(
        self, client, auth_headers, db, blueprint, llm
    ):
        """Every version predating the provenance change has no row recorded.
        Those must regenerate exactly as before, not fail."""
        calls, _ = llm
        _add_version(db, blueprint, version="v1", params={"prompt_source": "builtin_fallback"})

        _regen(client, auth_headers, blueprint.id)
        prompt = _revision(calls)["user"]

        assert "Revise the current content above" in prompt
        assert "never override the instructions above" not in prompt

    def test_a_failing_distillation_costs_the_guidance_not_the_regeneration(
        self, client, auth_headers, db, blueprint, llm
    ):
        calls, state = llm
        state["fail"] = True
        p = _make_prompt(client, auth_headers, "conv_four", "Write {{selected_module}}.")
        _add_version(db, blueprint, version="v1", params={"prompt_row_id": p["id"]})

        out = _regen(client, auth_headers, blueprint.id)

        assert out["updated_content"].startswith("## Revised")
        assert "never override the instructions above" not in _revision(calls)["user"]

    def test_an_empty_distillation_adds_no_block(
        self, client, auth_headers, db, blueprint, llm
    ):
        """"" is prompt_guidance's documented degraded answer. An empty
        delimited block would be noise the model has to interpret."""
        calls, state = llm
        state["distil"] = ""
        p = _make_prompt(client, auth_headers, "conv_five", "Write {{selected_module}}.")
        _add_version(db, blueprint, version="v1", params={"prompt_row_id": p["id"]})

        _regen(client, auth_headers, blueprint.id)

        assert "never override the instructions above" not in _revision(calls)["user"]
