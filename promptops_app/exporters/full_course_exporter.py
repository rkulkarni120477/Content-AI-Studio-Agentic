"""Full-course assembly exporter.

Builds complete course packages that include a table of contents and all blocks.
Also provides a ZIP exporter that bundles MD + HTML + DOCX in one download.
"""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from io import BytesIO

from promptops_app.exporters.html_exporter import build_html
from promptops_app.exporters.docx_exporter import build_docx

_NOW = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------------------
# Table-of-contents builder
# ---------------------------------------------------------------------------

def build_toc(blocks: list[tuple[str, str]]) -> str:
    """Return a plain-text table of contents string."""
    lines = ["Table of Contents", "=" * 40]
    for i, (lbl, _) in enumerate(blocks, 1):
        lines.append(f"  {i:>2}. {lbl}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown full course
# ---------------------------------------------------------------------------

def build_markdown(topic: str, blocks: list[tuple[str, str]]) -> str:
    """Return complete Markdown document with TOC and all block content."""
    toc_lines = [f"# {topic}", "", "## Table of Contents", ""]
    for i, (lbl, _) in enumerate(blocks, 1):
        toc_lines.append(f"{i}. {lbl}")
    toc_lines += ["", "---", ""]

    content_sections = []
    for lbl, cnt in blocks:
        content_sections.append(f"## {lbl}\n\n{cnt}")

    return "\n".join(toc_lines) + "\n\n".join(content_sections)


# ---------------------------------------------------------------------------
# ZIP bundle: MD + HTML + DOCX
# ---------------------------------------------------------------------------

def build_zip(
    topic: str,
    blocks: list[tuple[str, str]],
    template: str = "default",
    base_filename: str = "full_course",
) -> BytesIO:
    """Return a ZIP BytesIO containing MD + HTML + DOCX files."""
    md_content   = build_markdown(topic, blocks).encode()
    html_content = build_html(topic, blocks, template=template).encode()
    docx_buf     = build_docx(topic, blocks, template=template)
    docx_content = docx_buf.read()

    zip_buf = BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{base_filename}.md",   md_content)
        zf.writestr(f"{base_filename}.html", html_content)
        zf.writestr(f"{base_filename}.docx", docx_content)
        zf.writestr("README.txt", (
            f"Full Course Package: {topic}\n"
            f"Exported: {_NOW()}\n"
            f"Template: {template}\n"
            f"Files included:\n"
            f"  {base_filename}.md   — Markdown source\n"
            f"  {base_filename}.html — Styled HTML (open in browser)\n"
            f"  {base_filename}.docx — Word document\n"
            f"\nPowered by Content AI Studio\n"
        ).encode())
    zip_buf.seek(0)
    return zip_buf
