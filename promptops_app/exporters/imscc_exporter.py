"""IMS Common Cartridge 1.1 package exporter.

Builds a ``.imscc`` ZIP archive containing:
  - ``imsmanifest.xml``  — organization tree (TOC) + resource manifest
  - ``wiki_content/*.html`` — one HTML page per content block
  - ``assessment/*.xml`` — QTI 1.2 quizzes for assessment blocks (Canvas/Moodle/Blackboard)

The package is compatible with Canvas, Moodle, and other LMS platforms
that accept IMS CC 1.1 imports.
"""

from __future__ import annotations

import re
import uuid
import zipfile
from html import escape
from io import BytesIO
from typing import NamedTuple

from promptops_app.exporters.markdown_html import markdown_to_html
from promptops_app.exporters.qti_exporter import ASSESSMENT_RESOURCE_TYPE, build_qti_from_content
from promptops_app.exporters.qti_parser import is_assessment_block

_MANIFEST_NS = (
    'xmlns="http://www.imsglobal.org/xsd/imscp_v1p1" '
    'xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_v1p3" '
    'xmlns:imsmd="http://www.imsglobal.org/xsd/imsmd_v1p2" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)

_PAGE_CSS = """
  body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    max-width: 960px; margin: 40px auto; padding: 0 24px 48px;
    line-height: 1.75; color: #1e293b; background: #fff;
  }
  h1 { color: #1e40af; font-size: 1.6rem; border-bottom: 2px solid #e2e8f0;
       padding-bottom: 0.5rem; margin-bottom: 1.5rem; }
  h2, h3, h4 { color: #334155; margin-top: 1.5rem; }
  p  { margin: 0.75rem 0; }
  ul, ol { margin: 0.75rem 0 0.75rem 1.5rem; }
  li { margin: 0.35rem 0; }
  code { background: #f1f5f9; padding: 0.1rem 0.35rem; border-radius: 4px;
         font-size: 0.9em; }
  pre  { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
         padding: 1rem; overflow-x: auto; }
  blockquote { border-left: 4px solid #93c5fd; margin: 1rem 0; padding: 0.5rem 1rem;
               background: #f8fafc; color: #475569; }
  table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
  th, td { border: 1px solid #e2e8f0; padding: 0.5rem 0.75rem; text-align: left; }
  th { background: #f1f5f9; }
  img { max-width: 100%; height: auto; border-radius: 8px; margin: 1rem 0; }
  a   { color: #2563eb; }
"""

_IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_IMG_HTML_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_VIDEO_RE = re.compile(
    r"(?:\[video[:\s][^\]]*\]|"
    r"<video[^>]*>|"
    r"https?://(?:www\.)?(?:youtube\.com|youtu\.be|vimeo\.com)[^\s\)]*|"
    r"[^\s\)]+\.(?:mp4|webm|mov|m4v))",
    re.IGNORECASE,
)


class _ManifestEntry(NamedTuple):
    item_id: str
    resource_id: str
    label: str
    href: str
    resource_type: str


def _normalize_blocks(
    blocks: list[tuple[str, ...]],
) -> list[tuple[str, str, str, str]]:
    """Normalize export blocks to (label, content, block_type, content_html) quads.

    Accepts 2-, 3-, or 4-element tuples for backward compatibility:
      (label, content)
      (label, content, block_type)
      (label, content, block_type, content_html)
    """
    normalized: list[tuple[str, str, str, str]] = []
    for entry in blocks:
        label = entry[0] if len(entry) > 0 else ""
        content = entry[1] if len(entry) > 1 else ""
        block_type = (entry[2] or "") if len(entry) > 2 else ""
        content_html = (entry[3] or "") if len(entry) > 3 else ""
        normalized.append((label, content, block_type, content_html))
    return normalized


class _MediaRef(NamedTuple):
    original: str
    placeholder_path: str
    media_type: str  # "image" | "video"


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _placeholder_svg(label: str, media_type: str) -> bytes:
    """Return a minimal SVG placeholder for a missing image or video asset."""
    colour = "#3b82f6" if media_type == "image" else "#7c3aed"
    icon = "🖼" if media_type == "image" else "▶"
    safe_label = escape(label[:60])
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
  <rect width="640" height="360" fill="#f1f5f9"/>
  <rect x="20" y="20" width="600" height="320" rx="12" fill="#e2e8f0" stroke="{colour}" stroke-width="2"/>
  <text x="320" y="155" text-anchor="middle" font-family="Segoe UI,sans-serif" font-size="48">{icon}</text>
  <text x="320" y="210" text-anchor="middle" font-family="Segoe UI,sans-serif" font-size="16" fill="#475569">{safe_label}</text>
  <text x="320" y="240" text-anchor="middle" font-family="Segoe UI,sans-serif" font-size="13" fill="#94a3b8">Placeholder — replace with actual asset</text>
</svg>"""
    return svg.encode("utf-8")


def _detect_media(content: str, block_index: int) -> list[_MediaRef]:
    """Find image/video references in block content and assign placeholder paths."""
    refs: list[_MediaRef] = []
    seen: set[str] = set()
    counter = 0

    for match in _IMG_MD_RE.finditer(content):
        url = match.group(2).strip()
        if url in seen or url.startswith("#"):
            continue
        seen.add(url)
        counter += 1
        path = f"../web_resources/block{block_index}_img{counter}.svg"
        refs.append(_MediaRef(url, path, "image"))

    for match in _IMG_HTML_RE.finditer(content):
        url = match.group(1).strip()
        if url in seen:
            continue
        seen.add(url)
        counter += 1
        path = f"../web_resources/block{block_index}_img{counter}.svg"
        refs.append(_MediaRef(url, path, "image"))

    for match in _VIDEO_RE.finditer(content):
        url = match.group(0).strip()
        if url in seen:
            continue
        seen.add(url)
        counter += 1
        path = f"../web_resources/block{block_index}_vid{counter}.svg"
        refs.append(_MediaRef(url, path, "video"))

    return refs


def _rewrite_content_with_placeholders(content: str, refs: list[_MediaRef]) -> str:
    """Replace external media URLs with in-package placeholder paths."""
    result = content
    for ref in refs:
        result = result.replace(ref.original, ref.placeholder_path)
    return result


def _block_html(label: str, content: str, refs: list[_MediaRef]) -> str:
    """Build a single block HTML page with markdown rendered to proper HTML."""
    rewritten = _rewrite_content_with_placeholders(content, refs)
    body_html = markdown_to_html(rewritten)
    safe_title = escape(label or "Content")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title}</title>
  <style>{_PAGE_CSS}</style>
</head>
<body>
  <h1>{safe_title}</h1>
  <div class="content">
    {body_html}
  </div>
</body>
</html>"""


def _build_manifest(topic: str, modules: list[tuple[str, list[_ManifestEntry]]]) -> str:
    """Build imsmanifest.xml with a nested, multi-module organization tree.

    Canvas (and most LMSs) build the Table of Contents / Modules from a *nested*
    organization tree, not a flat list of items. The required shape is:

        <organization>
          <item>                          <!-- root wrapper, no title/ref -->
            <item><title>Module 1</title>   <!-- module = container, no ref -->
              <item identifierref=..>       <!-- leaf = links to a resource -->
                <title>Page</title>
              </item>
            </item>
            <item><title>Module 2</title> ... </item>
          </item>
        </organization>

    Each entry in ``modules`` is ``(module_title, [_ManifestEntry, ...])`` and
    becomes one module container. A flat list of leaf items directly under
    <organization> imports the pages but produces no module/TOC in Canvas.
    """
    manifest_id = _uid("manifest")
    org_id = _uid("org")
    root_id = _uid("root")

    safe_topic = escape(topic)

    module_xml = []
    all_entries: list[_ManifestEntry] = []
    for module_title, entries in modules:
        if not entries:
            continue
        all_entries.extend(entries)
        module_uid = _uid("module")
        leaf_xml = []
        for entry in entries:
            safe_label = escape(entry.label)
            leaf_xml.append(
                f'          <item identifier="{entry.item_id}" identifierref="{entry.resource_id}">\n'
                f"            <title>{safe_label}</title>\n"
                f"          </item>"
            )
        module_xml.append(
            f'        <item identifier="{module_uid}">\n'
            f"          <title>{escape(module_title)}</title>\n"
            f"{chr(10).join(leaf_xml)}\n"
            f"        </item>"
        )

    organization_body = (
        f'      <item identifier="{root_id}">\n'
        f"{chr(10).join(module_xml)}\n"
        f"      </item>"
    )

    resource_xml = []
    for entry in all_entries:
        if entry.resource_type == ASSESSMENT_RESOURCE_TYPE:
            resource_xml.append(
                f'    <resource identifier="{entry.resource_id}" '
                f'type="{ASSESSMENT_RESOURCE_TYPE}" href="{entry.href}">\n'
                f'      <file href="{entry.href}"/>\n'
                f"    </resource>"
            )
        else:
            resource_xml.append(
                f'    <resource identifier="{entry.resource_id}" type="webcontent" '
                f'adlcp:scormType="asset" href="{entry.href}">\n'
                f'      <file href="{entry.href}"/>\n'
                f"    </resource>"
            )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="{manifest_id}" {_MANIFEST_NS}>
  <metadata>
    <schema>IMS Common Cartridge</schema>
    <schemaversion>1.1.0</schemaversion>
    <imsmd:lom>
      <imsmd:general>
        <imsmd:title>
          <imsmd:langstring xml:lang="en">{safe_topic}</imsmd:langstring>
        </imsmd:title>
      </imsmd:general>
    </imsmd:lom>
  </metadata>
  <organizations>
    <organization identifier="{org_id}" structure="rooted-hierarchy">
{organization_body}
    </organization>
  </organizations>
  <resources>
{chr(10).join(resource_xml)}
  </resources>
</manifest>
"""


def build_imscc(
    topic: str,
    blocks: list[tuple[str, ...]],
    base_filename: str = "course",
    modules: list[tuple[str, list[int]]] | None = None,
) -> BytesIO:
    """Build an IMS Common Cartridge 1.1 package as a BytesIO ZIP stream.

    Parameters
    ----------
    topic : Course title used in the manifest organization.
    blocks : Ordered list of tuples. Each may be
        ``(label, content)``, ``(label, content, block_type)``, or
        ``(label, content, block_type, content_html)``. When ``content_html`` is
        present it is a complete standalone HTML document (the Canvas HTML
        rendition generated at publish) and is packaged verbatim; otherwise the
        markdown ``content`` is converted to HTML at export time.
    base_filename : Unused at runtime; kept for API symmetry with other exporters.
    modules : Optional list of ``(module_title, [block_index, ...])`` where each
        index is 0-based into ``blocks``. Blocks not referenced by any module are
        appended to a trailing "Course Content" module. When omitted, all blocks
        go into a single module titled after ``topic``.
    """
    buf = BytesIO()
    all_media: list[tuple[str, bytes]] = []
    normalized = _normalize_blocks(blocks)
    entries_by_index: dict[int, _ManifestEntry] = {}

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, (label, content, block_type, content_html) in enumerate(normalized, start=1):
            zero_idx = idx - 1
            item_id = _uid("item")
            resource_id = _uid("res")
            display_label = label or f"Block {idx}"

            if is_assessment_block(block_type, label):
                qti_xml, q_count = build_qti_from_content(display_label, content or "")
                if q_count > 0:
                    href = f"assessment/block_{idx}.xml"
                    zf.writestr(href, qti_xml.encode("utf-8"))
                    entries_by_index[zero_idx] = _ManifestEntry(
                        item_id, resource_id, display_label, href, ASSESSMENT_RESOURCE_TYPE,
                    )
                    continue

            href = f"wiki_content/block_{idx}.html"
            if content_html and content_html.strip():
                # Pre-rendered Canvas HTML — package as-is (self-contained doc).
                zf.writestr(href, content_html.encode("utf-8"))
            else:
                refs = _detect_media(content or "", idx)
                for ref in refs:
                    zip_path = ref.placeholder_path.replace("../", "")
                    all_media.append((zip_path, _placeholder_svg(ref.original, ref.media_type)))
                html = _block_html(label, content or "", refs)
                zf.writestr(href, html.encode("utf-8"))

            entries_by_index[zero_idx] = _ManifestEntry(
                item_id, resource_id, display_label, href, "webcontent",
            )

        written_paths: set[str] = set()
        for path, data in all_media:
            if path not in written_paths:
                zf.writestr(path, data)
                written_paths.add(path)

        grouped = _group_into_modules(topic, entries_by_index, modules)
        manifest = _build_manifest(topic, grouped)
        zf.writestr("imsmanifest.xml", manifest.encode("utf-8"))

    buf.seek(0)
    return buf


def _group_into_modules(
    topic: str,
    entries_by_index: dict[int, _ManifestEntry],
    modules: list[tuple[str, list[int]]] | None,
) -> list[tuple[str, list[_ManifestEntry]]]:
    """Group manifest entries into ``(module_title, entries)`` tuples."""
    if not modules:
        ordered = [entries_by_index[i] for i in sorted(entries_by_index)]
        return [(topic, ordered)]

    grouped: list[tuple[str, list[_ManifestEntry]]] = []
    used: set[int] = set()
    for title, indexes in modules:
        entries = []
        for i in indexes:
            entry = entries_by_index.get(i)
            if entry is not None and i not in used:
                entries.append(entry)
                used.add(i)
        if entries:
            grouped.append((title, entries))

    leftover = [entries_by_index[i] for i in sorted(entries_by_index) if i not in used]
    if leftover:
        grouped.append(("Course Content", leftover))

    return grouped
