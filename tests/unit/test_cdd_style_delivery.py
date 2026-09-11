"""Y3 (AIM_PIPELINE_REPAIR_WORKFLOW.txt, step 5), CDD side.

cdd.py's non-override path put style_context BOTH into extra_block (prepended
as "**ACTIVE STYLE -- Apply throughout:**") AND passed it as the
style_guidelines variable -- a template rendering both slots received the
style twice, verbatim. Fixed to deliver it only via the named variable.

Also confirms cdd.py's SEPARATE, pre-existing bug on the override path: found
while checking Step 5 was safe to ship (it wasn't touched by this fix, and
isn't -- CDD's override branch never reads style_context/extra_instructions
at all, because they are computed inside the `else` branch). Flagged
separately; these tests pin the CURRENT (unfixed) override behaviour so a
future fix has something to flip.
"""

from __future__ import annotations

import pytest

from promptops_app.services.llm_service import LLMResult

CANNED = (
    "## Course Overview\n\nBody.\n\n## Course Structure\n\nModule 1: Intro.\n"
)
STYLE_MARKER = "STYLE_MARKER_plain_language_no_jargon"


@pytest.fixture()
def project(db):
    from promptops_app.database import Project

    p = Project(name="CDD Style Project", created_by="test_admin")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def course(db, project):
    from promptops_app.database import Course

    c = Course(name="CDD Style Course", project_id=project.id, created_by="test_admin")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def style(db, project, course):
    from promptops_app.database import Style

    s = Style(style_id="cdd-style-delivery", name="Test Style",
             generated_summary="### Voice\n" + STYLE_MARKER,
             project_id=project.id, course_id=course.id, is_active=True)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture(autouse=True)
def stub_llm(monkeypatch):
    captured = {"system": "", "user": ""}

    def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return LLMResult(text=CANNED, model="mock-model", prompt_tokens=10,
                         completion_tokens=20, status="success", stop_reason="stop")

    monkeypatch.setattr("promptops_app.services.llm_service.generate_with_metadata", fake)
    captured["both"] = lambda: captured["system"] + "\n" + captured["user"]
    monkeypatch.setattr("app.api.v1.routers.cdd.dis_client.retrieve_context_sync",
                        lambda *a, **k: {"combined_context": "", "source_units": []})
    monkeypatch.setattr("app.api.v1.routers.cdd.dis_client.generated_upsert_sync",
                        lambda *a, **k: None)
    return captured


def _generate(client, headers, course, project, **overrides):
    body = {
        "course_id": course.id, "project_id": project.id,
        "course_title": "Test Course", "model_choice": "GPT-5.4",
    }
    body.update(overrides)
    return client.post("/api/v1/cdd/generate", json=body, headers=headers)


class TestStyleReachesTheCddPromptExactlyOnce:
    def test_style_reaches_the_prompt(self, client, auth_headers, db, course, project,
                                      style, stub_llm):
        resp = _generate(client, auth_headers, course, project, style_id=style.id)
        assert resp.status_code in (200, 201), resp.text
        assert STYLE_MARKER in stub_llm["both"]()

    def test_style_reaches_the_prompt_exactly_once(self, client, auth_headers, db,
                                                    course, project, style, stub_llm):
        _generate(client, auth_headers, course, project, style_id=style.id)
        both = stub_llm["both"]()
        assert both.count(STYLE_MARKER) == 1, (
            f"expected exactly one occurrence, found {both.count(STYLE_MARKER)}"
        )


class TestTheCddOverrideKeepsStyleAndInstructions:
    """Same class of bug as B4 (blueprints.py), on the CDD route -- and it was
    worse here: style_context was never even computed on the override branch.
    Fixed by resolving course/style_context unconditionally and appending them
    (plus the requester's own Extra Instructions) before the override's own
    grounding-block append."""

    def test_an_override_keeps_style_on_the_cdd_route(
        self, client, auth_headers, db, course, project, style, stub_llm
    ):
        _generate(client, auth_headers, course, project, style_id=style.id,
                  system_prompt_override="SYSTEM: custom.",
                  user_prompt_override="USER: custom.")
        assert STYLE_MARKER in stub_llm["both"]()

    def test_an_override_keeps_extra_instructions_on_the_cdd_route(
        self, client, auth_headers, db, course, project, style, stub_llm
    ):
        _generate(client, auth_headers, course, project,
                  extra_instructions="CDD_OVERRIDE_EXTRA_MARKER",
                  system_prompt_override="SYSTEM: custom.",
                  user_prompt_override="USER: custom.")
        assert "CDD_OVERRIDE_EXTRA_MARKER" in stub_llm["both"]()
