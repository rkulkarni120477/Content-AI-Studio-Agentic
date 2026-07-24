"""Canvas HTML Lesson Generator.

Builds a production-quality, Canvas-LMS-compatible HTML lesson from a block's
markdown. Uses a deterministic markdown→HTML path (full content preserved) plus
a locked single-column layout — not an LLM rewrite, which often truncates text
and emits multi-column tables that Canvas breaks.

The public entry point ``generate_canvas_html`` never raises — it returns the
HTML string on success or ``None`` on empty input / conversion failure, so
publishing a block is never blocked.
"""

from __future__ import annotations

import logging
import re
from html import escape
from typing import TYPE_CHECKING, Optional

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from promptops_app.services.usage_service import UsageLogContext


# Production / media-spec lines → learner-facing placeholder callouts.
_MEDIA_LINE_RE = re.compile(
    r"^(?P<prefix>\s*(?:[-*+]|\d+[.)])?\s*)"
    r"(?P<body>"
    r"(?:\[(?:image|img|visual|video|animation|audio|interactive)[^\]]*\])|"
    r"(?:(?:image|img|visual|video|animation|audio|interactive|infographic|illustration)"
    r"\s*(?:placeholder|spec|description)?\s*[:—-].+)|"
    r"(?:📷|▶|✨).+"
    r")\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_PLACEHOLDER_CLASS = {
    "video": "placeholder",
    "animation": "placeholder",
    "audio": "placeholder",
    "interactive": "placeholder",
}


def _placeholder_label(text: str) -> str:
    lower = text.lower()
    if "video" in lower or text.strip().startswith("▶"):
        return "▶ Video"
    if "animation" in lower or "✨" in text:
        return "✨ Animation"
    if "audio" in lower:
        return "🔊 Audio"
    if "interactive" in lower:
        return "✨ Interactive"
    return "📷 Visual"


def _rewrite_media_specs(markdown: str) -> str:
    """Turn media/production spec lines into HTML placeholder markers in markdown."""

    def repl(match: re.Match) -> str:
        body = match.group("body").strip()
        # Strip wrapping [spec] brackets for cleaner placeholder text.
        body = re.sub(r"^\[|\]$", "", body).strip()
        label = _placeholder_label(body)
        # Keep description after the first colon/dash when present.
        desc = re.sub(
            r"^(?:image|img|visual|video|animation|audio|interactive|infographic|illustration)"
            r"\s*(?:placeholder|spec|description)?\s*[:—-]\s*",
            "",
            body,
            flags=re.IGNORECASE,
        ).strip() or body
        desc = re.sub(r"^[📷▶✨🔊]\s*", "", desc).strip()
        safe = escape(f"{label}: {desc}")
        return f'\n\n<div class="placeholder">{safe}</div>\n\n'

    return _MEDIA_LINE_RE.sub(repl, markdown or "")


def _wrap_activity_sections(html: str) -> str:
    """Lightly mark common activity headings for card styling."""
    # Wrap blocks that start with common check headings — best-effort, non-destructive.
    pattern = re.compile(
        r"(<h[2-4][^>]*>\s*(?:Check Your Understanding|Try This|Knowledge Check|"
        r"Career Exploration|Multiple Choice|Quick Check|Reflection)\s*</h[2-4]>)"
        r"(.*?)(?=<h[1-4]\b|$)",
        re.IGNORECASE | re.DOTALL,
    )

    def repl(match: re.Match) -> str:
        return (
            f'<div class="check">\n{match.group(1)}\n{match.group(2).strip()}\n</div>\n'
        )

    return pattern.sub(repl, html)


def _wrap_objectives(html: str) -> str:
    pattern = re.compile(
        r"(<h[2-4][^>]*>\s*Learning Objectives\s*</h[2-4]>)"
        r"(.*?)(?=<h[1-4]\b|$)",
        re.IGNORECASE | re.DOTALL,
    )

    def repl(match: re.Match) -> str:
        return (
            f'<div class="objectives">\n{match.group(1)}\n{match.group(2).strip()}\n</div>\n'
        )

    return pattern.sub(repl, html)


def _plain_heading_text(html_inner: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html_inner or "")).strip()


def _normalize_title(text: str) -> str:
    """Normalize titles for duplicate detection (Canvas page title vs body headings)."""
    t = (text or "").strip().lower()
    t = re.sub(r"^lesson[_\s-]?\d+\s*[-:–—]\s*", "", t)
    t = re.sub(r"^lesson\s+\d+\s*[-:–—]\s*", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" -:–—")


def _titles_match(a: str, b: str) -> bool:
    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _strip_duplicate_page_titles(body_html: str, label: str) -> str:
    """Remove leading H1/H2 that repeat the Canvas page title.

    Canvas already renders the wiki Page title above the body, so repeating it
    in the HTML looks like a double (or triple) heading.
    """
    html = (body_html or "").lstrip()
    label = (label or "").strip()

    # Always drop a leading H1 — Canvas owns the page title slot.
    leading_h1 = re.match(r"<h1[^>]*>(.*?)</h1>\s*", html, flags=re.IGNORECASE | re.DOTALL)
    if leading_h1:
        html = html[leading_h1.end() :].lstrip()

    # Drop following H1/H2 when they restate the same lesson title.
    for _ in range(3):
        m = re.match(r"<h([12])[^>]*>(.*?)</h\1>\s*", html, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            break
        heading = _plain_heading_text(m.group(2))
        if label and _titles_match(heading, label):
            html = html[m.end() :].lstrip()
            continue
        break

    return html


def build_canvas_html_from_markdown(label: str, content: str) -> str:
    """Convert full markdown into a locked single-column Canvas HTML document.

    Does not inject a page H1 — Canvas Pages already show the wiki title.
    """
    from promptops_app.exporters.markdown_html import markdown_to_html
    from promptops_app.services.canvas_html_layout import lock_single_column_html
    from promptops_app.services.content_sanitizer import sanitize_html

    rewritten = _rewrite_media_specs(content or "")
    body_html = markdown_to_html(rewritten)
    body_html = _strip_duplicate_page_titles(body_html, label or "")
    body_html = _wrap_objectives(body_html)
    body_html = _wrap_activity_sections(body_html)

    fragment = f'<div class="cas-lesson">\n{body_html}\n</div>'
    # Allowlist-sanitize the content fragment (removes scripts, event handlers,
    # javascript: URLs, and non-YouTube iframes) BEFORE the trusted single-column
    # layout + <style> is applied — layout classes are allowlisted, so styling
    # survives while any unsafe authored/generated HTML is stripped.
    fragment = sanitize_html(fragment)
    return lock_single_column_html(fragment)


def generate_canvas_html(
    label: str,
    content: str,
    block_type: str = "",
    model_choice: Optional[str] = None,
    usage_ctx: Optional["UsageLogContext"] = None,
) -> Optional[str]:
    """Generate a Canvas-ready standalone HTML lesson from markdown content.

    Preserves the full lesson text (no LLM summarization). ``model_choice`` and
    ``usage_ctx`` are accepted for API compatibility but unused.
    """
    del model_choice, usage_ctx, block_type  # API compatibility
    text = (content or "").strip()
    if not text:
        return None

    try:
        html = build_canvas_html_from_markdown(label or "", text)
        if not html or not html.strip():
            return None
        return html
    except Exception:  # pragma: no cover - defensive; publish must not break
        _log.exception("canvas_html_generation_exception label=%r", label)
        return None
