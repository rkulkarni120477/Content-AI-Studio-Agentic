"""DOCX export engine — five templates.

Templates
---------
default       — Compact review-ready document.
storyboard    — Scene-card table: Scene # | Title | Narration | Visual Cue.
teacher_guide — Learner content + lined Teaching Notes section per block.
quiz_bank     — Numbered Q&A table extracted from content.
client        — Clean minimal formatting for client delivery.

All functions return a BytesIO buffer (binary DOCX data, seeked to 0).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from datetime import datetime, timezone
from io import BytesIO

from docx import Document as DocxDocument
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

_NOW = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
_BRAND = "Powered by Content AI Studio · AI-Generated eLearning Content"

# --- Palette (hex strings for OOXML attributes, RGBColor for python-docx) ----
_WHITE_HEX = "FFFFFF"
_NAVY_HEX = "17365D"
_STEEL_BLUE_HEX = "2F5597"
_ROW_LABEL_FILL_HEX = "D9EAF7"
_CELL_FILL_HEX = _WHITE_HEX
_DIVIDER_HEX = "B7B7B7"
_FOOTER_GREY_HEX = "808080"

_NAVY = RGBColor.from_string(_NAVY_HEX)
_ON_NAVY = RGBColor.from_string(_WHITE_HEX)
_FOOTER_GREY = RGBColor.from_string(_FOOTER_GREY_HEX)

# --- Typography (sizes in points) -------------------------------------------
_BODY_FONT = "Aptos"
_HEADING_FONT = "Aptos Display"
_CODE_FONT = "Consolas"

_BODY_SIZE = 9.5
_TITLE_SIZE = 20
_CODE_SIZE = 10
_FOOTER_SIZE = 9

# Heading level -> (point size, hex colour).
_HEADING_SPECS = {
    1: (15, _NAVY_HEX),
    2: (12, _STEEL_BLUE_HEX),
    3: (10.5, _NAVY_HEX),
    4: (10, _NAVY_HEX),
}

# --- Spacing (points unless noted) ------------------------------------------
_BODY_SPACE_AFTER = 5
_BODY_LINE_SPACING = 1.05
_TITLE_SPACE_BEFORE = 4
_TITLE_SPACE_AFTER = 10
_HEADING_SPACE_BEFORE = 10
_HEADING_SPACE_AFTER = 5
_LIST_SPACE_AFTER = 2
_CELL_SPACE_AFTER = 2
_DIVIDER_SPACE = 6
_QUOTE_INDENT_INCHES = 0.25

# --- Page setup (inches) ----------------------------------------------------
_PAGE_MARGIN_VERTICAL = 0.65
_PAGE_MARGIN_HORIZONTAL = 0.70
_HEADER_FOOTER_DISTANCE = 0.35

# --- Divider rule (OOXML border attribute values) ---------------------------
_DIVIDER_BORDER_STYLE = "single"
_DIVIDER_BORDER_SIZE = "4"   # eighths of a point
_DIVIDER_BORDER_SPACE = "1"  # points between the rule and the text

# --- Markdown / Word structure ----------------------------------------------
_FENCE = "```"
_MAX_LIST_LEVEL = 3        # built-in Word list styles stop at "List Bullet 3"
_MAX_HEADING_LEVEL = 4     # deeper Markdown headings collapse onto Heading 4
_LIST_INDENT_SPACES = 2    # leading spaces per Markdown nesting level
_TAB_WIDTH = 4

_BULLET_STYLE = "List Bullet"
_NUMBER_STYLE = "List Number"
_LIST_STYLE_NAMES = tuple(
    base if level == 1 else f"{base} {level}"
    for base in (_BULLET_STYLE, _NUMBER_STYLE)
    for level in range(1, _MAX_LIST_LEVEL + 1)
)
_PLAIN_TABLE_STYLE = "Normal Table"
_FALLBACK_TABLE_STYLE = "Table Grid"

# Fence languages holding prose rather than code, so they keep body styling.
_PROSE_FENCE_LANGUAGES = {"", "text", "plain", "plaintext", "markdown", "md"}

# A "| Field | Detail |" header carries no information, so it is dropped.
_GENERIC_TABLE_HEADER = ["field", "detail"]

# --- HTML detection ---------------------------------------------------------
# Tags the HTML pass understands or can safely unwrap. Anything else (e.g. a
# "<TBD>" placeholder) must not make a block look like HTML.
_HTML_TAGS = (
    "a|article|b|blockquote|br|code|div|em|h1|h2|h3|h4|h5|h6|hr|i|li|ol|p|pre|"
    "script|section|span|strong|style|sub|sup|table|tbody|td|th|thead|tr|u|ul"
)

# Prose such as "compare a<b and c>d" is tag-shaped under HTML5 rules ("and"
# and "c" read as boolean attributes), and feeding it to HTMLParser deletes the
# text. So a block only counts as HTML when it carries a closing tag, a bare
# opening/void tag, or an opening tag with real ``name=value`` attributes.
_HTML_TAG_RE = re.compile(
    rf"</(?:{_HTML_TAGS})\s*>"
    rf"|<(?:{_HTML_TAGS})\s*/?>"
    rf"|<(?:{_HTML_TAGS})\s+[^<>]*=[^<>]*>",
    re.I,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _HTMLToMarkdownish(HTMLParser):
    """Normalize generated HTML fragments into the Markdown subset rendered below."""

    _BLOCK_TAGS = {"p", "div", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.lists: list[dict[str, int | str]] = []
        self.table_rows: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.skip_depth = 0
        self.in_pre = False

    def _append(self, value: str) -> None:
        if self.skip_depth:
            return
        if self.cell is not None:
            self.cell.append(value)
        else:
            self.parts.append(value)

    def _newline(self) -> None:
        target = self.cell if self.cell is not None else self.parts
        if target and not target[-1].endswith("\n"):
            target.append("\n")

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._newline()
            self._append("#" * int(tag[1]) + " ")
        elif tag in self._BLOCK_TAGS:
            self._newline()
        elif tag == "br":
            self._append("\n")
        elif tag in {"strong", "b"}:
            self._append("**")
        elif tag in {"em", "i"}:
            self._append("*")
        elif tag == "code" and not self.in_pre:
            self._append("`")
        elif tag == "pre":
            self._newline()
            self.in_pre = True
        elif tag in {"ul", "ol"}:
            self._newline()
            self.lists.append({"tag": tag, "index": 0})
        elif tag == "li":
            self._newline()
            depth = max(0, len(self.lists) - 1)
            if self.lists and self.lists[-1]["tag"] == "ol":
                self.lists[-1]["index"] = int(self.lists[-1]["index"]) + 1
                marker = f"{self.lists[-1]['index']}. "
            else:
                marker = "- "
            self._append("  " * depth + marker)
        elif tag == "blockquote":
            self._newline()
            self._append("> ")
        elif tag == "hr":
            self._newline()
            self._append("---\n")
        elif tag == "table":
            self._newline()
            self.table_rows = []
        elif tag == "tr" and self.table_rows is not None:
            self.row = []
        elif tag in {"th", "td"} and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if re.fullmatch(r"h[1-6]", tag) or tag in self._BLOCK_TAGS:
            self._newline()
        elif tag in {"strong", "b"}:
            self._append("**")
        elif tag in {"em", "i"}:
            self._append("*")
        elif tag == "code" and not self.in_pre:
            self._append("`")
        elif tag == "pre":
            self.in_pre = False
            self._newline()
        elif tag == "li":
            self._newline()
        elif tag in {"ul", "ol"}:
            if self.lists:
                self.lists.pop()
            self._newline()
        elif tag in {"th", "td"} and self.cell is not None and self.row is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table_rows is not None:
            if self.row:
                self.table_rows.append(self.row)
            self.row = None
        elif tag == "table" and self.table_rows is not None:
            rows = self.table_rows
            self.table_rows = None
            if rows:
                width = max(len(row) for row in rows)
                rows = [row + [""] * (width - len(row)) for row in rows]
                self.parts.append("| " + " | ".join(rows[0]) + " |\n")
                self.parts.append("| " + " | ".join(["---"] * width) + " |\n")
                for row in rows[1:]:
                    self.parts.append("| " + " | ".join(row) + " |\n")
            self._newline()

    def handle_data(self, data: str) -> None:
        self._append(data)

    def text(self) -> str:
        return "".join(self.parts)


def _html_to_markdownish(text: str) -> str:
    """Convert embedded HTML to the same safe Markdown subset used by DOCX rendering."""
    if not text or not _HTML_TAG_RE.search(text):
        return text
    parser = _HTMLToMarkdownish()
    try:
        parser.feed(text)
        parser.close()
        return parser.text()
    except Exception:
        return text


_INLINE_RE = re.compile(
    r"(\*\*(.+?)\*\*|__(.+?)__|(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])|(?<![\w_])_([^_\n]+?)_(?![\w_])|`([^`\n]+?)`|\[([^\]]+)\]\(([^)]+)\))"
)


def _clean_plain_text(text: str) -> str:
    """Remove leftover formatting artifacts without changing normal punctuation."""
    return text.replace("**", "").replace("__", "").replace("```", "").replace("`", "")


def _add_inline_runs(paragraph, text: str) -> None:
    """Render common inline Markdown as native Word runs."""
    pos = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > pos:
            paragraph.add_run(_clean_plain_text(text[pos:match.start()]))
        token = match.group(0)
        if token.startswith(("**", "__")):
            run = paragraph.add_run(match.group(2) or match.group(3) or "")
            run.bold = True
        elif token.startswith("*") or token.startswith("_"):
            run = paragraph.add_run(match.group(4) or match.group(5) or "")
            run.italic = True
        elif token.startswith("`"):
            run = paragraph.add_run(match.group(6) or "")
            run.font.name = _CODE_FONT
        else:
            label, url = match.group(7) or "", match.group(8) or ""
            paragraph.add_run(f"{label} ({url})" if url else label)
        pos = match.end()
    if pos < len(text):
        paragraph.add_run(_clean_plain_text(text[pos:]))


def _list_style(doc: DocxDocument, base: str, indent: str) -> str:
    """Return the closest built-in Word list style for the Markdown indent."""
    level = min(_MAX_LIST_LEVEL, max(1, len(indent.expandtabs(_TAB_WIDTH)) // _LIST_INDENT_SPACES + 1))
    candidate = base if level == 1 else f"{base} {level}"
    return candidate if candidate in [style.name for style in doc.styles] else base


def _split_table_row(line: str) -> list[str]:
    line = line.strip().strip("|")
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line)]


def _is_table_separator(line: str) -> bool:
    # Without this pipe check a plain "---" rule reads as a one-column separator,
    # turning any preceding sentence that happens to contain "|" into a table.
    if "|" not in line:
        return False
    cells = _split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _add_table(doc: DocxDocument, rows: list[list[str]]) -> None:
    """Add a Markdown table as a compact native Word table."""
    if not rows:
        return

    generic_header = [cell.strip().lower() for cell in rows[0]] == _GENERIC_TABLE_HEADER
    display_rows = rows[1:] if generic_header else rows
    if not display_rows:
        return

    width = max(len(row) for row in display_rows)
    table = doc.add_table(rows=len(display_rows), cols=width)
    table.style = (
        _PLAIN_TABLE_STYLE
        if _PLAIN_TABLE_STYLE in [style.name for style in doc.styles]
        else _FALLBACK_TABLE_STYLE
    )
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    section = doc.sections[-1]
    available = section.page_width - section.left_margin - section.right_margin
    column_width = int(available / width)

    for row_index, values in enumerate(display_rows):
        row_pr = table.rows[row_index]._tr.get_or_add_trPr()
        row_pr.append(OxmlElement("w:cantSplit"))
        for col_index in range(width):
            cell = table.cell(row_index, col_index)
            cell.width = column_width
            cell.text = ""
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(_CELL_SPACE_AFTER)
            _add_inline_runs(paragraph, values[col_index] if col_index < len(values) else "")

            tc_pr = cell._tc.get_or_add_tcPr()
            shd = tc_pr.find(qn("w:shd"))
            if shd is None:
                shd = OxmlElement("w:shd")
                tc_pr.append(shd)

            meaningful_header = not generic_header and row_index == 0
            if meaningful_header:
                shd.set(qn("w:fill"), _NAVY_HEX)
                for run in paragraph.runs:
                    run.bold = True
                    run.font.color.rgb = _ON_NAVY
            elif col_index == 0:
                shd.set(qn("w:fill"), _ROW_LABEL_FILL_HEX)
                for run in paragraph.runs:
                    run.bold = True
                    run.font.color.rgb = _NAVY
            else:
                shd.set(qn("w:fill"), _CELL_FILL_HEX)


def _set_cell_markdown(cell, text: str) -> None:
    """Fill a cell with inline Markdown rendered as native runs, one line per paragraph.

    Templates that drop block text straight into a cell need this; assigning to
    ``cell.text`` would leave "**", "__" and backticks visible in the document.
    """
    cell.text = ""
    paragraph = cell.paragraphs[0]
    filled = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if filled:
            paragraph = cell.add_paragraph()
        _add_inline_runs(paragraph, line)
        filled = True


def _add_section_divider(doc: DocxDocument) -> None:
    """Render a Markdown horizontal rule as a clean Word section divider."""
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(_DIVIDER_SPACE)
    paragraph.paragraph_format.space_after = Pt(_DIVIDER_SPACE)
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), _DIVIDER_BORDER_STYLE)
    bottom.set(qn("w:sz"), _DIVIDER_BORDER_SIZE)
    bottom.set(qn("w:space"), _DIVIDER_BORDER_SPACE)
    bottom.set(qn("w:color"), _DIVIDER_HEX)
    borders.append(bottom)
    p_pr.append(borders)


def _configure_styles(doc: DocxDocument) -> None:
    """Apply compact, review-ready typography and spacing."""
    normal = doc.styles["Normal"]
    normal.font.name = _BODY_FONT
    normal.font.size = Pt(_BODY_SIZE)
    normal.paragraph_format.space_after = Pt(_BODY_SPACE_AFTER)
    normal.paragraph_format.line_spacing = _BODY_LINE_SPACING

    title = doc.styles["Title"]
    title.font.name = _HEADING_FONT
    title.font.size = Pt(_TITLE_SIZE)
    title.font.bold = True
    title.font.color.rgb = _NAVY
    title.paragraph_format.space_before = Pt(_TITLE_SPACE_BEFORE)
    title.paragraph_format.space_after = Pt(_TITLE_SPACE_AFTER)
    title_ppr = title.element.get_or_add_pPr()
    title_border = title_ppr.find(qn("w:pBdr"))
    if title_border is not None:
        title_ppr.remove(title_border)

    for level, (size, color) in _HEADING_SPECS.items():
        style = doc.styles[f"Heading {level}"]
        style.font.name = _HEADING_FONT
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(color)
        style.paragraph_format.space_before = Pt(_HEADING_SPACE_BEFORE)
        style.paragraph_format.space_after = Pt(_HEADING_SPACE_AFTER)
        style.paragraph_format.keep_with_next = True

    for style_name in _LIST_STYLE_NAMES:
        if style_name in [style.name for style in doc.styles]:
            style = doc.styles[style_name]
            style.font.name = _BODY_FONT
            style.font.size = Pt(_BODY_SIZE)
            style.paragraph_format.space_after = Pt(_LIST_SPACE_AFTER)


def _add_code_paragraph(doc: DocxDocument, text: str) -> None:
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    run.font.name = _CODE_FONT
    run.font.size = Pt(_CODE_SIZE)


def _add_md_lines(doc: DocxDocument, text: str) -> None:
    """Convert Markdown/HTML content into native Word structure and formatting."""
    parts = re.split(r"(```.*?```)", text, flags=re.DOTALL)
    lines = "".join(part if part.startswith("```") else _html_to_markdownish(part) for part in parts).splitlines()
    paragraph_lines: list[str] = []
    in_fence = False
    text_fence = False
    i = 0

    def flush_paragraph() -> None:
        if not paragraph_lines:
            return
        paragraph = doc.add_paragraph()
        _add_inline_runs(paragraph, " ".join(part.strip() for part in paragraph_lines if part.strip()))
        paragraph_lines.clear()

    while i < len(lines):
        raw = lines[i].rstrip()
        stripped = raw.strip()
        if stripped.startswith(_FENCE):
            flush_paragraph()
            if not in_fence:
                language = stripped[len(_FENCE):].strip().lower()
                text_fence = language in _PROSE_FENCE_LANGUAGES
                in_fence = True
            else:
                in_fence = False
                text_fence = False
            i += 1
            continue
        if in_fence:
            if stripped:
                if text_fence and re.match(r"^Section\s+\d+\b", stripped, re.I):
                    paragraph = doc.add_paragraph()
                    run = paragraph.add_run(stripped)
                    run.bold = True
                elif text_fence and re.match(r"^\s{2,}\S", raw):
                    paragraph = doc.add_paragraph(style=_list_style(doc, _BULLET_STYLE, raw[: len(raw) - len(raw.lstrip())]))
                    _add_inline_runs(paragraph, stripped)
                elif text_fence:
                    paragraph = doc.add_paragraph()
                    _add_inline_runs(paragraph, stripped)
                else:
                    _add_code_paragraph(doc, raw)
            i += 1
            continue
        if not stripped:
            flush_paragraph()
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        bullet = re.match(r"^(\s*)[-+*•]\s+(.+)$", raw)
        numbered = re.match(r"^(\s*)\d+[.)]\s+(.+)$", raw)
        horizontal = re.fullmatch(r"(?:-{3,}|\*{3,}|_{3,})", stripped)
        table_start = "|" in stripped and i + 1 < len(lines) and _is_table_separator(lines[i + 1])

        if heading:
            flush_paragraph()
            heading_text = heading.group(2).strip()
            if re.match(r"^Part\s+[IVXLC]+\b", heading_text, re.I):
                level = 1
            elif re.match(r"^[A-Z]\.\s+", heading_text):
                level = 2
            elif heading_text.lower().startswith("screen:"):
                level = 3
            else:
                level = min(_MAX_HEADING_LEVEL, len(heading.group(1)) + 1)
            paragraph = doc.add_heading("", level=level)
            _add_inline_runs(paragraph, heading_text)
        elif table_start:
            flush_paragraph()
            rows = [_split_table_row(raw)]
            i += 2  # skip the header row just consumed and its separator
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            _add_table(doc, rows)
            continue
        elif bullet:
            flush_paragraph()
            paragraph = doc.add_paragraph(style=_list_style(doc, _BULLET_STYLE, bullet.group(1)))
            _add_inline_runs(paragraph, bullet.group(2).strip())
        elif numbered:
            flush_paragraph()
            paragraph = doc.add_paragraph(style=_list_style(doc, _NUMBER_STYLE, numbered.group(1)))
            _add_inline_runs(paragraph, numbered.group(2).strip())
        elif horizontal:
            flush_paragraph()
            _add_section_divider(doc)
        elif stripped.startswith(">"):
            flush_paragraph()
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.left_indent = Inches(_QUOTE_INDENT_INCHES)
            _add_inline_runs(paragraph, stripped.lstrip("> "))
        else:
            paragraph_lines.append(stripped)
        i += 1
    flush_paragraph()


def _save(doc: DocxDocument) -> BytesIO:
    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def _brand_footer(doc: DocxDocument) -> None:
    doc.add_page_break()
    p = doc.add_paragraph(_BRAND)
    p.style = doc.styles["Subtitle"] if "Subtitle" in [s.name for s in doc.styles] else p.style
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER


# ---------------------------------------------------------------------------
# Template: default
# ---------------------------------------------------------------------------

def _default(topic: str, blocks: list[tuple[str, str]]) -> BytesIO:
    doc = DocxDocument()
    _configure_styles(doc)

    section = doc.sections[0]
    section.top_margin = Inches(_PAGE_MARGIN_VERTICAL)
    section.bottom_margin = Inches(_PAGE_MARGIN_VERTICAL)
    section.left_margin = Inches(_PAGE_MARGIN_HORIZONTAL)
    section.right_margin = Inches(_PAGE_MARGIN_HORIZONTAL)
    section.header_distance = Inches(_HEADER_FOOTER_DISTANCE)
    section.footer_distance = Inches(_HEADER_FOOTER_DISTANCE)

    title = doc.add_heading(topic, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    footer = section.footer.paragraphs[0]
    footer.text = topic
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if footer.runs:
        footer.runs[0].font.name = _BODY_FONT
        footer.runs[0].font.size = Pt(_FOOTER_SIZE)
        footer.runs[0].font.color.rgb = _FOOTER_GREY

    for index, (b_label, b_content) in enumerate(blocks):
        if index and re.match(r"^Part\s+II\b", b_label.strip(), re.I):
            doc.add_page_break()
        doc.add_heading(b_label, level=1)
        _add_md_lines(doc, b_content)

    return _save(doc)


# ---------------------------------------------------------------------------
# Template: storyboard
# ---------------------------------------------------------------------------

def _storyboard(topic: str, blocks: list[tuple[str, str]]) -> BytesIO:
    doc = DocxDocument()
    _configure_styles(doc)

    # Landscape page for storyboard
    section = doc.sections[0]
    section.page_width, section.page_height = section.page_height, section.page_width

    doc.add_heading(f"Storyboard — {topic}", level=0)
    p = doc.add_paragraph(f"Production Export · {_NOW()} · {len(blocks)} scene(s)")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    for i, (lbl, cnt) in enumerate(blocks, 1):
        doc.add_heading(f"Scene {i}: {lbl}", level=1)

        tbl = doc.add_table(rows=2, cols=3)
        tbl.style = "Table Grid"
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER

        # Header row
        hdr_cells = tbl.rows[0].cells
        hdr_cells[0].text = "Scene #"
        hdr_cells[1].text = "Narration / Content"
        hdr_cells[2].text = "Visual Notes / Cue"
        for cell in hdr_cells:
            for para in cell.paragraphs:
                run = para.runs[0] if para.runs else para.add_run(para.text)
                run.bold = True

        # Content row
        data_cells = tbl.rows[1].cells
        data_cells[0].text = str(i)
        _set_cell_markdown(data_cells[1], cnt.strip())
        data_cells[2].text = "[ Visual / animation description ]"

        tbl.columns[0].width = Inches(0.8)
        tbl.columns[1].width = Inches(5.0)
        tbl.columns[2].width = Inches(2.8)

        doc.add_paragraph("")

    _brand_footer(doc)
    return _save(doc)


# ---------------------------------------------------------------------------
# Template: teacher_guide
# ---------------------------------------------------------------------------

def _teacher_guide(topic: str, blocks: list[tuple[str, str]]) -> BytesIO:
    doc = DocxDocument()
    _configure_styles(doc)
    doc.add_heading(f"Teacher Guide — {topic}", level=0)
    doc.add_paragraph("INSTRUCTOR COPY — Not for distribution to learners", style="Subtitle")
    doc.add_paragraph(f"Date: {_NOW()}")
    doc.add_page_break()

    for lbl, cnt in blocks:
        doc.add_heading(lbl, level=1)

        # Learner content
        _add_md_lines(doc, cnt)
        doc.add_paragraph("")

        # Teaching notes box (shaded)
        doc.add_heading("Teaching Notes", level=3)
        p_note = doc.add_paragraph("Instructor notes for this section:")
        p_note.runs[0].italic = True
        p_note.runs[0].font.color.rgb = RGBColor(0x92, 0x40, 0x0E)

        for _ in range(4):
            p = doc.add_paragraph("_" * 80)
            p.runs[0].font.color.rgb = RGBColor(0xD9, 0x77, 0x06)

        doc.add_paragraph("")

    _brand_footer(doc)
    return _save(doc)


# ---------------------------------------------------------------------------
# Template: quiz_bank
# ---------------------------------------------------------------------------

_Q_RE = re.compile(r"^(\d+[\.\)]\s+|Q\d*[\.:]\s*|Question\s*\d*[\.:]\s*)", re.I)


def _quiz_bank(topic: str, blocks: list[tuple[str, str]]) -> BytesIO:
    doc = DocxDocument()
    _configure_styles(doc)
    doc.add_heading(f"Quiz Bank — {topic}", level=0)
    doc.add_paragraph(f"Question Bank Export · {_NOW()}")
    doc.add_page_break()

    questions: list[str] = []
    for _lbl, cnt in blocks:
        for line in cnt.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if _Q_RE.match(stripped) or stripped.endswith("?"):
                questions.append(_Q_RE.sub("", stripped).strip())

    if questions:
        doc.add_heading(f"Questions ({len(questions)} total)", level=1)
        tbl = doc.add_table(rows=1 + len(questions), cols=2)
        tbl.style = "Table Grid"

        # Header
        hdr = tbl.rows[0].cells
        hdr[0].text = "Q #"
        hdr[1].text = "Question"
        for cell in hdr:
            for para in cell.paragraphs:
                run = para.runs[0] if para.runs else para.add_run(para.text)
                run.bold = True

        for i, q in enumerate(questions, 1):
            row = tbl.rows[i].cells
            row[0].text = str(i)
            _set_cell_markdown(row[1], q)

        tbl.columns[0].width = Inches(0.6)
        tbl.columns[1].width = Inches(6.0)
    else:
        doc.add_paragraph(
            "No quiz questions detected. Ensure blocks contain numbered questions "
            "or sentences ending with '?'."
        )

    _brand_footer(doc)
    return _save(doc)


# ---------------------------------------------------------------------------
# Template: client
# ---------------------------------------------------------------------------

def _client(topic: str, blocks: list[tuple[str, str]]) -> BytesIO:
    doc = DocxDocument()
    _configure_styles(doc)

    # Cover
    doc.add_heading(topic, level=0)
    p_sub = doc.add_paragraph("Prepared for Client Review")
    p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_date = doc.add_paragraph(_NOW())
    p_date.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    for lbl, cnt in blocks:
        doc.add_heading(lbl, level=1)
        _add_md_lines(doc, cnt)
        doc.add_paragraph("")

    # Clean footer
    doc.add_page_break()
    p = doc.add_paragraph("Confidential — Prepared for Client Review")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.runs[0].font.color.rgb = RGBColor(0x9C, 0xA3, 0xAF)

    return _save(doc)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

TEMPLATES = {
    "default":       _default,
    "storyboard":    _storyboard,
    "teacher_guide": _teacher_guide,
    "quiz_bank":     _quiz_bank,
    "client":        _client,
}


def build_docx(topic: str, blocks: list[tuple[str, str]], template: str = "default") -> BytesIO:
    """Build a DOCX document for the given template.

    ``blocks`` is a list of (label, content) string tuples.
    Returns a seeked BytesIO buffer ready for download.
    Falls back to "default" for unknown template names.
    """
    fn = TEMPLATES.get(template, _default)
    return fn(topic, blocks)
