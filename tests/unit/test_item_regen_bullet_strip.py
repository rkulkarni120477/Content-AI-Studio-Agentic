"""Stripping the bullet a model re-adds, without eating markdown emphasis.

regen_single_item returns the text of ONE item and the caller puts the bullet
back, so a leading "- " in the model's reply has to go or it would be doubled.
The pattern that did this read the first character of `**Bold:**` as a bullet.

That made per-item regeneration lossy in a way nothing detected: the item came
back looking regenerated, the section committed cleanly, and one asterisk had
gone. Worksheet 1 of an AIM CDD is entirely `- **Field:** value` lines, so every
field label was one regeneration away from being malformed. Observed live on
CDD 169 item 12, which reads `- *Supplemental References:**` today.
"""

import pytest

from promptops_app.parsers.blueprint_parser import (
    _strip_leading_bullet,
    parse_items_from_section,
    patch_item_in_section,
)


@pytest.mark.parametrize("text", [
    "**Supplemental References:** Essential Texts: FAA",
    "**Primary Handbooks:** FAA-H-8083-30B",
    "**Block:** Block 2",
])
def test_bold_emphasis_survives(text):
    """The regression. A bold delimiter is not a bullet."""
    assert _strip_leading_bullet(text) == text


def test_italic_emphasis_survives():
    """Same defect, second victim: `*emphasis*` lost its opening delimiter too."""
    assert _strip_leading_bullet("*emphasis* leads this line") == "*emphasis* leads this line"


@pytest.mark.parametrize("text,expected", [
    ("- Normal bullet item", "Normal bullet item"),
    ("* Asterisk bullet", "Asterisk bullet"),
    ("• Unicode bullet", "Unicode bullet"),
    ("-No space after dash", "No space after dash"),
    ("-   Extra spaces", "Extra spaces"),
])
def test_real_bullets_are_still_stripped(text, expected):
    """The behaviour that made the strip necessary in the first place, kept."""
    assert _strip_leading_bullet(text) == expected


def test_only_the_first_bullet_is_stripped():
    assert _strip_leading_bullet("- - nested looking") == "- nested looking"


def test_text_with_no_bullet_is_untouched():
    assert _strip_leading_bullet("Plain text") == "Plain text"
    assert _strip_leading_bullet("") == ""


def test_a_bold_field_survives_a_full_parse_patch_round_trip():
    """End to end over the two functions that produced the corruption: the model
    returns the item correctly, the bullet is stripped, the line is rebuilt."""
    section = (
        "- **Primary Handbooks:** FAA-H-8083-30B\n"
        "- **Supplemental References:** Required Texts: Federal Aviation Administration\n"
    )
    items = parse_items_from_section(section)
    assert len(items) == 2

    model_reply = "**Supplemental References:** Essential Texts: FAA; AC43-4A"
    patched = patch_item_in_section(section, 1, _strip_leading_bullet(model_reply))

    assert "- **Supplemental References:** Essential Texts: FAA; AC43-4A" in patched
    assert "- *Supplemental" not in patched.replace("- **Supplemental", "")
    # The sibling is preserved byte-for-byte.
    assert "- **Primary Handbooks:** FAA-H-8083-30B" in patched


def test_a_model_that_re_adds_the_bullet_does_not_double_it():
    section = "- **Block:** Block 2\n"
    patched = patch_item_in_section(section, 0, _strip_leading_bullet("- **Block:** Block 02"))
    assert patched.strip() == "- **Block:** Block 02"


# ---------------------------------------------------------------------------
# The instruction reaching the model
# ---------------------------------------------------------------------------

def test_the_users_instruction_and_grounding_both_reach_the_prompt(monkeypatch):
    """The ⟳ button sends the "Regen instruction" box as `feedback`, which arrives
    here as `custom_instruction`. Pin that it lands in the prompt alongside the
    caller's grounding, since a silently dropped instruction would look exactly
    like a model that ignored it."""
    import promptops_app.parsers.blueprint_parser as bp

    seen = {}

    def fake_call_llm(model, sys_p, user_p, usage_ctx=None):
        seen["system"], seen["user"] = sys_p, user_p
        return "**Primary Handbooks:** FAA-H-8083-30B; AC43.13-1B/2B"

    monkeypatch.setattr(bp, "call_llm", fake_call_llm)

    out = bp.regen_single_item(
        section_title="WORKSHEET 1: BLOCK OVERVIEW",
        section_content="- **Primary Handbooks:** FAA-H-8083-30B\n- **Block:** Block 2",
        item_index=0,
        item_text="**Primary Handbooks:** FAA-H-8083-30B",
        custom_instruction="refer the syllabus and list every applicable handbook",
        context="### SEARCH RESULTS\nRequired Text(s): AC43.13-1B/2B",
    )

    assert "refer the syllabus and list every applicable handbook" in seen["user"]
    assert "AC43.13-1B/2B" in seen["user"]              # the retrieved grounding
    assert "**Primary Handbooks:** FAA-H-8083-30B" in seen["user"]   # the item itself
    # And the reply survives the bullet strip with its bold intact.
    assert out == "**Primary Handbooks:** FAA-H-8083-30B; AC43.13-1B/2B"


def test_an_empty_instruction_falls_back_rather_than_sending_a_blank(monkeypatch):
    import promptops_app.parsers.blueprint_parser as bp

    seen = {}
    monkeypatch.setattr(bp, "call_llm",
                        lambda m, s, u, c=None: seen.setdefault("user", u) or "x")
    bp.regen_single_item(section_title="S", section_content="- a", item_index=0,
                         item_text="a", custom_instruction="")
    assert "Instruction: Improve this item." in seen["user"]
