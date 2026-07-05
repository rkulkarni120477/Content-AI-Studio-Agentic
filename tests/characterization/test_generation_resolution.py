"""
Characterization: live prompt resolution in the CDD / Blueprint / Style paths.

Asserts, end-to-end through the FastAPI routers (mocked LLM only), which
template text each stage actually sends to the LLM today:

  * CDD + Blueprint resolve via build_prompt(..., db=db) → the .md FILE tier
    (no stem-named DB row exists), unless an admin creates a row whose
    Prompt.name equals the loader stem.
  * Inline *_override params bypass resolution entirely.
  * Style resolves its system prompt via load_template("style_understanding").

Phase 4/5 must keep all of this identical; Phase 8 changes resolution keys
on purpose (update these tests alongside).
"""

from __future__ import annotations

import pytest

from .conftest import make_db_prompt


def _cdd_payload(course, project, **overrides):
    body = {
        "course_id": course.id,
        "project_id": project.id,
        "course_title": "Foundations of Clinical Nursing",
        "estimated_duration_hours": 8,
        "target_audience": "Nursing Students Year 2",
        "expert_domain": "Clinical Nursing",
        "audience_category": "Professional/Corporate",
        "model_choice": "GPT-5.4",
    }
    body.update(overrides)
    return body


class TestCDDResolution:
    def test_file_tier_used_when_no_stem_row(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        resp = client.post(
            "/api/v1/cdd/generate",
            json=_cdd_payload(course, project),
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert len(capture_llm) == 1
        call = capture_llm[0]
        # The .md file's SYSTEM text.
        assert "structure-first approach" in call["system"]
        # Variables were rendered into the file's USER template.
        assert "Foundations of Clinical Nursing" in call["user"]
        assert "Clinical Nursing" in call["user"]
        assert "8 hours" in call["user"]

    def test_seeded_default_row_is_ignored(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        # The dormant-seed bug at router level: seeding the default_cdd_prompt
        # row changes NOTHING about what the LLM receives.
        from promptops_app.database import seed_data

        seed_data(db)
        resp = client.post(
            "/api/v1/cdd/generate",
            json=_cdd_payload(course, project),
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert "structure-first approach" in capture_llm[0]["system"]  # still file

    def test_stem_named_db_row_wins(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        make_db_prompt(
            db, "cdd_generation",
            system="DB CDD SYSTEM",
            user="DB CDD USER for {{course_name}}",
        )
        resp = client.post(
            "/api/v1/cdd/generate",
            json=_cdd_payload(course, project),
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        call = capture_llm[0]
        assert call["system"] == "DB CDD SYSTEM"
        assert call["user"] == "DB CDD USER for Foundations of Clinical Nursing"

    def test_override_bypasses_resolution(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        resp = client.post(
            "/api/v1/cdd/generate",
            json=_cdd_payload(
                course, project,
                system_prompt_override="OVERRIDE SYS",
                user_prompt_override="OVERRIDE USER",
            ),
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert capture_llm[0]["system"] == "OVERRIDE SYS"
        assert capture_llm[0]["user"] == "OVERRIDE USER"

    def test_override_produces_no_version_row(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        # Inline overrides are used-once-never-saved (plan Decision 3).
        from promptops_app.database import Prompt, PromptVersion

        before = (db.query(Prompt).count(), db.query(PromptVersion).count())
        client.post(
            "/api/v1/cdd/generate",
            json=_cdd_payload(
                course, project,
                system_prompt_override="S", user_prompt_override="U",
            ),
            headers=auth_headers,
        )
        after = (db.query(Prompt).count(), db.query(PromptVersion).count())
        assert after == before


class TestBlueprintResolution:
    def _payload(self, course, project, **overrides):
        body = {
            "course_id": course.id,
            "project_id": project.id,
            "selected_module": "Module 1: Introduction",
            "model_choice": "GPT-5.4",
        }
        body.update(overrides)
        return body

    def test_file_tier_and_mode_flags(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        resp = client.post(
            "/api/v1/blueprints/generate",
            json=self._payload(course, project, teacher_mode=False),
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        call = capture_llm[0]
        assert "Module 1: Introduction" in call["user"]
        # teacher/student flags render as Yes/No strings in the file template.
        assert "No CDD linked." in call["user"]

    def test_stem_named_db_row_wins(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        make_db_prompt(
            db, "blueprint_generation",
            system="DB BP SYSTEM",
            user="module={{selected_module}} teacher={{teacher_mode}}",
        )
        resp = client.post(
            "/api/v1/blueprints/generate",
            json=self._payload(course, project, teacher_mode=True),
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        call = capture_llm[0]
        assert call["system"] == "DB BP SYSTEM"
        assert call["user"] == "module=Module 1: Introduction teacher=Yes"


class TestStyleResolution:
    def test_system_prompt_resolves_from_file_by_default(self, db):
        from promptops_app.services.style_service import _load_system_prompt

        system, name, version = _load_system_prompt(db=db)
        assert name == "style_understanding"
        assert version == "v1"
        assert system  # file template's SYSTEM part

    def test_stem_named_db_row_wins(self, db):
        from promptops_app.services.style_service import _load_system_prompt

        make_db_prompt(
            db, "style_understanding",
            system="DB STYLE SYSTEM", user="ignored",
        )
        system, name, version = _load_system_prompt(db=db)
        assert system == "DB STYLE SYSTEM"

    def test_empty_db_system_falls_back_to_inline_constant(self, db):
        # `tmpl.system_template or _STYLE_UNDERSTANDING_SYSTEM` — a DB row
        # with an empty system prompt silently falls back to the constant.
        from promptops_app.services import style_service

        make_db_prompt(
            db, "style_understanding", system="", user="body only",
        )
        system, _, _ = style_service._load_system_prompt(db=db)
        assert system == style_service._STYLE_UNDERSTANDING_SYSTEM
