"""G3 (AIM_PIPELINE_REPAIR_WORKFLOW.txt, step 7): the summariser can drop five
of six DLU parts.

extract_cdd_summary / extract_blueprint_summary (cdd_parser.py) select
sections by a hardcoded priority-key list (Learning Objectives, Key Concepts,
...). A DLU day Outline's parts (Overview, Today's Mission, Learn It, Quick
Check, Up Next in Class, Day Reflection) match none of them today -- so no key
matches, summary_parts stays empty, and the all-or-nothing fallback returns
the FULL content. Correct by coincidence: the match is a plain substring test,
so a DLU document whose ONE section happens to contain a matching phrase (an
appendix titled "Key Concepts", say) would have the summariser return that
section ALONE, silently dropping the other five.

Fixed by routing documents blueprint_parser.is_dlu_blueprint recognises around
the priority-key matching entirely -- reusing the existing shape detector
(also used by the frontend's dluBlueprint.js and the Outline import path)
rather than a second, driftable copy of the part names.
"""

from __future__ import annotations

import json

DLU_OUTLINE_ALL_SIX_PARTS = (
    "### DLU Outline\n\n"
    "## Overview\n\nDay 4 covers exploded views.\n\n"
    "1. **Today's Mission**\nIdentify component relationships.\n\n"
    "2. **Learn It**\nExploded view conventions.\n\n"
    "3. **Quick Check**\nName three conventions.\n\n"
    "4. **Up Next in Class**\nAssembly diagrams.\n\n"
    "5. **Day Reflection**\nWhat surprised you?\n"
)

# The trap: a DLU document whose one JSON-parsed "section" happens to contain a
# phrase from the STANDARD priority-key list. Before the fix, this alone
# returned -- the other five parts, present in full_content, never reached it.
DLU_SECTIONS_WITH_A_TRAP = {
    "Appendix — Key Concepts Glossary": "Exploded view: a diagram spreading apart assembly components.",
}


class _FakeVersion:
    """Minimal stand-in for CDDVersion/BlueprintVersion -- only the attributes
    extract_cdd_summary/extract_blueprint_summary and is_dlu_blueprint read."""

    def __init__(self, full_content, sections=None):
        self.full_content = full_content
        self.sections = json.dumps(sections) if sections is not None else None


class TestExtractCddSummarySkipsPriorityKeysForDluDocuments:
    def test_all_six_parts_survive_with_no_sections_at_all(self):
        """The already-correct case (no sections -> full content) must keep
        working -- this is the regression guard."""
        from promptops_app.parsers.cdd_parser import extract_cdd_summary

        ver = _FakeVersion(DLU_OUTLINE_ALL_SIX_PARTS)
        result = extract_cdd_summary(ver)
        for part in ("Today's Mission", "Learn It", "Quick Check",
                     "Up Next in Class", "Day Reflection"):
            assert part in result

    def test_all_six_parts_survive_even_when_one_section_matches_a_priority_key(self):
        """The actual bug: a DLU document with a JSON `sections` blob whose one
        entry matches a priority key used to return ONLY that entry."""
        from promptops_app.parsers.cdd_parser import extract_cdd_summary

        ver = _FakeVersion(DLU_OUTLINE_ALL_SIX_PARTS, sections=DLU_SECTIONS_WITH_A_TRAP)
        result = extract_cdd_summary(ver)
        for part in ("Today's Mission", "Learn It", "Quick Check",
                     "Up Next in Class", "Day Reflection"):
            assert part in result, f"{part!r} was dropped -- summariser took the trap section alone"

    def test_a_standard_cdd_is_unaffected(self):
        """Non-DLU regression guard: a normal CDD with a real priority-key
        section must still summarise exactly as before."""
        from promptops_app.parsers.cdd_parser import extract_cdd_summary

        ver = _FakeVersion(
            "## Learning Objectives\nStudents will identify components.\n\n"
            "## Some Other Section\nIrrelevant filler text.\n",
            sections={
                "Learning Objectives": "Students will identify components.",
                "Some Other Section": "Irrelevant filler text.",
            },
        )
        result = extract_cdd_summary(ver)
        assert "Students will identify components." in result
        assert "Irrelevant filler text." not in result


class TestExtractBlueprintSummarySkipsPriorityKeysForDluDocuments:
    def test_all_six_parts_survive_even_when_one_section_matches_a_priority_key(self):
        from promptops_app.parsers.cdd_parser import extract_blueprint_summary

        ver = _FakeVersion(DLU_OUTLINE_ALL_SIX_PARTS, sections=DLU_SECTIONS_WITH_A_TRAP)
        result = extract_blueprint_summary(ver)
        for part in ("Today's Mission", "Learn It", "Quick Check",
                     "Up Next in Class", "Day Reflection"):
            assert part in result, f"{part!r} was dropped -- summariser took the trap section alone"

    def test_a_standard_blueprint_is_unaffected(self):
        from promptops_app.parsers.cdd_parser import extract_blueprint_summary

        ver = _FakeVersion(
            "## Learning Objectives\nStudents will identify components.\n\n"
            "## Some Other Section\nIrrelevant filler text.\n",
            sections={
                "Learning Objectives": "Students will identify components.",
                "Some Other Section": "Irrelevant filler text.",
            },
        )
        result = extract_blueprint_summary(ver)
        assert "Students will identify components." in result
        assert "Irrelevant filler text." not in result
