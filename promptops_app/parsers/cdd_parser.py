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
        r'(?:^|\n)\s*#*\s*'
        r'(Course Details|Course Structure|Course[\s-]+Level Assessment|Validation)'
        r'\s*:?\s*\n',
        _re.IGNORECASE,
    )

    positions = []
    for m in _marker_pattern.finditer(text):
        label_found = _re.sub(r'[\s-]+', ' ', m.group(1).strip())
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
# DLU (worksheet-based) CDD helpers
#
# A DLU CDD stores everything under "Course Structure" as a title/metadata
# header followed by several worksheet blocks. These helpers split that blob so
# the XLSX export can emit one sheet per worksheet. Standard CDDs have no
# worksheet labels, so is_dlu_cdd() returns False.
#
# Boundary detection is deliberately decoration-blind. The output format is
# dictated by whichever prompt generated the CDD, not by CAS, and prompts get
# rewritten — the same prompt has emitted "## WORKSHEET 1: X" one day and
# "**Worksheet 1: X**" the next. Matching only markdown headings silently
# demoted those CDDs to a flat blob (and a single-sheet XLSX), so we strip the
# decoration and match the label itself.
#
# Mirrored in frontend/src/utils/cddWorksheets.js — the two must agree or the
# screen and the export disagree about where worksheets start. Shared fixtures
# live in tests/characterization/test_dlu_cdd_export.py and
# frontend/src/utils/__tests__/cddWorksheets.test.js.
# ---------------------------------------------------------------------------

# Leading noise: blockquote markers, list bullets, and markdown heading hashes.
# Group 1 captures the hashes so a real heading can be told from a bare label.
_WS_LEAD_RE = _re.compile(r"^[\s>]*(?:[-*+]\s+)?(?:(#{1,6})\s*)?")
_WS_LABEL_RE = _re.compile(
    r"^(?:worksheet|work\s*sheet|sheet|ws)\s*#?\s*(\d{1,2})\b\s*[:\-–—.]?\s*(.*)$",
    _re.IGNORECASE,
)
# A boundary label is a short line. Anything longer is prose that merely
# mentions a worksheet ("...as recorded in Worksheet 2 of the prior block").
_WS_MAX_LABEL_LEN = 120


def _worksheet_mark(line: str) -> dict | None:
    """Parse a worksheet boundary label out of *line*, ignoring decoration.

    Accepts every form the models have actually produced: ``## WORKSHEET 1: X``,
    ``### Worksheet 1: X``, ``**Worksheet 1: X**``, ``Worksheet 1 — X``,
    ``- **Sheet 1: X**``. Returns None for anything else.
    """
    lead = _WS_LEAD_RE.match(line)
    is_heading = bool(lead.group(1))
    text = line[lead.end():].strip()
    # Emphasis wrappers, then a trailing colon left behind by "**Worksheet 1:**".
    text = _re.sub(r"^(\*{1,2}|_{1,2})", "", text)
    text = _re.sub(r"(\*{1,2}|_{1,2})\s*:?\s*$", "", text).strip()
    text = text.rstrip(":").strip()
    if not text or len(text) > _WS_MAX_LABEL_LEN:
        return None
    match = _WS_LABEL_RE.match(text)
    if not match:
        return None
    return {
        "num": int(match.group(1)),
        "title": match.group(2).strip(),
        "label": text,
        "is_heading": is_heading,
    }


def _dedupe_worksheet_marks(marks: list, text_len: int) -> list:
    """Keep one mark per worksheet number — whichever has the most content.

    A model that prints a table of contents ("- **Worksheet 1: ...**", one per
    line) before the real worksheets would otherwise split the document at the
    TOC entries and emit a run of near-empty worksheets. Preferring the
    occurrence with the largest body picks the real section every time.
    """
    best: dict = {}
    for i, mark in enumerate(marks):
        end = marks[i + 1]["start"] if i + 1 < len(marks) else text_len
        body = end - mark["start"]
        current = best.get(mark["num"])
        if current is None or body > current[0]:
            best[mark["num"]] = (body, mark)
    return sorted((mark for _, mark in best.values()), key=lambda m: m["start"])


def _find_worksheet_marks(text: str) -> list:
    """All worksheet boundary marks in *text*, with their line start offsets.

    Splits on "\\n" (rather than str.splitlines) so the offsets match the
    JS implementation exactly — splitlines also breaks on form feeds and the
    Unicode line separators, which would drift the two apart.
    """
    marks = []
    pos = 0
    for line in (text or "").split("\n"):
        mark = _worksheet_mark(line)
        if mark:
            marks.append({**mark, "start": pos})
        pos += len(line) + 1  # +1 for the consumed newline
    return _dedupe_worksheet_marks(marks, len(text or ""))


def _dlu_worksheet_marks(text: str) -> list:
    """Worksheet marks when *text* is a DLU CDD, else []."""
    marks = _find_worksheet_marks(text)
    if not marks:
        return []
    # A markdown-heading marker is unambiguous on its own. This is the original
    # rule, kept intact so nothing that renders as DLU today can stop doing so.
    if any(m["is_heading"] for m in marks):
        return marks
    # Bold/plain labels are weaker evidence, so require two distinct worksheet
    # numbers before reshaping the document — one stray mention is not a DLU CDD.
    if len({m["num"] for m in marks}) >= 2:
        return marks
    return []


def is_dlu_cdd(full_content: str) -> bool:
    """True when the CDD content is worksheet-based (DLU)."""
    if not full_content:
        return False
    return bool(_dlu_worksheet_marks(full_content))


_WS_ACRONYMS = {"ACS", "DLU", "FAA", "PPE", "SDS", "BOM", "IPC", "CDD"}


def _title_word(word: str) -> str:
    bare = _re.sub(r"[^A-Za-z]", "", word)
    if bare.upper() in _WS_ACRONYMS:
        return word.upper()
    return word.capitalize()


def _short_worksheet_label(num: int, full_title: str) -> str:
    """A short, human-readable label for a worksheet (used as the sheet name)."""
    short = _re.sub(r"^WORKSHEET\s+\d+\s*[:\-–—.]?\s*", "", full_title, flags=_re.IGNORECASE).strip()
    if not short:
        return f"Worksheet {num}"
    titled = " ".join(_title_word(w) for w in short.split())
    return f"{num} {titled}"


def split_cdd_worksheets(cs_text: str) -> list:
    """Split a DLU "Course Structure" blob into (label, content) sections.

    Returns an "Overview" section first (the title/metadata before Worksheet 1),
    then one section per worksheet in order. Returns [] when no worksheet
    labels are present (i.e. not a DLU CDD).

    Each section keeps its own boundary label line verbatim, so the model's
    original formatting survives a split/rebuild round trip.
    """
    text = cs_text or ""
    marks = _dlu_worksheet_marks(text)
    if not marks:
        return []

    out = []
    overview = text[:marks[0]["start"]].strip()
    if overview:
        out.append(("Overview", overview))
    for i, mark in enumerate(marks):
        end = marks[i + 1]["start"] if i + 1 < len(marks) else len(text)
        content = text[mark["start"]:end].strip()
        out.append((_short_worksheet_label(mark["num"], mark["title"]), content))
    return out


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

def _cap(text: str, max_chars: int) -> str:
    """0 (default across this module's extractors) = no cap, return unchanged."""
    return text[:max_chars] if max_chars else text


def extract_cdd_summary(cdd_version, max_chars: int = 0) -> str:
    """Extract a concise summary from a CDDVersion for context injection."""
    if not cdd_version:
        return "No CDD available."
    sections = safe_json_loads(cdd_version.sections) if cdd_version.sections else {}
    if not sections:
        return _cap(cdd_version.full_content or "", max_chars)

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
    return _cap(result, max_chars) if result else _cap(cdd_version.full_content or "", max_chars)


def extract_module_section(cdd_version, selected_module: str, max_chars: int = 0) -> str:
    """Pull the full text block for one module out of the CDD's Course Structure.

    *selected_module* is the free-text string sent by the Blueprint UI, e.g.
    "Module 2" or "Module 2 — Pharmacology Basics". Mirrors the module-splitting
    regex frontend/src/utils/blueprintModules.js uses to populate the dropdown,
    so the Blueprint LLM sees exactly the lessons/details the CDD wrote for this
    module, not just a generic course-wide summary.

    Returns "" if *selected_module* isn't a numbered module (e.g. a course-level
    assessment item) or no matching block is found.
    """
    if not cdd_version or not selected_module:
        return ""

    match = _re.search(r"module\s+(\d+)", selected_module, _re.IGNORECASE)
    if not match:
        return ""
    target_num = int(match.group(1))

    structure = parse_cdd_flat(cdd_version.full_content or "").get("Course Structure", "")
    if not structure:
        return ""

    # Tolerates the legacy "Module no.: 1" form some older CDDs produced, and
    # an optional markdown heading prefix ("### Module 1"), in addition to
    # the standard "Module 1" form.
    marker_re = _re.compile(
        r"(?:^|\n)\s*#*\s*[Mm]odule\s*(?:[Nn]o\.?\s*:?\s*)?(\d+)[:\s\-—]", _re.MULTILINE
    )
    positions = [(m.start(), int(m.group(1))) for m in marker_re.finditer(structure)]
    if not positions:
        return ""

    for i, (start, num) in enumerate(positions):
        if num != target_num:
            continue
        next_start = positions[i + 1][0] if i + 1 < len(positions) else len(structure)
        return _cap(structure[start:next_start].strip(), max_chars)

    return ""


def extract_blueprint_summary(bp_version, max_chars: int = 0) -> str:
    """Extract a concise summary from a BlueprintVersion for context injection."""
    if not bp_version:
        return "No Blueprint available."
    sections = safe_json_loads(bp_version.sections) if bp_version.sections else {}
    if not sections:
        return _cap(bp_version.full_content or "", max_chars)

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
    return _cap(result, max_chars) if result else _cap(bp_version.full_content or "", max_chars)
