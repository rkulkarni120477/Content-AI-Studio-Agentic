"""CDD (Course Design Document) parser and text manipulation helpers.

Contains NO Streamlit calls and NO database queries — pure text transformation.
Safe to use from any layer including future FastAPI endpoints.

Extracted from core/shared.py (Phase 3 refactoring).
"""

import json
import re as _re

from promptops_app.core.llm_client import safe_json_loads


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------

def parse_sections_from_text(text: str) -> dict:
    """Parse LLM output into {section_title: section_content} using ## headings.

    - Content before the first ## heading is discarded (no 'Preamble' bucket).
    - Empty sections are skipped.
    - 'Purpose' labels are globally renamed to 'Goal' per spec.
    """
    sections: dict = {}
    current_title = None
    current_lines: list = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current_title is not None:
                body = "\n".join(current_lines).strip()
                if body:
                    sections[current_title] = body
            current_title = line.replace("## ", "", 1).strip()
            current_lines = []
        else:
            if current_title is not None:
                current_lines.append(line)

    if current_title is not None:
        body = "\n".join(current_lines).strip()
        if body:
            sections[current_title] = body

    # Rename 'Purpose' → 'Goal' in titles and content
    cleaned: dict = {}
    for title, content in sections.items():
        new_title = (
            title.replace("Purpose", "Goal")
            if title.strip() in ("Purpose", "Module Purpose")
            else title
        )
        cleaned[new_title] = content.replace("Purpose:", "Goal:")
    return cleaned


def parse_cdd_flat(raw_text: str) -> dict:
    """Parse the flat CDD output schema into logical blocks.

    Recognised sections:
      - Course Details
      - Course Structure
      - Course Level Assessment
      - _validation  (hidden, preserved in DB)
      - _raw         (always stored — full text)

    The schema uses plain-text headings like 'Course Details:' rather than
    ## markdown headers.
    """
    result = {
        "_raw":                    raw_text,
        "Course Details":          "",
        "Course Structure":        "",
        "Course Level Assessment": "",
        "_validation":             "",
    }

    text = raw_text.strip()

    _MARKERS = [
        ("Course Details",          "Course Details"),
        ("Course Structure",        "Course Structure"),
        ("Course Level Assessment", "Course Level Assessment"),
        ("Validation",              "_validation"),
    ]

    _marker_pattern = _re.compile(
        r'(?:^|\n)\s*(?:##\s*)?'
        r'(Course Details|Course Structure|Course Level Assessment|Validation)'
        r'\s*:?\s*\n',
        _re.IGNORECASE,
    )

    positions = []
    for m in _marker_pattern.finditer(text):
        label_found = m.group(1).strip()
        for display, key in _MARKERS:
            if label_found.lower() == display.lower():
                positions.append((m.start(), m.end(), key))
                break

    if not positions:
        result["Course Structure"] = text
        return result

    for i, (start, end, key) in enumerate(positions):
        next_start = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        content = text[end:next_start].strip()
        result[key] = content.replace("Purpose:", "Goal:")

    return result


# ---------------------------------------------------------------------------
# UI filtering helpers
# ---------------------------------------------------------------------------

_CDD_UI_HIDDEN_PATTERNS = [
    r"(?i)progression\s+logic",
    r"(?i)step\s+\d+",
]

_UI_STRIP_PATTERNS = [
    r'(?im)^[•\-\*]?\s*\*{0,2}validation\s+complete\.?\*{0,2}\s*$',
    r'(?im)^[•\-\*]?\s*✅\s*validation\s+complete\.?\s*$',
    r'(?im)^\s*\*{0,2}✅\s*validation\s+complete\*{0,2}.*$',
    r'(?im)^.*\bvalidation\s+complete\b.*$',
    r'(?im)^[•\-\*]?\s*\*{0,2}blueprint\s+(is\s+)?(complete|ready|validated)\.?\*{0,2}\s*$',
    r'(?im)^[•\-\*]?\s*✅\s*blueprint\s+(is\s+)?(complete|ready|validated)\.?\s*$',
    r'(?im)^.*\bblueprint\s+(is\s+)?(complete|ready|validated)\b.*$',
    r'(?im)^.*blueprint\s+is\s+ready\s+for\s+(lesson|content)\s+generation.*$',
    r'(?im)^#+\s*step\s+[56]\s*[:\-—]?\s*(blueprint\s+)?validation.*$',
    r'(?im)^#+\s*(blueprint\s+)?validation\s*(check|complete|summary|confirmed)?.*$',
]


def _strip_ui_hidden_text(text: str) -> str:
    """Strip backend-only phrases (Validation Complete, Blueprint Complete, etc.)
    before rendering or downloading content."""
    result = text
    for pattern in _UI_STRIP_PATTERNS:
        result = _re.sub(pattern, "", result)
    result = _re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


# ---------------------------------------------------------------------------
# Content reconstruction
# ---------------------------------------------------------------------------

def _rebuild_cdd_full_content(parsed: dict) -> str:
    """Reconstruct full CDD text from parsed blocks (preserves hidden fields)."""
    parts = []
    for key in ["Course Details", "Course Structure", "Course Level Assessment", "_validation"]:
        val = parsed.get(key, "").strip()
        if val:
            if key == "_validation":
                parts.append(f"## Validation\n{val}")
            else:
                parts.append(f"## {key}\n{val}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Summary extraction (used by context injection)
# ---------------------------------------------------------------------------

def extract_cdd_summary(cdd_version, max_chars: int = 3000) -> str:
    """Extract a concise summary from a CDDVersion for context injection."""
    if not cdd_version:
        return "No CDD available."
    sections = safe_json_loads(cdd_version.sections) if cdd_version.sections else {}
    if not sections:
        return (cdd_version.full_content or "")[:max_chars]

    priority_keys = [
        "Learning Objectives",
        "Tone & Style Guidelines",
        "Key Concepts & Terminology",
        "Instructional Strategies",
        "Quality Standards & Constraints",
        "Target Learner Profile",
    ]
    summary_parts = []
    for key in priority_keys:
        for section_key, section_val in sections.items():
            if key.lower() in section_key.lower():
                summary_parts.append(f"**{section_key}:**\n{section_val}")
                break

    result = "\n\n".join(summary_parts)
    return result[:max_chars] if result else (cdd_version.full_content or "")[:max_chars]


def extract_blueprint_summary(bp_version, max_chars: int = 3000) -> str:
    """Extract a concise summary from a BlueprintVersion for context injection."""
    if not bp_version:
        return "No Blueprint available."
    sections = safe_json_loads(bp_version.sections) if bp_version.sections else {}
    if not sections:
        return (bp_version.full_content or "")[:max_chars]

    priority_keys = [
        "Learning Objectives",
        "Lesson Plan",
        "Key Concepts",
        "Tone & Approach",
        "Content Constraints",
    ]
    summary_parts = []
    for key in priority_keys:
        for section_key, section_val in sections.items():
            if key.lower() in section_key.lower():
                summary_parts.append(f"**{section_key}:**\n{section_val}")
                break

    result = "\n\n".join(summary_parts)
    return result[:max_chars] if result else (bp_version.full_content or "")[:max_chars]
