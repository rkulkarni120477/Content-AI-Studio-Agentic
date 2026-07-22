"""Export orchestration service.

All export requests from pages must go through ``export_content(db, request)``.
The service handles:
  - Content validation (non-empty, approved check)
  - Format routing → exporter modules
  - Clean file naming
  - Audit logging (format, template, entity, user)
  - Error handling (never crashes the UI)

Backward-compatible shims
--------------------------
``export_html(topic, blocks)`` and ``export_docx(topic, blocks)`` are kept so
existing code that imports them directly continues to work without changes.

Export templates
-----------------
default       — Standard formatted document (original behavior).
storyboard    — Scene-card layout for video/animation production teams.
teacher_guide — Content + instructor teaching notes per block.
quiz_bank     — Q&A extraction only.
client        — Clean minimal layout for client delivery.

Export formats
--------------
md    — Plain Markdown text.
html  — Full styled HTML document.
docx  — Microsoft Word document.
pdf   — PDF (requires reportlab; ExportResult.success=False if unavailable).
json  — JSON with topic + raw text.
zip   — ZIP bundle (MD + HTML + DOCX).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional

from promptops_app.core.logging import log_duration

_log = logging.getLogger(__name__)

# ── MIME types ────────────────────────────────────────────────────────────────
MIME_TYPES: dict[str, str] = {
    "md":   "text/markdown",
    "html": "text/html",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf":  "application/pdf",
    "json": "application/json",
    "zip":  "application/zip",
    "imscc": "application/vnd.ims.imsccv1p1+zip",
}

# ── Exportable workflow states ─────────────────────────────────────────────────
EXPORTABLE_STATES = frozenset({"approved", "published"})

# ── Human-readable template labels (for UI) ───────────────────────────────────
TEMPLATE_LABELS: dict[str, str] = {
    "default":       "Default",
    "storyboard":    "Storyboard",
    "teacher_guide": "Teacher Guide",
    "quiz_bank":     "Quiz Bank",
    "client":        "Client Export",
}


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------

@dataclass
class ExportRequest:
    """All parameters for one export operation.

    ``blocks`` must be a list of (label: str, content: str) tuples.
    ``fmt``    is the file extension / format key (md | html | docx | pdf | json | zip).
    ``template`` selects the visual layout (only applies to html/docx/zip).
    """
    fmt: str                                   # "md" | "html" | "docx" | "pdf" | "json" | "zip"
    topic: str                                 # document title
    blocks: list[tuple[str, str]]              # [(label, content), ...]
    user_name: str
    is_admin: bool
    block_types: Optional[list[str]] = None    # parallel to blocks — used by IMSCC export
    block_html: Optional[list[str]] = None     # parallel to blocks — pre-rendered Canvas HTML (IMSCC)
    modules: Optional[list[tuple[str, list[int]]]] = None  # (title, [block index]) — IMSCC module grouping
    block_item_ids: Optional[list[str]] = None # parallel to blocks — Canvas item ids for provenance-aware IMSCC re-export
    entity_type: str = ""                      # "generation" | "cdd" | "blueprint" | "full_course"
    entity_id: Optional[int] = None
    project_id: Optional[int] = None
    course_id: Optional[int] = None
    template: str = "default"                  # see TEMPLATE_LABELS
    file_name: Optional[str] = None            # override auto-generated name
    skip_approval_check: bool = False          # set True if caller already validated


@dataclass
class ExportResult:
    """Result of an export operation."""
    data: bytes
    file_name: str
    mime_type: str
    fmt: str
    template: str
    success: bool
    error_message: Optional[str] = None

    @property
    def is_error(self) -> bool:
        return not self.success


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class ExportValidationError(ValueError):
    """Raised when content fails pre-export validation."""


def validate_export(request: ExportRequest) -> None:
    """Check that the request can proceed. Raises ExportValidationError on failure."""
    if not request.blocks:
        raise ExportValidationError("No content to export. Generate content first.")

    all_empty = all(not cnt.strip() for _, cnt in request.blocks)
    if all_empty:
        raise ExportValidationError("All blocks are empty — nothing to export.")


# ---------------------------------------------------------------------------
# File naming
# ---------------------------------------------------------------------------

_SAFE_RE = re.compile(r"[^\w\-]")


def _safe(s: str, max_len: int = 40) -> str:
    """Convert a string to a safe filename component."""
    return _SAFE_RE.sub("_", (s or "export"))[:max_len].strip("_") or "export"


def make_filename(request: ExportRequest) -> str:
    """Generate a descriptive, filesystem-safe file name."""
    if request.file_name:
        return request.file_name

    topic_part = _safe(request.topic)
    entity     = request.entity_type or "export"
    tmpl       = "" if request.template == "default" else f"_{request.template}"
    date_part  = datetime.now(timezone.utc).strftime("%Y%m%d")

    if request.entity_id:
        return f"{entity}_{request.entity_id}_{topic_part}{tmpl}_{date_part}.{request.fmt}"
    return f"{entity}_{topic_part}{tmpl}_{date_part}.{request.fmt}"


# ---------------------------------------------------------------------------
# Format builders
# ---------------------------------------------------------------------------

def _build_md(request: ExportRequest) -> bytes:
    lines = [f"# {request.topic}", ""]
    for lbl, cnt in request.blocks:
        lines += [f"## {lbl}", "", cnt, ""]
    return "\n".join(lines).encode()


def _build_json(request: ExportRequest) -> bytes:
    payload = {
        "topic":    request.topic,
        "template": request.template,
        "exported": datetime.now(timezone.utc).isoformat(),
        "blocks": [{"label": lbl, "content": cnt} for lbl, cnt in request.blocks],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode()


def _build_html(request: ExportRequest) -> bytes:
    from promptops_app.exporters.html_exporter import build_html
    return build_html(request.topic, request.blocks, template=request.template).encode()


def _build_docx(request: ExportRequest) -> bytes:
    from promptops_app.exporters.docx_exporter import build_docx
    return build_docx(request.topic, request.blocks, template=request.template).read()


def _build_xlsx(request: ExportRequest) -> bytes:
    from promptops_app.exporters.xlsx_exporter import build_xlsx
    return build_xlsx(request.topic, request.blocks, template=request.template).read()


def _build_pdf(request: ExportRequest) -> bytes:
    from promptops_app.exporters.pdf_exporter import build_pdf
    return build_pdf(request.topic, request.blocks, template=request.template).read()


def _build_zip(request: ExportRequest) -> bytes:
    from promptops_app.exporters.full_course_exporter import build_zip
    base = _safe(request.topic)
    return build_zip(request.topic, request.blocks, template=request.template, base_filename=base).read()


def _build_imscc(request: ExportRequest) -> bytes:
    from promptops_app.exporters.imscc_exporter import build_imscc
    base = _safe(request.topic)
    typed_blocks: list[tuple[str, str, str, str]] = []
    for i, (lbl, cnt) in enumerate(request.blocks):
        bt = ""
        if request.block_types and i < len(request.block_types):
            bt = request.block_types[i] or ""
        html = ""
        if request.block_html and i < len(request.block_html):
            html = request.block_html[i] or ""
        typed_blocks.append((lbl, cnt, bt, html))
    return build_imscc(
        request.topic, typed_blocks, base_filename=base, modules=request.modules,
        item_ids=request.block_item_ids,
    ).read()


_BUILDERS = {
    "md":   _build_md,
    "json": _build_json,
    "html": _build_html,
    "docx": _build_docx,
    "xlsx": _build_xlsx,
    "pdf":  _build_pdf,
    "zip":  _build_zip,
    "imscc": _build_imscc,
}

# Document-oriented formats whose block text is cleaned of inline Markdown
# markers (e.g. "**") before building. Structured formats (json) and bundle
# formats (zip / imscc) are left untouched.
_CLEAN_FORMATS = {"md", "html", "docx", "xlsx", "pdf"}


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def _log_export(db, request: ExportRequest) -> None:
    """Write audit + system log entries. Never raises."""
    try:
        from promptops_app.database import log_event
        from promptops_app.services.audit_service import log_audit_event
        tmpl_label = TEMPLATE_LABELS.get(request.template, request.template)
        log_event(
            db, "export", request.user_name,
            f"Exported {request.fmt.upper()} [{tmpl_label}] — {request.entity_type} "
            f"#{request.entity_id or 'n/a'} — {request.topic}",
        )
        log_audit_event(
            db, request.user_name, "export.course",
            entity_type=request.entity_type,
            entity_id=request.entity_id,
            project_id=request.project_id,
            course_id=request.course_id,
            metadata={
                "format":   request.fmt,
                "template": request.template,
                "topic":    request.topic,
                "blocks":   len(request.blocks),
            },
        )
    except Exception as exc:
        _log.warning("Export audit log failed: %s", exc)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def export_content(db, request: ExportRequest) -> ExportResult:
    """Validate → build → audit → return. Never raises; errors go into ExportResult.

    Parameters
    ----------
    db       : SQLAlchemy session (used only for audit logging).
    request  : ExportRequest with all parameters.
    """
    try:
        # 1. Content validation
        validate_export(request)
    except ExportValidationError as exc:
        _log.info("Export blocked — validation: %s [user=%s]", exc, request.user_name)
        return ExportResult(
            data=b"", file_name="", mime_type="", fmt=request.fmt,
            template=request.template, success=False, error_message=str(exc),
        )

    # 2. Build content
    builder = _BUILDERS.get(request.fmt)
    if builder is None:
        return ExportResult(
            data=b"", file_name="", mime_type="", fmt=request.fmt,
            template=request.template, success=False,
            error_message=f"Unsupported export format: '{request.fmt}'. "
                          f"Supported: {', '.join(MIME_TYPES)}.",
        )

    # 2b. Strip inline Markdown markers (e.g. "**") for document exports so the
    #     downloaded text is clean. Structural markers (headings/bullets) are
    #     preserved for the DOCX exporter.
    if request.fmt in _CLEAN_FORMATS:
        from promptops_app.exporters.text_clean import clean_blocks
        request = replace(request, blocks=clean_blocks(request.blocks))

    try:
        with log_duration(
            f"export_service.build_{request.fmt}",
            extra={
                "fmt":      request.fmt,
                "template": request.template,
                "blocks":   len(request.blocks),
                "user":     request.user_name,
            },
        ):
            data = builder(request)
    except Exception as exc:
        _log.error(
            "export_service.build FAILED  fmt=%r  template=%r  user=%r  error=%s",
            request.fmt, request.template, request.user_name, exc, exc_info=True,
        )
        return ExportResult(
            data=b"", file_name="", mime_type="", fmt=request.fmt,
            template=request.template, success=False,
            error_message=f"Export failed: {exc}",
        )

    # 3. File naming + MIME
    fname = make_filename(request)
    mime  = MIME_TYPES.get(request.fmt, "application/octet-stream")

    # 4. Audit log (non-blocking)
    _log_export(db, request)

    _log.info(
        "Export OK [fmt=%s template=%s entity=%s#%s user=%s size=%d bytes]",
        request.fmt, request.template, request.entity_type,
        request.entity_id, request.user_name, len(data),
    )
    return ExportResult(
        data=data, file_name=fname, mime_type=mime,
        fmt=request.fmt, template=request.template, success=True,
    )


# ---------------------------------------------------------------------------
# Backward-compatible shims
# ---------------------------------------------------------------------------

def export_html(topic: str, blocks_content: list) -> str:
    """Shim — delegates to html_exporter.build_html with default template.

    Kept so existing pages that import ``export_html`` continue to work.
    """
    from promptops_app.exporters.html_exporter import build_html
    return build_html(topic, blocks_content, template="default")


def export_docx(topic: str, blocks_content: list) -> BytesIO:
    """Shim — delegates to docx_exporter.build_docx with default template.

    Kept so existing pages that import ``export_docx`` continue to work.
    """
    from promptops_app.exporters.docx_exporter import build_docx
    return build_docx(topic, blocks_content, template="default")
