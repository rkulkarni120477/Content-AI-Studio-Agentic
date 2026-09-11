"""Smoke test: does an AIM day-Outline generation actually USE its three inputs?

Making an Outline for an AIM course pulls three things and folds them into one
LLM call: the **Blueprint prompt** (the pipeline prompt the dropdown selected),
the **Style** (active instructional style), and the **Source Library** (DIS --
day-scoped digest context for AIM, blob retrieval otherwise). Each is resolved
in a different place, each degrades silently on its own, and nothing downstream
can tell an Outline written with all three from one written with none.

So these tests do not check that resolution *returns* something -- they check
that the text reaches the prompt the model was actually called with, and that
what the stored version claims about its own grounding is true.

Markers name the input, so a failure says which one went missing:
    STYLE_MARKER / SOURCE_MARKER / CDD_MARKER / EXTRA_MARKER
"""

from __future__ import annotations

import json

import pytest

from promptops_app.services.llm_service import LLMResult

CANNED = "## Module Overview\n\nBody.\n\n## Lesson Structure\n\n1. Lesson 1.1\n"

STYLE_MARKER = "STYLE_MARKER_plain_language_no_jargon"
SOURCE_MARKER = "SOURCE_MARKER_hydraulic_actuator_torque_spec"
CDD_MARKER = "CDD_MARKER_course_level_outcomes"
EXTRA_MARKER = "EXTRA_MARKER_use_metric_units"


@pytest.fixture()
def aim_project(db):
    """An AIM tenant -- client_name is what resolve_course_dis_client normalizes."""
    from promptops_app.database import Project

    p = Project(name="AIM", client_name="AIM", created_by="test_admin")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def aim_course(db, aim_project):
    """The course name carries the block label: infer_block_label parses it from
    the title because no stored course-to-DIS-block linkage exists."""
    from promptops_app.database import Course

    c = Course(name="Block 2 - Aircraft Drawings", project_id=aim_project.id,
               created_by="test_admin")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def aim_cdd(db, aim_course, aim_project):
    from promptops_app.database import CDDVersion, CourseDesignDocument

    doc = CourseDesignDocument(
        title="AIM Block 2 CDD", course_title=aim_course.name, course_id=aim_course.id,
        project_id=aim_project.id, active_version="v1", created_by="test_admin")
    db.add(doc)
    db.commit()
    db.refresh(doc)
    db.add(CDDVersion(
        cdd_id=doc.id, version="v1", is_active=True, created_by="test_admin",
        full_content=(
            "## Course Overview\n\n" + CDD_MARKER + "\n\n"
            "## Course Structure\n\nDay 4 content.\n"
        ),
    ))
    aim_course.active_cdd_id = doc.id
    db.commit()
    return doc


@pytest.fixture()
def aim_style(db, aim_project, aim_course):
    from promptops_app.database import Style

    s = Style(style_id="aim-house-style", name="AIM House Style",
              generated_summary="### Voice\n" + STYLE_MARKER,
              project_id=aim_project.id, course_id=aim_course.id, is_active=True)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture()
def digest_on(monkeypatch):
    """AIM is the allowlisted client; the master switch is off by default."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "digest_pipeline_enabled", True)
    monkeypatch.setattr(settings, "digest_pipeline_clients", "aim")


@pytest.fixture()
def dis(monkeypatch):
    """Stub both Source Library reads and record which client_id each was asked for.

    Day-scoped (get_day_context_sync) is the AIM path; the blob query
    (retrieve_context_sync) is the fallback. Recording client_id is the point:
    retrieval must read the COURSE's library, not the caller's default.
    """
    calls = {"day": [], "blob": []}

    def fake_day(block, day, audience="instructor", current_user=None, client_id="", **kw):
        calls["day"].append({"block": block, "day": day, "client_id": client_id})
        return {
            "block": block, "day_number": day, "topic": "Hydraulic Actuators",
            "acs_codes": ["AC.II.A.1"],
            "digest": {"derived_objective": "Identify actuator components."},
            "units": [{"unit_type": "handbook", "title": "Actuators",
                       "content_unit_id": "u-1", "text": SOURCE_MARKER}],
            "supplement": [],
        }

    def fake_blob(purpose, payload, current_user=None, client_id="", timeout=None):
        calls["blob"].append({"purpose": purpose, "client_id": client_id})
        return {"combined_context": SOURCE_MARKER, "source_units": ["u-1"]}

    monkeypatch.setattr("app.core.dis_day_context.dis_client.get_day_context_sync", fake_day)
    monkeypatch.setattr("app.api.v1.routers.blueprints.dis_client.retrieve_context_sync", fake_blob)
    monkeypatch.setattr("app.api.v1.routers.blueprints.dis_client.generated_upsert_sync",
                        lambda *a, **k: None)
    return calls


@pytest.fixture()
def sent(monkeypatch):
    """Capture the (system, user) prompt pair the model was actually called with."""
    captured = {"system": "", "user": ""}

    def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return LLMResult(text=CANNED, model="mock-model", prompt_tokens=10,
                         completion_tokens=20, status="success", stop_reason="stop")

    monkeypatch.setattr("promptops_app.services.llm_service.generate_with_metadata", fake)
    captured["both"] = lambda: captured["system"] + "\n" + captured["user"]
    return captured


def _generate(client, headers, course, project, **overrides):
    body = {
        "course_id": course.id,
        "project_id": project.id,
        "selected_module": "Day 4: Exploded Views",
        "model_choice": "GPT-5.4",
        "day_number": 4,
    }
    body.update(overrides)
    return client.post("/api/v1/blueprints/generate", json=body, headers=headers)


def _params_of(db, blueprint_id):
    from promptops_app.database import BlueprintVersion

    ver = (db.query(BlueprintVersion)
             .filter_by(blueprint_id=blueprint_id, version="v1").one())
    return json.loads(ver.generation_params or "{}")


class TestTheThreeInputsReachTheModel:
    """Each input is resolved in a different place, so each is asserted
    separately rather than as one 'it generated' pass/fail."""

    def test_source_library_is_read_for_the_courses_own_client(
        self, client, auth_headers, db, aim_course, aim_project, aim_cdd, digest_on, dis, sent
    ):
        resp = _generate(client, auth_headers, aim_course, aim_project)
        assert resp.status_code in (200, 201), resp.text

        assert dis["day"], "day-scoped Source Library was never queried for an AIM course"
        assert dis["day"][0]["client_id"] == "aim"
        assert dis["day"][0]["block"] == "Block 2"   # parsed from the course name
        assert dis["day"][0]["day"] == 4

    def test_source_material_reaches_the_prompt(
        self, client, auth_headers, db, aim_course, aim_project, aim_cdd, digest_on, dis, sent
    ):
        _generate(client, auth_headers, aim_course, aim_project)
        assert SOURCE_MARKER in sent["both"]()

    def test_style_reaches_the_prompt(
        self, client, auth_headers, db, aim_course, aim_project, aim_cdd, aim_style,
        digest_on, dis, sent
    ):
        _generate(client, auth_headers, aim_course, aim_project, style_id=aim_style.id)
        assert STYLE_MARKER in sent["both"]()

    def test_style_reaches_the_prompt_exactly_once(
        self, client, auth_headers, db, aim_course, aim_project, aim_cdd, aim_style,
        digest_on, dis, sent
    ):
        """Y3: style_context used to be BOTH prepended into extra_block AND
        passed as the style_guidelines variable -- a template rendering both
        slots received it twice, verbatim. Only the named variable should
        deliver it now."""
        _generate(client, auth_headers, aim_course, aim_project, style_id=aim_style.id)
        both = sent["both"]()
        assert both.count(STYLE_MARKER) == 1, (
            f"expected the style to reach the prompt exactly once, found "
            f"{both.count(STYLE_MARKER)} occurrences"
        )

    def test_cdd_reaches_the_prompt(
        self, client, auth_headers, db, aim_course, aim_project, aim_cdd, digest_on, dis, sent
    ):
        _generate(client, auth_headers, aim_course, aim_project)
        assert CDD_MARKER in sent["both"]()

    def test_the_tenants_blueprint_prompt_ran_not_the_hardcoded_one(
        self, client, auth_headers, db, aim_course, aim_project, aim_cdd, digest_on, dis, sent
    ):
        """Which prompt produced the Outline has to be answerable from the row."""
        bp_id = _generate(client, auth_headers, aim_course, aim_project).json()["blueprint_id"]
        params = _params_of(db, bp_id)

        assert params.get("prompt_source") != "builtin_fallback", (
            "resolution fell through to the hardcoded template -- the tenant's "
            "Blueprint prompt was not used"
        )


class TestTheStoredRowTellsTheTruthAboutItsGrounding:
    @pytest.mark.xfail(strict=True, reason=(
        "BUG: blueprints.py sets dis_source_units=[] on the day-scoped path "
        "(`if day_context_block: dis_context_block, dis_source_units, ... = \"\", [], \"\"`), "
        "and resolve_day_context_block (dis_day_context.py) discards the units "
        "_dis_day_context_block already returned. The AIM day path is the ONLY one "
        "that grounds an Outline and records nothing about what it was grounded in — "
        "generation_params AND the DIS generated_upsert both go out empty."
    ))
    def test_day_grounded_outline_records_its_source_units(
        self, client, auth_headers, db, aim_course, aim_project, aim_cdd, digest_on, dis, sent
    ):
        """dis_source_units is the only record of WHICH source material a
        document was written from -- the traceability the DLU review asks for."""
        bp_id = _generate(client, auth_headers, aim_course, aim_project).json()["blueprint_id"]

        assert _params_of(db, bp_id)["dis_source_units"], (
            "day-scoped grounding fed the prompt but recorded no source units"
        )

    @pytest.mark.xfail(strict=True, reason=(
        "BUG: blueprints.py calls context_was_dropped(dis_context_block, ...) but on "
        "the day-scoped path dis_context_block is forced to \"\", so the check is "
        "vacuous and returns False without looking at day_context_block. The "
        "source-blind detector is switched off for exactly the AIM path it matters "
        "most on."
    ))
    def test_dropped_context_is_flagged_when_the_prompt_has_no_slot(
        self, client, auth_headers, db, aim_course, aim_project, aim_cdd, digest_on, dis, sent,
        monkeypatch
    ):
        """A prompt with nowhere to put the day context runs source-blind. That
        outcome is invisible in the output, so it must be on the row."""
        from promptops_app.prompts import prompt_builder

        def slotless(name, variables, **kwargs):
            tpl = type("T", (), {"name": name, "version": "v1", "prompt_row_id": 7,
                                 "prompt_title": "Slotless", "source": "library"})()
            return ("SYSTEM: write an outline.",
                    "USER: module " + str(variables.get("selected_module", "")),
                    tpl)

        monkeypatch.setattr(prompt_builder, "build_prompt_resolved", slotless)

        bp_id = _generate(client, auth_headers, aim_course, aim_project).json()["blueprint_id"]

        assert SOURCE_MARKER not in sent["both"]()          # it really was dropped
        assert _params_of(db, bp_id).get("source_context_dropped") is True


class TestAnInlineEditedPromptKeepsTheOtherInputs:
    def test_style_survives_a_prompt_override(
        self, client, auth_headers, db, aim_course, aim_project, aim_cdd, aim_style,
        digest_on, dis, sent
    ):
        """Editing the prompt inline replaces the TEMPLATE, not the Style the
        user also selected -- dropping it silently changes the house voice."""
        _generate(client, auth_headers, aim_course, aim_project, style_id=aim_style.id,
                  system_prompt_override="SYSTEM: custom.",
                  user_prompt_override="USER: custom.")

        assert STYLE_MARKER in sent["both"]()

    def test_extra_instructions_survive_a_prompt_override(
        self, client, auth_headers, db, aim_course, aim_project, aim_cdd, digest_on, dis, sent
    ):
        _generate(client, auth_headers, aim_course, aim_project,
                  extra_instructions=EXTRA_MARKER,
                  system_prompt_override="SYSTEM: custom.",
                  user_prompt_override="USER: custom.")

        assert EXTRA_MARKER in sent["both"]()
