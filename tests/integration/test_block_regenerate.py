"""Integration tests for POST /api/v1/{block_id}/regenerate (full-block regenerate).

Regression guard for CAS-… : the endpoint previously returned 500 because the
handler called ``IMPROVISE_BLOCK_PROMPT_TEMPLATE.format()`` with placeholder
names that did not match the template, raising ``KeyError`` before the LLM was
ever reached. These tests drive the full FastAPI → service → repository → DB
stack (LLM mocked) and assert:

  * a successful regenerate returns 200 with the new content,
  * the previous content is preserved in version history (BlockVersion row),
  * the pinned CDD + Blueprint context is injected into the LLM prompt
    (the "regenerate strictly according to the Blueprint" User-Guide intent).
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def regen_block(db):
    """A draft Block whose Generation is linked to a CDD + Blueprint + project."""
    from promptops_app.database import (
        Block,
        CourseDesignDocument,
        Generation,
        ModuleBlueprint,
        Project,
    )

    project = Project(name="Regen Proj", is_active=True)
    db.add(project)
    db.flush()

    cdd = CourseDesignDocument(
        title="Spring Framework CDD",
        course_title="Spring Course",
        project_id=project.id,
        created_by="tester",
    )
    db.add(cdd)
    db.flush()

    bp = ModuleBlueprint(
        cdd_id=cdd.id,
        title="Spring Intro Blueprint",
        module_title="Introduction to Spring",
        module_number=1,
        project_id=project.id,
        created_by="tester",
    )
    db.add(bp)
    db.flush()

    generation = Generation(
        prompt_name="Lesson Generator",
        prompt_version="1",
        block_type="lesson",
        topic="Introduction to Spring Framework",
        output_text="original output",
        cdd_id=cdd.id,
        blueprint_id=bp.id,
        project_id=project.id,
        created_by="tester",
    )
    db.add(generation)
    db.flush()

    block = Block(
        generation_id=generation.id,
        block_type="lesson",
        block_label="Lesson - Introduction to Spring Framework",
        content="OLD CONTENT — first draft of the Spring lesson.",
        workflow_state="draft",
        position=0,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return {"block": block, "cdd": cdd, "bp": bp, "generation": generation}


@pytest.fixture()
def capture_llm(monkeypatch):
    """Patch the LLM call to record the (system, user) prompts and return success.

    The regenerate handler does ``from promptops_app.services.llm_service import
    generate_with_metadata`` at call time, so patching the attribute on that
    module is what the handler resolves.
    """
    from unittest.mock import MagicMock

    captured: dict = {}

    def _fake_generate(model_choice, system_prompt, user_prompt, *args, **kwargs):
        captured["model_choice"] = model_choice
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        result = MagicMock()
        result.text = "## Introduction\n\nNEW regenerated Spring content.\n"
        result.status = "success"
        result.model = "gpt-4o-test"
        result.prompt_tokens = 120
        result.completion_tokens = 340
        result.error_type = None
        return result

    monkeypatch.setattr(
        "promptops_app.services.llm_service.generate_with_metadata",
        _fake_generate,
    )
    return captured


class TestBlockRegenerate:
    def test_regenerate_returns_200_and_replaces_content(
        self, client, auth_headers, regen_block, capture_llm
    ):
        block = regen_block["block"]

        resp = client.post(
            f"/api/v1/{block.id}/regenerate",
            headers=auth_headers,
            json={
                "model_choice": "GPT-5.4",
                "feedback_instruction": (
                    "Regenerate Lesson 1 strictly according to the Blueprint. "
                    "Keep it within 20 minutes."
                ),
            },
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["block_id"] == block.id
        assert "NEW regenerated Spring content" in body["content"]
        assert "OLD CONTENT" not in body["content"]
        assert body["version_created"]  # a version label was returned

    def test_previous_content_preserved_in_version_history(
        self, client, auth_headers, db, regen_block, capture_llm
    ):
        from promptops_app.database import Block, BlockVersion

        block = regen_block["block"]

        resp = client.post(
            f"/api/v1/{block.id}/regenerate",
            headers=auth_headers,
            json={"feedback_instruction": "improve"},
        )
        assert resp.status_code == 200, resp.text

        # The pre-regeneration content is snapshotted as a regeneration version.
        versions = (
            db.query(BlockVersion)
            .filter(BlockVersion.block_id == block.id)
            .all()
        )
        assert any(
            v.change_source == "regeneration" and "OLD CONTENT" in (v.content or "")
            for v in versions
        ), [(v.change_source, (v.content or "")[:30]) for v in versions]

        # The live block now holds the new content.
        refreshed = db.query(Block).filter(Block.id == block.id).first()
        assert "NEW regenerated Spring content" in (refreshed.content or "")

    def test_cdd_and_blueprint_context_reach_the_prompt(
        self, client, auth_headers, regen_block, capture_llm
    ):
        block = regen_block["block"]

        resp = client.post(
            f"/api/v1/{block.id}/regenerate",
            headers=auth_headers,
            json={"feedback_instruction": "focus on core components"},
        )
        assert resp.status_code == 200, resp.text

        user_prompt = capture_llm["user_prompt"]
        # CDD + Blueprint context injection is present…
        assert "GENERATION CONTEXT" in user_prompt
        assert "Spring Framework CDD" in user_prompt          # the pinned CDD title
        assert "Spring Intro Blueprint" in user_prompt        # the pinned Blueprint title
        # …the user's instruction is carried through…
        assert "focus on core components" in user_prompt
        # …and the persona prefix is fully rendered (no unfilled placeholders).
        assert "{" not in capture_llm["system_prompt"]

    def test_regenerate_unknown_block_returns_404(
        self, client, auth_headers, capture_llm
    ):
        resp = client.post(
            "/api/v1/99999999/regenerate",
            headers=auth_headers,
            json={"feedback_instruction": "x"},
        )
        assert resp.status_code == 404
