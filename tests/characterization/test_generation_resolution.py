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


class TestComponentKeyedRouterResolution:
    """Phase 8 scope wiring — with PROMPT_RESOLVE_BY_COMPONENT on, the CDD and
    Blueprint routers pass their course context so scope locks and component
    defaults reach live generation calls."""

    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "1")

    def test_seeded_default_row_reaches_live_cdd_generation(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        # The flip of test_seeded_default_row_is_ignored: flag on, the seeded
        # default IS what the LLM receives.
        from promptops_app.database import seed_data
        from promptops_app.repositories.prompt_repository import (
            get_active_version, get_default_prompt,
        )

        seed_data(db)
        seeded_system = get_active_version(
            db, get_default_prompt(db, "cdd").id
        ).system_prompt

        resp = client.post(
            "/api/v1/cdd/generate",
            json=_cdd_payload(course, project),
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert capture_llm[0]["system"] == seeded_system

    def test_course_scope_lock_reaches_live_cdd_generation(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        from promptops_app.repositories.prompt_repository import set_fixed_prompt

        make_db_prompt(db, "default-cdd", system="DEFAULT SYS", user="default",
                       component_type="cdd", is_default=True)
        fixed = make_db_prompt(db, "course-cdd", system="FIXED SYS",
                               user="fixed for {{course_name}}",
                               component_type="cdd")
        set_fixed_prompt(
            db, component="cdd", scope_level="course", course_id=course.id,
            prompt_id=fixed.id, fixed_by="test_admin", fixed_by_role="admin",
        )

        resp = client.post(
            "/api/v1/cdd/generate",
            json=_cdd_payload(course, project),
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert capture_llm[0]["system"] == "FIXED SYS"
        assert capture_llm[0]["user"] == "fixed for Foundations of Clinical Nursing"

    def test_course_scope_lock_reaches_live_blueprint_generation(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        from promptops_app.repositories.prompt_repository import set_fixed_prompt

        fixed = make_db_prompt(db, "course-bp", system="FIXED BP SYS",
                               user="module={{selected_module}}",
                               component_type="blueprint")
        set_fixed_prompt(
            db, component="blueprint", scope_level="course", course_id=course.id,
            prompt_id=fixed.id, fixed_by="test_admin", fixed_by_role="admin",
        )

        resp = client.post(
            "/api/v1/blueprints/generate",
            json={"course_id": course.id, "project_id": project.id,
                  "selected_module": "Module 1: Introduction",
                  "model_choice": "GPT-5.4"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        assert capture_llm[0]["system"] == "FIXED BP SYS"
        assert capture_llm[0]["user"] == "module=Module 1: Introduction"

    def test_blueprint_teacher_and_student_resolve_their_own_variants(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        # The Phase 8 variant split: once teacher/student rows exist, each mode
        # resolves its own independently-versioned row.
        make_db_prompt(db, "bp-teacher", system="TEACHER SYS",
                       user="teacher module={{selected_module}}",
                       component_type="blueprint", is_default=True,
                       variant="teacher")
        make_db_prompt(db, "bp-student", system="STUDENT SYS",
                       user="student module={{selected_module}}",
                       component_type="blueprint", is_default=True,
                       variant="student")

        payload = {"course_id": course.id, "project_id": project.id,
                   "selected_module": "Module 1: Introduction",
                   "model_choice": "GPT-5.4"}

        resp = client.post("/api/v1/blueprints/generate",
                           json={**payload, "teacher_mode": True},
                           headers=auth_headers)
        assert resp.status_code in (200, 201), resp.text
        assert capture_llm[0]["system"] == "TEACHER SYS"

        resp = client.post("/api/v1/blueprints/generate",
                           json={**payload, "teacher_mode": False},
                           headers=auth_headers)
        assert resp.status_code in (200, 201), resp.text
        assert capture_llm[1]["system"] == "STUDENT SYS"

    def test_blueprint_variant_request_uses_null_variant_seeded_default(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        # No teacher/student rows authored yet → both modes keep resolving the
        # NULL-variant default (the seeded-default fallback promise).
        make_db_prompt(db, "bp-base", system="BASE BP SYS",
                       user="module={{selected_module}} teacher={{teacher_mode}}",
                       component_type="blueprint", is_default=True)

        resp = client.post(
            "/api/v1/blueprints/generate",
            json={"course_id": course.id, "project_id": project.id,
                  "selected_module": "Module 1: Introduction",
                  "model_choice": "GPT-5.4", "teacher_mode": True},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        assert capture_llm[0]["system"] == "BASE BP SYS"
        assert capture_llm[0]["user"] == "module=Module 1: Introduction teacher=Yes"

    def test_declared_variable_violation_surfaces_not_fallback(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        # Strict-variable enforcement: a default row declaring a variable the
        # CDD call cannot supply must 500 PROMPT_MISCONFIGURED — never
        # silently regenerate from the constant fallback.
        from promptops_app.database import PromptVariable

        row = make_db_prompt(db, "strict-cdd", system="s", user="{{no_such_var}}",
                             component_type="cdd", is_default=True)
        db.add(PromptVariable(prompt_id=row.id, name="no_such_var"))
        db.commit()

        resp = client.post(
            "/api/v1/cdd/generate",
            json=_cdd_payload(course, project),
            headers=auth_headers,
        )
        assert resp.status_code == 500, resp.text
        body = resp.json()
        assert body["error"]["code"] == "PROMPT_MISCONFIGURED"
        assert "no_such_var" in body["error"]["message"]
        assert capture_llm == []  # the LLM was never called


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


class TestPromptProvenancePersisted:
    """PL↔CAS sync review (plan Phase 11): the prompt that produced a CDD or
    Blueprint version is persisted in its generation_params — the registry
    template's name+version, the full inline-override text, or the builtin
    fallback marker. Before this, an override drove the LLM call and was
    discarded (unrecoverable)."""

    @staticmethod
    def _params(db, model, fk_field, fk_value):
        import json as _json

        row = db.query(model).filter(getattr(model, fk_field) == fk_value).first()
        assert row is not None
        return _json.loads(row.generation_params)

    def test_cdd_override_text_is_persisted(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        from promptops_app.database import CDDVersion

        resp = client.post(
            "/api/v1/cdd/generate",
            json=_cdd_payload(
                course, project,
                system_prompt_override="OVERRIDE SYS",
                user_prompt_override="OVERRIDE USER for {topic}",
            ),
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert capture_llm[0]["system"] == "OVERRIDE SYS"
        params = self._params(db, CDDVersion, "cdd_id", resp.json()["cdd_id"])
        assert params["prompt_source"] == "override"
        assert params["system_prompt_override"] == "OVERRIDE SYS"
        assert params["user_prompt_override"] == "OVERRIDE USER for {topic}"

    def test_cdd_registry_resolution_records_template_name_and_version(
        self, client, auth_headers, db, course, project, capture_llm, monkeypatch
    ):
        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "1")
        from promptops_app.database import CDDVersion

        make_db_prompt(db, "prov-cdd", system="PROV SYS", user="prov user",
                       component_type="cdd", is_default=True)
        resp = client.post(
            "/api/v1/cdd/generate",
            json=_cdd_payload(course, project),
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        params = self._params(db, CDDVersion, "cdd_id", resp.json()["cdd_id"])
        assert params["prompt_source"] == "registry"
        # Same convention as Generation.prompt_name (Phase 8 Generate wiring):
        # the logical template (stem) name + the resolved row's version tag.
        assert params["prompt_name"] == "cdd_generation"
        assert params["prompt_version"] == "v1"
        assert "system_prompt_override" not in params

    def test_blueprint_override_text_is_persisted(
        self, client, auth_headers, db, course, project, capture_llm
    ):
        from promptops_app.database import BlueprintVersion

        resp = client.post(
            "/api/v1/blueprints/generate",
            json={"course_id": course.id, "project_id": project.id,
                  "selected_module": "Module 1: Introduction",
                  "model_choice": "GPT-5.4",
                  "system_prompt_override": "BP OVERRIDE SYS",
                  "user_prompt_override": "BP OVERRIDE USER"},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 201), resp.text
        params = self._params(db, BlueprintVersion, "blueprint_id",
                              resp.json()["blueprint_id"])
        assert params["prompt_source"] == "override"
        assert params["system_prompt_override"] == "BP OVERRIDE SYS"
        assert params["user_prompt_override"] == "BP OVERRIDE USER"
