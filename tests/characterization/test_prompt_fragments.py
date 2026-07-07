"""Phase 9 — shared fragment library.

Fragments are versioned reusable prompt blocks (persona_tone, style_guide,
…). The resolution contract mirrors the Phase 8 registry tiers: with
PROMPT_RESOLVE_BY_COMPONENT off, or with no authored fragment, callers get
the legacy constant byte-identical; flag-on, an authored fragment overrides
it. Seeded fragment text equals the constants, so even flag-on output is
unchanged until an admin edits a fragment.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.conftest import _TestSessionLocal


@pytest.fixture()
def job_env(monkeypatch):
    monkeypatch.setattr(
        "promptops_app.jobs.generation_jobs.SessionLocal", _TestSessionLocal
    )
    monkeypatch.setattr(
        "promptops_app.services.ce_validation_service.run_ce_validation",
        lambda out, db, **kwargs: out,
    )

    class _FakeTask:
        id = "fake-task-id"

    class _FakeCeleryTask:
        @staticmethod
        def delay(*args, **kwargs):
            return _FakeTask()

    monkeypatch.setattr(
        "promptops_app.jobs.plagiarism_jobs.run_plagiarism_scan",
        _FakeCeleryTask(),
    )


def _make_job(db, **param_overrides):
    from promptops_app.database import GenerationJob

    params = {
        "topic": "Infection Control",
        "b_type": "Lesson",
        "model_choice": "GPT-5.4",
        "target_audience": "Nursing Students Year 2",
        "expert_domain": "Clinical Nursing",
        "expert_exp": 20,
        "aud_cat": "Professional/Corporate",
        "user_name": "test_admin",
    }
    params.update(param_overrides)
    job = GenerationJob(
        id=str(uuid.uuid4()),
        job_type="generation",
        status="queued",
        input_payload_json=json.dumps(params),
    )
    db.add(job)
    db.commit()
    return job


class TestFragmentRepository:
    def test_set_get_round_trip_and_versioning(self, db):
        from promptops_app.repositories.fragment_repository import (
            get_active_fragment_text,
            set_fragment_text,
        )

        assert get_active_fragment_text(db, "guardrails") is None

        v1 = set_fragment_text(db, "guardrails", "Never invent citations.",
                               created_by="admin")
        assert (v1.version, v1.version_number, v1.is_active) == ("v1", 1, True)
        assert get_active_fragment_text(db, "guardrails") == "Never invent citations."

        v2 = set_fragment_text(db, "guardrails", "Never invent citations. Ever.",
                               created_by="admin", change_reason="tightened")
        assert (v2.version, v2.version_number) == ("v2", 2)
        assert get_active_fragment_text(db, "guardrails") == "Never invent citations. Ever."

        from promptops_app.database import PromptFragmentVersion
        active = (db.query(PromptFragmentVersion)
                  .filter(PromptFragmentVersion.is_active.is_(True)).all())
        assert len(active) == 1  # the flag moved, append-only history kept


class TestFragmentSeeds:
    def test_seed_creates_persona_and_style_guide_idempotently(self, db):
        from promptops_app.database import seed_prompt_fragments
        from promptops_app.prompts.brace_conversion import find_single_brace_vars
        from promptops_app.repositories.fragment_repository import (
            get_active_fragment_text,
        )

        assert sorted(seed_prompt_fragments(db)) == ["persona_tone", "style_guide"]
        db.commit()
        assert seed_prompt_fragments(db) == []  # re-run creates nothing

        persona = get_active_fragment_text(db, "persona_tone")
        assert "{{expert_domain}}" in persona  # converted to {{double}}
        assert find_single_brace_vars(persona) == []
        style = get_active_fragment_text(db, "style_guide")
        assert "UNIFIED INSTRUCTIONAL VOICE" in style


class TestFragmentResolution:
    def test_flag_off_returns_constant_fallback(self, db, monkeypatch):
        from promptops_app.prompts.fragment_composer import resolve_fragment
        from promptops_app.repositories.fragment_repository import set_fragment_text

        monkeypatch.delenv("PROMPT_RESOLVE_BY_COMPONENT", raising=False)
        set_fragment_text(db, "persona_tone", "DB PERSONA", created_by="admin")
        text, source = resolve_fragment(db, "persona_tone", fallback="CONSTANT")
        assert (text, source) == ("CONSTANT", "constant")

    def test_flag_on_prefers_db_fragment(self, db, monkeypatch):
        from promptops_app.prompts.fragment_composer import resolve_fragment
        from promptops_app.repositories.fragment_repository import set_fragment_text

        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "1")
        set_fragment_text(db, "persona_tone", "DB PERSONA", created_by="admin")
        text, source = resolve_fragment(db, "persona_tone", fallback="CONSTANT")
        assert (text, source) == ("DB PERSONA", "db")
        # Unauthored keys still fall back.
        text, source = resolve_fragment(db, "guardrails", fallback="G-CONST")
        assert (text, source) == ("G-CONST", "constant")

    def test_render_fragment_substitutes_double_braces(self, db, monkeypatch):
        from promptops_app.prompts.fragment_composer import render_fragment
        from promptops_app.repositories.fragment_repository import set_fragment_text

        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "1")
        set_fragment_text(db, "persona_tone", "Act as {{expert_domain}} expert.",
                          created_by="admin")
        out = render_fragment(db, "persona_tone", {"expert_domain": "Nursing"},
                              fallback_rendered="LEGACY")
        assert out == "Act as Nursing expert."

    def test_compose_prompt_orders_fragments_around_stage(self, db, monkeypatch):
        from promptops_app.prompts.fragment_composer import compose_prompt
        from promptops_app.repositories.fragment_repository import set_fragment_text

        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "1")
        set_fragment_text(db, "guardrails", "GUARD", created_by="admin")
        set_fragment_text(db, "output_contract_markdown", "CONTRACT",
                          created_by="admin")
        out = compose_prompt(db, "STAGE {{x}}", variables={"x": "1"},
                             output_contract_key="output_contract_markdown")
        # context_header/persona_tone unauthored → skipped, order preserved.
        assert out == "GUARD\n\nSTAGE {{x}}\n\nCONTRACT"


class TestPersonaFragmentInJob:
    def test_flag_off_ignores_authored_fragment(self, db, job_env, capture_llm,
                                                monkeypatch):
        from promptops_app.jobs.generation_jobs import run_generation_job
        from promptops_app.repositories.fragment_repository import set_fragment_text

        monkeypatch.delenv("PROMPT_RESOLVE_BY_COMPONENT", raising=False)
        set_fragment_text(db, "persona_tone", "CUSTOM PERSONA {{expert_domain}}.",
                          created_by="admin")
        job = _make_job(db)
        run_generation_job(job.id)
        assert capture_llm[0]["system"].startswith(
            "Act as 20 yr Domain expert in Clinical Nursing."
        )

    def test_flag_on_authored_fragment_reaches_llm(self, db, job_env, capture_llm,
                                                   monkeypatch):
        from promptops_app.jobs.generation_jobs import run_generation_job
        from promptops_app.repositories.fragment_repository import set_fragment_text

        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "1")
        set_fragment_text(db, "persona_tone", "CUSTOM PERSONA {{expert_domain}}.",
                          created_by="admin")
        job = _make_job(db)
        run_generation_job(job.id)
        assert capture_llm[0]["system"].startswith("CUSTOM PERSONA Clinical Nursing.")

    def test_flag_on_without_fragment_keeps_legacy_text(self, db, job_env,
                                                        capture_llm, monkeypatch):
        from promptops_app.jobs.generation_jobs import run_generation_job

        monkeypatch.setenv("PROMPT_RESOLVE_BY_COMPONENT", "1")
        job = _make_job(db)
        run_generation_job(job.id)
        assert capture_llm[0]["system"].startswith(
            "Act as 20 yr Domain expert in Clinical Nursing."
        )


class TestFragmentAPI:
    def test_admin_round_trip(self, client, auth_headers):
        resp = client.put(
            "/api/v1/prompts/fragments/guardrails",
            json={"content": "Do not fabricate sources.",
                  "change_reason": "initial"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["fragment_key"] == "guardrails"
        assert body["active_version"] == "v1"
        assert body["content"] == "Do not fabricate sources."

        resp = client.put(
            "/api/v1/prompts/fragments/guardrails",
            json={"content": "Do not fabricate sources. Cite everything."},
            headers=auth_headers,
        )
        assert resp.json()["active_version"] == "v2"

        listing = client.get("/api/v1/prompts/fragments", headers=auth_headers)
        assert listing.status_code == 200
        rows = {r["fragment_key"]: r for r in listing.json()}
        assert rows["guardrails"]["content"].endswith("Cite everything.")

    def test_unknown_key_422(self, client, auth_headers):
        resp = client.put(
            "/api/v1/prompts/fragments/not_a_slot",
            json={"content": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_single_brace_content_422(self, client, auth_headers):
        resp = client.put(
            "/api/v1/prompts/fragments/guardrails",
            json={"content": "Act as {expert_domain} expert."},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        assert "double" in resp.json()["error"]["message"].lower()

    def test_non_admin_403(self, client, author_headers):
        assert client.get("/api/v1/prompts/fragments",
                          headers=author_headers).status_code == 403
        assert client.put("/api/v1/prompts/fragments/guardrails",
                          json={"content": "x"},
                          headers=author_headers).status_code == 403
