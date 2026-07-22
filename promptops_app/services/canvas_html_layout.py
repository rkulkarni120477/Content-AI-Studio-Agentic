"""Canvas-safe single-column layout lock for lesson HTML.

LLM-generated HTML often uses tables / flex / grid for "cards". Canvas Pages
break those into narrow side-by-side columns. This module forces a stacked,
full-width course layout regardless of what the model emitted.
"""

from __future__ import annotations

import re

_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_DOCTYPE_RE = re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE)
_HTML_SHELL_RE = re.compile(r"</?(?:html|head)[^>]*>", re.IGNORECASE)
_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<(?:td|th)\b[^>]*>(.*?)</(?:td|th)>", re.IGNORECASE | re.DOTALL)
_CELL_OPEN_RE = re.compile(r"<(?:td|th)\b", re.IGNORECASE)
_STYLE_ATTR_RE = re.compile(r'\sstyle\s*=\s*(["\'])(.*?)\1', re.IGNORECASE | re.DOTALL)
_WIDTH_ATTR_RE = re.compile(r'\s(?:width|height)\s*=\s*(["\'])(.*?)\1', re.IGNORECASE)
_COLGROUP_RE = re.compile(r"<colgroup\b[^>]*>.*?</colgroup>", re.IGNORECASE | re.DOTALL)
_COL_RE = re.compile(r"<col\b[^>]*/?>", re.IGNORECASE)

# Inline CSS properties that create multi-column / clipped Canvas layouts.
_BAD_CSS_PROP_RE = re.compile(
    r"(?:"
    r"display\s*:\s*(?:inline-)?(?:flex|grid)\b|"
    r"float\s*:|"
    r"position\s*:\s*(?:absolute|fixed)\b|"
    r"column-count\s*:|"
    r"columns\s*:|"
    r"grid-template[^;]*|"
    r"grid-column[^;]*|"
    r"grid-row[^;]*|"
    r"flex\s*:|"
    r"flex-direction\s*:|"
    r"flex-wrap\s*:|"
    r"flex-basis\s*:|"
    r"flex-grow\s*:|"
    r"flex-shrink\s*:|"
    r"writing-mode\s*:|"
    r"transform\s*:|"
    r"text-overflow\s*:|"
    r"overflow(?:-x|-y)?\s*:\s*(?:hidden|auto|scroll)\b|"
    r"-webkit-line-clamp\s*:|"
    r"line-clamp\s*:|"
    r"max-height\s*:|"
    r"height\s*:\s*\d|"
    r"white-space\s*:\s*nowrap\b|"
    r"width\s*:\s*\d{1,2}(?:\.\d+)?%|"  # percentage column widths
    r"max-width\s*:\s*\d{1,2}(?:\.\d+)?%|"
    r"min-width\s*:\s*\d+px"
    r")[^;]*;?",
    re.IGNORECASE,
)

CANVAS_LOCKED_CSS = """
.cas-lesson {
  font-family: "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.7;
  color: #1e293b;
  max-width: 720px;
  margin: 0 auto;
  padding: 0.5rem 0 2rem;
  box-sizing: border-box;
}
.cas-lesson *,
.cas-lesson *::before,
.cas-lesson *::after { box-sizing: border-box; }
.cas-lesson h1 {
  color: #1e3a8a;
  font-size: 1.75rem;
  line-height: 1.3;
  margin: 0 0 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 2px solid #e2e8f0;
}
.cas-lesson h2 {
  color: #1e40af;
  font-size: 1.35rem;
  margin: 1.75rem 0 0.75rem;
}
.cas-lesson h3 {
  color: #334155;
  font-size: 1.15rem;
  margin: 1.25rem 0 0.5rem;
}
.cas-lesson h4 { color: #475569; margin: 1rem 0 0.5rem; }
.cas-lesson p {
  margin: 0.75rem 0;
  overflow: visible !important;
  text-overflow: clip !important;
  white-space: normal !important;
  max-height: none !important;
  -webkit-line-clamp: unset !important;
}
.cas-lesson ul, .cas-lesson ol {
  display: block !important;
  margin: 0.75rem 0 0.75rem 1.5rem;
  padding-left: 1.25rem;
  list-style-position: outside;
  text-align: left !important;
  width: auto !important;
  max-width: 100% !important;
}
.cas-lesson li {
  display: list-item !important;
  margin: 0.45rem 0;
  width: auto !important;
  max-width: 100% !important;
  float: none !important;
  text-align: left !important;
  overflow: visible !important;
  white-space: normal !important;
}
.cas-lesson img { max-width: 100%; height: auto; display: block; margin: 1rem 0; }
.cas-lesson a { color: #2563eb; }
.cas-lesson blockquote {
  margin: 1rem 0;
  padding: 0.75rem 1rem;
  border-left: 4px solid #93c5fd;
  background: #f8fafc;
  color: #475569;
}
.cas-lesson .cas-card,
.cas-lesson .card,
.cas-lesson .topic,
.cas-lesson .objectives,
.cas-lesson .check,
.cas-lesson .placeholder,
.cas-lesson .cas-stack {
  display: block !important;
  width: 100% !important;
  max-width: 100% !important;
  float: none !important;
  margin: 1.25rem 0;
  padding: 1rem 1.15rem;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
}
.cas-lesson .objectives { background: #f8fafc; border-color: #bfdbfe; }
.cas-lesson .check,
.cas-lesson .knowledge-check {
  background: #fffbeb;
  border-color: #fcd34d;
}
.cas-lesson .placeholder {
  background: #f1f5f9;
  border-style: dashed;
  border-color: #94a3b8;
  color: #475569;
  text-align: center;
}
.cas-lesson .cas-stack {
  border: none;
  background: transparent;
  padding: 0.35rem 0;
  margin: 0.5rem 0;
}

/* Force every layout container into one vertical column (Canvas-safe). */
.cas-lesson div,
.cas-lesson section,
.cas-lesson article,
.cas-lesson aside,
.cas-lesson header,
.cas-lesson footer,
.cas-lesson main,
.cas-lesson figure,
.cas-lesson figcaption,
.cas-lesson table,
.cas-lesson thead,
.cas-lesson tbody,
.cas-lesson tfoot,
.cas-lesson tr,
.cas-lesson th,
.cas-lesson td {
  display: block !important;
  float: none !important;
  position: static !important;
  width: 100% !important;
  max-width: 100% !important;
  min-width: 0 !important;
  flex: none !important;
  grid-column: auto !important;
  grid-row: auto !important;
  columns: auto !important;
  column-count: 1 !important;
  writing-mode: horizontal-tb !important;
  transform: none !important;
  box-sizing: border-box !important;
}
.cas-lesson table { border: none !important; margin: 0.75rem 0; }
.cas-lesson th, .cas-lesson td {
  border: none !important;
  padding: 0.5rem 0 !important;
  text-align: left !important;
}
.cas-lesson col, .cas-lesson colgroup { display: none !important; }

/* Never clip lesson text (LLM/export CSS sometimes adds ellipsis/line-clamp). */
.cas-lesson p,
.cas-lesson li,
.cas-lesson td,
.cas-lesson th,
.cas-lesson div,
.cas-lesson section,
.cas-lesson article {
  overflow: visible !important;
  text-overflow: clip !important;
  white-space: normal !important;
  max-height: none !important;
  -webkit-line-clamp: unset !important;
  line-clamp: unset !important;
}
""".strip()


def _extract_body(html: str) -> str:
    text = (html or "").strip()
    if not text:
        return ""
    body_match = _BODY_RE.search(text)
    if body_match:
        body = body_match.group(1)
    else:
        body = text
    body = _STYLE_RE.sub("", body)
    body = _DOCTYPE_RE.sub("", body)
    body = _HTML_SHELL_RE.sub("", body)
    return body.strip()


def _sanitize_style_attr(style: str) -> str:
    cleaned = _BAD_CSS_PROP_RE.sub("", style or "")
    cleaned = re.sub(r";\s*;+", ";", cleaned)
    return cleaned.strip().strip(";").strip()


def _sanitize_inline_styles(html: str) -> str:
    def repl(match: re.Match) -> str:
        quote = match.group(1)
        cleaned = _sanitize_style_attr(match.group(2))
        if not cleaned:
            return ""
        return f' style={quote}{cleaned}{quote}'

    html = _STYLE_ATTR_RE.sub(repl, html)
    html = _WIDTH_ATTR_RE.sub("", html)
    html = _COLGROUP_RE.sub("", html)
    html = _COL_RE.sub("", html)
    return html


def _row_cell_count(row_inner: str) -> int:
    return len(_CELL_OPEN_RE.findall(row_inner))


def _flatten_table(table_html: str) -> str:
    """Turn multi-column layout tables into stacked blocks; keep simple tables."""
    rows = _TR_RE.findall(table_html)
    if not rows:
        cells = _CELL_RE.findall(table_html)
        if len(cells) <= 1:
            return table_html
        parts = [f'<div class="cas-stack">{c.strip()}</div>' for c in cells if c.strip()]
        return "\n".join(parts)

    max_cols = max((_row_cell_count(r) for r in rows), default=0)
    if max_cols <= 1:
        return table_html

    parts: list[str] = []
    for row in rows:
        for cell in _CELL_RE.findall(row):
            cell = cell.strip()
            if cell:
                parts.append(f'<div class="cas-stack">{cell}</div>')
    return "\n".join(parts)


def _flatten_layout_tables(html: str) -> str:
    """Repeatedly flatten nested multi-column tables into stacked divs."""
    result = html
    for _ in range(12):
        tables = list(_TABLE_RE.finditer(result))
        if not tables:
            break
        # Replace from the end so offsets stay valid; prefer innermost by length.
        tables_sorted = sorted(tables, key=lambda m: (m.end() - m.start(), -m.start()))
        changed = False
        for match in tables_sorted:
            original = match.group(0)
            flat = _flatten_table(original)
            if flat != original:
                result = result[: match.start()] + flat + result[match.end() :]
                changed = True
                break
        if not changed:
            break
    return result


def lock_single_column_html(html: str) -> str:
    """Normalize any lesson HTML into a Canvas-safe single-column document.

    - Drops model CSS and injects locked course CSS
    - Flattens multi-column layout tables
    - Strips flex/grid/float/percentage-width inline styles
    - Wraps content in ``.cas-lesson``
    """
    text = (html or "").strip()
    if not text:
        return text

    body = _extract_body(text)
    body = _flatten_layout_tables(body)
    body = _sanitize_inline_styles(body)

    if 'class="cas-lesson"' not in body and "class='cas-lesson'" not in body:
        body = f'<div class="cas-lesson">\n{body}\n</div>'

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8"/>\n'
        f"<style>\n{CANVAS_LOCKED_CSS}\n</style>\n"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>"
    )


def lock_page_body_fragment(html: str) -> str:
    """Return a Canvas Page body fragment (style + stacked content), not a full doc.

    Used by the IMSCC exporter when packaging wiki pages.
    """
    locked = lock_single_column_html(html)
    body = _extract_body(locked)
    return f"<style>\n{CANVAS_LOCKED_CSS}\n</style>\n{body}".strip()
