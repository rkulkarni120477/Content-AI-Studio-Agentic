"""
Shared fixtures for the characterization suite.

These tests capture *today's* behavior of the prompt-resolution and
generation paths (PROMPT_CONSOLIDATION_PLAN.md, Phase 0S). They are the
safety net for the Phase 4/5/8 rewrites: any change to what a caller
receives from the prompt system should make one of these fail.

They intentionally document known quirks (e.g. the dormant-seed name-key
mismatch) — when a later phase fixes a quirk on purpose, update the test
alongside the fix.
"""

from __future__ import annotations

import pytest

from promptops_app.services.llm_service import LLMResult

# Canned LLM output — valid Markdown that the CDD/Blueprint parsers accept.
CANNED_LLM_TEXT = (
    "## Course Details\n\nThis is a test course about clinical nursing.\n\n"
    "## Course Structure\n\nModule 1: Introduction\nModule 2: Advanced Topics\n\n"
    "## Course Level Assessment\n\nFinal exam covering all modules."
)


@pytest.fixture()
def capture_llm(monkeypatch):
    """Patch every LLM entry point; record prompts; return a canned result.

    Two patch points are required:
      * ``promptops_app.services.llm_service.generate_with_metadata`` — the
        CDD/Blueprint/Style routers import it *inside* their handlers, so
        patching the source module works for them.
      * ``promptops_app.jobs.generation_jobs._llm_call`` — bound at module
        import time (``from ... import generate_with_metadata as _llm_call``),
        so it must be patched where it is used.

    Returns the list of recorded calls; each entry is a dict with
    ``model`` / ``system`` / ``user`` keys.
    """
    calls: list[dict] = []

    def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
        calls.append(
            {"model": model_choice, "system": system_prompt, "user": user_prompt}
        )
        return LLMResult(
            text=CANNED_LLM_TEXT,
            model="mock-model",
            prompt_tokens=100,
            completion_tokens=200,
            status="success",
        )

    monkeypatch.setattr(
        "promptops_app.services.llm_service.generate_with_metadata", fake
    )
    monkeypatch.setattr("promptops_app.jobs.generation_jobs._llm_call", fake)
    return calls


@pytest.fixture()
def project(db):
    """A minimal Project row."""
    from promptops_app.database import Project

    p = Project(name="Char Project", created_by="test_admin")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def course(db, project):
    """A minimal Course row under `project`."""
    from promptops_app.database import Course

    c = Course(name="Char Course", project_id=project.id, created_by="test_admin")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def make_db_prompt(db, name: str, *, system: str, user: str,
                   version: str = "v1", component_type: str | None = None,
                   is_default: bool = False, is_active: bool = True):
    """Create a native Prompt + PromptVersion pair the way the admin UI does."""
    from promptops_app.database import Prompt, PromptVersion

    prompt = Prompt(
        name=name,
        description=f"characterization row {name}",
        owner="test_admin",
        active_version=version,
        component_type=component_type,
        is_default=is_default,
    )
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    pv = PromptVersion(
        prompt_id=prompt.id,
        version=version,
        system_prompt=system,
        user_prompt_template=user,
        change_reason="characterization",
        is_active=is_active,
        created_by="test_admin",
    )
    db.add(pv)
    db.commit()
    return prompt
