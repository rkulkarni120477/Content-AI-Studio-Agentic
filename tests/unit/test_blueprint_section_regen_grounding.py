"""Section regeneration has to see the section it is regenerating.

The prompt was built from the section title, the module title, the blueprint
title and a 1500-char CDD summary — and nothing else. The current text was never
sent, so "Regenerate Section" did not revise anything: it drafted a fresh
section that had never seen the one it replaced.

On a whole-document section that is destructive rather than merely wrong.
blueprint_versions 396 -> 397 on the dev database is the real case: a 5,611-char
Day 20 final-exam blueprint (ACS coverage tables, Master Mechanic Moment, open
items needing human attention) was regenerated with the instruction "ACS
Alignment & Source is N/A why all data is there check source and internet" and
came back as 2,712 chars of generic "Module 20 / Lesson 1 / Lesson 2 / Lesson 3 /
Capstone project" filler that echoed the prompt's own header fields back. The
instruction asked for the N/A cells to be checked, which is impossible without
the table; the model could only invent a replacement.

These tests pin that the current content reaches the model, that the guardrail
telling it to revise rather than redraft travels with it, and that a caller which
cannot supply the content still works.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def project(db):
    from promptops_app.database import Project

    p = Project(name="Regen Project", created_by="test_admin")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture()
def course(db, project):
    from promptops_app.database import Course

    c = Course(name="Regen Course", project_id=project.id, created_by="test_admin")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def blueprint(db, project, course):
    from promptops_app.database import ModuleBlueprint

    bp = ModuleBlueprint(
        project_id=project.id,
        course_id=course.id,
        module_number=20,
        module_title="Module 20",
        title="Day 20 Blueprint",
        created_by="test_admin",
    )
    db.add(bp)
    db.commit()
    db.refresh(bp)
    return bp


def _regen_call(calls):
    """The regeneration call, selected by content rather than by index.

    Section regeneration now makes up to two LLM calls: prompt_guidance distils
    the authoring prompt's conventions first, then the revision itself runs. The
    distillation is memoized on a hash of the prompt text, so whether it happens
    at all depends on what ran before it in the process — indexing calls[0] would
    pass or fail depending on test ORDER. Selecting the call that carries the
    revision contract is stable either way.
    """
    hits = [c for c in calls if "Revise the current content above" in (c["user"] or "")
            or "REVISION INSTRUCTIONS" in (c["user"] or "")]
    assert hits, (
        "no regeneration call found; calls were: "
        + repr([(c["user"] or "")[:80] for c in calls])
    )
    return hits[-1]


@pytest.fixture()
def stub_llm(monkeypatch):
    """Record the prompts and return canned text — no network, no cost.

    Patches ``generate_with_metadata``, which is what the route calls: it needs
    the result object to request an output budget and to see whether the reply
    hit it, neither of which ``generate_text``'s bare string can carry.
    """
    from promptops_app.core.llm_client import LLMResult

    calls: list[dict] = []

    def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
        calls.append({
            "system": system_prompt, "user": user_prompt,
            "max_tokens": kwargs.get("max_tokens"),
        })
        return LLMResult(
            text="## Revised\n\nRevised content.", model="mock-model",
            prompt_tokens=10, completion_tokens=20, stop_reason="end_turn",
        )

    monkeypatch.setattr(
        "promptops_app.services.llm_service.generate_with_metadata", fake,
    )
    return calls


# The kind of content that cannot survive being redrafted from a title: every
# value here is a fact the model has no way to reconstruct.
CURRENT = (
    "**B. ACS Coverage**\n\n"
    "| ACS Element | Direct Coverage | Supporting Coverage |\n"
    "|-------------|-----------------|---------------------|\n"
    "| AM.I.B      | N/A             | N/A                 |\n"
    "| AM.I.E      | N/A             | N/A                 |\n\n"
    "**D. Issues Requiring Human Attention**\n\n"
    "- Approval Blocker: Confirm the handbook edition for Days 14-16.\n"
)


def _regen(client, auth_headers, bp_id, **overrides):
    body = {
        "section_key": "Part I — DLU-Wide Information",
        "feedback": "ACS Alignment & Source is N/A why all data is there",
        "model_choice": "GPT-5.4",
    }
    body.update(overrides)
    resp = client.post(
        f"/api/v1/blueprints/{bp_id}/regenerate-section", json=body, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestCurrentContentReachesTheModel:
    def test_the_section_being_revised_is_in_the_prompt(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        _regen(client, auth_headers, blueprint.id, section_content=CURRENT)
        prompt = _regen_call(stub_llm)["user"]
        # Not a paraphrase or a summary — the actual rows the instruction is about.
        assert "| AM.I.B      | N/A             | N/A                 |" in prompt
        assert "Approval Blocker: Confirm the handbook edition for Days 14-16." in prompt

    def test_it_is_sent_whole_rather_than_clipped(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        # Comfortably inside the model's output ceiling, so the size guard does
        # not fire and this measures clipping only. Anything ABOVE the ceiling is
        # refused outright — see TestItRefusesRatherThanTruncate.
        big = "\n".join(f"| ROW-{i:04d} | data | more |" for i in range(500))
        _regen(client, auth_headers, blueprint.id, section_content=big)
        prompt = _regen_call(stub_llm)["user"]
        assert "| ROW-0000 | data | more |" in prompt
        assert "| ROW-0499 | data | more |" in prompt, "the tail was dropped"

    def test_the_model_is_asked_for_its_real_output_ceiling(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        # No max_tokens meant inheriting a flat default, capping a capable model
        # at a fraction of its range.
        _regen(client, auth_headers, blueprint.id, section_content=CURRENT)
        assert _regen_call(stub_llm)["max_tokens"] == 16384

    def test_the_revise_dont_redraft_guardrail_travels_with_it(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        _regen(client, auth_headers, blueprint.id, section_content=CURRENT)
        prompt = _regen_call(stub_llm)["user"]
        assert "source of truth" in prompt
        assert "Do NOT replace it with a fresh draft" in prompt

    def test_the_instruction_still_reaches_the_model(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        _regen(client, auth_headers, blueprint.id, section_content=CURRENT)
        assert "ACS Alignment & Source is N/A" in _regen_call(stub_llm)["user"]

    def test_teacher_mode_is_grounded_too(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        _regen(client, auth_headers, blueprint.id, section_content=CURRENT, teacher_mode=True)
        prompt = _regen_call(stub_llm)["user"]
        assert "Approval Blocker: Confirm the handbook edition for Days 14-16." in prompt
        assert "Do NOT replace it with a fresh draft" in prompt


class TestCallersThatCannotSupplyContent:
    def test_omitting_it_does_not_break_the_request(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        # The template now carries a {current_content} placeholder; a caller that
        # predates the field must not produce a KeyError from .format().
        body = _regen(client, auth_headers, blueprint.id)
        assert body["updated_content"] == "## Revised\n\nRevised content."

    def test_an_empty_section_is_told_to_draft_from_the_cdd(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        _regen(client, auth_headers, blueprint.id, section_content="")
        assert "If the current content above is empty" in _regen_call(stub_llm)["user"]


class TestTheTemplatesThemselves:
    @pytest.mark.parametrize("name", [
        "BLUEPRINT_SECTION_REGENERATE_PROMPT",
        "TEACHER_BLUEPRINT_SECTION_REGENERATE_PROMPT",
    ])
    def test_both_variants_have_the_placeholder(self, name):
        from promptops_app import prompt_templates

        assert "{current_content}" in getattr(prompt_templates, name), (
            f"{name} would silently drop the section content"
        )


class TestItRefusesRatherThanTruncate:
    """A reply that hit the output cap must not overwrite stored content.

    The route used ``generate_text``, which returns a bare string: it could
    neither ask for the model's real output ceiling nor see whether the reply hit
    it. A capped reply was committed as if complete — the same silent-loss shape
    the CDD router already refuses.
    """

    def test_a_target_too_large_to_return_is_refused_before_spending(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        # 16,384-token cap on GPT-5.4; ~4 chars/token, so this is comfortably over.
        huge = "word " * 200_000
        resp = client.post(
            f"/api/v1/blueprints/{blueprint.id}/regenerate-section",
            json={"section_key": "Full Document", "section_content": huge,
                  "feedback": "tidy it", "model_choice": "GPT-5.4"},
            headers=auth_headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "REGENERATION_TOO_LARGE"
        # Refused before the call, so nothing was spent.
        assert stub_llm == []

    def test_a_truncated_reply_is_not_applied(
        self, client, auth_headers, blueprint, monkeypatch,
    ):
        from promptops_app.core.llm_client import LLMResult

        def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
            # max_tokens must now be requested explicitly, not left to the default.
            assert kwargs.get("max_tokens"), "no output budget was requested"
            return LLMResult(
                text="### Part I\n\nRevised up to here and then cut off mid-",
                model="gpt-4o", stop_reason="max_tokens",
            )

        monkeypatch.setattr(
            "promptops_app.services.llm_service.generate_with_metadata", fake,
        )
        resp = client.post(
            f"/api/v1/blueprints/{blueprint.id}/regenerate-section",
            json={"section_key": "Part I", "section_content": CURRENT,
                  "feedback": "expand", "model_choice": "GPT-5.4"},
            headers=auth_headers,
        )
        assert resp.status_code >= 400, "a truncated reply was returned as if complete"
        assert "incomplete" in resp.text.lower()

    def test_a_complete_reply_still_succeeds(
        self, client, auth_headers, blueprint, monkeypatch,
    ):
        from promptops_app.core.llm_client import LLMResult

        def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
            return LLMResult(text="### Part I\n\nAll good.", model="gpt-4o",
                             stop_reason="end_turn")

        monkeypatch.setattr(
            "promptops_app.services.llm_service.generate_with_metadata", fake,
        )
        resp = client.post(
            f"/api/v1/blueprints/{blueprint.id}/regenerate-section",
            json={"section_key": "Part I", "section_content": CURRENT,
                  "feedback": "expand", "model_choice": "GPT-5.4"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["updated_content"] == "### Part I\n\nAll good."


# ---------------------------------------------------------------------------
# Scoped CDD context (replaces the blind 1,500-char prefix)
# ---------------------------------------------------------------------------

# A worksheet-shaped CDD, the structure real Block CDDs use. The answer to
# "why does Day 20 read N/A" lives in the DAY-BY-DAY row, which on the real
# document (CDD 183, 78,189 chars) sits at line 173 of 189 — far past anything
# a 1,500-char prefix of the document could ever reach.
WORKSHEET_CDD = {
    # Padded to mirror the real document's proportions: on CDD 183 the row that
    # answers the instruction sits at line 173 of 189, so a prefix of the
    # document cannot reach it however the prefix is sized. Without this padding
    # the whole fixture fits inside 1,500 chars and the old prefix accidentally
    # contained the answer — the test would pass against the bug.
    "WORKSHEET 1: INTRO TO THE BLUEPRINT": (
        "| The Purpose of the Blueprint | The Block Blueprint is a planning "
        "document we use to show all the elements of a course. " + ("It is a "
        "working document that gathers the syllabus and ACS standards in one "
        "place. ") * 40 + "|\n"
    ),
    "WORKSHEET 2: BLOCK OVERVIEW": (
        "- **Block Number:** 2\n"
        "- **Block Name:** General Science II\n"
    ),
    "WORKSHEET 4: ACS CODE REGISTRY": (
        "| Code | Subject | Title |\n|---|---|---|\n"
        "| AM.I.B | B | Aircraft Drawings |\n"
        "| AM.I.E | E | Materials and Processes |\n"
        "| AM.I.G | G | Cleaning and Corrosion Control |\n"
    ),
    "WORKSHEET 5: DAY-BY-DAY MAP": (
        "| Day | Topic | Sources | ACS Codes |\n|---|---|---|---|\n"
        "| Day 1 | Introduction to Aircraft Drawings | FAA-H-8083-30B Ch.4 | AM.I.B |\n"
        "| Day 19 | Review - Cleaning and Corrosion | AC 43.13-1B Ch.6 | (review) |\n"
        "| Day 20 | Test — Block 2: Final Exam | N/A - exam day, no handbook reading "
        "assigned | Full-block coverage (AM.I.B, AM.I.E, AM.I.G — all codes taught "
        "Days 1–19) |\n"
    ),
}


@pytest.fixture()
def worksheet_cdd(db, project, course):
    """A worksheet-shaped CDD linked to the blueprint under test."""
    import json

    from promptops_app.database import CourseDesignDocument, CDDVersion

    body = "\n\n".join(f"## {k}\n{v}" for k, v in WORKSHEET_CDD.items())
    cdd = CourseDesignDocument(
        project_id=project.id, course_id=course.id,
        title="Block 02 — CDD", course_title=course.name, created_by="test_admin",
    )
    db.add(cdd)
    db.commit()
    db.refresh(cdd)
    ver = CDDVersion(
        cdd_id=cdd.id, version="v1", is_active=True, full_content=body,
        sections=json.dumps(WORKSHEET_CDD), created_by="test_admin",
        change_reason="seed",
    )
    db.add(ver)
    db.commit()
    return cdd


@pytest.fixture()
def grounded_blueprint(db, blueprint, worksheet_cdd):
    blueprint.cdd_id = worksheet_cdd.id
    db.commit()
    db.refresh(blueprint)
    return blueprint


class TestTheCddContextIsScopedNotClipped:
    def test_the_fixture_is_out_of_reach_of_a_prefix(self):
        # Guards the test above: if the fixture ever shrinks back under the old
        # 1,500-char cap, that test would pass against the unfixed code.
        body = "\n\n".join(f"## {k}\n{v}" for k, v in WORKSHEET_CDD.items())
        assert "Final Exam" not in body[:1500]

    def test_the_day_row_that_answers_the_instruction_is_in_the_prompt(
        self, client, auth_headers, grounded_blueprint, stub_llm,
    ):
        _regen(client, auth_headers, grounded_blueprint.id, section_content=CURRENT)
        prompt = _regen_call(stub_llm)["user"]
        assert "Test — Block 2: Final Exam" in prompt
        assert "Full-block coverage (AM.I.B, AM.I.E, AM.I.G" in prompt

    def test_the_scope_comes_from_the_blueprint_not_the_instruction(
        self, client, auth_headers, grounded_blueprint, stub_llm,
    ):
        # The instruction names no day at all. The blueprint is Day 20, and the
        # day is a property of the document — parse_scope only reads the
        # instruction, so the route has to supply the scope itself.
        _regen(client, auth_headers, grounded_blueprint.id,
               feedback="fix the coverage cells", section_content=CURRENT)
        prompt = _regen_call(stub_llm)["user"]
        assert "Test — Block 2: Final Exam" in prompt, "Day 20 was not scoped in"
        assert "Introduction to Aircraft Drawings" not in prompt, "Day 1 leaked in"

    def test_the_context_is_labelled_authoritative(
        self, client, auth_headers, grounded_blueprint, stub_llm,
    ):
        _regen(client, auth_headers, grounded_blueprint.id, section_content=CURRENT)
        assert "authoritative" in _regen_call(stub_llm)["user"].lower()


class TestContextFallbacks:
    def test_a_blueprint_with_no_cdd_says_so_rather_than_sending_nothing(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        # `blueprint` has no cdd_id.
        _regen(client, auth_headers, blueprint.id, section_content=CURRENT)
        assert "No CDD linked." in _regen_call(stub_llm)["user"]

    def test_the_builder_reports_which_path_it_took(self, db, grounded_blueprint):
        import app.api.v1.routers.blueprints as R

        text, prov = R._blueprint_regen_context(
            db, grounded_blueprint, instruction="fix it",
            section_key="Part I", section_content="",
        )
        assert prov["grounded"] is True
        assert prov["scope"] == "days=20"
        assert "day_rows" in prov["context_sources"]
        assert text.strip()

    def test_no_cdd_is_reported_as_such(self, db, blueprint):
        import app.api.v1.routers.blueprints as R

        text, prov = R._blueprint_regen_context(
            db, blueprint, instruction="", section_key="Part I", section_content="",
        )
        assert prov == {"grounded": False, "reason": "no_cdd"}
        assert text == "No CDD linked."


# ---------------------------------------------------------------------------
# Item regeneration — the same grounding, and the same refusal
# ---------------------------------------------------------------------------

SECTION_WITH_ITEMS = (
    "- First bullet about drawings.\n"
    "- Second bullet about materials.\n"
    "- Third bullet about corrosion.\n"
)


@pytest.fixture()
def stub_item_llm(monkeypatch):
    """Capture what the item-regeneration prompt actually contains."""
    calls: list[dict] = []

    def fake(model_choice, system_prompt, user_prompt, *args, **kwargs):
        calls.append({"system": system_prompt, "user": user_prompt})
        return "Regenerated bullet."

    # regen_single_item calls call_llm, which is llm_service.generate_text
    # re-exported into the parser module.
    monkeypatch.setattr("promptops_app.parsers.blueprint_parser.call_llm", fake)
    return calls


def _regen_item(client, auth_headers, bp_id, **overrides):
    body = {
        "section_key": "Part I — DLU-Wide Information",
        "section_content": SECTION_WITH_ITEMS,
        "item_index": 1,
        "feedback": "why is the ACS alignment N/A",
        "model_choice": "GPT-5.4",
    }
    body.update(overrides)
    resp = client.post(
        f"/api/v1/blueprints/{bp_id}/regenerate-item", json=body, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestItemRegenerationIsGroundedToo:
    def test_it_gets_the_scoped_day_rows_not_a_300_char_slice(
        self, client, auth_headers, grounded_blueprint, stub_item_llm,
    ):
        _regen_item(client, auth_headers, grounded_blueprint.id)
        prompt = stub_item_llm[0]["user"]
        assert "Test — Block 2: Final Exam" in prompt
        assert "Full-block coverage (AM.I.B, AM.I.E, AM.I.G" in prompt

    def test_the_instruction_is_no_longer_polluted_with_document_text(
        self, client, auth_headers, grounded_blueprint, stub_item_llm,
    ):
        # The CDD blob used to be concatenated onto the requester's words, so the
        # model read "why is the ACS alignment N/A Module: Module 20. CDD
        # context: # Course Design Doc..." as one instruction.
        _regen_item(client, auth_headers, grounded_blueprint.id)
        prompt = stub_item_llm[0]["user"]
        line = next(l for l in prompt.split("\n") if l.startswith("Instruction:"))
        assert line.strip() == "Instruction: why is the ACS alignment N/A"
        assert "CDD context:" not in prompt

    def test_the_module_identity_survives_the_move(
        self, client, auth_headers, grounded_blueprint, stub_item_llm,
    ):
        _regen_item(client, auth_headers, grounded_blueprint.id)
        assert "Module: Module 20" in stub_item_llm[0]["user"]

    def test_an_item_too_large_to_return_is_refused_before_spending(
        self, client, auth_headers, grounded_blueprint, stub_item_llm,
    ):
        # A markdown table parses to ONE item, so "one item" can be a whole day
        # table — the case where a spliced fragment silently loses rows.
        huge = "- " + ("word " * 200_000)
        resp = client.post(
            f"/api/v1/blueprints/{grounded_blueprint.id}/regenerate-item",
            json={"section_key": "Part I", "section_content": huge, "item_index": 0,
                  "feedback": "tidy", "model_choice": "GPT-5.4"},
            headers=auth_headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "REGENERATION_TOO_LARGE"
        assert stub_item_llm == [], "the model was called despite the refusal"

    def test_a_normal_item_regeneration_still_works(
        self, client, auth_headers, grounded_blueprint, stub_item_llm,
    ):
        body = _regen_item(client, auth_headers, grounded_blueprint.id)
        assert body["patched_item"] == "Regenerated bullet."
        # Siblings untouched.
        assert "First bullet about drawings." in body["updated_content"]
        assert "Third bullet about corrosion." in body["updated_content"]
        assert "Second bullet about materials." not in body["updated_content"]
