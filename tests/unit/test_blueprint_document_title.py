"""The Blueprint page's Title box has to reach the stored row.

It rendered next to the module dropdown and looked exactly like the CDD page's
working one, but nothing consumed it: the page folded it into
``extra_instructions`` as the prose line "Preferred blueprint title: …", the
generate request had no field for it, and the router titled every row
``"<selected_module> Blueprint"`` unconditionally. QA saw a box that took input
and changed nothing.

``ModuleBlueprint.title`` is not decoration. ``_list_dlu_day_components`` labels
the Generate page's day dropdown from it via ``parse_day_and_title``, and the
section-regeneration prompt is handed it as ``course_title``, so a title the
requester cannot set is a title that is wrong in three places at once.
"""

from __future__ import annotations

import pytest

from promptops_app.parsers.blueprint_parser import parse_day_and_title
from promptops_app.services.llm_service import LLMResult


CANNED = (
    "## Module Overview\n\nA generated module.\n\n"
    "## Lesson Structure\n\n1. Lesson 1.1 Intro\n"
)


@pytest.fixture()
def project(db):
    from promptops_app.database import Project

    p = Project(name="Title Project", created_by="test_admin")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def course(db, project):
    from promptops_app.database import Course

    c = Course(name="Title Course", project_id=project.id, created_by="test_admin")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def stub_llm(monkeypatch):
    """Record the prompts and return canned text — no network, no cost."""
    calls: list[dict] = []

    def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
        calls.append({"system": system_prompt, "user": user_prompt})
        return LLMResult(
            text=CANNED, model="mock-model", prompt_tokens=10,
            completion_tokens=20, status="success",
        )

    monkeypatch.setattr("promptops_app.services.llm_service.generate_with_metadata", fake)
    return calls


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


def _title_of(db, blueprint_id):
    from promptops_app.database import ModuleBlueprint

    return db.query(ModuleBlueprint).filter_by(id=blueprint_id).one().title


def test_document_title_is_persisted_as_the_row_title(
    client, auth_headers, db, course, project, stub_llm
):
    """The regression: the requester's title is what gets stored."""
    body = _generate(
        client, auth_headers, course, project,
        document_title="Day 4 — Exploded Views and Assembly Diagrams",
    )
    assert _title_of(db, body["blueprint_id"]) == "Day 4 — Exploded Views and Assembly Diagrams"


def test_blank_and_whitespace_titles_fall_back_to_the_module(
    client, auth_headers, db, course, project, stub_llm
):
    """A title is never stored blank — the module-derived default stands in."""
    for value in (None, "", "   "):
        body = _generate(client, auth_headers, course, project, document_title=value)
        assert _title_of(db, body["blueprint_id"]) == "Day 4: Exploded Views Blueprint"


def test_title_is_stored_trimmed(client, auth_headers, db, course, project, stub_llm):
    body = _generate(client, auth_headers, course, project, document_title="  Padded Title  ")
    assert _title_of(db, body["blueprint_id"]) == "Padded Title"


def test_omitting_the_field_is_identical_to_today(
    client, auth_headers, db, course, project, stub_llm
):
    """Every existing caller sends no document_title; none of them may change."""
    body = _generate(client, auth_headers, course, project)
    assert _title_of(db, body["blueprint_id"]) == "Day 4: Exploded Views Blueprint"


def test_the_title_is_not_smuggled_into_the_prompt(
    client, auth_headers, db, course, project, stub_llm
):
    """It replaces the old prose hint, so it must not still be an instruction."""
    _generate(client, auth_headers, course, project, document_title="My Custom Outline")
    assert stub_llm, "the LLM was never called"
    assert not any("Preferred blueprint title" in c["user"] for c in stub_llm)


@pytest.mark.parametrize("title,expected_day,expected_topic", [
    # Titles that keep the "Day N" shape stay fully parseable.
    ("Day 4 — Exploded Views", 4, "Exploded Views"),
    ("Day 4: Exploded Views Blueprint", 4, "Exploded Views"),
    # A free-form title loses only the topic: the day falls back to
    # module_number, which is derived from selected_module and so is unaffected
    # by whatever the requester typed. The day never disappears from the
    # Generate page's dropdown.
    ("Whatever The ID Wants To Call It", 4, ""),
])
def test_a_custom_title_never_costs_the_day_number(title, expected_day, expected_topic):
    assert parse_day_and_title(title, module_number=4) == (expected_day, expected_topic)
