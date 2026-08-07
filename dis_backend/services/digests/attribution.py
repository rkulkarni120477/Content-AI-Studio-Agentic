"""Day-attribution for block content units (ENUMERATE owns attribution).

The live ingestion pipeline only gives a unit a ``day_number`` when its source
FILENAME carries a ``B#D#`` token (see ``client_profiles/aim.py`` +
``content_unit_creation_agent.py``). So slide decks / ebook pages get a day, but
instructor guides, projects, quizzes, activities and study questions are NULL and
are invisible to any per-day fetch or block-wide coverage check.

This module resolves those unmapped units to days with three deterministic signals
(no LLM, no OpenSearch), in priority order:

  S1 item cross-reference  — the unit carries "Project 2-4" / "Quiz 3"; the
                             calendar's assignments_json / assessments_json say
                             which DAY references that item. Highest confidence.
  S2 day token in filename — "Day 3", "Days 1-4", "B2D3" in title / source_file_name.
  S3 topic term-overlap    — unit topic/text terms vs each calendar-day's hint
                             terms; argmax with a >=2 overlap floor (mirrors the
                             read-time heuristic in context_retrieval.py).

Proven on real Block 2 (Phase 0b): recovered 103/104 (99%) orphaned units, taking
days with only slides/pages from 18 -> 1. Pure Python, deterministic.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

# Unit types that carry teachable substance and are worth attributing to a day.
# Structural/reference types (calendar_day, syllabus_section, answer_key_item,
# chunk) are handled separately by ENUMERATE.
SUBSTANTIVE = {"slide", "guide_section", "project_task", "quiz_question", "page",
               "study_question", "activity"}

_STOP = set("the a an and or of to for in on with by from is are be this that as at "
            "you your it its will can may day block project quiz page part using use "
            "student students instructor guide activity introduction".split())
_WORD = re.compile(r"[a-z0-9]+")
_PROJECT = re.compile(r"project[:\s#]*?(\d+-\d+)", re.I)
_QUIZ = re.compile(r"quiz[:\s#]*?(\d+)", re.I)
_DAY_TOKENS = [
    re.compile(r"\bB\d+D(\d+)\b", re.I),
    re.compile(r"\bdays?\s*(\d+)\s*(?:--|-|to|through|thru)\s*(\d+)\b", re.I),  # range
    re.compile(r"\bday\s*(\d+)\b", re.I),
]


def terms(text: str) -> Set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if len(w) >= 3 and w not in _STOP}


def build_calendar_refs(days: List[Dict[str, Any]]) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Map 'Project 2-1'->day, 'Quiz 1'->day from the calendar's own references.

    The calendar day rows enumerate which project/quiz is assigned/assessed on each
    day inside assignments_json / assessments_json; that is authoritative for S1.
    """
    proj_ref: Dict[str, int] = {}
    quiz_ref: Dict[str, int] = {}
    for d in days:
        dn = d.get("day_number")
        if dn is None:
            continue
        blob = " ".join(str(d.get(k) or "") for k in
                        ("assignments_json", "assessments_json", "source_text"))
        for m in _PROJECT.findall(blob):
            proj_ref.setdefault(m, dn)          # first day that references it
        for m in _QUIZ.findall(blob):
            quiz_ref.setdefault(m, dn)
    return proj_ref, quiz_ref


def day_terms_by_day(days: List[Dict[str, Any]]) -> Dict[int, Set[str]]:
    out: Dict[int, Set[str]] = {}
    for d in days:
        dn = d.get("day_number")
        if dn is None:
            continue
        out[dn] = terms(" ".join(str(d.get(k) or "") for k in
                                 ("topic", "lesson_title", "source_text")))
    return out


def parse_day_tokens(text: str, max_day: int) -> List[int]:
    for i, rx in enumerate(_DAY_TOKENS):
        m = rx.search(text or "")
        if not m:
            continue
        if i == 1:  # range "Days 1-4"
            lo, hi = int(m.group(1)), int(m.group(2))
            if 1 <= lo <= hi <= max_day:
                return list(range(lo, hi + 1))
        else:
            d = int(m.group(1))
            if 1 <= d <= max_day:
                return [d]
    return []


def attribute(unit: Dict[str, Any], days: List[Dict[str, Any]],
              proj_ref: Dict[str, int], quiz_ref: Dict[str, int],
              dterms: Dict[int, Set[str]]) -> Tuple[List[int], str, float]:
    """Resolve one unit to one or more day numbers.

    Returns ``(days, signal, confidence)``. ``days`` is empty when unresolved;
    ``signal`` names the winning rule (e.g. ``"S1:project 2-4"``), ``confidence``
    is a 0..1 heuristic score. Never raises.
    """
    md = unit.get("metadata_json") or {}
    title = str(unit.get("title") or md.get("title") or "")
    fname = str(md.get("source_file_name") or "")
    text = str(unit.get("text_content") or "")[:1500]
    day_numbers = [d["day_number"] for d in days if d.get("day_number") is not None]
    if not day_numbers:
        return [], "unresolved", 0.0
    max_day = max(day_numbers)

    # S1: item cross-reference (highest confidence)
    for m in _PROJECT.findall(f"{title} {fname} {text[:200]}"):
        if m in proj_ref:
            return [proj_ref[m]], f"S1:project {m}", 1.0
    for m in _QUIZ.findall(f"{title} {fname}"):
        if m in quiz_ref:
            return [quiz_ref[m]], f"S1:quiz {m}", 1.0

    # S2: explicit day token in title / filename
    for src in (title, fname):
        toks = parse_day_tokens(src, max_day)
        if toks:
            return toks, "S2:filename-day", 0.9

    # S3: topic term overlap, argmax with a >=2 floor
    ut = terms(f"{title} {md.get('topic', '')} {text}")
    best_day, best = None, 0
    for dn, dt in dterms.items():
        ov = len(ut & dt)
        if ov > best:
            best, best_day = ov, dn
    if best_day is not None and best >= 2:
        return [best_day], f"S3:overlap({best})", min(0.8, 0.3 + best * 0.05)

    return [], "unresolved", 0.0


_ACS_SEGMENT_RE = re.compile(r"^([A-Za-z]*)(\d*)$")


def acs_sort_key(code: str):
    """Natural sort per dot-separated ACS-code segment (e.g. 'AM.I.G.K10' vs
    'AM.I.G.K2') — a plain string sort orders K10-K19 before K2 since '1' < '2'
    character-wise. Shared by every module that lists/sorts ACS codes for
    display, so none of them can independently reintroduce this."""
    key = []
    for segment in (code or "").split("."):
        m = _ACS_SEGMENT_RE.match(segment)
        letters, digits = m.groups() if m else (segment, "")
        key.append((letters, int(digits) if digits else -1))
    return key
