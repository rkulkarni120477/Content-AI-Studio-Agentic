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
                             terms, after block-wide boilerplate terms are removed;
                             argmax accepted only when it clears an absolute floor,
                             beats the runner-up by a margin, and covers a real
                             share of the winning day's hint set.

Proven on real Block 2 (Phase 0b): recovered 103/104 (99%) orphaned units, taking
days with only slides/pages from 18 -> 1. Pure Python, deterministic.

S3 is deliberately PRECISION-biased (see the threshold block below). An unresolved
unit is surfaced in ``EnumerateResult.unattributed`` and in block coverage, so it
can be fixed by tagging; a unit attributed to the WRONG day silently becomes source
text for that day's digest and corrupts its extraction. Prefer no answer to a wrong
one here.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any, Dict, List, Set, Tuple

log = logging.getLogger(__name__)

# Unit types that carry teachable substance and are worth attributing to a day.
# Structural/reference types (calendar_day, syllabus_section, answer_key_item,
# chunk) are handled separately by ENUMERATE.
SUBSTANTIVE = {"slide", "guide_section", "project_task", "quiz_question", "page",
               "study_question", "activity"}

#: Document types that describe the BLOCK as a whole rather than any one day: a
#: syllabus, a course calendar, or a whole-document handbook/standards reference.
#: Matches the taxonomy in config/settings.py. Shared with worksheets.py so the
#: two modules can never disagree about what "block-wide reference" means.
BLOCK_WIDE_REFERENCE_DOC_TYPES = frozenset({"syllabus", "course_calendar", "ebook_reference"})

_STOP = set("the a an and or of to for in on with by from is are be this that as at "
            "you your it its will can may day block project quiz page part using use "
            "student students instructor guide activity introduction".split())
_WORD = re.compile(r"[a-z0-9]+")
_PROJECT = re.compile(r"project[:\s#]*?(\d+-\d+)", re.I)
_QUIZ = re.compile(r"quiz[:\s#]*?(\d+)", re.I)
#: A block's culminating graded exam, as the calendar names it in assessments_json
#: (e.g. "Block 2: Final Exam"). Distinct from a numbered per-day quiz.
_FINAL_EXAM = re.compile(r"\bfinal\s+exam\b", re.I)

#: Document types whose unit is the block's final exam. Such a unit has exactly one
#: correct day — the one the CALENDAR schedules the exam on — so it must never be
#: placed by term overlap, which scatters it across the block: a final exam covers
#: every day's vocabulary, so it overlaps every day's hint terms. Caught live on
#: Block 2, where the calendar puts the final exam on day 20 and S3 had placed its
#: questions on days 8 and 10.
_FINAL_EXAM_DOC_TYPES = frozenset({"final_exam", "final_exam_answer_key"})
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


#: A hint term appearing in at least this SHARE of a block's calendar days cannot
#: discriminate between them, so counting it as overlap evidence is noise that
#: scales with document length. In a real AIM block the terms filtered out are
#: exactly the per-day boilerplate the calendar repeats on every row — "acs",
#: "faa", "8083", "pgs", "reference", "reading", "hangar", "topics", "covered" —
#: plus the course's own subject words ("aircraft", "corrosion", "metals"), which
#: by definition say nothing about WHICH day a unit belongs to.
_UBIQUITY_SHARE = 0.34

#: Below this many days, "shared by most days" carries no information at all, so the
#: filter is skipped entirely rather than stripping a tiny block down to nothing.
_UBIQUITY_MIN_BLOCK_DAYS = 3

#: Floor on the document frequency that makes a term boilerplate. Separate from the
#: block-size guard above (they happen to share a value): this one keeps
#: ``_UBIQUITY_SHARE`` from rounding down to "appears in 1 day" on a short block,
#: which would delete every distinctive term and leave S3 with nothing to match on.
_UBIQUITY_MIN_DOC_FREQ = 3

# --- S3 acceptance thresholds -------------------------------------------------- #
# S3 previously accepted the argmax day on a raw overlap of >=2 with no margin and
# no normalization. On real Block 2 that pulled 22 pages of an unrelated ACS
# document onto Day 1 (~90k chars of turbine-compressor / fire-detection /
# lavatory-servicing text on an Aircraft Drawings day), because:
#   * ties resolved to the LOWEST day number via dict insertion order, making the
#     first day a silent sink for everything unresolvable — half the accepted
#     units had a margin of exactly 0 over the runner-up;
#   * boilerplate carried the overlap (winning sets were ['acs','faa','powerplant']
#     and ['acs','areas','inspection']);
#   * overlap was an absolute count, so a 4k-char page yields ~100 terms against a
#     day hint set of 12-47 and clears a floor of 2 by volume alone.
# Precision matters more than recall here: a wrongly-attributed unit poisons a
# digest's extraction, while an unattributed one is reported, not lost.
# Kept at 2 deliberately: after ubiquity filtering, a day's distinctive vocabulary can
# legitimately be only two or three terms, and a higher floor would make such a day
# unreachable by S3 at all. The MARGIN carries the real weight — with the boilerplate
# already removed, requiring the winner to beat the runner-up by 2 means the matched
# terms are effectively unique to that day, which is exactly what "belongs to this day"
# should mean.
_S3_MIN_OVERLAP = 2        # discriminative terms, after ubiquity filtering
_S3_MIN_MARGIN = 2         # the winner must actually beat the runner-up
_S3_MIN_DAY_COVERAGE = 0.12  # ... and touch a real share of what defines that day


def ubiquitous_terms(dterms: Dict[int, Set[str]]) -> Set[str]:
    """Hint terms too widely shared across a block's days to carry any signal.

    Pure function of the day-term sets so it can be asserted on directly.
    """
    if len(dterms) < _UBIQUITY_MIN_BLOCK_DAYS:
        return set()
    df = Counter(t for dt in dterms.values() for t in dt)
    cutoff = max(_UBIQUITY_MIN_DOC_FREQ, math.ceil(len(dterms) * _UBIQUITY_SHARE))
    return {t for t, c in df.items() if c >= cutoff}


def day_terms_by_day(days: List[Dict[str, Any]],
                     *, drop_ubiquitous: bool = True) -> Dict[int, Set[str]]:
    """Per-day hint terms for S3, with block-wide boilerplate removed.

    ``drop_ubiquitous=False`` returns the raw sets (diagnostics / regression
    comparison only) — attribution should always use the filtered ones.
    """
    out: Dict[int, Set[str]] = {}
    for d in days:
        dn = d.get("day_number")
        if dn is None:
            continue
        out[dn] = terms(" ".join(str(d.get(k) or "") for k in
                                 ("topic", "lesson_title", "source_text")))
    if not drop_ubiquitous:
        return out
    common = ubiquitous_terms(out)
    if common:
        log.debug("attribution: dropped %d block-ubiquitous hint terms: %s",
                  len(common), sorted(common)[:20])
        out = {dn: dt - common for dn, dt in out.items()}
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


def _doc_types(md: Dict[str, Any]) -> Set[str]:
    """Every document-type label a unit's metadata carries, lowercased.

    Reads ``doc_type`` / ``document_type`` / ``content_type`` and the ``type:`` /
    ``content_type:`` tags, because ingest vintages do not populate them together.
    """
    out: Set[str] = set()
    for key in ("doc_type", "document_type", "content_type"):
        v = str(md.get(key) or "").strip().lower()
        if v:
            out.add(v)
    for tag in (md.get("tags") or []):
        t = str(tag)
        if t.startswith(("type:", "content_type:")):
            v = t.split(":", 1)[1].strip().lower()
            if v:
                out.add(v)
    return out


def final_exam_day(days: List[Dict[str, Any]]) -> int | None:
    """The calendar day that schedules the block's final exam, or None.

    Authoritative in the same way S1's project/quiz cross-references are: the
    calendar row says what is assessed that day.
    """
    for d in days:
        dn = d.get("day_number")
        if dn is None:
            continue
        blob = " ".join(str(d.get(k) or "") for k in ("assessments_json", "assignments_json"))
        if _FINAL_EXAM.search(blob):
            return dn
    return None


def _is_block_wide_reference(md: Dict[str, Any]) -> bool:
    """Whether a unit's metadata marks it as part of a block-wide reference document.

    Checks every key the ingestion pipeline has used for this over time
    (``doc_type`` / ``document_type`` / ``content_type``) plus the ``type:`` tag,
    because they are not consistently populated together across ingest vintages.
    """
    return bool(_doc_types(md) & BLOCK_WIDE_REFERENCE_DOC_TYPES)


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

    # S1: item cross-reference (highest confidence). The block's final exam is
    # resolved the same calendar-authoritative way, and — unlike a project or quiz
    # token — is checked FIRST because a final exam's own text is full of numbered
    # project/quiz references from across the block that would otherwise win below.
    if _doc_types(md) & _FINAL_EXAM_DOC_TYPES:
        fx = final_exam_day(days)
        if fx is not None:
            return [fx], "S1:final-exam", 1.0
        return [], "unresolved:final-exam-day-unknown", 0.0

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

    # S3 is not applicable to a page of a block-wide reference. One page of a
    # 90-page certification-standards PDF has no day: sharing vocabulary with a
    # day's topic is not evidence of belonging to it, only evidence that both are
    # about the same trade. S1/S2 above still apply — an explicit "Day 3" or
    # "Project 2-1" token in such a document IS real evidence — so this only
    # withholds the purely statistical signal. Caught live on Block 2: pages of
    # ACS-1.pdf (fire detection, fuel sumps, lavatory servicing) were landing on
    # an Aircraft Drawings day purely on shared aviation vocabulary.
    if _is_block_wide_reference(md):
        return [], "unresolved:block-wide-reference", 0.0

    # S3: topic term overlap — argmax over days, accepted ONLY when the winner is
    # discriminative (see the thresholds' rationale above). Everything else is
    # returned UNRESOLVED on purpose: an unresolved unit is surfaced in
    # EnumerateResult.unattributed and in coverage, whereas a wrongly-attributed
    # one silently becomes source text for the wrong day's digest.
    ut = terms(f"{title} {md.get('topic', '')} {text}")
    ranked = sorted(((len(ut & dt), dn) for dn, dt in dterms.items() if dt),
                    key=lambda p: (-p[0], p[1]))   # explicit, not dict-order
    if not ranked:
        return [], "unresolved", 0.0
    best, best_day = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0
    coverage = best / (len(dterms[best_day]) or 1)
    if (best >= _S3_MIN_OVERLAP
            and best - runner_up >= _S3_MIN_MARGIN
            and coverage >= _S3_MIN_DAY_COVERAGE):
        return ([best_day], f"S3:overlap({best}/{runner_up})",
                min(0.8, 0.3 + best * 0.05))
    if best:
        # Distinguishable in telemetry from "no overlap at all" — a rising
        # S3-weak count is the signal that a block's calendar hints have become
        # too generic to attribute against.
        return [], f"unresolved:S3-weak({best}/{runner_up})", 0.0
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
