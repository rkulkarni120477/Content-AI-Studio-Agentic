"""CendocXML node → markdown + HTML helpers (Cendoc import only).

Converts ``cl:*`` elements into lesson markdown / ``content_html``. Image
resolution and optional data-URI embedding happen here so shared
``editor_builder`` never needs a format branch.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
from xml.etree import ElementTree as ET

CENDOC_NS = "http://xml.cengage-learning.com/cendoc-core"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
MAX_DATA_URI_BYTES = int(1.5 * 1024 * 1024)  # 1.5 MB


def local(tag: str) -> str:
    """Strip Clark notation / prefix → local name."""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    if ":" in tag:
        return tag.split(":", 1)[-1]
    return tag


def q(name: str) -> str:
    return f"{{{CENDOC_NS}}}{name}"


def text_of(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def title_of(el: ET.Element) -> str:
    """First nested ``title`` under complex-meta / simple-meta / direct child."""
    for path in (
        f".//{q('complex-meta')}/{q('title')}",
        f"./{q('complex-meta')}/{q('title')}",
        f"./{q('simple-meta')}/{q('title')}",
        f"./{q('title')}",
        f".//{q('title')}",
    ):
        found = el.find(path)
        if found is not None:
            t = text_of(found)
            if t:
                return t
    return ""


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _xref_label(el: ET.Element) -> str:
    """Build visible cross-ref text from pre-text / ordinal / post-text."""
    pre = el.get("pre-text") or ""
    ordinal = el.get("ordinal") or ""
    post = el.get("post-text") or ""
    composed = f"{pre}{ordinal}{post}".strip()
    if composed:
        return composed
    inner = text_of(el)
    if inner:
        return inner
    return el.get("link-target") or ""


def _footnote_body(el: ET.Element) -> str:
    composed = el.find(f".//{q('composed-content')}")
    if composed is not None and text_of(composed):
        return text_of(composed)
    return text_of(el)


def _mathml_to_text(el: ET.Element) -> str:
    """Best-effort plaintext from MathML (fractions → a/b, sub/sup → _/^)."""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        name = local(node.tag)
        if name == "media-object":
            return
        if name == "mfrac":
            kids = [c for c in list(node) if local(c.tag) != "media-object"]
            num = _mathml_to_text(kids[0]) if kids else ""
            den = _mathml_to_text(kids[1]) if len(kids) > 1 else ""
            parts.append(f"({num}/{den})")
            return
        if name in ("msub", "msup", "msubsup"):
            kids = list(node)
            base = _mathml_to_text(kids[0]) if kids else ""
            if name == "msub" and len(kids) > 1:
                parts.append(f"{base}_{_mathml_to_text(kids[1])}")
            elif name == "msup" and len(kids) > 1:
                parts.append(f"{base}^{_mathml_to_text(kids[1])}")
            elif name == "msubsup" and len(kids) > 2:
                parts.append(
                    f"{base}_{_mathml_to_text(kids[1])}^{_mathml_to_text(kids[2])}"
                )
            else:
                parts.append(base)
            return
        if node.text:
            parts.append(node.text)
        for child in list(node):
            if local(child.tag) == "media-object":
                if child.tail:
                    parts.append(child.tail)
                continue
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _equation_image(el: ET.Element, images: ImageResolver | None) -> tuple[str | None, str]:
    """Return (data_uri_or_path, alt_text) from nested media-object if present."""
    obj = el.find(f".//{q('media-object')}")
    alt = _mathml_to_text(el) or "equation"
    if obj is None or images is None:
        return None, alt
    link = obj.get("link-target") or ""
    path = images.resolve(link)
    if not path:
        return None, alt
    uri = images.to_data_uri(path) or f"book_images/{os.path.basename(path)}"
    return uri, alt


class ImageResolver:
    """Resolve ``link-target`` basenames against ``book_images/``; embed small files."""

    def __init__(self, images_dir: str | None):
        self.images_dir = images_dir
        self.by_name: dict[str, str] = {}
        self.used: list[tuple[str, str]] = []  # (filename, abs_path)
        self.warnings: list[str] = []
        if images_dir and os.path.isdir(images_dir):
            for name in os.listdir(images_dir):
                path = os.path.join(images_dir, name)
                if os.path.isfile(path):
                    self.by_name[name.lower()] = path

    def resolve(self, link_target: str) -> str | None:
        if not link_target:
            return None
        # Skip URLs and internal identifiers without extensions.
        if "://" in link_target:
            return None
        base = os.path.basename(link_target.replace("\\", "/"))
        if not base or "." not in base:
            return None
        path = self.by_name.get(base.lower())
        if path is None:
            warn = f"Image not found in book_images: {base}"
            if warn not in self.warnings:
                self.warnings.append(warn)
            return None
        if (base, path) not in self.used:
            self.used.append((base, path))
        return path

    def to_data_uri(self, path: str) -> str | None:
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        if size > MAX_DATA_URI_BYTES:
            self.warnings.append(
                f"Image too large for inline embed ({os.path.basename(path)}, {size} bytes)."
            )
            return None
        mime, _ = mimetypes.guess_type(path)
        mime = mime or "application/octet-stream"
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError:
            return None
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"


def _inline_md(el: ET.Element, images: ImageResolver | None = None) -> str:
    """Render mixed content of a paragraph-like element to markdown inline text."""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in list(node):
            name = local(child.tag)
            if name == "style":
                styles = (child.get("styles") or "").lower()
                inner = _inline_md(child, images)
                if "bold" in styles and "italic" in styles:
                    parts.append(f"***{inner}***")
                elif "bold" in styles:
                    parts.append(f"**{inner}**")
                elif "italic" in styles:
                    parts.append(f"*{inner}*")
                elif "underscore" in styles:
                    parts.append(f"_{inner}_")
                else:
                    parts.append(inner)
            elif name == "key-term-entry":
                # Inline glossary callout: bold the term only (def often repeats in prose).
                term = text_of(child.find(q("key-term"))) or text_of(child)
                parts.append(f"**{term}**")
            elif name == "key-term":
                parts.append(f"**{text_of(child)}**")
            elif name == "key-term-def":
                parts.append(text_of(child))
            elif name == "uri":
                label = text_of(child) or child.get("link-target") or ""
                href = child.get("link-target") or label
                parts.append(f"[{label}]({href})")
            elif name == "xref":
                parts.append(_xref_label(child))
            elif name == "aux-ref":
                parts.append(text_of(child))
            elif name == "footnote":
                body = _footnote_body(child)
                parts.append(f" *(Source: {body})*" if body else "[^]")
            elif name == "ref":
                composed = child.find(q("composed-content"))
                parts.append(text_of(composed) or text_of(child))
            elif name == "bibcitation-freestyle":
                parts.append(text_of(child))
            elif name == "learn-obj":
                label = child.get("manual-label")
                text = text_of(child)
                parts.append(f"**{label}.** {text}" if label else text)
            elif name == "list":
                parts.append("\n" + _list_md(child, images))
            elif name == "superscript":
                parts.append(f"^{text_of(child)}" if text_of(child) else "")
            elif name == "subscript":
                parts.append(f"_{text_of(child)}" if text_of(child) else "")
            elif name == "run-in-head":
                parts.append(f"**{text_of(child)}**")
            elif name in ("inline-math", "math-expr", "equation"):
                uri, alt = _equation_image(child, images)
                if uri:
                    parts.append(f"![{alt}]({uri})")
                else:
                    parts.append(f"`{alt}`" if alt else "")
            elif name in ("person-name", "given-names", "surname", "org", "org-name"):
                parts.append(text_of(child))
            else:
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


def _inline_html(el: ET.Element, images: ImageResolver | None = None) -> str:
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.text:
            parts.append(_escape_html(node.text))
        for child in list(node):
            name = local(child.tag)
            if name == "style":
                styles = (child.get("styles") or "").lower()
                inner = _inline_html(child, images)
                if "bold" in styles and "italic" in styles:
                    parts.append(f"<strong><em>{inner}</em></strong>")
                elif "bold" in styles:
                    parts.append(f"<strong>{inner}</strong>")
                elif "italic" in styles:
                    parts.append(f"<em>{inner}</em>")
                elif "underscore" in styles:
                    parts.append(f"<u>{inner}</u>")
                else:
                    parts.append(inner)
            elif name == "key-term-entry":
                term = text_of(child.find(q("key-term"))) or text_of(child)
                parts.append(f"<strong>{_escape_html(term)}</strong>")
            elif name == "key-term":
                parts.append(f"<strong>{_escape_html(text_of(child))}</strong>")
            elif name == "uri":
                label = text_of(child) or child.get("link-target") or ""
                href = child.get("link-target") or label
                parts.append(
                    f'<a href="{_escape_html(href)}">{_escape_html(label)}</a>'
                )
            elif name == "xref":
                parts.append(_escape_html(_xref_label(child)))
            elif name == "aux-ref":
                parts.append(_escape_html(text_of(child)))
            elif name == "footnote":
                body = _footnote_body(child)
                if body:
                    parts.append(
                        f'<cite class="cendoc-footnote">{_escape_html(body)}</cite>'
                    )
                else:
                    parts.append("<sup>*</sup>")
            elif name == "ref":
                composed = child.find(q("composed-content"))
                parts.append(_escape_html(text_of(composed) or text_of(child)))
            elif name == "list":
                parts.append(_list_html(child, images))
            elif name == "superscript":
                parts.append(f"<sup>{_escape_html(text_of(child))}</sup>")
            elif name == "subscript":
                parts.append(f"<sub>{_escape_html(text_of(child))}</sub>")
            elif name == "run-in-head":
                parts.append(f"<strong>{_escape_html(text_of(child))}</strong>")
            elif name in ("inline-math", "math-expr", "equation"):
                uri, alt = _equation_image(child, images)
                if uri:
                    parts.append(
                        f'<img src="{_escape_html(uri)}" alt="{_escape_html(alt)}" />'
                    )
                else:
                    parts.append(f"<code>{_escape_html(alt)}</code>" if alt else "")
            elif name in ("person-name", "given-names", "surname", "org", "org-name",
                          "learn-obj", "bibcitation-freestyle", "key-term-def"):
                parts.append(_escape_html(text_of(child)))
            else:
                walk(child)
            if child.tail:
                parts.append(_escape_html(child.tail))

    walk(el)
    return "".join(parts)


def _list_is_ordered(el: ET.Element) -> bool:
    """True for Ordered lists. Avoid matching 'ordered' inside 'Unordered'."""
    style = (el.get("list-style") or "").strip().lower()
    if style == "unordered" or style.startswith("unordered"):
        return False
    if style == "ordered" or style.startswith("ordered"):
        return True
    return bool(el.get("numeration"))


def _item_chunks_md(item: ET.Element, images: ImageResolver | None = None) -> list[str]:
    """All block children of a list item (title + body paras, nested lists, …)."""
    chunks: list[str] = []
    for child in list(item):
        name = local(child.tag)
        if name == "para":
            text = _inline_md(child, images)
            if text:
                chunks.append(text)
        elif name == "list":
            nested = _list_md(child, images)
            if nested:
                chunks.append(nested)
        elif images is not None:
            block = block_md(child, images)
            if block:
                chunks.append(block)
        else:
            text = text_of(child)
            if text:
                chunks.append(text)
    if not chunks:
        fallback = text_of(item).strip()
        if fallback:
            chunks.append(fallback)
    return chunks


def _item_body_md(item: ET.Element, images: ImageResolver | None = None) -> str:
    chunks = _item_chunks_md(item, images)
    if not chunks:
        return ""
    if len(chunks) == 1:
        return chunks[0]
    # First line on the bullet; continuation indented under the item.
    first, *rest = chunks
    cont = "\n\n".join(rest).replace("\n", "\n  ")
    return f"{first}\n\n  {cont}"


def _item_body_html(item: ET.Element, images: ImageResolver | None = None) -> str:
    parts: list[str] = []
    for child in list(item):
        name = local(child.tag)
        if name == "para":
            text = _inline_html(child, images)
            if text:
                parts.append(f"<p>{text}</p>")
        elif name == "list":
            nested = _list_html(child, images)
            if nested:
                parts.append(nested)
        elif images is not None:
            block = block_html(child, images)
            if block:
                parts.append(block)
        else:
            text = text_of(child).strip()
            if text:
                parts.append(_escape_html(text))
    if not parts:
        fallback = text_of(item).strip()
        return _escape_html(fallback) if fallback else ""
    # Single short para: keep compact <li>text</li> without wrapping <p>.
    if len(parts) == 1 and parts[0].startswith("<p>") and parts[0].endswith("</p>"):
        return parts[0][3:-4]
    return "".join(parts)


def _list_md(el: ET.Element, images: ImageResolver | None = None) -> str:
    ordered = _list_is_ordered(el)
    lines: list[str] = []
    for i, item in enumerate(el.findall(q("item")), start=1):
        body = _item_body_md(item, images)
        label = item.get("manual-label")
        if ordered:
            prefix = f"{label}. " if label else f"{i}. "
        else:
            prefix = "- "
        lines.append(f"{prefix}{body}" if body else prefix.rstrip())
    return "\n".join(lines)


def _list_html(el: ET.Element, images: ImageResolver | None = None) -> str:
    ordered = _list_is_ordered(el)
    tag = "ol" if ordered else "ul"
    items: list[str] = []
    for item in el.findall(q("item")):
        body = _item_body_html(item, images)
        items.append(f"<li>{body}</li>")
    return f"<{tag}>{''.join(items)}</{tag}>"


def _pick_media_object(media: ET.Element) -> ET.Element | None:
    """Prefer media-size=page, else first object with a filename link-target."""
    objects = media.findall(q("media-object"))
    if not objects:
        return None
    page = [o for o in objects if (o.get("media-size") or "").lower() == "page"]
    pool = page or objects
    for obj in pool:
        lt = obj.get("link-target") or ""
        if lt and "://" not in lt and "." in os.path.basename(lt):
            return obj
    return pool[0]


def _figure_md(el: ET.Element, images: ImageResolver) -> str:
    caption_el = el.find(f".//{q('caption')}")
    alt_el = el.find(f".//{q('alt-text')}")
    caption = text_of(caption_el)
    alt = text_of(alt_el) or caption or "figure"
    media = el.find(q("media"))
    if media is None:
        return f"*{caption}*" if caption else ""
    obj = _pick_media_object(media)
    link = (obj.get("link-target") if obj is not None else "") or ""
    path = images.resolve(link)
    if path:
        uri = images.to_data_uri(path) or f"book_images/{os.path.basename(path)}"
        lines = [f"![{alt}]({uri})"]
        if caption:
            lines.append(f"*{caption}*")
        return "\n".join(lines)
    return f"*[Figure: {caption or alt}]*" if (caption or alt) else ""


def _figure_html(el: ET.Element, images: ImageResolver) -> str:
    caption_el = el.find(f".//{q('caption')}")
    alt_el = el.find(f".//{q('alt-text')}")
    caption = text_of(caption_el)
    alt = text_of(alt_el) or caption or "figure"
    media = el.find(q("media"))
    if media is None:
        return f"<p><em>{_escape_html(caption)}</em></p>" if caption else ""
    obj = _pick_media_object(media)
    link = (obj.get("link-target") if obj is not None else "") or ""
    path = images.resolve(link)
    if path:
        uri = images.to_data_uri(path) or f"book_images/{os.path.basename(path)}"
        cap = f"<figcaption>{_escape_html(caption)}</figcaption>" if caption else ""
        return (
            f'<figure><img src="{_escape_html(uri)}" alt="{_escape_html(alt)}" />'
            f"{cap}</figure>"
        )
    return f"<p><em>Figure: {_escape_html(caption or alt)}</em></p>"


def _col_names(table_el: ET.Element) -> list[str]:
    tgroup = table_el.find(q("tgroup"))
    if tgroup is None:
        return []
    return [c.get("colname") or "" for c in tgroup.findall(q("colspec"))]


def _entry_colspan(entry: ET.Element, col_names: list[str]) -> int:
    namest = entry.get("namest")
    nameend = entry.get("nameend")
    if namest and nameend and col_names:
        try:
            return max(1, col_names.index(nameend) - col_names.index(namest) + 1)
        except ValueError:
            return 1
    return 1


def _table_rows(el: ET.Element) -> tuple[list[ET.Element], list[ET.Element]]:
    """Return (header_rows, body_rows)."""
    thead = el.find(f".//{q('thead')}")
    tbody = el.find(f".//{q('tbody')}")
    header = thead.findall(q("row")) if thead is not None else []
    if tbody is not None:
        body = tbody.findall(q("row"))
    else:
        all_rows = el.findall(f".//{q('row')}")
        body = [r for r in all_rows if r not in header]
        if not header and body:
            # Fallback: first row as header when no thead.
            header = [body[0]]
            body = body[1:]
    return header, body


def _table_md(el: ET.Element) -> str:
    header_rows, body_rows = _table_rows(el)
    rows = header_rows + body_rows
    if not rows:
        return ""
    col_names = _col_names(el)
    grid: list[list[str]] = []
    for row in rows:
        cells = row.findall(q("entry"))
        line: list[str] = []
        for c in cells:
            text = text_of(c).replace("|", "\\|")
            span = _entry_colspan(c, col_names)
            line.append(text)
            for _ in range(span - 1):
                line.append("")
        grid.append(line or [""])
    width = max(len(r) for r in grid)
    for r in grid:
        while len(r) < width:
            r.append("")
    header = grid[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for r in grid[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _table_html(el: ET.Element) -> str:
    header_rows, body_rows = _table_rows(el)
    if not header_rows and not body_rows:
        return ""
    col_names = _col_names(el)
    parts = ["<table>"]

    def emit_rows(row_els: list[ET.Element], cell_tag: str) -> None:
        for row in row_els:
            parts.append("<tr>")
            for c in row.findall(q("entry")):
                span = _entry_colspan(c, col_names)
                attrs = f' colspan="{span}"' if span > 1 else ""
                parts.append(
                    f"<{cell_tag}{attrs}>{_escape_html(text_of(c))}</{cell_tag}>"
                )
            parts.append("</tr>")

    if header_rows:
        parts.append("<thead>")
        emit_rows(header_rows, "th")
        parts.append("</thead>")
    if body_rows:
        parts.append("<tbody>")
        emit_rows(body_rows, "td")
        parts.append("</tbody>")
    parts.append("</table>")
    return "".join(parts)


def _table_wrapper_md(el: ET.Element, images: ImageResolver) -> str:
    label = text_of(el.find(f"./{q('simple-meta')}/{q('label')}"))
    title = title_of(el)
    heading_bits = [b for b in (label, title) if b]
    heading = ": ".join(heading_bits) if heading_bits else ""
    table = el.find(q("table"))
    body = _table_md(table) if table is not None else ""
    # Any non-meta/table children (rare captions).
    extra = []
    for child in list(el):
        name = local(child.tag)
        if name in ("simple-meta", "complex-meta", "metadata-wrapper", "table"):
            continue
        chunk = block_md(child, images)
        if chunk:
            extra.append(chunk)
    parts = []
    if heading:
        parts.append(f"**{heading}**")
    if body:
        parts.append(body)
    parts.extend(extra)
    return "\n\n".join(parts)


def _table_wrapper_html(el: ET.Element, images: ImageResolver) -> str:
    label = text_of(el.find(f"./{q('simple-meta')}/{q('label')}"))
    title = title_of(el)
    heading_bits = [b for b in (label, title) if b]
    heading = ": ".join(heading_bits) if heading_bits else ""
    table = el.find(q("table"))
    inner = []
    if heading:
        inner.append(f"<strong>{_escape_html(heading)}</strong>")
    if table is not None:
        inner.append(_table_html(table))
    for child in list(el):
        name = local(child.tag)
        if name in ("simple-meta", "complex-meta", "metadata-wrapper", "table"):
            continue
        chunk = block_html(child, images)
        if chunk:
            inner.append(chunk)
    return f'<div class="cendoc-table">{"".join(inner)}</div>'


def _key_term_list_md(el: ET.Element) -> str:
    lines: list[str] = []
    for entry in el.findall(q("key-term-entry")):
        term = text_of(entry.find(q("key-term")))
        defin = text_of(entry.find(q("key-term-def")))
        if term and defin:
            lines.append(f"**{term}** — {defin}")
        elif term:
            lines.append(f"**{term}**")
        elif defin:
            lines.append(defin)
    return "\n\n".join(lines)


def _key_term_list_html(el: ET.Element) -> str:
    items: list[str] = []
    for entry in el.findall(q("key-term-entry")):
        term = text_of(entry.find(q("key-term")))
        defin = text_of(entry.find(q("key-term-def")))
        if term and defin:
            items.append(
                f"<li><strong>{_escape_html(term)}</strong> — {_escape_html(defin)}</li>"
            )
        elif term:
            items.append(f"<li><strong>{_escape_html(term)}</strong></li>")
    return f'<ul class="cendoc-key-terms">{"".join(items)}</ul>' if items else ""


def _learn_obj_list_md(el: ET.Element, images: ImageResolver) -> str:
    chunks: list[str] = []
    for child in list(el):
        name = local(child.tag)
        if name == "learn-obj":
            label = child.get("manual-label")
            text = text_of(child)
            chunks.append(f"**{label}.** {text}" if label else f"**{text}**")
        else:
            chunk = block_md(child, images)
            if chunk:
                chunks.append(chunk)
    return "\n\n".join(chunks)


def _learn_obj_list_html(el: ET.Element, images: ImageResolver) -> str:
    parts: list[str] = ['<div class="cendoc-learn-obj-list">']
    for child in list(el):
        name = local(child.tag)
        if name == "learn-obj":
            label = child.get("manual-label")
            text = text_of(child)
            label_html = f"<strong>{_escape_html(label)}.</strong> " if label else ""
            parts.append(f"<p>{label_html}{_escape_html(text)}</p>")
        else:
            chunk = block_html(child, images)
            if chunk:
                parts.append(chunk)
    parts.append("</div>")
    return "".join(parts)


def _epigraph_md(el: ET.Element) -> str:
    credit_el = el.find(q("credit-byline"))
    credit = text_of(credit_el)
    # Direct text on epigraph (quote), excluding credit-byline children.
    quote_parts: list[str] = []
    if el.text and el.text.strip():
        quote_parts.append(el.text.strip())
    for child in list(el):
        if local(child.tag) == "credit-byline":
            continue
        if local(child.tag) == "para":
            quote_parts.append(_inline_md(child))
        else:
            t = text_of(child)
            if t:
                quote_parts.append(t)
        if child.tail and child.tail.strip():
            quote_parts.append(child.tail.strip())
    quote = " ".join(quote_parts).strip() or text_of(el)
    if credit and quote.endswith(credit):
        quote = quote[: -len(credit)].strip()
    out = f"> {quote}" if quote else ""
    if credit:
        out += f"\n>\n> — {credit}"
    return out


def _epigraph_html(el: ET.Element) -> str:
    credit = text_of(el.find(q("credit-byline")))
    quote_parts: list[str] = []
    if el.text and el.text.strip():
        quote_parts.append(el.text.strip())
    for child in list(el):
        if local(child.tag) == "credit-byline":
            continue
        if local(child.tag) == "para":
            quote_parts.append(_inline_html(child))
        else:
            t = text_of(child)
            if t:
                quote_parts.append(_escape_html(t))
        if child.tail and child.tail.strip():
            quote_parts.append(_escape_html(child.tail.strip()))
    quote = " ".join(quote_parts).strip() or _escape_html(text_of(el))
    foot = f"<footer>{_escape_html(credit)}</footer>" if credit else ""
    return f"<blockquote class=\"cendoc-epigraph\">{quote}{foot}</blockquote>"


def _math_block_md(el: ET.Element, images: ImageResolver) -> str:
    uri, alt = _equation_image(el, images)
    if uri:
        return f"![{alt}]({uri})"
    return f"`{alt}`" if alt else ""


def _math_block_html(el: ET.Element, images: ImageResolver) -> str:
    uri, alt = _equation_image(el, images)
    if uri:
        return f'<p><img src="{_escape_html(uri)}" alt="{_escape_html(alt)}" /></p>'
    return f"<p><code>{_escape_html(alt)}</code></p>" if alt else ""


def _sidebar_md(el: ET.Element, images: ImageResolver) -> str:
    label = ""
    meta = el.find(q("complex-meta"))
    if meta is not None:
        label_el = meta.find(q("label"))
        label = text_of(label_el) or title_of(el)
    body_parts: list[str] = []
    for child in list(el):
        name = local(child.tag)
        if name in ("complex-meta", "metadata-wrapper", "simple-meta"):
            continue
        chunk = block_md(child, images)
        if chunk:
            body_parts.append(chunk)
    body = "\n\n".join(body_parts)
    if label:
        return f"> **{label}**\n>\n> " + body.replace("\n", "\n> ")
    return "> " + body.replace("\n", "\n> ")


def _sidebar_html(el: ET.Element, images: ImageResolver) -> str:
    label = ""
    meta = el.find(q("complex-meta"))
    if meta is not None:
        label = text_of(meta.find(q("label"))) or title_of(el)
    inner: list[str] = []
    if label:
        inner.append(f"<strong>{_escape_html(label)}</strong>")
    for child in list(el):
        name = local(child.tag)
        if name in ("complex-meta", "metadata-wrapper", "simple-meta"):
            continue
        chunk = block_html(child, images)
        if chunk:
            inner.append(chunk)
    return f'<aside class="cendoc-sidebar">{"".join(inner)}</aside>'


def block_md(el: ET.Element, images: ImageResolver, heading_level: int = 0) -> str:
    """Render a block-level Cendoc element to markdown."""
    name = local(el.tag)
    if name == "para":
        return _inline_md(el, images)
    if name == "list":
        return _list_md(el, images)
    if name == "figure":
        return _figure_md(el, images)
    if name == "table":
        return _table_md(el)
    if name == "table-wrapper":
        return _table_wrapper_md(el, images)
    if name == "sidebar":
        return _sidebar_md(el, images)
    if name == "key-term-list":
        return _key_term_list_md(el)
    if name == "learn-obj-list":
        return _learn_obj_list_md(el, images)
    if name == "learn-obj":
        label = el.get("manual-label")
        text = text_of(el)
        return f"**{label}.** {text}" if label else f"**{text}**"
    if name == "epigraph":
        return _epigraph_md(el)
    if name in ("math-expr", "equation", "inline-math"):
        return _math_block_md(el, images)
    if name == "quiz":
        return quiz_to_markdown(el)
    if name == "excerpt":
        quote = _inline_md(el.find(q("para")) or el, images)
        credit = text_of(el.find(q("credit-byline")))
        out = f"> {quote}"
        if credit:
            out += f"\n>\n> — {credit}"
        return out
    if name in ("sect1", "sect2", "sect3", "sect4", "simple-section", "opener",
                "dedication", "acknowledgements", "preface", "foreword"):
        return section_to_markdown(el, images, heading_level=heading_level or {
            "sect1": 2, "sect2": 3, "sect3": 4, "sect4": 5,
            "simple-section": 2, "opener": 2,
            "dedication": 2, "acknowledgements": 2, "preface": 2, "foreword": 2,
        }.get(name, 2))
    # Skip structural/meta wrappers by walking children.
    if name in ("complex-meta", "simple-meta", "metadata-wrapper", "media",
                "media-object", "media-reference", "tgroup", "colspec"):
        return ""
    parts = [block_md(c, images, heading_level) for c in list(el)]
    # Text-bearing leaves (e.g. unknown wrappers with only .text) — emit text.
    if not any(parts) and el.text and el.text.strip() and not list(el):
        return el.text.strip()
    return "\n\n".join(p for p in parts if p)


def block_html(el: ET.Element, images: ImageResolver, heading_level: int = 0) -> str:
    name = local(el.tag)
    if name == "para":
        return f"<p>{_inline_html(el, images)}</p>"
    if name == "list":
        return _list_html(el, images)
    if name == "figure":
        return _figure_html(el, images)
    if name == "table":
        return _table_html(el)
    if name == "table-wrapper":
        return _table_wrapper_html(el, images)
    if name == "sidebar":
        return _sidebar_html(el, images)
    if name == "key-term-list":
        return _key_term_list_html(el)
    if name == "learn-obj-list":
        return _learn_obj_list_html(el, images)
    if name == "learn-obj":
        label = el.get("manual-label")
        text = text_of(el)
        label_html = f"<strong>{_escape_html(label)}.</strong> " if label else ""
        return f"<p>{label_html}{_escape_html(text)}</p>"
    if name == "epigraph":
        return _epigraph_html(el)
    if name in ("math-expr", "equation", "inline-math"):
        return _math_block_html(el, images)
    if name == "quiz":
        return quiz_to_html(el)
    if name == "excerpt":
        quote = _inline_html(el.find(q("para")) or el, images)
        credit = text_of(el.find(q("credit-byline")))
        foot = f"<footer>{_escape_html(credit)}</footer>" if credit else ""
        return f"<blockquote>{quote}{foot}</blockquote>"
    if name in ("sect1", "sect2", "sect3", "sect4", "simple-section", "opener",
                "dedication", "acknowledgements", "preface", "foreword"):
        return section_to_html(el, images, heading_level=heading_level or {
            "sect1": 2, "sect2": 3, "sect3": 4, "sect4": 5,
            "simple-section": 2, "opener": 2,
            "dedication": 2, "acknowledgements": 2, "preface": 2, "foreword": 2,
        }.get(name, 2))
    if name in ("complex-meta", "simple-meta", "metadata-wrapper", "media",
                "media-object", "media-reference", "tgroup", "colspec"):
        return ""
    child_html = "".join(block_html(c, images, heading_level) for c in list(el))
    if not child_html and el.text and el.text.strip() and not list(el):
        return f"<p>{_escape_html(el.text.strip())}</p>"
    return child_html


def section_to_markdown(
    el: ET.Element,
    images: ImageResolver,
    *,
    heading_level: int = 2,
    include_own_title: bool = True,
) -> str:
    """Render a section element; nested sectN become deeper headings."""
    chunks: list[str] = []
    title = title_of(el)
    if not title and local(el.tag) == "dedication":
        title = "Dedication"
    if include_own_title and title and heading_level > 0:
        chunks.append(f"{'#' * heading_level} {title}")

    for child in list(el):
        cname = local(child.tag)
        if cname in ("complex-meta", "simple-meta", "metadata-wrapper"):
            continue
        if cname in ("sect1", "sect2", "sect3", "sect4", "simple-section"):
            nested_level = {
                "sect1": 2, "sect2": 3, "sect3": 4, "sect4": 5, "simple-section": heading_level + 1,
            }.get(cname, heading_level + 1)
            chunk = section_to_markdown(child, images, heading_level=nested_level)
        else:
            chunk = block_md(child, images, heading_level)
        if chunk:
            chunks.append(chunk)
    return "\n\n".join(chunks)


def section_to_html(
    el: ET.Element,
    images: ImageResolver,
    *,
    heading_level: int = 2,
    include_own_title: bool = True,
) -> str:
    chunks: list[str] = []
    title = title_of(el)
    if not title and local(el.tag) == "dedication":
        title = "Dedication"
    if include_own_title and title and heading_level > 0:
        chunks.append(f"<h{heading_level}>{_escape_html(title)}</h{heading_level}>")

    for child in list(el):
        cname = local(child.tag)
        if cname in ("complex-meta", "simple-meta", "metadata-wrapper"):
            continue
        if cname in ("sect1", "sect2", "sect3", "sect4", "simple-section"):
            nested_level = {
                "sect1": 2, "sect2": 3, "sect3": 4, "sect4": 5, "simple-section": heading_level + 1,
            }.get(cname, heading_level + 1)
            chunk = section_to_html(child, images, heading_level=nested_level)
        else:
            chunk = block_html(child, images, heading_level)
        if chunk:
            chunks.append(chunk)
    return "\n".join(chunks)


def _quiz_items(quiz_el: ET.Element) -> list[ET.Element]:
    """Collect short-answer / MC / TF / essay items under a quiz."""
    wanted = {
        "sa-item", "mc-item", "tf-item", "essay-item", "matching-item",
        "short-answer-item", "multiple-choice-item", "true-false-item",
    }
    items: list[ET.Element] = []
    seen: set[int] = set()
    for el in quiz_el.iter():
        name = local(el.tag)
        if name in wanted or (
            name.endswith("-item") and el.find(f".//{q('question')}") is not None
        ):
            ident = id(el)
            if ident not in seen:
                seen.add(ident)
                items.append(el)
    return items


def quiz_to_markdown(quiz_el: ET.Element) -> str:
    """``cl:quiz`` → markdown Q&A for an assessment / in-body quiz block."""
    lines: list[str] = ["# Chapter Exercises", ""]
    items = _quiz_items(quiz_el)
    for i, item in enumerate(items, start=1):
        q_el = item.find(f".//{q('question')}")
        a_el = item.find(f".//{q('answer')}")
        q_para = q_el.find(q("para")) if q_el is not None else None
        a_para = a_el.find(q("para")) if a_el is not None else None
        q_text = text_of(q_para if q_para is not None else q_el)
        a_text = text_of(a_para if a_para is not None else a_el)
        lines.append(f"**Q{i}.** {q_text}")
        # Multiple-choice / TF choices if present.
        for choice in item.findall(f".//{q('choice')}") + item.findall(f".//{q('distractor')}"):
            choice_text = text_of(choice)
            if choice_text:
                lines.append(f"- {choice_text}")
        if a_text:
            lines.append(f"*Answer:* {a_text}")
        lines.append("")
    if len(lines) <= 2:
        # Fallback: any question paras in the quiz.
        for i, q_el in enumerate(quiz_el.findall(f".//{q('question')}"), start=1):
            lines.append(f"**Q{i}.** {text_of(q_el)}")
            lines.append("")
    return "\n".join(lines).strip()


def quiz_to_html(quiz_el: ET.Element) -> str:
    md_lines = quiz_to_markdown(quiz_el).split("\n")
    parts = ['<div class="cendoc-quiz">']
    for line in md_lines:
        if line.startswith("# "):
            parts.append(f"<h2>{_escape_html(line[2:])}</h2>")
        elif line.startswith("**Q") and ".** " in line:
            parts.append(f"<p>{_escape_html(line.replace('**', ''))}</p>")
        elif line.startswith("- "):
            parts.append(f"<li>{_escape_html(line[2:])}</li>")
        elif line.startswith("*Answer:*"):
            parts.append(f"<p><em>{_escape_html(line)}</em></p>")
        elif line.strip():
            parts.append(f"<p>{_escape_html(line)}</p>")
    parts.append("</div>")
    return "".join(parts)
