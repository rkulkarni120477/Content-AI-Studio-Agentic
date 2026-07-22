"""Text cleaning helpers for exports.

Generated content is Markdown-flavoured (e.g. ``**Course Title:**``). When it
is dropped verbatim into a Word/Excel/plain-text export, the raw emphasis
markers (``**``, ``__``, backticks) show up as literal symbols. These helpers
strip the inline markers while keeping the words — and, importantly, leave
line-leading structure markers (``#`` headings, ``-``/``*``/``•`` bullets)
untouched so the DOCX exporter can still render them as real headings/bullets.
"""

from __future__ import annotations

import re

# Inline emphasis / code — keep the inner text, drop the markers.
_BOLD_STAR = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)   # **bold**
_BOLD_UNDER = re.compile(r"__(.+?)__", re.DOTALL)       # __bold__
_INLINE_CODE = re.compile(r"`([^`]+?)`")                # `code`


def strip_inline_md(text: str) -> str:
    """Remove inline Markdown emphasis/code markers, keeping the words.

    Line-leading markers (headings, bullets) are deliberately preserved.
    """
    if not text:
        return text
    text = _BOLD_STAR.sub(r"\1", text)
    text = _BOLD_UNDER.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    # Drop any unbalanced/leftover bold markers so no "**" ever reaches output.
    text = text.replace("**", "")
    return text


def clean_blocks(blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return a copy of (label, content) blocks with inline markers stripped."""
    return [(lbl, strip_inline_md(cnt)) for lbl, cnt in blocks]
