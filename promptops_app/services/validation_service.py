"""Content validation service for PromptOps.

Validates generated course blocks before allowing export.
Returns a structured result dict; never raises on bad content.

Output format
-------------
{
    "passed": bool,            # True only when errors == 0
    "errors": [
        {
            "rule":     str,   # machine-readable rule ID
            "severity": "error",
            "location": str,   # e.g. "Lesson — Introduction to Python"
            "message":  str,   # human explanation
            "block_id": int,
        }
    ],
    "warnings": [ ... same shape ... ],
    "summary": {
        "total_checks": int,
        "passed":        int,
        "warnings":      int,
        "errors":        int,
    }
}

Each rule function signature
-----------------------------
    def _rule_xxx(blocks, **kw) -> tuple[list[dict], int]
        returns (issues, checks_performed)
"""

from __future__ import annotations

import re
from typing import Any

# ── Compiled patterns ─────────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(
    r"\b(TODO|TBD|FIXME|PLACEHOLDER|lorem\s+ipsum|ENTER_HERE"
    r"|INSERT_HERE|\[fill\s+in\]|\[add\s+here\]|\[your\s+\w+\]"
    r"|SAMPLE_TEXT|DRAFT_ONLY)\b",
    re.IGNORECASE,
)

# Markdown image: ![alt](url)
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")

# HTML tags NOT allowed inside markdown (allowlist of safe inline tags)
_ALLOWED_HTML = frozenset(
    "br em strong code pre blockquote ul ol li p a hr b i u s strike sub sup".split()
)
_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)[^>]*>", re.IGNORECASE)

# Learning objective indicators
_LO_RE = re.compile(
    r"(learning\s+objective|learning\s+goal|by\s+the\s+end\s+of\s+(this\s+)?"
    r"(lesson|module|section|unit)|student(s)?\s+will\s+(be\s+able\s+to|learn|understand)"
    r"|learner(s)?\s+will|after\s+completing\s+(this|the))",
    re.IGNORECASE,
)

# Quiz answer-key indicators
_AK_RE = re.compile(
    r"(answer\s+key|correct\s+answer|the\s+answer\s+is|answers?:\s*[a-d\d]"
    r"|\bcorrect:\s*[a-d]|answer\s+choice\s*[a-d])",
    re.IGNORECASE,
)

# Teacher notes indicators
_TN_RE = re.compile(
    r"(teacher\s+note|instructor\s+note|facilitator\s+note"
    r"|teaching\s+tip|note\s+to\s+(teacher|instructor)|instructor\s+only)",
    re.IGNORECASE,
)

# Question detector for assessments — requires ≥2 word chars after the dot so
# answer-key numbering like "1. b" does not false-positive as a question.
_QUESTION_RE = re.compile(r"(\?\s*$|\bQ\s*\d+[\.:)]|\d+[\.:]\s+\w{2,})", re.MULTILINE)

# ── Block type helpers ────────────────────────────────────────────────────────

_LESSON_KEYWORDS    = frozenset({"lesson", "module", "unit", "section", "topic"})
_QUIZ_KEYWORDS      = frozenset({"quiz", "assessment", "test", "exam", "knowledge check",
                                  "knowledge_check", "check"})
_ASSIGNMENT_KEYWORDS = frozenset({"assignment", "activity", "exercise", "project", "practice"})


def _type(block: Any) -> str:
    return (block.block_type or "").lower().strip()


def _is_lesson(block: Any) -> bool:
    t = _type(block)
    return any(kw in t for kw in _LESSON_KEYWORDS)


def _is_quiz(block: Any) -> bool:
    t = _type(block)
    return any(kw in t for kw in _QUIZ_KEYWORDS)


def _loc(block: Any) -> str:
    """Return a human-readable location string for error messages."""
    label = (block.block_label or "").strip()
    btype = (block.block_type or "Block").title()
    short = (label[:55] + "…") if len(label) > 55 else label
    display = f"{btype} — {short}" if short else f"{btype} #{block.id}"
    return display


def _issue(rule: str, severity: str, block: Any, message: str) -> dict:
    return {
        "rule":     rule,
        "severity": severity,
        "block_id": block.id,
        "location": _loc(block),
        "message":  message,
    }


# ── Rule implementations ──────────────────────────────────────────────────────

def _rule_empty_lesson_title(blocks, **_) -> tuple[list, int]:
    """Lesson / module blocks must have a non-empty title."""
    issues, count = [], 0
    for b in blocks:
        if not _is_lesson(b):
            continue
        count += 1
        if not (b.block_label or "").strip():
            issues.append(_issue(
                "empty_lesson_title", "error", b,
                "Lesson has no title. Every lesson must have a non-empty title.",
            ))
    return issues, count


def _rule_empty_lesson_content(blocks, **_) -> tuple[list, int]:
    """Lesson blocks must have meaningful content."""
    issues, count = [], 0
    for b in blocks:
        if not _is_lesson(b):
            continue
        count += 1
        content = (b.content or "").strip()
        word_count = len(content.split())
        if not content:
            issues.append(_issue(
                "empty_lesson_content", "error", b,
                "Lesson has no content.",
            ))
        elif word_count < 30:
            issues.append(_issue(
                "empty_lesson_content", "warning", b,
                f"Lesson content is very short ({word_count} words). Consider expanding.",
            ))
    return issues, count


def _rule_learning_objectives(blocks, **_) -> tuple[list, int]:
    """Every lesson/module should declare learning objectives."""
    issues, count = [], 0
    for b in blocks:
        if not _is_lesson(b):
            continue
        count += 1
        if not _LO_RE.search(b.content or ""):
            issues.append(_issue(
                "learning_objectives", "warning", b,
                "No learning objectives found. "
                "Add a 'Learning Objectives' or 'By the end of this lesson…' section.",
            ))
    return issues, count


def _rule_quiz_answer_key(blocks, **_) -> tuple[list, int]:
    """Every quiz/assessment must contain an answer key."""
    issues, count = [], 0
    for b in blocks:
        if not _is_quiz(b):
            continue
        count += 1
        content = (b.content or "").strip()
        if not content:
            issues.append(_issue(
                "quiz_answer_key", "error", b,
                "Quiz has no content at all.",
            ))
        elif not _AK_RE.search(content):
            issues.append(_issue(
                "quiz_answer_key", "error", b,
                "Quiz is missing an answer key. "
                "Add an 'Answer Key' or 'Correct Answer' section.",
            ))
    return issues, count


def _rule_empty_assessment(blocks, **_) -> tuple[list, int]:
    """Quiz/assessment blocks must contain actual questions."""
    issues, count = [], 0
    for b in blocks:
        if not _is_quiz(b):
            continue
        count += 1
        content = (b.content or "").strip()
        if not content:
            continue  # already flagged by quiz_answer_key
        if not _QUESTION_RE.search(content):
            issues.append(_issue(
                "empty_assessment", "warning", b,
                "Assessment has no detectable questions "
                "(no '?' or numbered items found).",
            ))
    return issues, count


def _rule_alt_text_length(blocks, **_) -> tuple[list, int]:
    """Alt text on images must be ≤ 120 characters."""
    issues, count = [], 0
    for b in blocks:
        content = b.content or ""
        imgs = _IMG_RE.findall(content)
        for alt, _url in imgs:
            count += 1
            if len(alt) > 120:
                issues.append(_issue(
                    "alt_text_length", "error", b,
                    f"Alt text is {len(alt)} chars (max 120): "
                    f"'{alt[:60]}{'…' if len(alt) > 60 else ''}'",
                ))
    if count == 0 and blocks:
        count = 1  # at least 1 check (no images = trivially passes)
    return issues, count


def _rule_duplicate_labels(blocks, **_) -> tuple[list, int]:
    """Block labels should be unique within the export set."""
    seen: dict[str, int] = {}
    issues = []
    for b in blocks:
        label = (b.block_label or "").strip().lower()
        if not label:
            continue
        if label in seen:
            issues.append(_issue(
                "duplicate_labels", "warning", b,
                f"Duplicate title '{b.block_label}' — "
                f"same as Block #{seen[label]}. Use unique titles.",
            ))
        else:
            seen[label] = b.id
    return issues, max(len(blocks), 1)


def _rule_placeholder_text(blocks, **_) -> tuple[list, int]:
    """Content must not contain placeholder text (TODO, TBD, lorem ipsum, etc.)."""
    issues, count = [], 0
    for b in blocks:
        count += 1
        text = (b.content or "") + " " + (b.block_label or "")
        hits = _PLACEHOLDER_RE.findall(text)
        if hits:
            unique = list(dict.fromkeys(h.upper() for h in hits))[:5]
            issues.append(_issue(
                "placeholder_text", "error", b,
                f"Placeholder text found: {', '.join(unique)}. Replace with real content.",
            ))
    return issues, count


def _rule_image_references(blocks, **_) -> tuple[list, int]:
    """Image tags must have a non-empty URL; empty alt text is a warning."""
    issues, count = [], 0
    for b in blocks:
        content = b.content or ""
        imgs = _IMG_RE.findall(content)
        for alt, url in imgs:
            count += 1
            url = url.strip()
            alt = alt.strip()
            if not url:
                issues.append(_issue(
                    "image_references", "error", b,
                    f"Image has no URL (alt: '{alt or 'empty'}'). Fix or remove the tag.",
                ))
            elif not alt:
                issues.append(_issue(
                    "image_references", "warning", b,
                    f"Image is missing alt text (url: '{url[:50]}'). "
                    "Add descriptive alt text for accessibility.",
                ))
    if count == 0 and blocks:
        count = 1
    return issues, count


def _rule_unsupported_formatting(blocks, **_) -> tuple[list, int]:
    """Flag non-standard HTML tags embedded in markdown."""
    issues, count = [], 0
    for b in blocks:
        count += 1
        content = b.content or ""
        bad = [
            f"<{tag}>"
            for _, tag in _HTML_TAG_RE.findall(content)
            if tag.lower() not in _ALLOWED_HTML
        ]
        if bad:
            unique = list(dict.fromkeys(bad))[:5]
            issues.append(_issue(
                "unsupported_formatting", "warning", b,
                f"Non-standard HTML tags found: {', '.join(unique)}. "
                "Use Markdown formatting instead.",
            ))
    return issues, count


def _rule_teacher_notes(blocks, *, teacher_mode: bool = False, **_) -> tuple[list, int]:
    """When teacher mode is enabled every lesson must contain teacher notes."""
    if not teacher_mode:
        return [], 0
    issues, count = [], 0
    for b in blocks:
        if not _is_lesson(b):
            continue
        count += 1
        if not _TN_RE.search(b.content or ""):
            issues.append(_issue(
                "teacher_notes", "warning", b,
                "Teacher notes missing (teacher mode is on). "
                "Add an 'Instructor Note' or 'Teacher Notes' section.",
            ))
    return issues, count


# ── Rule registry ─────────────────────────────────────────────────────────────

_RULES = [
    _rule_empty_lesson_title,
    _rule_empty_lesson_content,
    _rule_learning_objectives,
    _rule_quiz_answer_key,
    _rule_empty_assessment,
    _rule_alt_text_length,
    _rule_duplicate_labels,
    _rule_placeholder_text,
    _rule_image_references,
    _rule_unsupported_formatting,
    _rule_teacher_notes,
]


# ── Public API ────────────────────────────────────────────────────────────────

def validate_blocks(blocks: list, *, teacher_mode: bool = False) -> dict:
    """Run all validation rules against *blocks*.

    Parameters
    ----------
    blocks:
        List of Block ORM objects (needs .id, .block_type, .block_label,
        .content attributes).  Safe to call with an empty list.
    teacher_mode:
        When True the teacher-notes rule is also enforced.

    Returns
    -------
    dict matching the output format described at the module top.
    """
    all_issues: list[dict] = []
    total_checks = 0

    for rule_fn in _RULES:
        issues, checks = rule_fn(blocks, teacher_mode=teacher_mode)
        all_issues.extend(issues)
        total_checks += checks

    errors   = [i for i in all_issues if i["severity"] == "error"]
    warnings = [i for i in all_issues if i["severity"] == "warning"]
    passed   = max(total_checks - len(errors) - len(warnings), 0)

    return {
        "passed":   len(errors) == 0,
        "errors":   errors,
        "warnings": warnings,
        "summary":  {
            "total_checks": total_checks,
            "passed":        passed,
            "warnings":      len(warnings),
            "errors":        len(errors),
        },
    }
