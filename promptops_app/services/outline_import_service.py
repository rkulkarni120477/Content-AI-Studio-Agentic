"""Import an existing DLU Outline file into the canonical per-day Outline shape.

The user uploads an Outline they already have (Excel, DOCX or PDF) and it must
appear on the Outline (Blueprint) page exactly as if it had been generated — the
same six accordion sections (Overview, Today's Mission, Learn It, Quick Check, Up
Next in Class, Day Reflection), every piece of content present, and pinned as
active. This module is the "extract → normalize" half of that; persistence and
pinning live in the blueprints router (import_outline), which either appends a
new version to the existing Outline for that day or creates a fresh one — so an
imported Outline is byte-for-byte a normal blueprint in the same tables.

The one thing that makes the display identical for free: a generated DLU Outline
is just markdown whose day parts sit under a ``### DLU Outline`` heading as
``N. **Part Name**`` lines (see aim_prompts/AIM_DLU_OUTLINE_PROMPT.md). The
frontend ``dluBlueprint.js`` / backend ``blueprint_parser.is_dlu_blueprint``
detect that shape by pattern alone — so if this module emits the same shape, the
screen renders it as the day accordions with no renderer change.

Two fidelity tiers, chosen per file (see normalize_import):
  * DETERMINISTIC — a file whose extracted text is ALREADY the DLU Outline shape
    (a re-imported CAS export, or a markdown-structured source) is passed through
    untouched, so nothing can be reworded or dropped.
  * LLM RESTRUCTURE — any other Excel/DOCX/PDF is reorganized into the DLU Outline
    shape by an LLM under a strict "preserve every value verbatim, never omit"
    contract. This is the common path, because an arbitrary uploaded file rarely
    carries the exact numbered-part markdown the renderer keys on.
"""
from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import List, Optional, Tuple

from promptops_app.parsers.blueprint_parser import (
    is_dlu_blueprint,
    parse_day_and_title,
)
from promptops_app.parsers.cdd_parser import parse_sections_from_text

_log = logging.getLogger(__name__)

#: Extensions we accept. Legacy binary .doc/.ppt are excluded on purpose — the
#: same limitation file_parser.py already documents; ask the user to re-save.
SUPPORTED_EXTS = (".xlsx", ".xls", ".docx", ".pdf")

#: Input size above which a single LLM restructure call risks silently truncating
#: (and therefore dropping) content. Past this we keep the deterministic fallback
#: rather than an LLM call that might not round-trip losslessly.
_LLM_INPUT_CHAR_CAP = 180_000

#: Output ceiling for the restructure call. One day's outline is far smaller than
#: a whole-block table, but keep generous headroom so nothing is clipped.
_LLM_MAX_OUTPUT_TOKENS = 16_000


@dataclass
class OutlineImportResult:
    """Everything the endpoint needs to persist an imported Outline."""
    raw_output: str                       # DLU Outline markdown → BlueprintVersion.full_content
    sections: dict                        # parse_sections_from_text(raw_output)
    derived_title: str                    # blueprint title ("Day N: <topic> Blueprint")
    day_number: Optional[int]             # the day this outline is for (None → caller must resolve)
    topic: str                            # day topic, best-effort
    method: str                           # "passthrough" | "llm_restructure" | "raw_fallback"
    is_dlu: bool                          # whether it renders as the DLU day accordions
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Small markdown helpers (mirror cdd_import_service so both imports read alike)
# --------------------------------------------------------------------------- #
def _cell(text: str) -> str:
    """Sanitize one markdown table cell: a raw '|' or newline breaks the row (and
    can truncate every row after it in a strict renderer)."""
    return " ".join((text or "").split()).replace("|", "/")


def _rows_to_markdown_table(rows: List[List[str]]) -> str:
    """A 2-D grid → a GitHub-flavored markdown table (first row is the header).
    Every row is padded to the widest row so the column count is uniform."""
    grid = [[_cell(c) for c in r] for r in rows if any((c or "").strip() for c in r)]
    if not grid:
        return ""
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]
    out = ["| " + " | ".join(grid[0]) + " |", "|" + "---|" * width]
    for r in grid[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def _slug(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


# --------------------------------------------------------------------------- #
# Extraction — every file type is reduced to one flat markdown string so the
# detect-or-restructure decision below is uniform across Excel / Word / PDF.
# --------------------------------------------------------------------------- #
def _trim_grid(rows: List[List[str]]) -> List[List[str]]:
    """Drop fully-empty leading/trailing rows and trailing empty columns, and
    strip the decoration our own XLSX export adds, so re-importing an export
    round-trips cleanly."""
    cleaned = []
    for r in rows:
        joined = " ".join(c for c in r if c).strip()
        if "Content AI Studio" in joined:   # brand/meta row written by xlsx_exporter
            continue
        cleaned.append(r)
    while cleaned and not any((c or "").strip() for c in cleaned[0]):
        cleaned.pop(0)
    while cleaned and not any((c or "").strip() for c in cleaned[-1]):
        cleaned.pop()
    if not cleaned:
        return []
    width = max(len(r) for r in cleaned)
    cleaned = [r + [""] * (width - len(r)) for r in cleaned]
    last = width
    while last > 0 and all(not (r[last - 1] or "").strip() for r in cleaned):
        last -= 1
    return [r[:last] for r in cleaned]


def _extract_xlsx_pandas(data: bytes) -> List[Tuple[str, List[List[str]]]]:
    """Pandas fallback for workbooks openpyxl can't open (notably legacy .xls)."""
    import pandas as pd

    book = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=str)
    sheets = []
    for name, df in book.items():
        rows = [
            ["" if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v)
             for v in row]
            for row in df.values.tolist()
        ]
        grid = _trim_grid(rows)
        if grid:
            sheets.append((str(name), grid))
    return sheets


def _extract_xlsx(data: bytes) -> List[Tuple[str, List[List[str]]]]:
    """Every worksheet as (sheet_title, trimmed 2-D string grid), in workbook
    order. Falls back to pandas for files openpyxl rejects (e.g. legacy .xls)."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        _log.info("outline_import openpyxl could not open workbook (%s) — trying pandas", exc)
        try:
            return _extract_xlsx_pandas(data)
        except Exception as exc2:
            raise ValueError(
                "Could not read the Excel file. If it is a legacy .xls, save it as "
                ".xlsx and re-upload."
            ) from exc2

    try:
        sheets = []
        for ws in wb.worksheets:
            rows = [
                ["" if v is None else str(v) for v in row]
                for row in ws.iter_rows(values_only=True)
            ]
            grid = _trim_grid(rows)
            if grid:
                sheets.append((ws.title, grid))
        return sheets
    finally:
        wb.close()


def _xlsx_to_markdown(data: bytes) -> str:
    """Flatten a workbook to markdown: each sheet as a heading + a table. A single
    2-column sheet is rendered as ``**Label:** value`` lines so a key/value day
    header survives as label lines the restructure step can read."""
    sheets = _extract_xlsx(data)
    if not sheets:
        raise ValueError("No readable content found in the uploaded workbook.")
    parts: List[str] = []
    for name, grid in sheets:
        parts.append(f"## {name}")
        width = max((len(r) for r in grid), default=0)
        if width <= 2:
            for r in grid:
                label = _cell(r[0]) if r else ""
                value = _cell(r[1]) if len(r) > 1 else ""
                if label:
                    parts.append(f"**{label}:** {value}".rstrip())
        else:
            parts.append(_rows_to_markdown_table(grid))
    return "\n\n".join(p for p in parts if p.strip()).strip()


def _extract_docx(data: bytes) -> str:
    """DOCX → flat markdown, in document order. Bold runs are wrapped in ``**`` so
    an already-structured outline's part headers (e.g. **Today's Mission**) survive
    the round-trip and can be recognised by the pass-through detector below."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    def _para_md(para) -> str:
        pieces = []
        for run in para.runs:
            t = run.text
            if not t:
                continue
            if run.bold and t.strip():
                pieces.append(f"**{t.strip()}**")
            else:
                pieces.append(t)
        text = "".join(pieces).strip()
        return _slug(text) if text else _slug(para.text)

    doc = Document(io.BytesIO(data))
    out: List[str] = []
    for child in doc.element.body.iterchildren():
        tag = child.tag
        if tag.endswith("}p"):
            para = Paragraph(child, doc)
            text = _para_md(para)
            if text:
                out.append(text)
        elif tag.endswith("}tbl"):
            table = Table(child, doc)
            rows = [[c.text for c in row.cells] for row in table.rows]
            md = _rows_to_markdown_table(rows)
            if md:
                out.append(md)
    return "\n\n".join(out).strip()


def _extract_pdf(data: bytes) -> str:
    """PDF → plain text, page by page."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        txt = page.extract_text() or ""
        if txt.strip():
            parts.append(txt)
    return "\n\n".join(parts).strip()


# --------------------------------------------------------------------------- #
# Day / topic derivation and DLU shape detection
# --------------------------------------------------------------------------- #
def _detect_day_topic(text: str) -> Tuple[Optional[int], str]:
    """Best-effort (day_number, topic) from extracted content.

    Priority: the canonical title line ``# DLU Outline - Day N: <topic>``, then a
    ``Day Number: N`` / ``Day: N`` label line, then the first bare ``Day N``. Topic
    comes from the title line or a ``Topic: ...`` label. Never raises.
    """
    day: Optional[int] = None
    topic = ""

    m = re.search(r"(?im)^\s*#\s*DLU\s+Outline\s*[-–—:]\s*Day\s*(\d+)\s*[:\-–—]?\s*(.*)$", text)
    if m:
        day = int(m.group(1))
        topic = _slug(m.group(2))
    if day is None:
        m = re.search(r"(?im)^[\s>*_-]*\**\s*Day\s*(?:Number)?\s*\**\s*[:\-]\s*\**\s*(\d+)", text)
        if m:
            day = int(m.group(1))
    if day is None:
        m = re.search(r"(?i)\bday\s*(\d+)\b", text)
        if m:
            day = int(m.group(1))

    if not topic:
        tm = re.search(r"(?im)^[\s>*_-]*\**\s*Topic\s*\**\s*[:\-]\s*\**\s*(.+?)\s*\**\s*$", text)
        if tm:
            topic = _slug(tm.group(1))
            topic = re.split(r"\s{2,}|\|", topic)[0].strip()[:80]
    return day, topic


def _looks_like_dlu(text: str) -> bool:
    """Would this markdown render as the DLU day accordions? Reuses the canonical
    detector the frontend/backend already agree on (``### DLU Outline`` header or
    ≥2 of the five part markers)."""
    return is_dlu_blueprint(SimpleNamespace(full_content=text or ""))


def _is_structured_dlu(text: str) -> bool:
    """Stricter than _looks_like_dlu: is the extracted text ALREADY the numbered
    DLU Outline markdown (so it can be passed through with no LLM)? Requires the
    explicit ``### DLU Outline`` heading, or at least three bold part headers."""
    low = (text or "").lower()
    if "### dlu outline" in low:
        return True
    bold_parts = re.findall(
        r"\*\*\s*(?:today'?s mission|learn it(?:\s*\(review content\))?|quick check|"
        r"up next(?: in class)?|day reflection)\s*\*\*",
        low,
    )
    return len(bold_parts) >= 3


def _ensure_title(text: str, day: Optional[int], topic: str) -> str:
    """Prepend the canonical ``# DLU Outline - Day N: <topic>`` title when the
    passed-through content has no leading title line, so the header reads the same
    as a generated Outline."""
    if re.match(r"(?is)^\s*#\s*DLU\s+Outline", text or ""):
        return text.strip()
    label = f"Day {day}" if day else "Day"
    title = f"# DLU Outline - {label}: {topic}".rstrip(": ").strip()
    return f"{title}\n\n{text.strip()}"


# --------------------------------------------------------------------------- #
# LLM restructure (any file not already in the DLU Outline shape)
# --------------------------------------------------------------------------- #
_RESTRUCTURE_SYSTEM = (
    "You are a document-structuring assistant for an eLearning platform. You convert an "
    "uploaded daily lesson Outline (a DLU Outline — one instructional day) into Content AI "
    "Studio's canonical DLU Outline markdown layout.\n\n"
    "ABSOLUTE RULES:\n"
    "1. Preserve every piece of content EXACTLY — do not summarize, rewrite, paraphrase, "
    "invent, or omit any value, number, ACS code, name, objective, or table row. You only "
    "reorganize and label existing content into the target shape.\n"
    "2. Keep every table as a GitHub-flavored markdown table with ALL rows and ALL columns "
    "intact.\n"
    "3. The document describes ONE day. Put that day's number in the title line as a bare "
    "numeral, and use the FIRST day number that appears in the source.\n"
    "4. Output GitHub-flavored Markdown only — no commentary, no explanations, no code fences."
)

# The target shape is aim_prompts/AIM_DLU_OUTLINE_PROMPT.md's OUTPUT SHAPE, condensed.
# The five parts MUST sit under "### DLU Outline" as "N. **Name**" lines — that is
# the exact pattern dluBlueprint.js / is_dlu_blueprint key on to render the accordions.
_RESTRUCTURE_USER = """Convert the uploaded Outline content below into this exact structure. Keep every heading verbatim.

# DLU Outline - Day <number>: <topic label>

Then the day header as bold label lines (value on the same line), for whichever of these the source provides: **Block:**, **Day Number:**, **Day Type:**, **Topic:**, **Concept Type:**, **Concept Scope:**, **Derived Day Objective:**, **Codes Addressed:**, and any other label/value facts present in the source. Never invent a value that is not in the source.

Then any appendix sections the source contains, each as its own "## SECTION NAME" heading with the content beneath it (for example "## ACS CODES ADDRESSED" as a markdown table, "## SOURCE MAP", "## OPEN ITEMS", "## APPROVAL STATUS"). Include only the ones the source actually has.

Then a horizontal rule (---), then this heading exactly:

### DLU Outline

followed by the five parts, each a numbered line carrying only its bold name, with the source's content for that part as indented labelled bullet lines beneath it:
1. **Today's Mission**
2. **Learn It**
3. **Quick Check**
4. **Up Next in Class**
5. **Day Reflection**

If the source clearly marks this as a review day, write the second part as "2. **Learn It (Review Content)**". Map the source's content into whichever parts it belongs to; if the source has content that does not fit a part, keep it in the appendix sections above the rule so NOTHING from the source is dropped. Write no other line that is nothing but a bold phrase — those lines mark the part boundaries.

UPLOADED CONTENT:
---
{content}
---
"""


def _llm_restructure(content: str, *, model_choice: str, usage_ctx=None) -> str:
    """Ask the LLM to reorganize *content* into DLU Outline markdown, losslessly.
    Raises on an LLM error so the caller can fall back to a raw wrap rather than
    persist a half-empty document."""
    from promptops_app.services.llm_service import generate_with_metadata

    user_prompt = _RESTRUCTURE_USER.format(content=content)
    result = generate_with_metadata(
        model_choice, _RESTRUCTURE_SYSTEM, user_prompt,
        usage_ctx=usage_ctx, max_tokens=_LLM_MAX_OUTPUT_TOKENS,
    )
    if result.status == "error":
        raise RuntimeError(f"LLM restructure failed: {result.error_type}")
    return result.text or ""


def _raw_wrap(text: str, day: Optional[int], topic: str) -> str:
    """Last-resort wrap: a title plus the faithful extracted text under one
    heading. Renders as a single 'Imported Content' section (not the DLU
    accordions) but loses nothing."""
    label = f"Day {day}" if day else "Day"
    title = f"# DLU Outline - {label}: {topic}".rstrip(": ").strip()
    return f"{title}\n\n## Imported Content\n\n{text.strip()}"


# --------------------------------------------------------------------------- #
# Title / filename derivation
# --------------------------------------------------------------------------- #
def _filename_stem(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    return _slug(stem.replace("_", " ").replace("-", " ")) or "Imported Outline"


def _build_title(document_title: str, day: Optional[int], topic: str, stem: str) -> str:
    """The blueprint title. A DLU day title reads 'Day N: <topic> Blueprint' so
    parse_day_and_title recovers the day from it (mirrors the generate path)."""
    if _slug(document_title):
        return _slug(document_title)
    if day and topic:
        return f"Day {day}: {topic} Blueprint"
    if day:
        return f"Day {day} Blueprint"
    return f"{stem} Blueprint"


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def normalize_import(filename: str, data: bytes, *,
                     course_title: str = "", document_title: str = "",
                     day_hint: Optional[int] = None,
                     model_choice: str = "GPT-5.4", usage_ctx=None) -> OutlineImportResult:
    """Extract *data* and normalize it into the canonical DLU Outline day shape.

    ``day_hint`` is the day the user had selected in the dropdown; it is used only
    when the file itself carries no detectable day, so the file always wins.
    ``model_choice`` / ``usage_ctx`` are only used on the LLM restructure path.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in SUPPORTED_EXTS:
        raise ValueError(
            f"Unsupported file type '{ext or filename}'. Upload an Excel (.xlsx/.xls), "
            "Word (.docx) or PDF (.pdf) Outline."
        )

    stem = _filename_stem(filename)
    warnings: List[str] = []

    if ext in (".xlsx", ".xls"):
        flat_text = _xlsx_to_markdown(data)
    elif ext == ".docx":
        flat_text = _extract_docx(data)
        if not flat_text:
            raise ValueError("No readable content found in the uploaded Word document.")
    else:  # .pdf
        flat_text = _extract_pdf(data)
        if not flat_text:
            raise ValueError("No extractable text found in the uploaded PDF.")

    day, topic = _detect_day_topic(flat_text)
    if day is None and day_hint:
        day = int(day_hint)

    # Tier 1 — already the DLU Outline shape → pass through untouched (no LLM).
    if _is_structured_dlu(flat_text):
        raw_output = _ensure_title(flat_text, day, topic)
        method = "passthrough"
    else:
        # Tier 2 — reorganize into the DLU Outline shape under the preserve-all
        # contract. Oversized input or an unrecognized result falls back to a raw
        # wrap so content is never dropped, only shown less structured.
        if len(flat_text) > _LLM_INPUT_CHAR_CAP:
            warnings.append(
                "The file was too large to auto-structure into the Outline layout, so "
                "its full extracted content was imported as a single section."
            )
            _log.warning("outline_import oversize llm bypass file=%r chars=%d",
                         filename, len(flat_text))
            raw_output = _raw_wrap(flat_text, day, topic)
            method = "raw_fallback"
        else:
            try:
                restructured = _llm_restructure(
                    flat_text, model_choice=model_choice, usage_ctx=usage_ctx,
                )
                if _looks_like_dlu(restructured):
                    raw_output = _ensure_title(restructured, day, topic)
                    method = "llm_restructure"
                    # A day named in the restructured title is more reliable than
                    # one scraped from raw extraction — prefer it.
                    r_day, r_topic = _detect_day_topic(raw_output)
                    if r_day is not None:
                        day = r_day
                    if r_topic:
                        topic = r_topic
                else:
                    warnings.append(
                        "Automatic Outline structuring did not apply cleanly; the full "
                        "content was imported as a single section."
                    )
                    raw_output = _raw_wrap(flat_text, day, topic)
                    method = "raw_fallback"
            except Exception as exc:
                _log.warning("outline_import llm restructure failed file=%r error=%s — "
                             "falling back to raw wrap", filename, exc)
                warnings.append(
                    "Automatic Outline structuring was unavailable; the full content "
                    "was imported as a single section."
                )
                raw_output = _raw_wrap(flat_text, day, topic)
                method = "raw_fallback"

    parsed_sections = parse_sections_from_text(raw_output)
    derived_title = _build_title(document_title, day, topic, stem)

    return OutlineImportResult(
        raw_output=raw_output,
        sections=parsed_sections,
        derived_title=derived_title,
        day_number=day,
        topic=topic,
        method=method,
        is_dlu=_looks_like_dlu(raw_output),
        warnings=warnings,
    )
