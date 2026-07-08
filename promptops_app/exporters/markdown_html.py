"""Convert markdown block content to safe HTML for export pipelines."""

from __future__ import annotations

import re

# Lazy import — markdown is optional at import time but required for exports.
_md = None


def _get_markdown():
    global _md
    if _md is None:
        import markdown as md_lib
        _md = md_lib
    return _md


def markdown_to_html(content: str) -> str:
    """Render markdown (or plain text) as an HTML fragment.

    Uses the ``markdown`` library with common extensions.  Falls back to a
    minimal converter when the library is unavailable.
    """
    text = (content or "").strip()
    if not text:
        return ""

    try:
        md = _get_markdown()
        return md.markdown(
            text,
            extensions=["extra", "nl2br", "sane_lists", "tables"],
            output_format="html5",
        )
    except Exception:
        return _fallback_markdown_to_html(text)


def _fallback_markdown_to_html(text: str) -> str:
    """Lightweight markdown converter for headings, lists, emphasis, and links."""
    lines = text.splitlines()
    out: list[str] = []
    in_ul = False
    in_ol = False

    def close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            close_lists()
            continue

        hm = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if hm:
            close_lists()
            level = len(hm.group(1))
            out.append(f"<h{level}>{_inline(hm.group(2))}</h{level}>")
            continue

        if re.match(r"^[-*+]\s+", stripped):
            if not in_ul:
                close_lists()
                in_ul = True
                out.append("<ul>")
            item = re.sub(r"^[-*+]\s+", "", stripped)
            out.append(f"<li>{_inline(item)}</li>")
            continue

        if re.match(r"^\d+[\.\)]\s+", stripped):
            if not in_ol:
                close_lists()
                in_ol = True
                out.append("<ol>")
            item = re.sub(r"^\d+[\.\)]\s+", "", stripped)
            out.append(f"<li>{_inline(item)}</li>")
            continue

        close_lists()
        out.append(f"<p>{_inline(stripped)}</p>")

    close_lists()
    return "\n".join(out)


def _inline(text: str) -> str:
    """Apply inline markdown transforms to already-escaped-safe segments."""
    # Links and images first (before escaping would break them)
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        r'<img alt="\1" src="\2" />',
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text
