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
                term = text_of(child.find(q("key-term"))) or text_of(child)
                parts.append(f"**{term}**")
            elif name == "key-term":
                parts.append(f"**{text_of(child)}**")
            elif name == "uri":
                label = text_of(child) or child.get("link-target") or ""
                href = child.get("link-target") or label
                parts.append(f"[{label}]({href})")
            elif name == "footnote":
                # Drop footnote body; keep a marker.
                parts.append("[^]")
            elif name == "ref":
                composed = child.find(q("composed-content"))
                parts.append(text_of(composed) or text_of(child))
            elif name == "learn-obj":
                parts.append(text_of(child))
            elif name == "list":
                # Nested list inside para — render as inline bullets on new lines later.
                parts.append("\n" + _list_md(child, images))
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
                if "bold" in styles:
                    parts.append(f"<strong>{inner}</strong>")
                elif "italic" in styles:
                    parts.append(f"<em>{inner}</em>")
                else:
                    parts.append(inner)
            elif name == "key-term-entry":
                term = text_of(child.find(q("key-term"))) or text_of(child)
                parts.append(f"<strong>{_escape_html(term)}</strong>")
            elif name == "uri":
                label = text_of(child) or child.get("link-target") or ""
                href = child.get("link-target") or label
                parts.append(
                    f'<a href="{_escape_html(href)}">{_escape_html(label)}</a>'
                )
            elif name == "footnote":
                parts.append("<sup>*</sup>")
            elif name == "list":
                parts.append(_list_html(child, images))
            else:
                walk(child)
            if child.tail:
                parts.append(_escape_html(child.tail))

    walk(el)
    return "".join(parts)


def _list_md(el: ET.Element, images: ImageResolver | None = None) -> str:
    style = (el.get("list-style") or "").lower()
    ordered = "ordered" in style or el.get("numeration")
    lines: list[str] = []
    for i, item in enumerate(el.findall(q("item")), start=1):
        para = item.find(q("para"))
        body = _inline_md(para, images) if para is not None else text_of(item)
        label = item.get("manual-label")
        if ordered:
            prefix = f"{label}. " if label else f"{i}. "
        else:
            prefix = "- "
        lines.append(f"{prefix}{body}")
    return "\n".join(lines)


def _list_html(el: ET.Element, images: ImageResolver | None = None) -> str:
    style = (el.get("list-style") or "").lower()
    ordered = "ordered" in style or el.get("numeration")
    tag = "ol" if ordered else "ul"
    items: list[str] = []
    for item in el.findall(q("item")):
        para = item.find(q("para"))
        body = _inline_html(para, images) if para is not None else _escape_html(text_of(item))
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


def _table_md(el: ET.Element) -> str:
    rows = el.findall(f".//{q('row')}")
    if not rows:
        return ""
    grid: list[list[str]] = []
    for row in rows:
        cells = row.findall(q("entry"))
        grid.append([text_of(c).replace("|", "\\|") for c in cells] or [""])
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
    rows = el.findall(f".//{q('row')}")
    if not rows:
        return ""
    parts = ["<table>"]
    for i, row in enumerate(rows):
        cells = row.findall(q("entry"))
        tag = "th" if i == 0 else "td"
        parts.append("<tr>")
        for c in cells:
            parts.append(f"<{tag}>{_escape_html(text_of(c))}</{tag}>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


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
    if name == "sidebar":
        return _sidebar_md(el, images)
    if name == "excerpt":
        quote = _inline_md(el.find(q("para")) or el, images)
        credit = text_of(el.find(q("credit-byline")))
        out = f"> {quote}"
        if credit:
            out += f"\n>\n> — {credit}"
        return out
    if name in ("sect1", "sect2", "sect3", "sect4", "simple-section", "opener"):
        return section_to_markdown(el, images, heading_level=heading_level or {
            "sect1": 2, "sect2": 3, "sect3": 4, "sect4": 5,
            "simple-section": 2, "opener": 2,
        }.get(name, 2))
    # Skip structural/meta wrappers by walking children.
    if name in ("complex-meta", "simple-meta", "metadata-wrapper", "media",
                "media-object", "media-reference"):
        return ""
    parts = [block_md(c, images, heading_level) for c in list(el)]
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
    if name == "sidebar":
        return _sidebar_html(el, images)
    if name == "excerpt":
        quote = _inline_html(el.find(q("para")) or el, images)
        credit = text_of(el.find(q("credit-byline")))
        foot = f"<footer>{_escape_html(credit)}</footer>" if credit else ""
        return f"<blockquote>{quote}{foot}</blockquote>"
    if name in ("sect1", "sect2", "sect3", "sect4", "simple-section", "opener"):
        return section_to_html(el, images, heading_level=heading_level or {
            "sect1": 2, "sect2": 3, "sect3": 4, "sect4": 5,
            "simple-section": 2, "opener": 2,
        }.get(name, 2))
    if name in ("complex-meta", "simple-meta", "metadata-wrapper", "media",
                "media-object", "media-reference"):
        return ""
    return "".join(block_html(c, images, heading_level) for c in list(el))


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
    if include_own_title and title and heading_level > 0:
        chunks.append(f"{'#' * heading_level} {title}")

    for child in list(el):
        cname = local(child.tag)
        if cname in ("complex-meta", "simple-meta", "metadata-wrapper", "quiz"):
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
    if include_own_title and title and heading_level > 0:
        chunks.append(f"<h{heading_level}>{_escape_html(title)}</h{heading_level}>")

    for child in list(el):
        cname = local(child.tag)
        if cname in ("complex-meta", "simple-meta", "metadata-wrapper", "quiz"):
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


def quiz_to_markdown(quiz_el: ET.Element) -> str:
    """Short-answer ``cl:quiz`` → markdown Q&A for an assessment block."""
    lines: list[str] = ["# Chapter Exercises", ""]
    items = quiz_el.findall(f".//{q('sa-item')}")
    for i, item in enumerate(items, start=1):
        q_el = item.find(f".//{q('question')}")
        a_el = item.find(f".//{q('answer')}")
        q_para = q_el.find(q("para")) if q_el is not None else None
        a_para = a_el.find(q("para")) if a_el is not None else None
        q_text = text_of(q_para if q_para is not None else q_el)
        a_text = text_of(a_para if a_para is not None else a_el)
        lines.append(f"**Q{i}.** {q_text}")
        if a_text:
            lines.append(f"*Answer:* {a_text}")
        lines.append("")
    return "\n".join(lines).strip()
