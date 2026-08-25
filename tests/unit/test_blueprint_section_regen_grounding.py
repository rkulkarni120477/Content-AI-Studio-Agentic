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
        prompt = stub_llm[0]["user"]
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
        prompt = stub_llm[0]["user"]
        assert "| ROW-0000 | data | more |" in prompt
        assert "| ROW-0499 | data | more |" in prompt, "the tail was dropped"

    def test_the_model_is_asked_for_its_real_output_ceiling(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        # No max_tokens meant inheriting a flat default, capping a capable model
        # at a fraction of its range.
        _regen(client, auth_headers, blueprint.id, section_content=CURRENT)
        assert stub_llm[0]["max_tokens"] == 16384

    def test_the_revise_dont_redraft_guardrail_travels_with_it(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        _regen(client, auth_headers, blueprint.id, section_content=CURRENT)
        prompt = stub_llm[0]["user"]
        assert "source of truth" in prompt
        assert "Do NOT replace it with a fresh draft" in prompt

    def test_the_instruction_still_reaches_the_model(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        _regen(client, auth_headers, blueprint.id, section_content=CURRENT)
        assert "ACS Alignment & Source is N/A" in stub_llm[0]["user"]

    def test_teacher_mode_is_grounded_too(
        self, client, auth_headers, blueprint, stub_llm,
    ):
        _regen(client, auth_headers, blueprint.id, section_content=CURRENT, teacher_mode=True)
        prompt = stub_llm[0]["user"]
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
        assert "If the current content above is empty" in stub_llm[0]["user"]


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
