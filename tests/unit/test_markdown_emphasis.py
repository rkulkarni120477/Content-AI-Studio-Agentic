"""Unbalanced markdown emphasis: detect it, repair only what is unambiguous.

Every string in ``_REAL_CORRUPTION`` was read out of this deployment's own
database, one shape per stored item type. They share one root cause — an
item-regeneration bullet strip that matched a zero-width gap after the bullet
character and so removed the opening ``*`` of ``**Label:**`` — and each one
renders as a stray asterisk beside an unbolded label for every reader.

The damage is sticky, which is why detection matters as much as the repair: a
save preserves untouched lines byte for byte, so one malformed label rides
forward into every later version until somebody edits that exact line by hand.
Four consecutive versions of the same CDD carried the identical broken line.
"""

import pytest

from promptops_app.parsers.markdown_emphasis import (
    Unbalanced,
    find_unbalanced,
    repair_emphasis,
)

#: (stored text, the text it should have been) — measured, not invented.
_REAL_CORRUPTION = [
    # cdd_versions 192/194/195/196 — a bullet item. The line the user reported.
    ("- *Supplemental References:** Essential Texts: Federal Aviation Administration",
     "- **Supplemental References:** Essential Texts: Federal Aviation Administration"),
    # cdd_versions 189 — same shape, different label.
    ("- *Title:** Agile Project Simulation Experience",
     "- **Title:** Agile Project Simulation Experience"),
    # cdd_versions 16 — a unicode bullet.
    ("• *Lesson 1.1: Foundations of Patient-Centered Care**",
     "• **Lesson 1.1: Foundations of Patient-Centered Care**"),
    # cdd_versions 167 — a PARAGRAPH item, so no prefix was re-added.
    ("*Course Goal:** Equip students with a thorough understanding of marketing",
     "**Course Goal:** Equip students with a thorough understanding of marketing"),
    # blocks 265 — a HEADING item, prefix re-added after the strip.
    ("## *Description:** This lecture provides a comprehensive recap",
     "## **Description:** This lecture provides a comprehensive recap"),
    # block_versions 561/564/565, blocks 47 — a NUMBERED item.
    ("2. *Objective 2: Implement the principles of [Module Topic] effectively**",
     "2. **Objective 2: Implement the principles of [Module Topic] effectively**"),
    # blueprint_versions 16 — the same defect reached blueprints too.
    ("- *Exploring Careers That Keep Our Communities Safe**",
     "- **Exploring Careers That Keep Our Communities Safe**"),
]


@pytest.mark.parametrize("stored,expected", _REAL_CORRUPTION)
def test_every_stored_corruption_shape_is_repaired(stored, expected):
    fixed, findings = repair_emphasis(stored)
    assert fixed == expected
    assert [f.repairable for f in findings] == [True]


@pytest.mark.parametrize("stored,_expected", _REAL_CORRUPTION)
def test_repair_is_idempotent(stored, _expected):
    """Running the guard twice must not add a third asterisk."""
    once, _ = repair_emphasis(stored)
    twice, findings = repair_emphasis(once)
    assert twice == once
    assert findings == []


# ---------------------------------------------------------------------------
# What must NOT be touched
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "- **Supplemental References:** Essential Texts: FAA",   # already correct
    "*italic* leads this line",                              # a healthy italic run
    "- *italic* inside a bullet",
    "plain prose with no emphasis at all",
    "- **Bold:** and *italic* on one line",
    "| Day | **Focus** |",                                   # a table row
    "",
])
def test_healthy_markdown_is_returned_byte_for_byte(text):
    fixed, findings = repair_emphasis(text)
    assert fixed == text
    assert findings == []


def test_an_ambiguous_malformation_is_reported_but_not_guessed_at():
    """block_versions 150/337/709 — the MODEL's own mangling, not the strip's.

    ``*Self-esteem**needs*`` was meant to be ``*Self-esteem needs*``. Recovering
    that means guessing where a space belongs, so the line is reported and left
    alone: a visible defect beats an invisible rewrite.
    """
    line = ("The next level of needs focus on *esteem needs*. *Self-esteem**needs* "
            "include self-respect and a sense of accomplishment.")
    fixed, findings = repair_emphasis(line)
    assert fixed == line
    assert len(findings) == 1
    assert findings[0].repairable is False
    assert "ambiguous" in findings[0].describe()


def test_an_italic_opener_before_an_unclosed_bold_is_not_doubled():
    """The opener is healthy; the missing character is somewhere else entirely.

    Distinguished by how the first delimiter closes — a single ``*`` means an
    italic run, so doubling it would corrupt a line that was merely incomplete.
    """
    line = "*italic* text with **bold that never closes"
    fixed, findings = repair_emphasis(line)
    assert fixed == line
    assert [f.repairable for f in findings] == [False]


def test_a_bullet_whose_marker_is_an_asterisk_is_still_a_bullet():
    """``*`` is a bullet only with real whitespace after it — the exact
    distinction whose absence caused the corruption in the first place."""
    fixed, _ = repair_emphasis("* *Label:** value")
    assert fixed == "* **Label:** value"


# ---------------------------------------------------------------------------
# Multi-line behaviour
# ---------------------------------------------------------------------------

def test_only_the_broken_line_changes():
    text = ("- **Primary Handbooks:** FAA-H-8083-30B\n"
            "- *Supplemental References:** Essential Texts: FAA\n"
            "- **Block:** Block 2\n")
    fixed, findings = repair_emphasis(text)
    lines = fixed.split("\n")
    assert lines[0] == "- **Primary Handbooks:** FAA-H-8083-30B"
    assert lines[1] == "- **Supplemental References:** Essential Texts: FAA"
    assert lines[2] == "- **Block:** Block 2"
    assert [f.line_number for f in findings] == [2]


def test_line_numbers_are_one_based_so_a_message_is_actionable():
    _, findings = repair_emphasis("ok\nok\n- *Broken:** here")
    assert findings[0].line_number == 3


def test_find_unbalanced_reports_without_changing_anything():
    text = "- *Broken:** here\n- **Fine:** here"
    found = find_unbalanced(text)
    assert [(f.line_number, f.repairable) for f in found] == [(1, True)]
    assert isinstance(found[0], Unbalanced)


def test_bold_cannot_span_lines_so_a_split_pair_is_two_findings():
    """Markdown closes emphasis within a line. Text opened on one line and
    closed on the next is broken twice over, and both halves are reported."""
    found = find_unbalanced("**opened here\nand closed there**")
    assert [f.line_number for f in found] == [1, 2]


# ---------------------------------------------------------------------------
# The guard is wired into the path that produced the corruption
# ---------------------------------------------------------------------------

def test_item_regeneration_repairs_a_malformed_reply(monkeypatch, caplog):
    """A model reply with an unbalanced label must not reach the splice.

    ``_strip_leading_bullet`` no longer eats delimiters, so this covers the OTHER
    producer: the model itself returning a malformed label.
    """
    import logging

    import promptops_app.parsers.blueprint_parser as bp

    monkeypatch.setattr(bp, "call_llm",
                        lambda m, s, u, c=None: "*Supplemental References:** Essential Texts: FAA")
    with caplog.at_level(logging.WARNING):
        out = bp.regen_single_item(
            section_title="WORKSHEET 1: BLOCK OVERVIEW",
            section_content="- **Supplemental References:** old",
            item_index=0,
            item_text="**Supplemental References:** old",
            custom_instruction="refresh the references",
        )

    assert out == "**Supplemental References:** Essential Texts: FAA"
    assert any("unbalanced markdown emphasis" in r.getMessage() for r in caplog.records)


def test_item_regeneration_reports_a_reply_it_cannot_repair(monkeypatch, caplog):
    """Unrepairable is still reported — a silent best effort is what let the
    original defect run undetected for as long as it did."""
    import logging

    import promptops_app.parsers.blueprint_parser as bp

    reply = "Programming enables *electronic**health* records for doctors"
    monkeypatch.setattr(bp, "call_llm", lambda m, s, u, c=None: reply)
    with caplog.at_level(logging.WARNING):
        out = bp.regen_single_item(section_title="S", section_content="- a",
                                   item_index=0, item_text="a", custom_instruction="x")

    assert out == reply          # unchanged: no guess was made
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "unbalanced markdown emphasis" in messages
    assert "ambiguous" in messages


def test_a_healthy_reply_is_not_logged_about(monkeypatch, caplog):
    """No warning on the happy path, or the signal is worthless."""
    import logging

    import promptops_app.parsers.blueprint_parser as bp

    monkeypatch.setattr(bp, "call_llm", lambda m, s, u, c=None: "**Fine:** value")
    with caplog.at_level(logging.WARNING):
        out = bp.regen_single_item(section_title="S", section_content="- a",
                                   item_index=0, item_text="a", custom_instruction="x")
    assert out == "**Fine:** value"
    assert not [r for r in caplog.records if "unbalanced" in r.getMessage()]


def test_the_streamlit_era_copy_no_longer_carries_the_zero_width_pattern():
    """promptops_app/core/shared.py holds a second, older copy of
    regen_single_item whose body cannot run — the re-export at the end of that
    module rebinds the name to the parser's version, and the module is not
    importable at all without Streamlit. It kept the exact regex that caused the
    corruption, which an extraction from this file would have carried back out.
    Asserted on the source text, since the module cannot be imported here.
    """
    from pathlib import Path

    src = Path("promptops_app/core/shared.py").read_text(encoding="utf-8")
    body = src.split("def regen_single_item(", 1)[1]
    assert r'sub(r"^[-*•]\s*"' not in body, (
        "the zero-width bullet strip is back in shared.py — it reads the first "
        "'*' of '**Label:**' as a bullet"
    )
    assert "_strip_leading_bullet(" in body
    assert "repair_emphasis(" in body
