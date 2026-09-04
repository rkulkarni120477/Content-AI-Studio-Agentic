"""Blueprint generation must not store a reply the output cap cut short.

The regeneration paths were hardened first (3ef487a, aafd4c8) because that is
where a destroyed Day 20 outline was noticed. Generation was left as it was:
it checked ``llm_result.status == "error"`` and nothing else, so a reply that
stopped mid-table because it ran out of output room was stored as the document,
written as v1, and auto-pinned as the active version. Nothing downstream can
tell that document from a complete one — later regenerations read it as the
blueprint, and so does every generation that grounds on it.

It also passed no ``max_tokens``, inheriting DEFAULT_MAX_OUTPUT_TOKENS (16384)
even on the three catalog models that return up to 64000. Raising the ceiling
and refusing a truncated reply belong together: without the first, the second
would start failing generations that only truncated because of an arbitrary cap.
"""

from __future__ import annotations

import pytest

from promptops_app.services.llm_service import LLMResult


COMPLETE = (
    "## Module Overview\n\nA generated module.\n\n"
    "## Lesson Structure\n\n1. Lesson 1.1 Intro\n"
)


@pytest.fixture()
def project(db):
    from promptops_app.database import Project

    p = Project(name="Truncation Project", created_by="test_admin")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def course(db, project):
    from promptops_app.database import Course

    c = Course(name="Truncation Course", project_id=project.id, created_by="test_admin")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def llm(monkeypatch):
    """Stub generate_with_metadata, recording kwargs so max_tokens is assertable.

    ``stop_reason`` drives LLMResult.truncated, which is a property over
    _TRUNCATED_STOP_REASONS = {"length", "max_tokens"} — so the fixture sets the
    provider's own value rather than the derived flag.
    """
    calls: list[dict] = []
    state = {"stop_reason": None, "text": COMPLETE}

    def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
        calls.append({"model_choice": model_choice, "kwargs": kwargs})
        return LLMResult(
            text=state["text"], model="mock-model", prompt_tokens=10,
            completion_tokens=20, status="success", stop_reason=state["stop_reason"],
        )

    monkeypatch.setattr("promptops_app.services.llm_service.generate_with_metadata", fake)
    return calls, state


def _generate(client, auth_headers, course, project, **overrides):
    body = {
        "course_id": course.id,
        "project_id": project.id,
        "selected_module": "Day 4: Exploded Views",
        "model_choice": "GPT-5.4",
    }
    body.update(overrides)
    return client.post("/api/v1/blueprints/generate", json=body, headers=auth_headers)


def _blueprint_count(db):
    from promptops_app.database import ModuleBlueprint

    return db.query(ModuleBlueprint).count()


class TestTruncatedRepliesAreRefused:
    @pytest.mark.parametrize("stop_reason", ["length", "max_tokens"])
    def test_a_truncated_reply_is_not_stored(
        self, client, auth_headers, db, course, project, llm, stop_reason
    ):
        calls, state = llm
        state["stop_reason"] = stop_reason
        before = _blueprint_count(db)

        resp = _generate(client, auth_headers, course, project)

        assert resp.status_code >= 400, resp.text
        assert calls, "the model was never called"
        # The point of the guard: nothing was written. A stored fragment is
        # indistinguishable from a finished document, so a visible failure is
        # the only outcome the user can act on.
        assert _blueprint_count(db) == before

    def test_the_failure_says_what_to_do_about_it(
        self, client, auth_headers, db, course, project, llm
    ):
        calls, state = llm
        state["stop_reason"] = "length"

        resp = _generate(client, auth_headers, course, project)

        assert "output room" in resp.text
        assert "not applied" in resp.text or "nothing was changed" in resp.text


class TestCompleteRepliesStillGoThrough:
    def test_the_guard_does_not_fire_on_a_finished_reply(
        self, client, auth_headers, db, course, project, llm
    ):
        """Control: without this the two tests above would pass if the route
        had simply stopped storing anything at all."""
        calls, state = llm
        state["stop_reason"] = "stop"
        before = _blueprint_count(db)

        resp = _generate(client, auth_headers, course, project)

        assert resp.status_code in (200, 201), resp.text
        assert _blueprint_count(db) == before + 1

    def test_a_missing_stop_reason_is_not_treated_as_truncated(
        self, client, auth_headers, db, course, project, llm
    ):
        """Providers that report nothing must not be read as a failure."""
        calls, state = llm
        state["stop_reason"] = None

        resp = _generate(client, auth_headers, course, project)

        assert resp.status_code in (200, 201), resp.text


class TestTheModelGetsItsOwnCeiling:
    @pytest.mark.parametrize(
        "model_choice,expected",
        [("GPT-5.4", 16384), ("Claude Sonnet 5 (Bedrock)", 64000)],
    )
    def test_max_tokens_is_the_catalog_ceiling(
        self, client, auth_headers, db, course, project, llm, model_choice, expected
    ):
        calls, state = llm
        state["stop_reason"] = "stop"

        _generate(client, auth_headers, course, project, model_choice=model_choice)

        assert calls, "the model was never called"
        assert calls[0]["kwargs"].get("max_tokens") == expected

    def test_a_64k_model_is_no_longer_held_to_the_16k_default(
        self, client, auth_headers, db, course, project, llm
    ):
        """The regression this pairs with: omitting max_tokens inherited
        DEFAULT_MAX_OUTPUT_TOKENS, capping a 64000-token model at 16384."""
        from promptops_app.core.llm_client import DEFAULT_MAX_OUTPUT_TOKENS

        calls, state = llm
        state["stop_reason"] = "stop"

        _generate(client, auth_headers, course, project,
                  model_choice="Claude Sonnet 5 (Bedrock)")

        assert calls[0]["kwargs"]["max_tokens"] > DEFAULT_MAX_OUTPUT_TOKENS
