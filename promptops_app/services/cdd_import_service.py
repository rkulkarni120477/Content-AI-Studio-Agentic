"""Import an existing Blueprint / DLU CDD file into the canonical CDD shape.

The user uploads a Blueprint they already have (Excel, DOCX or PDF) and it must
appear in "Your Title Design Documents" exactly as if it had been generated from
scratch — same worksheet layout, every piece of content present, and pinned as
active. This module is the "extract → normalize" half of that; persistence and
pinning reuse ``block_wide_service.persist_cdd_and_respond`` unchanged, so an
imported CDD is byte-for-byte a normal CDD in the same tables.

The one thing that makes the display identical for free: a generated DLU CDD is
just markdown whose sections are ``## WORKSHEET N: TITLE`` headings (see
block_wide_service._render_worksheets). ``cdd_parser.is_dlu_cdd`` /
``CddContentView`` detect that shape by heading pattern alone — so if this module
emits the same shape, the screen renders it as worksheets with no renderer change.

Two fidelity tiers, chosen per file (see normalize_import):
  * DETERMINISTIC — Excel (and structured DOCX) map cleanly to worksheets with no
    LLM in the loop, so nothing can be dropped or reworded. This is the primary
    path and the one Excel uploads take.
  * LLM RESTRUCTURE — unstructured DOCX/PDF/text is reorganized into the worksheet
    shape by an LLM under a strict "preserve every value verbatim, never omit"
    contract. Used only when no deterministic structure is present.
"""
from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from promptops_app.parsers.cdd_parser import is_dlu_cdd, parse_sections_from_text

_log = logging.getLogger(__name__)

#: Extensions we accept. Legacy binary .doc/.ppt are excluded on purpose — the
#: same limitation file_parser.py already documents; ask the user to re-save.
SUPPORTED_EXTS = (".xlsx", ".xls", ".docx", ".pdf")

#: Input size above which a single LLM restructure call risks silently truncating
#: (and therefore dropping) content. Past this we keep the deterministic fallback
#: rather than an LLM call that might not round-trip losslessly. Deliberately well
#: under the model's context so the prompt scaffolding + output headroom fit too.
_LLM_INPUT_CHAR_CAP = 180_000

#: Output ceiling for the restructure call. A full day-by-day table is large
#: (~18k tokens for a 20-day block), so the historical default cap would truncate
#: it; this gives the reorganized document room to come back whole.
_LLM_MAX_OUTPUT_TOKENS = 16_000


# --------------------------------------------------------------------------- #
# Canonical worksheet titles (mirror block_wide_service._WORKSHEET_TITLES).
# Kept as a local copy rather than imported so an import never depends on the
# generation pipeline's internals — but the strings must stay in step so an
# imported CDD reads identically to a generated one.
# --------------------------------------------------------------------------- #
_CANON_TITLES = {
    "block overview": "BLOCK OVERVIEW",
    "source file inventory": "SOURCE FILE INVENTORY",
    "acs code registry": "ACS CODE REGISTRY",
    "acs registry": "ACS CODE REGISTRY",
    "day-by-day map": "DAY-BY-DAY MAP",
    "day by day map": "DAY-BY-DAY MAP",
    "day-by-day": "DAY-BY-DAY MAP",
    "patterns & design notes": "PATTERNS & DESIGN NOTES",
    "patterns and design notes": "PATTERNS & DESIGN NOTES",
    "patterns notes": "PATTERNS & DESIGN NOTES",
    "patterns": "PATTERNS & DESIGN NOTES",
}

#: The two worksheets that read as label/value in a generated CDD, so a matching
#: 2-column sheet is rendered as "- **Label:** value" bullets rather than a table.
_KV_TITLES = {"BLOCK OVERVIEW", "PATTERNS & DESIGN NOTES"}


@dataclass
class ImportResult:
    """Everything the endpoint needs to persist an imported CDD as a normal CDD."""
    raw_output: str                       # worksheet markdown → CDDVersion.full_content
    sections: dict                        # parse_sections_from_text(raw_output)
    derived_title: str                    # document title (filename/heading fallback)
    derived_block: str                    # block / course_title
    method: str                           # "xlsx" | "docx" | "llm_restructure" | ...
    is_dlu: bool                          # whether it renders as DLU worksheets
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Small markdown helpers
# --------------------------------------------------------------------------- #
def _cell(text: str) -> str:
    """Sanitize one markdown table cell: a raw '|' or newline breaks the row (and
    can truncate every row after it in a strict renderer). Same discipline as
    block_wide_service._cell, but empty stays empty here (import preserves blanks
    faithfully rather than substituting an em dash)."""
    return " ".join((text or "").split()).replace("|", "/")


def _rows_to_markdown_table(rows: List[List[str]]) -> str:
    """A 2-D grid → a GitHub-flavored markdown table (first row is the header).

    Every row is padded to the widest row so the column count is uniform — an
    uneven grid otherwise renders as a broken table and can hide trailing cells.
    """
    grid = [[_cell(c) for c in r] for r in rows if any((c or "").strip() for c in r)]
    if not grid:
        return ""
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]
    out = ["| " + " | ".join(grid[0]) + " |", "|" + "---|" * width]
    for r in grid[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def _rows_to_kv_bullets(rows: List[List[str]]) -> str:
    """A 2-column grid → "- **Label:** value" bullets (Block Overview / Patterns
    shape). Rows with an empty first cell are dropped — they carry no label to
    hang the value on and would render as a stray bullet."""
    out = []
    for r in rows:
        label = _cell(r[0]) if r else ""
        value = _cell(r[1]) if len(r) > 1 else ""
        if not label:
            continue
        out.append(f"- **{label}:** {value}".rstrip())
    return "\n".join(out)


def _canonical_title(name: str) -> str:
    """Map a sheet/heading name to its canonical worksheet title when it matches
    one, else the name upper-cased. A leading ordinal like "4_" or "1 " (how the
    AIM reference workbook names its sheets) is stripped before matching."""
    base = re.sub(r"^\s*\d+\s*[._)-]\s*", "", (name or "").strip())
    key = re.sub(r"\s+", " ", base).strip().lower()
    return _CANON_TITLES.get(key, base.upper() or "WORKSHEET")


def _slug(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


# --------------------------------------------------------------------------- #
# Extraction — each returns a list of (worksheet_title, body_markdown) sections,
# or a raw text blob for the LLM path.
# --------------------------------------------------------------------------- #
def _trim_grid(rows: List[List[str]]) -> List[List[str]]:
    """Drop fully-empty leading/trailing rows and trailing empty columns, and
    strip the decoration our own XLSX export adds (a title row echoing the sheet
    name, the "Generated by…" line, the brand footer) so re-importing an export
    round-trips cleanly. Interior blank rows are kept — they can be meaningful
    spacing in a hand-built workbook."""
    cleaned = []
    for r in rows:
        joined = " ".join(c for c in r if c).strip()
        # Brand/meta rows written by xlsx_exporter — never real content.
        if "Content AI Studio" in joined:
            continue
        cleaned.append(r)
    # Trim leading/trailing all-empty rows.
    while cleaned and not any((c or "").strip() for c in cleaned[0]):
        cleaned.pop(0)
    while cleaned and not any((c or "").strip() for c in cleaned[-1]):
        cleaned.pop()
    if not cleaned:
        return []
    # Trim trailing all-empty columns.
    width = max(len(r) for r in cleaned)
    cleaned = [r + [""] * (width - len(r)) for r in cleaned]
    last = width
    while last > 0 and all(not (r[last - 1] or "").strip() for r in cleaned):
        last -= 1
    return [r[:last] for r in cleaned]


def _extract_xlsx_pandas(data: bytes) -> List[Tuple[str, List[List[str]]]]:
    """Pandas fallback for workbooks openpyxl can't open (notably legacy .xls,
    which needs the xlrd engine). Reads every sheet, keeps the header row, and
    stringifies cells; NaN becomes an empty cell so blanks stay blank."""
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
    order. read_only + data_only so a large workbook streams and formulas come
    back as their last-computed value rather than the formula text. Falls back to
    pandas for files openpyxl rejects (e.g. legacy .xls)."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        _log.info("cdd_import openpyxl could not open workbook (%s) — trying pandas", exc)
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


def _sections_from_xlsx(sheets: List[Tuple[str, List[List[str]]]]) -> List[Tuple[str, str]]:
    """Each sheet → one worksheet section. A 2-column sheet whose name maps to a
    key/value worksheet becomes bullets; everything else becomes a markdown table
    so every cell survives verbatim."""
    out = []
    for name, grid in sheets:
        title = _canonical_title(name)
        width = max((len(r) for r in grid), default=0)
        if title in _KV_TITLES and width <= 2:
            body = _rows_to_kv_bullets(grid)
        else:
            body = _rows_to_markdown_table(grid)
        out.append((title, body))
    return out


def _extract_docx(data: bytes) -> Tuple[List[Tuple[str, str]], str]:
    """DOCX → (deterministic sections, flat markdown).

    Walks the body in document order so paragraphs and tables keep their relative
    position. A paragraph that reads as a section boundary (a Heading style, or a
    "Worksheet N"/canonical-name line) opens a new section; tables become markdown
    tables; other paragraphs are body text. Returns both the structured sections
    (used when at least two are found) and the full flat markdown (the LLM path's
    input when structure is too thin to trust)."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from promptops_app.parsers.cdd_parser import _worksheet_mark

    doc = Document(io.BytesIO(data))
    sections: List[Tuple[str, List[str]]] = []
    flat: List[str] = []
    current_title: Optional[str] = None
    current_body: List[str] = []

    def _open(title: str):
        nonlocal current_title, current_body
        if current_title is not None:
            sections.append((current_title, current_body))
        current_title = title
        current_body = []

    for child in doc.element.body.iterchildren():
        tag = child.tag
        if tag.endswith("}p"):
            para = Paragraph(child, doc)
            text = _slug(para.text)
            if not text:
                continue
            style = (para.style.name if para.style else "") or ""
            mark = _worksheet_mark(text)
            canon = _canonical_title(text)
            is_boundary = bool(mark) or (
                style.lower().startswith("heading")
                and (canon in _CANON_TITLES.values() or len(text) <= 80)
            )
            if is_boundary:
                title = _canonical_title(mark["title"] if mark and mark["title"] else text)
                _open(title)
                flat.append(f"\n## {title}\n")
            else:
                if current_title is not None:
                    current_body.append(text)
                flat.append(text)
        elif tag.endswith("}tbl"):
            table = Table(child, doc)
            rows = [[c.text for c in row.cells] for row in table.rows]
            md = _rows_to_markdown_table(rows)
            if md:
                if current_title is not None:
                    current_body.append(md)
                flat.append(md)

    if current_title is not None:
        sections.append((current_title, current_body))

    structured = [(t, "\n".join(b).strip()) for t, b in sections if "\n".join(b).strip()]
    return structured, "\n".join(flat).strip()


def _extract_pdf(data: bytes) -> str:
    """PDF → plain text, page by page. PDFs carry no reliable table/section
    structure, so this feeds the LLM restructure path rather than a deterministic
    mapping."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        txt = page.extract_text() or ""
        if txt.strip():
            parts.append(txt)
    return "\n\n".join(parts).strip()


# --------------------------------------------------------------------------- #
# Rendering — worksheet markdown identical in shape to a generated DLU CDD
# --------------------------------------------------------------------------- #
def _render_worksheets(course_title: str, block: str, sections: List[Tuple[str, str]]) -> str:
    """(title, body) sections → the "## WORKSHEET N: TITLE" markdown that
    is_dlu_cdd / CddContentView already understand. Intro mirrors
    block_wide_service.render_cdd_markdown so an imported CDD's header reads the
    same as a generated one."""
    out = [f"# Course Design Document — {course_title}"]
    if block:
        out.append(f"**Block:** {block}")
    for i, (title, body) in enumerate(sections, start=1):
        out += ["", f"## WORKSHEET {i}: {title.upper()}", "", (body or "").strip()]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# LLM restructure (unstructured DOCX/PDF/text)
# --------------------------------------------------------------------------- #
_RESTRUCTURE_SYSTEM = (
    "You are a document-structuring assistant for an eLearning platform. You convert an "
    "uploaded Course Design Document / Block Blueprint into Content AI Studio's canonical "
    "worksheet layout.\n\n"
    "ABSOLUTE RULES:\n"
    "1. Preserve every piece of content EXACTLY — do not summarize, rewrite, paraphrase, "
    "invent, or omit any value, number, ACS code, name, date, or table row. You only "
    "reorganize and label existing content.\n"
    "2. Keep every table as a GitHub-flavored markdown table with ALL rows and ALL columns "
    "intact. Never collapse or truncate a day-by-day table.\n"
    "3. Output GitHub-flavored Markdown only — no commentary, no explanations, no code fences."
)

_RESTRUCTURE_USER = """Convert the uploaded blueprint content below into this exact structure. Use "## WORKSHEET N: TITLE" headings verbatim.

# Course Design Document — {course_title}
**Block:** {block}

## WORKSHEET 1: BLOCK OVERVIEW
Key facts as `- **Label:** value` bullets.

## WORKSHEET 2: SOURCE FILE INVENTORY
A markdown table, if the source has one.

## WORKSHEET 3: ACS CODE REGISTRY
A markdown table, if the source has one.

## WORKSHEET 4: DAY-BY-DAY MAP
A markdown table with one row per day — keep EVERY row and EVERY column.

## WORKSHEET 5: PATTERNS & DESIGN NOTES
Bullets.

If the source has content that does not fit these five worksheets, add more "## WORKSHEET N: ..." sections so that NOTHING from the source is dropped. Omit a worksheet only if the source truly has no content for it.

UPLOADED CONTENT:
---
{content}
---
"""


def _llm_restructure(content: str, course_title: str, block: str, *,
                     model_choice: str, usage_ctx=None) -> str:
    """Ask the LLM to reorganize *content* into worksheet markdown, losslessly.

    Raises on an LLM error so the caller can fall back to a deterministic wrap
    rather than persist a half-empty document.
    """
    from promptops_app.services.llm_service import generate_with_metadata

    user_prompt = _RESTRUCTURE_USER.format(
        course_title=course_title or "Imported Blueprint",
        block=block or "",
        content=content,
    )
    result = generate_with_metadata(
        model_choice, _RESTRUCTURE_SYSTEM, user_prompt,
        usage_ctx=usage_ctx, max_tokens=_LLM_MAX_OUTPUT_TOKENS,
    )
    if result.status == "error":
        raise RuntimeError(f"LLM restructure failed: {result.error_type}")
    return result.text or ""


# --------------------------------------------------------------------------- #
# Title / block derivation
# --------------------------------------------------------------------------- #
def _filename_stem(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    return _slug(stem.replace("_", " ").replace("-", " ")) or "Imported Blueprint"


def _derive_block(sections: List[Tuple[str, str]], flat_text: str, fallback: str) -> str:
    """Best-effort block/course-title from the content: a "Block: X" line in the
    Block Overview worksheet or anywhere in the text, else the fallback."""
    haystacks = [b for _, b in sections] + [flat_text or ""]
    for body in haystacks:
        m = re.search(r"(?im)^[\s>*-]*\**\s*Block\s*\**\s*[:\-]\s*\**\s*(.+?)\s*\**\s*$", body)
        if m:
            candidate = _slug(m.group(1))
            if candidate:
                return candidate
    return fallback


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def normalize_import(filename: str, data: bytes, *,
                     course_title: str = "", document_title: str = "",
                     model_choice: str = "GPT-5.4", usage_ctx=None) -> ImportResult:
    """Extract *data* and normalize it into the canonical CDD worksheet shape.

    ``course_title`` / ``document_title`` are the user's edits from the form (may
    be blank, in which case they are derived from the file). ``model_choice`` /
    ``usage_ctx`` are only used on the LLM restructure path.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in SUPPORTED_EXTS:
        raise ValueError(
            f"Unsupported file type '{ext or filename}'. Upload an Excel (.xlsx/.xls), "
            "Word (.docx) or PDF (.pdf) blueprint."
        )

    stem = _filename_stem(filename)
    warnings: List[str] = []
    sections: List[Tuple[str, str]] = []
    flat_text = ""
    method = ""

    if ext in (".xlsx", ".xls"):
        sheets = _extract_xlsx(data)
        if not sheets:
            raise ValueError("No readable content found in the uploaded workbook.")
        sections = _sections_from_xlsx(sheets)
        method = "xlsx"
    elif ext == ".docx":
        structured, flat_text = _extract_docx(data)
        # Two or more recognizable sections is enough structure to trust the
        # deterministic mapping; anything thinner goes to the LLM so a loosely
        # formatted document still lands in worksheets.
        if len(structured) >= 2:
            sections = structured
            method = "docx"
        else:
            method = "llm_restructure"
    else:  # .pdf
        flat_text = _extract_pdf(data)
        if not flat_text:
            raise ValueError("No extractable text found in the uploaded PDF.")
        method = "llm_restructure"

    block = _slug(course_title) or _derive_block(sections, flat_text, stem)

    if method == "llm_restructure":
        source_text = flat_text
        if len(source_text) > _LLM_INPUT_CHAR_CAP:
            # An oversized single call risks silent truncation — dropping content,
            # which the whole feature promises not to do. Keep the extracted text
            # whole in one worksheet instead; it still renders and nothing is lost.
            warnings.append(
                "The document was too large to auto-structure into worksheets, so "
                "its full extracted content was imported as a single section."
            )
            _log.warning("cdd_import oversize llm bypass file=%r chars=%d",
                         filename, len(source_text))
            sections = [("IMPORTED CONTENT", source_text)]
            raw_output = _render_worksheets(block, block if course_title else "", sections)
            method = "raw_fallback"
        else:
            try:
                raw_output = _llm_restructure(
                    source_text, block, block if course_title else "",
                    model_choice=model_choice, usage_ctx=usage_ctx,
                )
                if not is_dlu_cdd(raw_output):
                    # The model didn't produce the worksheet shape — don't ship an
                    # unrecognized blob; wrap the faithful extracted text instead.
                    warnings.append(
                        "Automatic worksheet structuring did not apply cleanly; the "
                        "full content was imported as a single section."
                    )
                    sections = [("IMPORTED CONTENT", source_text)]
                    raw_output = _render_worksheets(block, "", sections)
                    method = "raw_fallback"
            except Exception as exc:
                _log.warning("cdd_import llm restructure failed file=%r error=%s — "
                             "falling back to raw wrap", filename, exc)
                warnings.append(
                    "Automatic worksheet structuring was unavailable; the full "
                    "content was imported as a single section."
                )
                sections = [("IMPORTED CONTENT", source_text)]
                raw_output = _render_worksheets(block, "", sections)
                method = "raw_fallback"
    else:
        raw_output = _render_worksheets(block, block, sections)

    parsed_sections = parse_sections_from_text(raw_output)
    derived_title = _slug(document_title) or f"{block} — CDD"

    return ImportResult(
        raw_output=raw_output,
        sections=parsed_sections,
        derived_title=derived_title,
        derived_block=block,
        method=method,
        is_dlu=is_dlu_cdd(raw_output),
        warnings=warnings,
    )
