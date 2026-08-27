"""A Blueprint written without its sources must say so.

Generation is best-effort about Source Library retrieval on purpose: an
unreachable library degrades the document to CDD-and-style grounding rather than
failing the request, and cdd.py documents the same contract for its own second
retrieval pass. That is not what was wrong.

What was wrong is that the outcome was unobservable. `_dis_context_block` caught
every exception, logged one line and returned "", so the requester got the
ordinary success toast and a document that reads like any other, and the stored
version recorded nothing either — after the fact there was no way to tell a
fully grounded blueprint from one whose library was down when it was written.

These tests pin the distinction the code now has to make: DIS answering with
nothing is normal and silent, DIS failing to answer is reported.
"""

from __future__ import annotations

import json

import pytest

from promptops_app.services.llm_service import LLMResult


CANNED = "## Module Overview\n\nBody.\n\n## Lesson Structure\n\n1. Lesson 1.1\n"


@pytest.fixture()
def project(db):
    from promptops_app.database import Project

    p = Project(name="Grounding Project", created_by="test_admin")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def course(db, project):
    from promptops_app.database import Course

    c = Course(name="Grounding Course", project_id=project.id, created_by="test_admin")
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


@pytest.fixture()
def dis(monkeypatch):
    """Drive retrieve_context_sync directly.

    Patched at the router's own reference so the disabled short-circuit in
    request_sync (tests/conftest.py sets DIS_ENABLED=false) is not what is under
    test here — the three outcomes below are.
    """
    state = {"mode": "empty"}

    def fake(purpose, payload, current_user=None, client_id="", timeout=None):
        if state["mode"] == "raise":
            raise RuntimeError("connect to dis_backend:8000 failed: token abc123")
        if state["mode"] == "context":
            return {"combined_context": "SOURCE MATERIAL", "source_units": ["unit-1"]}
        return {"combined_context": "", "source_units": []}

    monkeypatch.setattr(
        "app.api.v1.routers.blueprints.dis_client.retrieve_context_sync", fake)
    return state


def _generate(client, auth_headers, course, project):
    return client.post("/api/v1/blueprints/generate", json={
        "course_id": course.id,
        "project_id": project.id,
        "selected_module": "Day 4: Exploded Views",
        "model_choice": "GPT-5.4",
    }, headers=auth_headers)


def _params_of(db, blueprint_id):
    from promptops_app.database import BlueprintVersion

    ver = (db.query(BlueprintVersion)
             .filter_by(blueprint_id=blueprint_id, version="v1").one())
    return json.loads(ver.generation_params or "{}")


class TestAnUnreachableLibraryIsReported:
    def test_the_response_says_grounding_was_unavailable(
        self, client, auth_headers, db, course, project, dis
    ):
        dis["mode"] = "raise"

        resp = _generate(client, auth_headers, course, project)

        # Still a success: degrading is the deliberate behaviour.
        assert resp.status_code in (200, 201), resp.text
        assert resp.json()["source_context_unavailable"] == "RuntimeError"

    def test_the_version_records_it(
        self, client, auth_headers, db, course, project, dis
    ):
        """The after-the-fact question — "was this document grounded?" — has to
        be answerable from the stored row, not from a log that has rotated."""
        dis["mode"] = "raise"

        bp_id = _generate(client, auth_headers, course, project).json()["blueprint_id"]

        assert _params_of(db, bp_id)["source_context_unavailable"] == "RuntimeError"

    def test_the_reason_does_not_leak_the_upstream_message(
        self, client, auth_headers, db, course, project, dis
    ):
        """The exception's text can carry a host, a URL or a token. It is stored
        on the version and shown to the requester, so only the class name goes."""
        dis["mode"] = "raise"

        resp = _generate(client, auth_headers, course, project)

        # Asserted first so this cannot pass by the field simply not existing —
        # which is how it passed against the code before the field was added.
        assert resp.json()["source_context_unavailable"] == "RuntimeError"
        assert "dis_backend:8000" not in resp.text
        assert "abc123" not in resp.text


class TestAnAnsweringLibraryIsNotReported:
    def test_nothing_to_add_is_not_a_failure(
        self, client, auth_headers, db, course, project, dis
    ):
        """A course whose library holds no matching material is the ordinary
        case. Reporting it would train the warning to be ignored."""
        dis["mode"] = "empty"

        resp = _generate(client, auth_headers, course, project)

        assert resp.status_code in (200, 201), resp.text
        assert resp.json()["source_context_unavailable"] is None
        bp_id = resp.json()["blueprint_id"]
        assert "source_context_unavailable" not in _params_of(db, bp_id)

    def test_retrieved_context_is_not_reported_either(
        self, client, auth_headers, db, course, project, dis
    ):
        dis["mode"] = "context"

        resp = _generate(client, auth_headers, course, project)

        assert resp.json()["source_context_unavailable"] is None
        bp_id = resp.json()["blueprint_id"]
        assert _params_of(db, bp_id)["dis_source_units"] == ["unit-1"]
