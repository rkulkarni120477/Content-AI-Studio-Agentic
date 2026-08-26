"""AIM Teacher Calendar parser — column-aware and merged-cell-aware.

The generic ``extract_calendar_structure`` treats every non-empty spreadsheet row
as a day, so it swallows the repeating header rows, blank/separator rows, and the
vertically-merged spillover rows of an AIM 'Block N Teacher Calendar', and it does
not understand the ACS-code / quiz / project / handbook columns.

This module reads the workbook directly (so merged ranges are visible) and emits
one rich record per teaching day, in the same ``days[]`` shape the pipeline already
consumes (``services.agents.content_unit_creation_agent``), plus structured fields
(``acs_codes``, ``handbook_refs``, ``quiz``, ``projects`` ...) used for tagging and
a clean natural-language ``source_text`` used for embedding.

Entry points:
    looks_like_aim_teacher_calendar(content) -> bool   # self-gating format check
    build_calendar_structure(content, filename, block_hint=None) -> dict

Storage scope
-------------
The ``days[]`` this returns becomes one ``calendar_day`` content unit each
(``content_unit_creation_agent``), which ingestion then writes to BOTH configured
stores for the tenant: the vector store (OpenSearch, for semantic + tag search) and
the structure store (RDS, the relational system-of-record). Both are populated at
ingestion; as of 2026-07 the live read path (``services/context_retrieval.py``) still
reads S3 source JSON and queries neither store, so they are filled ahead of that
retrieval wiring. See ``config/clients/aim.yaml`` for the enable/disable decision.
"""
from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional
from services.blocks import block_label

NBSP = "\xa0"
BULLETS = "•▪◦‣·*-–—"

# Number of leading columns that carry meaning in an AIM teacher calendar.
_MAX_COLS = 11
# A row is a header if it repeats these column titles.
_HEADER_SIGNATURE = {"days", "subject/day", "topics covered", "acs codes"}


# ── cell helpers ────────────────────────────────────────────────────────────
def _clean(v: Any) -> str:
    s = "" if v is None else str(v)
    return s.replace("\r", "\n").replace(NBSP, " ").strip()


def _split_list(cell: str) -> List[str]:
    """Split a bulleted / newline-delimited cell into clean items."""
    out: List[str] = []
    for line in (cell or "").split("\n"):
        line = re.sub(r"\s{2,}", " ", line.strip().lstrip(BULLETS).strip())
        if line:
            out.append(line)
    return out


def _is_header(cells: List[str]) -> bool:
    low = {c.strip().lower() for c in cells if c.strip()}
    return len(_HEADER_SIGNATURE & low) >= 3


def _is_punct(tok: str) -> bool:
    return bool(tok) and re.fullmatch(r"[^\w]+", tok) is not None


# ── field parsers ───────────────────────────────────────────────────────────
def expand_acs(cell: str) -> List[str]:
    """Expand ACS-code cells into a flat, de-duplicated list.

    Handles single codes, newline/comma/slash lists, and ranges such as
    'AM.I.B.K1 - AM.I.B.K4' or 'AM.I.B.K1 – AM.I.B.K4' (expanded within the same
    prefix + category)."""
    if not cell:
        return []
    codes: List[str] = []
    for chunk in re.split(r"[\n,;/]+", cell.replace(NBSP, " ")):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(
            r"([A-Z]+(?:\.[A-Z0-9]+)*\.)([A-Z]+)(\d+)\s*[-–—]\s*"
            r"(?:[A-Z]+(?:\.[A-Z0-9]+)*\.)?([A-Z]+)?(\d+)$",
            chunk,
        )
        if m:
            prefix, cat1, n1, cat2, n2 = m.groups()
            if (cat2 or cat1) == cat1 and int(n2) >= int(n1):
                codes.extend(f"{prefix}{cat1}{i}" for i in range(int(n1), int(n2) + 1))
                continue
        codes.extend(re.findall(r"[A-Z]+(?:\.[A-Z0-9]+)+", chunk))
    seen, uniq = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def parse_handbook(cell: str) -> List[Dict[str, Any]]:
    """Parse handbook references into {handbook, chapter, pages}."""
    if not cell:
        return []
    refs: List[Dict[str, Any]] = []
    current_hb: Optional[str] = None
    for line in cell.replace(NBSP, " ").split("\n"):
        line = line.strip().lstrip(BULLETS).strip()
        if not line:
            continue
        hb = re.search(r"FAA-H-\d{4}-\d+[A-Z]?", line)
        if hb:
            current_hb = hb.group(0)
            line = line.replace(current_hb, "").strip()
        for ch in re.finditer(
            r"Ch\.?\s*(\d+)\s*pgs?\.?\s*([\d\-\s]+(?:to[\d\-\s]+)?(?:[;,]\s*[\d\-\s]+)*)",
            line, re.I,
        ):
            refs.append({
                "handbook": current_hb,
                "chapter": int(ch.group(1)),
                "pages": re.sub(r"\s+", " ", ch.group(2)).strip(" ;,"),
            })
    # Emit a bare handbook only if no chapter reference was found at all.
    if current_hb and not refs:
        refs.append({"handbook": current_hb, "chapter": None, "pages": ""})
    return refs


def parse_quiz(cell: str, subj_day_to_global: Dict[tuple, int]) -> Optional[Dict[str, Any]]:
    """Parse a quiz cross-reference, resolving referenced subject-days to globals.

    Examples:
        'Quiz 1 from Aircraft Drawings - Day 1'
        'Quiz 3 from Aircraft Drawings - Days 1 -- 3'   (range)
        'Quiz 8 from Cleaning and Corrosion Control - Days 1 & 2'  (list)
        'Block 2: Final Exam'   (no number -> preserved as raw)
    """
    if not cell:
        return None
    raw = cell.replace(NBSP, " ").strip()
    num = re.search(r"Quiz\s*(\d+)", raw, re.I)
    covers_m = re.search(r"from\s+(.*?)\s*-\s*Days?\s*([\d\s\-–—&,and]+)", raw, re.I)
    covers_unit, covers_days = None, []
    if covers_m:
        covers_unit = covers_m.group(1).strip()
        spec = covers_m.group(2)
        nums = [int(n) for n in re.findall(r"\d+", spec)]
        if len(nums) == 2 and re.search(r"\d\s*[-–—]+\s*\d", spec):
            sub_days = list(range(nums[0], nums[1] + 1))   # inclusive range
        else:
            sub_days = nums
        for d in sub_days:
            g = subj_day_to_global.get((covers_unit.lower(), d))
            if g:
                covers_days.append(g)
    return {
        "number": int(num.group(1)) if num else None,
        "covers_unit": covers_unit,
        "covers_days": sorted(set(covers_days)),
        "raw": raw,
    }


def _day_type(subject_day: str) -> str:
    low = subject_day.lower().strip()
    if low == "test" or "final exam" in low:
        return "test"
    if low.startswith("review"):
        return "review"
    return "instruction"


def _subject_unit(subject_day: str) -> tuple:
    """'Aircraft Drawings - Day 2' -> ('Aircraft Drawings', 2)."""
    m = re.match(r"(.*?)\s*-\s*Day\s*(\d+)\s*$", subject_day, re.I)
    if m:
        return m.group(1).strip(), int(m.group(2))
    return subject_day.strip(), None


def _compose_text(rec: Dict[str, Any]) -> str:
    """Clean natural-language surface for embedding (not raw cells)."""
    head = f"Block {rec['block_number']}, Day {rec['day_number']} — {rec['subject_unit']}"
    if rec.get("subject_day"):
        head += f" (Day {rec['subject_day']} of unit)"
    parts = [head + "."]
    if rec["topics_list"]:
        parts.append("Topics covered: " + "; ".join(rec["topics_list"]) + ".")
    if rec["handbook_refs"]:
        hb = "; ".join(
            f"{r['handbook']} Ch. {r['chapter']} pgs {r['pages']}" if r.get("chapter")
            else str(r.get("handbook") or "") for r in rec["handbook_refs"]
        )
        parts.append(f"Reference reading: {hb}.")
    if rec["projects"]:
        parts.append("Project(s): " + ", ".join(rec["projects"]) + ".")
    q = rec["quiz"]
    if q and q.get("number") is not None:
        cd = f" covering day(s) {', '.join(map(str, q['covers_days']))}" if q["covers_days"] else ""
        parts.append(f"Quiz {q['number']}{cd}.")
    if rec["acs_codes"]:
        parts.append("ACS codes: " + ", ".join(rec["acs_codes"]) + ".")
    if rec["hangar_activities"]:
        parts.append("Hangar activity: " + "; ".join(rec["hangar_activities"]) + ".")
    return " ".join(parts)


# ── workbook loading ─────────────────────────────────────────────────────────
def _first_sheet_rows(content: bytes):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)  # merges visible
    return wb, wb.worksheets[0]


def looks_like_aim_teacher_calendar(content: bytes) -> bool:
    """True if the first worksheet contains the AIM teacher-calendar header row.

    Used to self-gate: the parser only runs on this specific column layout, so
    other tenants' spreadsheets fall through to the generic extractor untouched.
    """
    try:
        _wb, ws = _first_sheet_rows(content)
    except Exception:
        return False
    for row in ws.iter_rows(min_row=1, max_row=8, max_col=_MAX_COLS, values_only=True):
        if _is_header([_clean(c) for c in row]):
            return True
    return False


def build_calendar_structure(content: bytes, filename: str,
                             block_hint: Optional[str] = None) -> Dict[str, Any]:
    """Parse an AIM teacher-calendar workbook into the pipeline ``calendar_structure``."""
    wb, ws = _first_sheet_rows(content)
    max_c = min(ws.max_column, _MAX_COLS)

    # Dense grid; propagate the top-left value of each vertical merge downward so a
    # merged 'Days' cell keeps its number across the day's spillover rows.
    grid = [[_clean(ws.cell(r, c).value) for c in range(1, max_c + 1)]
            for r in range(1, ws.max_row + 1)]
    for mr in ws.merged_cells.ranges:
        if mr.min_col > max_c:
            continue
        top = _clean(ws.cell(mr.min_row, mr.min_col).value)
        for r in range(mr.min_row, mr.max_row + 1):
            if r == mr.min_row:
                continue
            for c in range(mr.min_col, min(mr.max_col, max_c) + 1):
                if not grid[r - 1][c - 1]:
                    grid[r - 1][c - 1] = ""  # explicit: spillover cell stays empty

    block_num = None
    for src in (block_hint, filename):
        if src:
            m = re.search(r"(?:Block\s*0*)?(\d+)", src, re.I)
            if m:
                block_num = int(m.group(1))
                break

    # Pass 1: gather day rows, folding real spillover content (skip punctuation-only
    # separator rows such as a stray backtick that Excel stores as a full-width merge).
    raw_days: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for row in grid:
        cells = (row + [""] * max_c)[:max_c]
        if _is_header(cells):
            continue
        day_cell = cells[0].strip()
        if re.fullmatch(r"\d+", day_cell):
            current = {"day_number": int(day_cell), "cells": [[c] if c else [] for c in cells]}
            raw_days.append(current)
        elif current:
            content_cells = [c for c in cells[1:] if c]
            if content_cells and not all(_is_punct(c) for c in content_cells):
                for i in range(1, max_c):
                    if cells[i] and not _is_punct(cells[i]):
                        current["cells"][i].append(cells[i])

    # Pass 2: subject-day -> global-day lookup (needed to resolve quiz references).
    prelim, subj_lookup = [], {}
    for rd in raw_days:
        joined = ["\n".join(x) for x in rd["cells"]]
        unit, sub_day = _subject_unit(joined[1])
        prelim.append((rd["day_number"], unit, sub_day, joined))
        if sub_day is not None:
            subj_lookup[(unit.lower(), sub_day)] = rd["day_number"]

    days: List[Dict[str, Any]] = []
    for day_number, unit, sub_day, joined in prelim:
        topics_list = _split_list(joined[2])
        rec: Dict[str, Any] = {
            "day_number": day_number,
            "block": block_label(block_num) if block_num else "",
            "block_id": f"B{block_num}" if block_num else "",
            "block_number": block_num,
            "subject_unit": unit,
            "subject_day": sub_day,
            "day_type": _day_type(joined[1]),
            "lesson_title": joined[1],
            "topic": "; ".join(topics_list)[:300],
            "topics_list": topics_list,
            "acs_codes": expand_acs(joined[3]),
            "acs_codes_raw": re.sub(r"\s+", " ", joined[3]).strip(),
            "handbook_refs": parse_handbook(joined[4]),
            "projects": _split_list(joined[5]),
            "project_acs_codes": expand_acs(joined[6]),
            "quiz": parse_quiz(joined[7], subj_lookup),
            "supplemental_resources": _split_list(joined[8]),
            "test_prep_activities": _split_list(joined[9]),
            "hangar_activities": _split_list(joined[10]) if max_c > 10 else [],
        }
        rec["source_text"] = _compose_text(rec)
        # Backward-compatible fields consumed by the existing pipeline / retrieval.
        rec["week_number"] = None
        rec["activities"] = rec["hangar_activities"]
        rec["assignments"] = rec["projects"]
        rec["assessments"] = [rec["quiz"]["raw"]] if rec["quiz"] and rec["quiz"].get("raw") else []
        rec["source_location"] = f"{ws.title}_day_{day_number}"
        days.append(rec)

    days.sort(key=lambda d: d["day_number"])
    return {
        "structure_type": "course_calendar",
        "processing_profile": "aim_teacher_calendar",
        "profile": "aim_teacher_calendar",
        "course_name": "AIM General",
        "block": block_label(block_num) if block_num else "",
        "block_number": block_num,
        "total_days_detected": len(days),
        "days": days,
        "confidence": "high" if days else "low",
        "warnings": [] if days else ["No AIM calendar day rows detected."],
    }
