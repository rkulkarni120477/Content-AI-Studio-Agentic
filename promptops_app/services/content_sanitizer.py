"""
Backend content sanitization (defense-in-depth).

Two entry points, matching the requirement's "sanitize before saving and before
rendering":

* ``sanitize_html(html)`` — allowlist sanitizer for **generated HTML**. Built on
  ``nh3`` (Rust/ammonia) with a whitelist mirroring the frontend editor (plus
  layout ``class``/``id``), YouTube-only ``<iframe>`` src enforcement,
  ``data:image`` restricted to ``<img>``, and ``style`` reduced to ``text-align``.
  Wired into ``canvas_html_service.build_canvas_html_from_markdown`` — it cleans
  the content fragment before the trusted single-column layout/``<style>`` is
  added, so it is the authoritative barrier on the LMS/export render path while
  leaving that trusted layout intact.

* ``sanitize_stored_content(markdown)`` — a **Markdown-safe** scrub applied on
  save. Stored block content is Markdown with small inline-HTML islands, so we
  must NOT run an HTML sanitizer over the whole string (it would HTML-escape
  prose like ``a < b`` and ``A & B``). Instead this strips only unambiguously
  dangerous, tag-shaped constructs and leaves plain Markdown untouched (with a
  fast path for the common pure-Markdown case). Defense-in-depth on the write
  path; the render-time ``sanitize_html`` above is the authoritative barrier.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import nh3

# ---------------------------------------------------------------------------
# Allowlist.
# KEEP IN SYNC with the frontend allowlist in `frontend/src/utils/markdownHtml.js`
# (ALLOWED_TAGS / ALLOWED_ATTR / ALLOWED_EMBED_HOSTS). Changes on one side should
# be mirrored on the other.
# ---------------------------------------------------------------------------

ALLOWED_TAGS: set[str] = {
    "p", "br", "span", "div",
    "strong", "b", "em", "i", "u", "s", "del", "mark", "sub", "sup",
    "code", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "blockquote", "hr",
    "a", "img",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "figure", "figcaption",
    "iframe",
}

ALLOWED_ATTRS: dict[str, set[str]] = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "iframe": {"src", "width", "height", "allow", "allowfullscreen", "frameborder"},
    # class/id are needed so generated export/Canvas layout classes (objectives,
    # check, cas-lesson, placeholder, code highlight) survive sanitization. They
    # carry no execution risk.
    "*": {"style", "align", "colspan", "rowspan", "class", "id"},
}

URL_SCHEMES: set[str] = {"http", "https", "mailto", "tel", "data"}

ALLOWED_EMBED_HOSTS: set[str] = {
    "www.youtube.com", "youtube.com",
    "www.youtube-nocookie.com", "youtube-nocookie.com",
}

_TEXT_ALIGN_RE = re.compile(r"text-align\s*:\s*(left|right|center|justify)", re.I)


def is_allowed_embed_src(src: str | None) -> bool:
    """True only for an https YouTube /embed/ URL."""
    try:
        u = urlparse((src or "").strip())
    except ValueError:
        return False
    return (
        u.scheme == "https"
        and u.netloc in ALLOWED_EMBED_HOSTS
        and u.path.startswith("/embed/")
    )


def _attribute_filter(tag: str, attr: str, value: str) -> str | None:
    """nh3 per-attribute hook. Return a value to keep, or None to drop it."""
    if attr == "style":
        m = _TEXT_ALIGN_RE.search(value or "")
        return f"text-align: {m.group(1).lower()}" if m else None
    if tag == "iframe" and attr == "src":
        return value if is_allowed_embed_src(value) else None
    if attr == "src" and value.strip().lower().startswith("data:"):
        # data: URIs are only allowed on <img>, and only for images.
        low = value.strip().lower()
        return value if (tag == "img" and low.startswith("data:image/")) else None
    if attr == "href" and value.strip().lower().startswith("data:"):
        return None
    return value


def sanitize_html(html: str | None) -> str:
    """Allowlist-sanitize generated HTML before it is rendered/exported."""
    if not html:
        return ""
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        url_schemes=URL_SCHEMES,
        attribute_filter=_attribute_filter,
        link_rel="noopener noreferrer",
    )


# ---------------------------------------------------------------------------
# Markdown-safe save-time scrub
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[a-zA-Z/!]")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_DANGLING_SCRIPT_STYLE_RE = re.compile(r"</?(script|style)\b[^>]*>", re.I)
_EVENT_HANDLER_RE = re.compile(r"""\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.I)
_DANGEROUS_URL_ATTR_RE = re.compile(
    r"""\s(?:href|src)\s*=\s*("|')?\s*(?:javascript|vbscript|data:text/html)[^\s>"']*\1?""",
    re.I,
)
_IFRAME_RE = re.compile(r"<iframe\b[^>]*>(?:</iframe>)?", re.I)
_SRC_RE = re.compile(r"""src\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)


def _scrub_iframe(match: re.Match) -> str:
    tag = match.group(0)
    m = _SRC_RE.search(tag)
    src = next((g for g in (m.group(1), m.group(2), m.group(3)) if g), "") if m else ""
    return tag if is_allowed_embed_src(src) else ""


def sanitize_stored_content(content: str | None) -> str:
    """
    Strip dangerous HTML constructs from stored Markdown WITHOUT corrupting prose.

    Fast path: content with no HTML tags is returned unchanged. Otherwise only
    tag-shaped dangerous patterns are removed (script/style blocks, event-handler
    attributes, javascript:/vbscript:/data:text-html URLs, non-YouTube iframes).
    """
    if not content or not _HTML_TAG_RE.search(content):
        return content or ""

    cleaned = _SCRIPT_STYLE_RE.sub("", content)
    cleaned = _DANGLING_SCRIPT_STYLE_RE.sub("", cleaned)
    cleaned = _EVENT_HANDLER_RE.sub("", cleaned)
    cleaned = _DANGEROUS_URL_ATTR_RE.sub("", cleaned)
    cleaned = _IFRAME_RE.sub(_scrub_iframe, cleaned)
    return cleaned
