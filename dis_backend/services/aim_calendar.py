"""AIM Teacher Calendar parser — column-aware and merged-cell-aware.

The generic ``extract_calendar_structure`` treats every non-empty spreadsheet row
as a day, so it swallows the repeating header rows, blank/separator rows, and the
vertically-merged spillover rows of an AIM 'Block N Teacher Calendar', and it does
not understand the ACS-code / quiz / project / handbook columns.

This module reads the workbook directly (so merged ranges are visible) and emits
one rich record per teaching day per sheet. Multi-sheet workbooks (e.g. "ALL
Block Calendars_NEW FORMAT.xlsx") produce a ``sheets[]`` array; each sheet
becomes one Source Library section with block stamped on the unit (AKTR-style).

Entry points:
    looks_like_aim_teacher_calendar(content) -> bool   # self-gating format check
    build_calendar_structure(content, filename, block_hint=None) -> dict

Storage scope
-------------
Each sheet becomes one ``calendar_sheet`` content unit
(``content_unit_creation_agent``). Day rows land in Postgres
``dis_calendar_days`` (one calendar_id per sheet). Source Library
``content.json`` keeps sheet-level units (``per_unit``), not one blob.
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
# A row is a header if its cells name the AIM calendar's columns. Matched by
# CONCEPT, not by exact title: the 2026-08 workbooks head the same columns
# "Block Days | Subject Days | Topics Covered | ACS Codes for Topics", and an
# exact-title signature ({"days", "subject/day", "topics covered", "acs codes"},
# needing 3 hits) scored one — so seven real AIM calendars fell through to the
# generic extractor, which numbered every row day 1 and left 2 usable days out
# of 20. Requiring day + topic + (acs | handbook) keeps this self-gating: another
# tenant's spreadsheet has no ACS-code or FAA-handbook column.
_HEADER_CONCEPTS = (
    ("day", re.compile(r"\bdays?\b", re.I)),
    ("topic", re.compile(r"\btopics?\b", re.I)),
    ("acs", re.compile(r"\bacs\b", re.I)),
    ("handbook", re.compile(r"\bhandbook\b", re.I)),
)

#: Strict block number from a sheet name / banner / "Block N" filename — never
#: a bare digit like the Windows download suffix "(1)".
_BLOCK_RE = re.compile(r"\b(?:block|blk)\s*0*(\d+)\b", re.I)

#: Subject-unit section header rows in the NEW FORMAT have no day number in
#: column A and a non-numeric label in column B (e.g. "Aircraft Drawings").
_SUBJECT_HEADER_SKIP = re.compile(
    r"^(?:block\s+\d+|day\s*/?\s*night|weekend|topics?\s+covered)\b",
    re.I,
)


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
    """True for the AIM calendar's column-title row, in any of its wordings."""
    text = [c.strip() for c in cells if c.strip()]
    if not text:
        return False
    seen = {name for name, rx in _HEADER_CONCEPTS if any(rx.search(c) for c in text)}
    return "day" in seen and "topic" in seen and bool(seen & {"acs", "handbook"})


def _is_punct(tok: str) -> bool:
    return bool(tok) and re.fullmatch(r"[^\w]+", tok) is not None


def _schedule_from_name(sheet_name: str) -> str:
    low = (sheet_name or "").lower()
    if "weekend" in low:
        return "weekend"
    if "day" in low and "night" in low:
        return "day_night"
    if "day/night" in low or "day-night" in low:
        return "day_night"
    return "unknown"


def _block_number_from_text(*texts: Optional[str]) -> Optional[int]:
    """Block number from sheet name / banner / explicit 'Block N' only."""
    for text in texts:
        if not text:
            continue
        m = _BLOCK_RE.search(str(text))
        if m:
            return int(m.group(1))
    return None


# ── field parsers ───────────────────────────────────────────────────────────
def expand_acs(cell: str) -> List[str]:
    """Expand ACS-code cells into a flat, de-duplicated list.

    Handles single codes, newline/comma/slash lists, ranges such as
    'AM.I.B.K1 - AM.I.B.K4', and NEW FORMAT shorthand after a full code
    ('AM.I.B.K1, K2, R1' -> AM.I.B.K1, AM.I.B.K2, AM.I.B.R1).
    """
    if not cell:
        return []
    codes: List[str] = []
    last_prefix: Optional[str] = None  # e.g. "AM.I.B."
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
                last_prefix = prefix
                continue
        full = re.findall(r"[A-Z]+(?:\.[A-Z0-9]+)+", chunk)
        if full:
            codes.extend(full)
            # Remember prefix of the last full code for shorthand siblings.
            last = full[-1]
            mpref = re.match(r"^([A-Z]+(?:\.[A-Z0-9]+)*\.)[A-Z]+\d+$", last)
            if mpref:
                last_prefix = mpref.group(1)
            continue
        # Shorthand: "K2", "R1", "S5" after a full dotted code on the same cell.
        short = re.fullmatch(r"([A-Z]+)(\d+)", chunk)
        if short and last_prefix:
            codes.append(f"{last_prefix}{short.group(1)}{short.group(2)}")
    seen, uniq = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def parse_handbook(cell: str) -> List[Dict[str, Any]]:
    """Parse handbook references into {handbook, chapter, pages}.

    Accepts both ``pgs.`` (legacy) and ``pp.`` (NEW FORMAT).
    """
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
            r"Ch\.?\s*(\d+)\s*(?:pgs?|pp)\.?\s*([\d\-\s]+(?:to[\d\-\s]+)?(?:[;,]\s*[\d\-\s]+)*)",
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


def _is_subject_section_header(cells: List[str]) -> Optional[str]:
    """NEW FORMAT section row: empty day col, non-numeric subject label in col B."""
    day_cell = (cells[0] if cells else "").strip()
    if day_cell:
        return None
    label = (cells[1] if len(cells) > 1 else "").strip()
    if not label or re.fullmatch(r"\d+", label):
        return None
    if _is_header(cells) or _SUBJECT_HEADER_SKIP.search(label):
        return None
    # Banner rows often put the whole "Block N (Day/Night): ..." in col B.
    if _BLOCK_RE.search(label) and ":" in label:
        return None
    return label


# ── workbook loading ─────────────────────────────────────────────────────────
def _load_workbook(content: bytes):
    import openpyxl
    return openpyxl.load_workbook(io.BytesIO(content), data_only=True)


def _sheet_has_aim_header(ws, max_scan: int = 8) -> bool:
    for row in ws.iter_rows(min_row=1, max_row=max_scan, max_col=_MAX_COLS, values_only=True):
        if _is_header([_clean(c) for c in row]):
            return True
    return False


def looks_like_aim_teacher_calendar(content: bytes) -> bool:
    """True if ANY worksheet contains the AIM teacher-calendar header row.

    Used to self-gate: the parser only runs on this specific column layout, so
    other tenants' spreadsheets fall through to the generic extractor untouched.
    """
    try:
        wb = _load_workbook(content)
    except Exception:
        return False
    for ws in wb.worksheets:
        if _sheet_has_aim_header(ws):
            return True
    return False


def _banner_text(grid: List[List[str]], max_rows: int = 3) -> str:
    parts: List[str] = []
    for row in grid[:max_rows]:
        for cell in row:
            if cell and not _is_header((row + [""] * _MAX_COLS)[:_MAX_COLS]):
                parts.append(cell)
    return " ".join(parts)


def _parse_sheet(ws, sheet_index: int, block_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Parse one AIM calendar worksheet into a sheet record, or None if no header."""
    if not _sheet_has_aim_header(ws):
        return None

    max_c = min(ws.max_column or 1, _MAX_COLS)
    grid = [[_clean(ws.cell(r, c).value) for c in range(1, max_c + 1)]
            for r in range(1, (ws.max_row or 0) + 1)]
    for mr in ws.merged_cells.ranges:
        if mr.min_col > max_c:
            continue
        for r in range(mr.min_row, mr.max_row + 1):
            if r == mr.min_row:
                continue
            for c in range(mr.min_col, min(mr.max_col, max_c) + 1):
                if not grid[r - 1][c - 1]:
                    grid[r - 1][c - 1] = ""

    banner = _banner_text(grid)
    block_num = _block_number_from_text(ws.title, banner, block_hint)
    schedule = _schedule_from_name(ws.title)

    # Pass 1: day rows + NEW FORMAT subject-section headers carried forward.
    raw_days: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    current_subject: Optional[str] = None
    for row in grid:
        cells = (row + [""] * max_c)[:max_c]
        if _is_header(cells):
            continue
        section = _is_subject_section_header(cells)
        if section:
            current_subject = section
            current = None
            continue
        day_cell = cells[0].strip()
        if re.fullmatch(r"\d+", day_cell):
            # NEW FORMAT: col B is subject-day number; prepend carried unit name.
            subject_cell = cells[1].strip() if len(cells) > 1 else ""
            if current_subject and re.fullmatch(r"\d+", subject_cell):
                cells = list(cells)
                cells[1] = f"{current_subject} - Day {subject_cell}"
            elif current_subject and not subject_cell:
                cells = list(cells)
                cells[1] = current_subject
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
        lesson_title = joined[1]
        if unit and sub_day is not None and not re.search(r"\bDay\s*\d+\s*$", lesson_title, re.I):
            lesson_title = f"{unit} - Day {sub_day}"
        rec: Dict[str, Any] = {
            "day_number": day_number,
            "block": block_label(block_num) if block_num else "",
            "block_id": f"B{block_num}" if block_num else "",
            "block_number": block_num,
            "subject_unit": unit,
            "subject_day": sub_day,
            "day_type": _day_type(lesson_title),
            "lesson_title": lesson_title,
            "topic": "; ".join(topics_list)[:300],
            "topics_list": topics_list,
            "acs_codes": expand_acs(joined[3] if len(joined) > 3 else ""),
            "acs_codes_raw": re.sub(r"\s+", " ", (joined[3] if len(joined) > 3 else "")).strip(),
            "handbook_refs": parse_handbook(joined[4] if len(joined) > 4 else ""),
            "projects": _split_list(joined[5] if len(joined) > 5 else ""),
            "project_acs_codes": expand_acs(joined[6] if len(joined) > 6 else ""),
            "quiz": parse_quiz(joined[7] if len(joined) > 7 else "", subj_lookup),
            "supplemental_resources": _split_list(joined[8] if len(joined) > 8 else ""),
            "test_prep_activities": _split_list(joined[9] if len(joined) > 9 else ""),
            "hangar_activities": _split_list(joined[10]) if max_c > 10 and len(joined) > 10 else [],
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
        "sheet_name": ws.title,
        "sheet_index": sheet_index,
        "block": block_label(block_num) if block_num else "",
        "block_number": block_num,
        "block_id": f"B{block_num}" if block_num else "",
        "schedule": schedule,
        "banner": banner[:500] if banner else "",
        "total_days_detected": len(days),
        "days": days,
    }


def build_calendar_structure(content: bytes, filename: str,
                             block_hint: Optional[str] = None) -> Dict[str, Any]:
    """Parse an AIM teacher-calendar workbook into the pipeline ``calendar_structure``.

    Multi-sheet workbooks return ``sheets[]`` (one entry per AIM calendar sheet).
    Top-level ``days`` is empty for multi-sheet files so callers that still read
    ``days`` do not silently treat Block 1's schedule as the whole workbook.
    Single-sheet (legacy) workbooks also populate top-level ``days`` /
    ``block`` for backward compatibility with older readers.
    """
    wb = _load_workbook(content)
    # Filename block only when it says "Block N" / "BLK N" — never "(1)".
    file_block = _block_number_from_text(filename)
    hint_block = _block_number_from_text(block_hint) if block_hint else None
    # Prefer an explicit Block-N hint; else sheet name; else filename Block-N.
    effective_hint = block_label(hint_block or file_block) if (hint_block or file_block) else None

    sheets: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for idx, ws in enumerate(wb.worksheets):
        try:
            parsed = _parse_sheet(ws, idx, block_hint=effective_hint)
        except Exception as exc:  # noqa: BLE001 — one bad sheet must not sink the book
            skipped.append({"sheet": ws.title, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        if parsed is None:
            skipped.append({"sheet": ws.title, "reason": "no AIM calendar header row"})
            continue
        if not parsed.get("days"):
            skipped.append({"sheet": ws.title, "reason": "no day rows detected"})
            continue
        sheets.append(parsed)

    total_days = sum(int(s.get("total_days_detected") or 0) for s in sheets)
    blocks_covered = []
    for s in sheets:
        b = s.get("block")
        if b and b not in blocks_covered:
            blocks_covered.append(b)

    # Legacy single-sheet shape: keep top-level days/block populated.
    single = len(sheets) == 1
    first = sheets[0] if single else None
    return {
        "structure_type": "course_calendar",
        "processing_profile": "aim_teacher_calendar",
        "profile": "aim_teacher_calendar",
        "course_name": "AIM General",
        "block": first.get("block", "") if single else "",
        "block_number": first.get("block_number") if single else None,
        "blocks_covered": blocks_covered,
        "total_days_detected": total_days,
        "sheets": sheets,
        # Empty for multi-sheet so upsert / day-unit paths do not invent a
        # single-block calendar from sheet 0 alone.
        "days": list(first.get("days") or []) if single else [],
        "skipped_sheets": skipped,
        "confidence": "high" if sheets else "low",
        "warnings": [] if sheets else ["No AIM calendar day rows detected."],
    }


def iter_calendar_sheets(calendar_structure: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize legacy (days-only) and multi-sheet calendar_structure to sheets[]."""
    if not calendar_structure:
        return []
    sheets = calendar_structure.get("sheets") or []
    if sheets:
        return list(sheets)
    days = calendar_structure.get("days") or []
    if not days:
        return []
    return [{
        "sheet_name": calendar_structure.get("sheet_name") or "Calendar",
        "sheet_index": 0,
        "block": calendar_structure.get("block") or "",
        "block_number": calendar_structure.get("block_number"),
        "block_id": calendar_structure.get("block_id") or "",
        "schedule": calendar_structure.get("schedule") or "unknown",
        "total_days_detected": len(days),
        "days": days,
    }]
