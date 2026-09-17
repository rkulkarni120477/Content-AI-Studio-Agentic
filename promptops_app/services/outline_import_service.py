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

from promptops_app.parsers.blueprint_parser import is_dlu_blueprint
from promptops_app.parsers.cdd_parser import parse_sections_from_text

_log = logging.getLogger(__name__)

#: Extensions we accept. Legacy binary .doc/.ppt are excluded on purpose — the
#: same limitation file_parser.py already documents; ask the user to re-save.
SUPPORTED_EXTS = (".xlsx", ".xls", ".docx", ".pdf")

#: Input size above which a single LLM restructure call risks silently truncating
#: (and therefore dropping) content. Past this we keep the deterministic fallback
#: rather than an LLM call that might not round-trip losslessly.
_LLM_INPUT_CHAR_CAP = 180_000

#: Output ceiling for the restructure call. Because the restructure PRESERVES the
#: source verbatim (only reorganizing it), the output is roughly the size of the
#: input — so a large Outline (e.g. a dense AIM day ~50 KB / ~15k tokens) can
#: exceed a 16k cap and get truncated, which the truncated-guard then rejects into
#: a single "Imported Content" section. We ask for the model's full output budget
#: instead; generate_with_metadata caps this to the model's own max_output_tokens
#: (64k for the current models), so it is never unsafe, and you only pay for the
#: tokens actually produced. The truncation guard in _llm_restructure still catches
#: the rare file too large even for that, falling back losslessly.
_LLM_MAX_OUTPUT_TOKENS = 64_000


@dataclass
class OutlineImportResult:
    """Everything the endpoint needs to persist an imported Outline."""
    raw_output: str                       # DLU Outline markdown → BlueprintVersion.full_content
    sections: dict                        # parse_sections_from_text(raw_output)
    derived_title: str                    # blueprint title ("Day N: …" / "Module N: …")
    kind: str                             # "day" (DLU day Outline) or "module" (module Outline)
    unit_number: int                      # the day or module number this Outline is filed under
    file_unit: Optional[int]              # day/module actually found IN the file (None if none) — lets
                                          # the caller refuse when the file and the picked unit disagree
    topic: str                            # day/module topic, best-effort
    method: str                           # "passthrough" | "llm_restructure" | "raw_fallback" | "module"
    is_dlu: bool                          # whether it renders as the DLU day accordions (day kind only)
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


def _clean_topic(raw: str) -> str:
    """Sanitize a captured topic. Drops bold/heading markers, stops at a table
    pipe, and rejects a leftover ``Label: value`` fragment (a stray colon) — the
    LLM sometimes echoes a bold label line where the topic should be, and that
    must never end up in the title."""
    t = _slug(raw or "").replace("*", "").strip()
    t = re.split(r"\s{2,}|\|", t)[0].strip()
    t = re.sub(r"(?i)\s*blueprint\s*$", "", t).strip()
    t = t.strip(":-–— ").strip()
    if not t or ":" in t:
        return ""
    return t[:80]


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
        nonempty = [c for c in r if (c or "").strip()]
        joined = " ".join(nonempty).strip()
        if "Content AI Studio" in joined:   # brand/meta row written by xlsx_exporter
            continue
        # The CAS xlsx export lays a day out as a "Section | Content" two-column
        # sheet; that literal header row is decoration, not content — drop it so
        # it doesn't land in the imported body as noise.
        if [c.strip().lower() for c in nonempty] == ["section", "content"]:
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


def _sheet_to_markdown(name: str, grid: List[List[str]]) -> str:
    """One worksheet → markdown, choosing a rendering that never destroys content.

    A cell in a CAS-exported Outline can hold an ENTIRE markdown section — its
    embedded ``### DLU Outline`` heading, the numbered parts, whole ``|`` tables,
    all with real newlines. Those must be reproduced VERBATIM, because the
    frontend parser is line-based: collapse the newlines (the old bug) and the
    day accordions never appear. So:

      * "Prose" sheet — any cell carries a newline, markdown markers, or a long
        blob → emit each cell verbatim (newlines and ``|`` kept). A row shaped
        [short-label, long-body] becomes ``## label`` + body, matching the
        export's Section|Content layout.
      * "Grid" sheet — every cell is short and single-line → a genuine data grid,
        rendered as a sanitized markdown table (2-column key/value → bullets).
        Only here is per-cell sanitization safe.
    """
    cells = [c for r in grid for c in (r or [])]
    is_prose = any(
        ("\n" in (c or "")) or len(c or "") > 200 or "###" in (c or "")
        or "**" in (c or "") or ("|" in (c or "") and "---" in (c or ""))
        for c in cells
    )
    if is_prose:
        parts: List[str] = []
        for r in grid:
            nonempty = [c for c in r if (c or "").strip()]
            if not nonempty:
                continue
            if len(nonempty) >= 2:
                head = _slug(nonempty[0])
                body = "\n".join(c.strip() for c in nonempty[1:]).strip()
                parts.append(f"## {head}\n\n{body}" if body else f"## {head}")
            else:
                parts.append(nonempty[0].strip())
        return "\n\n".join(p for p in parts if p.strip())

    width = max((len(r) for r in grid), default=0)
    if width <= 2:
        lines: List[str] = []
        for r in grid:
            # Strip a trailing ':' the source may already carry on the label, so a
            # "Day Number:" cell renders as "**Day Number:** N", not "**Day Number::**"
            # (the doubled colon broke _detect_day_topic's label match).
            label = _slug(r[0]).rstrip(":").strip() if r else ""
            value = _slug(r[1]) if len(r) > 1 else ""
            if label:
                lines.append(f"**{label}:** {value}".rstrip())
        return "\n".join(lines)
    return _rows_to_markdown_table(grid)


def _xlsx_to_markdown(data: bytes) -> str:
    """Flatten a workbook to markdown, one ``## sheet`` block per worksheet, with
    every cell's content preserved faithfully (see _sheet_to_markdown)."""
    sheets = _extract_xlsx(data)
    if not sheets:
        raise ValueError("No readable content found in the uploaded workbook.")
    parts: List[str] = []
    for name, grid in sheets:
        body = _sheet_to_markdown(name, grid)
        if body.strip():
            parts.append(body)
    return "\n\n".join(parts).strip()


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

    Every pattern is anchored to the START of a line — a title, a label, or a
    "Day N" heading. A "Day N" buried mid-sentence (e.g. "continues the work from
    Day 2") is deliberately NOT matched: taking the first such number anywhere in
    the file mis-filed outlines onto the wrong day. Returns (None, "") when no
    anchored day is present, so the caller can require an explicit day instead of
    guessing. Never raises.
    """
    day: Optional[int] = None
    topic = ""

    # 1. Canonical title line: "# DLU Outline - Day N: <topic>".
    m = re.search(r"(?im)^\s*#\s*DLU\s+Outline\s*[-–—:]\s*Day\s*(\d+)\s*[:\-–—]?\s*(.*)$", text)
    if m:
        day = int(m.group(1))
        topic = _clean_topic(m.group(2))
    # 2. A "Day Number: N" / "Day: N" label line (bold/quote markers optional).
    if day is None:
        m = re.search(r"(?im)^[\s>*_#-]*\**\s*Day(?:\s*Number)?\s*\**\s*[:\-]\s*\**\s*(\d+)", text)
        if m:
            day = int(m.group(1))
    # 3. A "Day N" heading at the start of a line ("Day 7: Weather Systems",
    #    "## Day 7") — the CAS export's title row shape.
    if day is None:
        m = re.search(r"(?im)^[\s>*_#-]*\**\s*Day\s+(\d+)\b\s*[:\-–—]?\s*(.*)$", text)
        if m:
            day = int(m.group(1))
            if not topic:
                topic = _clean_topic(m.group(2))
    # 4. "Day N" inside a heading — next to a "Block N" on the same line
    #    ("Block 5 — Day 6: Scope & Sequence"), or immediately followed by a
    #    heading separator ("Day 6:"). A prose sentence ("continues on Day 2 last
    #    week") has neither the Block pairing nor the trailing separator, so it is
    #    still ignored — that mis-filing was the whole point of the anchoring.
    if day is None:
        m = (re.search(r"(?im)^[^\n]*\bBlock\s*\d+\b[^\n]*?\bDay\s+(\d+)\b\s*[:\-–—]?\s*(.*)$", text)
             or re.search(r"(?im)\bDay\s+(\d+)\s*[:\-–—]\s*([^\n|]*)", text))
        if m:
            day = int(m.group(1))
            if not topic:
                topic = _clean_topic(m.group(2))

    if not topic:
        tm = re.search(r"(?im)^[\s>*_-]*\**\s*Topic\s*\**\s*[:\-]\s*\**\s*(.+?)\s*\**\s*$", text)
        if tm:
            topic = _clean_topic(tm.group(1))
    return day, topic


def _detect_module(text: str) -> Tuple[Optional[int], str]:
    """Anchored (module_number, topic) for a module-based Outline. Same discipline
    as _detect_day_topic: a "Module N" heading or label line, or "Module N:"
    followed by a separator — never a "Module N" buried in a prose sentence."""
    num: Optional[int] = None
    topic = ""
    # A "Module N" / "Module Number: N" heading or label at the start of a line.
    m = re.search(r"(?im)^[\s>*_#-]*\**\s*Module(?:\s*Number)?\s*\**\s*[:\-]?\s*\**\s*(\d+)\b\s*[:\-–—]?\s*(.*)$", text)
    if m:
        num = int(m.group(1))
        topic = _clean_topic(m.group(2))
    # "Module N:" with a heading separator anywhere (e.g. "Block 5 — Module 2: …").
    if num is None:
        m = re.search(r"(?im)\bModule\s+(\d+)\s*[:\-–—]\s*([^\n|]*)", text)
        if m:
            num = int(m.group(1))
            topic = _clean_topic(m.group(2))
    if not topic:
        tm = re.search(r"(?im)^[\s>*_-]*\**\s*Topic\s*\**\s*[:\-]\s*\**\s*(.+?)\s*\**\s*$", text)
        if tm:
            topic = _clean_topic(tm.group(1))
    return num, topic


def _looks_like_dlu(text: str) -> bool:
    """Would this markdown render as the DLU day accordions? Reuses the canonical
    detector the frontend/backend already agree on (``### DLU Outline`` header or
    ≥2 of the five part markers)."""
    return is_dlu_blueprint(SimpleNamespace(full_content=text or ""))


def _is_structured_dlu(text: str) -> bool:
    """Stricter than _looks_like_dlu: is the extracted text ALREADY the numbered
    DLU Outline markdown, laid out on its own lines the way the frontend parser
    reads it (so it can be passed through with no LLM)?

    Every signal is line-anchored. A blob with the markers collapsed onto one line
    fails here on purpose and drops to the LLM restructure tier, which rebuilds the
    line structure — the old substring check let such a blob pass through and the
    accordions never rendered."""
    if re.search(r"(?im)^\s{0,3}#{2,4}\s*dlu\s+outline\b", text or ""):
        return True
    bold_parts = re.findall(
        r"(?im)^\s*(?:\d+\.\s*)?\*\*\s*(?:today'?s mission|learn it(?:\s*\(review content\))?|"
        r"quick check|up next(?: in class)?|day reflection)\s*\*\*",
        text or "",
    )
    return len(bold_parts) >= 3


def _stamp_title(body: str, day: Optional[int], topic: str) -> str:
    """Force the canonical ``# DLU Outline - Day N: <topic>`` title onto *body*,
    replacing any leading title the source/LLM already carried.

    Stamping (rather than only prepending-if-absent) guarantees the document's
    stated day equals the day it is actually filed under — the LLM can echo a day
    it read from a passing mention, and we must never let the header disagree with
    the resolved day."""
    b = re.sub(r"(?is)^\s*#\s*DLU\s+Outline[^\n]*\n?", "", (body or "").strip(), count=1).strip()
    label = f"Day {day}" if day else "Day"
    title = f"# DLU Outline - {label}: {topic}".rstrip(": ").strip()
    return f"{title}\n\n{b}" if b else title


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


class _RestructureTruncated(RuntimeError):
    """The restructure reply hit the model's output cap. Distinct from a generic
    error so the caller can RETRY on a larger-output model before giving up — a
    16k-ceiling model (e.g. the default) truncates a big Outline that a 64k model
    handles whole."""


def _high_output_model(exclude: str = "") -> Optional[str]:
    """The catalog model with the largest output budget (ties broken toward a
    'structured' model), for retrying a restructure the selected model truncated.
    Excludes the model already tried. None if the catalog can't be read."""
    try:
        from promptops_app.core.models import MODEL_CATALOG, resolve_model
        excluded = resolve_model(exclude).display_name if exclude else ""
        candidates = [m for m in MODEL_CATALOG if m.display_name != excluded]
        if not candidates:
            return None
        best = max(candidates, key=lambda m: (
            m.max_output_tokens, "structured" in tuple(getattr(m, "tags", ()) or ())))
        return best.display_name
    except Exception:
        return None


def _llm_restructure(content: str, *, model_choice: str, usage_ctx=None) -> str:
    """Ask the LLM to reorganize *content* into DLU Outline markdown, losslessly.
    Raises ``_RestructureTruncated`` if the reply is cut off at the output cap, and
    a generic error otherwise, so the caller can escalate or fall back rather than
    persist a half-empty document."""
    from promptops_app.services.llm_service import generate_with_metadata

    user_prompt = _RESTRUCTURE_USER.format(content=content)
    result = generate_with_metadata(
        model_choice, _RESTRUCTURE_SYSTEM, user_prompt,
        usage_ctx=usage_ctx, max_tokens=_LLM_MAX_OUTPUT_TOKENS,
    )
    if result.status == "error":
        raise RuntimeError(f"LLM restructure failed: {result.error_type}")
    # A reply cut off at the output cap (LLMResult.truncated, from the provider's
    # length/max_tokens stop reason) is missing content — and this path stores the
    # reply AS the document, so a half-finished Outline would silently drop material.
    # generate_with_metadata caps max_tokens to the model's own ceiling, so this
    # fires when the SELECTED model's ceiling is the bottleneck — the caller then
    # retries on a larger-output model.
    if getattr(result, "truncated", False):
        raise _RestructureTruncated("LLM restructure truncated (hit the output cap)")
    return result.text or ""


def _restructure_with_retry(content: str, *, model_choice: str, usage_ctx, warnings, filename: str) -> Optional[str]:
    """Restructure into DLU markdown, escalating to a larger-output model when the
    selected one truncates. Returns the model's reply (which the caller validates),
    or None on hard failure — in which case a user-facing warning is appended and
    the caller falls back to a lossless raw wrap."""
    try:
        return _llm_restructure(content, model_choice=model_choice, usage_ctx=usage_ctx)
    except _RestructureTruncated:
        big = _high_output_model(exclude=model_choice)
        if big:
            _log.info("outline_import restructure truncated on %r — retrying on larger-output "
                      "model %r file=%r", model_choice, big, filename)
            try:
                return _llm_restructure(content, model_choice=big, usage_ctx=usage_ctx)
            except Exception as exc:
                _log.warning("outline_import restructure retry failed/truncated on %r file=%r "
                             "error=%s — raw fallback", big, filename, exc)
        else:
            _log.warning("outline_import no larger-output model available to retry file=%r", filename)
        warnings.append(
            "This Outline was too large to auto-structure into the parts, so its full "
            "content was imported as a single section.")
        return None
    except Exception as exc:
        _log.warning("outline_import llm restructure failed file=%r error=%s — raw fallback",
                     filename, exc)
        warnings.append(
            "Automatic Outline structuring was unavailable; the full content was "
            "imported as a single section.")
        return None


def _raw_body(text: str) -> str:
    """Last-resort body: the faithful extracted text under one heading. Renders as
    a single 'Imported Content' section (not the DLU accordions) but loses nothing.
    The caller stamps the title via _stamp_title."""
    return f"## Imported Content\n\n{(text or '').strip()}"


# --------------------------------------------------------------------------- #
# Title / filename derivation
# --------------------------------------------------------------------------- #
def _filename_stem(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    return _slug(stem.replace("_", " ").replace("-", " ")) or "Imported Outline"


def _day_from_filename(filename: str) -> Optional[int]:
    """A day named in the file's own name, e.g. 'Block_5_Day_6_Scope.docx' → 6.
    A deliberate, reliable signal, used only when the content surfaces no day in a
    recognizable heading."""
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    m = re.search(r"(?i)(?:^|[^a-z0-9])day[ _-]?(\d+)", stem)
    return int(m.group(1)) if m else None


def _module_from_filename(filename: str) -> Optional[int]:
    """A module named in the file's own name, e.g. 'Module_2_Outline.docx' → 2
    (also 'Mod2'). Fallback when the content has no module heading."""
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    m = re.search(r"(?i)(?:^|[^a-z0-9])mod(?:ule)?[ _-]?(\d+)", stem)
    return int(m.group(1)) if m else None


def _build_title(document_title: str, kind: str, unit: int, topic: str, stem: str) -> str:
    """The blueprint title. It must read '<Day|Module> N: …' so the unit stays
    recoverable from it — the router matches an existing Outline for that unit by
    the prefix (to add a new version instead of a duplicate) and the Generate
    dropdown reads the unit from it."""
    word = "Day" if kind == "day" else "Module"
    custom = _slug(document_title)
    if custom:
        # Respect the user's label, but keep a "<Day|Module> N:" prefix so
        # versioning and unit-recovery still work; an already-prefixed title stays.
        if not re.match(r"(?i)^\s*(?:day|module)\s+\d+\b", custom):
            return f"{word} {unit}: {custom}"
        return custom
    if topic:
        return f"{word} {unit}: {topic} Blueprint"
    return f"{word} {unit} Blueprint"


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def normalize_import(filename: str, data: bytes, *,
                     document_title: str = "",
                     hint_kind: Optional[str] = None,
                     hint_number: Optional[int] = None,
                     model_choice: str = "GPT-5.6 Terra", usage_ctx=None) -> OutlineImportResult:
    """Extract *data* and normalize it into an Outline — either a DLU **day**
    Outline (five-part accordions) or a **module** Outline (freeform sections).

    The kind and unit are decided from the FILE (a day/module anchored to a
    heading, label, title, or the filename); ``hint_kind``/``hint_number`` are the
    dropdown selection, used only to disambiguate or when the file names no unit.
    An LLM-inferred number is never used — the model can echo a unit from a passing
    mention and misfile the Outline. ``model_choice``/``usage_ctx`` are used only
    on the day-Outline LLM restructure path.
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

    # ---- Resolve day-vs-module and the unit number, BEFORE any LLM call ------
    file_day, day_topic = _detect_day_topic(flat_text)
    if file_day is None:
        file_day = _day_from_filename(filename)
    file_module, mod_topic = _detect_module(flat_text)
    if file_module is None:
        file_module = _module_from_filename(filename)

    hk = (hint_kind or "").lower()
    hn = int(hint_number) if hint_number is not None else None

    # The FILE decides day-vs-module when it clearly indicates one; the dropdown
    # hint only disambiguates when the file shows both, or supplies the kind when
    # the file names neither.
    if file_day is not None and file_module is None:
        kind, file_unit, topic = "day", file_day, day_topic
    elif file_module is not None and file_day is None:
        kind, file_unit, topic = "module", file_module, mod_topic
    elif file_day is not None and file_module is not None:
        if hk == "module":
            kind, file_unit, topic = "module", file_module, mod_topic
        else:
            kind, file_unit, topic = "day", file_day, day_topic
    elif hk in ("day", "module") and hn is not None:
        kind, file_unit, topic = hk, None, ""
    else:
        raise ValueError(
            "We couldn't tell which day or module this Outline is for. Please select "
            "it from the dropdown and upload the file again."
        )

    # Same-kind file/dropdown disagreement → refuse rather than misfile.
    if file_unit is not None and hn is not None and hk == kind and file_unit != hn:
        word = "Day" if kind == "day" else "Module"
        raise ValueError(
            f"This file looks like {word} {file_unit}, but {word} {hn} is selected. "
            f"Please confirm which {word.lower()} this Outline is for and try again."
        )

    unit = file_unit if file_unit is not None else hn
    if unit is None:
        raise ValueError(
            "We couldn't tell which day or module this Outline is for. Please select "
            "it from the dropdown and upload the file again."
        )

    # ---- Build the content ---------------------------------------------------
    if kind == "module":
        # Module Outlines have no fixed part-shape (module → lessons), so preserve
        # the extracted markdown as-is — lossless, no LLM. It renders through the
        # standard "## section" parser like a generated module blueprint.
        raw_output = flat_text.strip()
        method = "module"
        is_dlu = False
    else:
        # Day (DLU) Outline — structure into the five-part shape.
        if _is_structured_dlu(flat_text):
            body = flat_text
            method = "passthrough"
        elif len(flat_text) > _LLM_INPUT_CHAR_CAP:
            warnings.append(
                "The file was too large to auto-structure into the Outline layout, so "
                "its full extracted content was imported as a single section."
            )
            _log.warning("outline_import oversize llm bypass file=%r chars=%d",
                         filename, len(flat_text))
            body = _raw_body(flat_text)
            method = "raw_fallback"
        else:
            # Reorganize into the five-part shape, escalating to a larger-output
            # model if the selected one truncates on a big file (see
            # _restructure_with_retry). Warnings for hard failures are appended there.
            restructured = _restructure_with_retry(
                flat_text, model_choice=model_choice, usage_ctx=usage_ctx,
                warnings=warnings, filename=filename,
            )
            if restructured is not None and _looks_like_dlu(restructured):
                body = restructured
                method = "llm_restructure"
                # Take a nicer topic from the restructured title if we have none —
                # but NOT the day (see the unit-resolution note above).
                _, r_topic = _detect_day_topic(restructured)
                if r_topic and not topic:
                    topic = r_topic
            else:
                if restructured is not None:
                    # The model replied but not in the recognizable shape.
                    warnings.append(
                        "Automatic Outline structuring did not apply cleanly; the full "
                        "content was imported as a single section."
                    )
                body = _raw_body(flat_text)
                method = "raw_fallback"
        # Stamp the title to the RESOLVED day so the stated day always matches the
        # day it is filed under. Only structured tiers render as the accordions.
        raw_output = _stamp_title(body, unit, topic)
        is_dlu = (method != "raw_fallback")

    parsed_sections = parse_sections_from_text(raw_output)
    derived_title = _build_title(document_title, kind, unit, topic, stem)

    return OutlineImportResult(
        raw_output=raw_output,
        sections=parsed_sections,
        derived_title=derived_title,
        kind=kind,
        unit_number=unit,
        file_unit=file_unit,
        topic=topic,
        method=method,
        is_dlu=is_dlu,
        warnings=warnings,
    )
