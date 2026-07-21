"""Page-body HTML → markdown for the IMSCC importer (inverse of the exporter).

The exporter wraps every lesson as a Canvas wiki page::

    <html><head><title>…</title>…</head>
    <body>
      <style>…locked CSS…</style>
      <div class="cas-lesson"> …lesson HTML… </div>
    </body></html>

(see ``promptops_app/exporters/imscc_exporter.py`` +
``promptops_app/services/canvas_html_layout.py``). This module inverts that:
read the ``<title>``, take the ``<body>``, drop the locked ``<style>`` block,
unwrap ``.cas-lesson``, and convert the remaining fragment to markdown.

We keep the cleaned body HTML too — it is stored on ``Block.content_html`` as a
fidelity fallback so re-export can reproduce the original layout even where the
markdown round-trip is lossy (Ground-truth §Exporter gap #1).

``markdownify`` is the real converter (added to requirements). A small regex
fallback keeps the module importable/testable when the dependency is absent.
"""

from __future__ import annotations

import logging
import re
from html import unescape

_log = logging.getLogger(__name__)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_DOCTYPE_RE = re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE)
_HTML_SHELL_RE = re.compile(r"</?(?:html|head)[^>]*>", re.IGNORECASE)
_CAS_LESSON_OPEN_RE = re.compile(
    r"""<div\b[^>]*\bclass\s*=\s*["'][^"']*\bcas-lesson\b[^"']*["'][^>]*>""",
    re.IGNORECASE,
)
_DIV_TOKEN_RE = re.compile(r"<div\b[^>]*>|</div\s*>", re.IGNORECASE)
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def page_html_to_markdown(html: str) -> tuple[str, str, str]:
    """Convert a Canvas wiki-page HTML file to ``(title, markdown, raw_body_html)``.

    ``title`` comes from the ``<title>`` tag (empty if absent). ``markdown`` is
    the converted lesson body. ``raw_body_html`` is the cleaned body fragment
    (style stripped, ``.cas-lesson`` unwrapped) for ``Block.content_html``.
    """
    text = html or ""
    title = _extract_title(text)

    body = _extract_body(text)
    body = _STYLE_RE.sub("", body)
    body = _SCRIPT_RE.sub("", body)
    body = _unwrap_cas_lesson(body).strip()

    markdown = _fragment_to_markdown(body)
    return title, markdown, body


def _extract_title(html: str) -> str:
    match = _TITLE_RE.search(html)
    return unescape(match.group(1)).strip() if match else ""


def _extract_body(html: str) -> str:
    text = (html or "").strip()
    if not text:
        return ""
    match = _BODY_RE.search(text)
    if match:
        return match.group(1).strip()
    # No <body> wrapper — strip any stray doc shell and use the whole fragment.
    text = _DOCTYPE_RE.sub("", text)
    text = _HTML_SHELL_RE.sub("", text)
    return text.strip()


def _unwrap_cas_lesson(body: str) -> str:
    """Return the inner HTML of the first ``.cas-lesson`` div (balanced), or body."""
    match = _CAS_LESSON_OPEN_RE.search(body)
    if not match:
        return body
    start = match.end()
    depth = 1
    for token in _DIV_TOKEN_RE.finditer(body, start):
        if token.group(0).lower().startswith("</div"):
            depth -= 1
            if depth == 0:
                return body[start:token.start()]
        else:
            depth += 1
    return body[start:]  # unbalanced markup — take everything after the open tag


# ── conversion ────────────────────────────────────────────────────────────────

def _fragment_to_markdown(fragment: str) -> str:
    """Convert an HTML fragment to markdown (markdownify, regex fallback)."""
    if not fragment.strip():
        return ""
    try:
        from markdownify import markdownify as _markdownify
    except ImportError:  # pragma: no cover - exercised only without the dep
        _log.warning("markdownify not installed — using the regex HTML→markdown fallback.")
        markdown = _fallback_to_markdown(fragment)
    else:
        markdown = _markdownify(fragment, heading_style="ATX", bullets="-")
    return _BLANK_LINES_RE.sub("\n\n", markdown).strip()


def _fallback_to_markdown(html: str) -> str:
    """Minimal HTML→markdown used only when ``markdownify`` is unavailable."""
    text = html
    text = _STYLE_RE.sub("", text)
    text = _SCRIPT_RE.sub("", text)

    for level in range(1, 7):
        text = re.sub(
            rf"<h{level}[^>]*>(.*?)</h{level}>",
            lambda m, lv=level: f"\n\n{'#' * lv} {_strip_inline(m.group(1))}\n\n",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

    text = re.sub(r"<(strong|b)\b[^>]*>(.*?)</\1>", r"**\2**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<(em|i)\b[^>]*>(.*?)</\1>", r"*\2*", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<code\b[^>]*>(.*?)</code>", r"`\1`", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(
        r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        r"[\2](\1)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<li\b[^>]*>(.*?)</li>", lambda m: f"- {_strip_inline(m.group(1))}\n", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:div|section|article|ul|ol|blockquote|tr)\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def _strip_inline(html: str) -> str:
    """Collapse an inline HTML fragment to whitespace-normalised text."""
    text = re.sub(r"<[^>]+>", "", html or "")
    return unescape(re.sub(r"\s+", " ", text)).strip()
