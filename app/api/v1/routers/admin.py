"""
Admin router — system administration endpoints.

Streamlit equivalent: Admin-only sections in ``pages/analytics.py``
and the Central Repository page (``pages/central.py``).

All endpoints in this router require Admin role.

Includes:
  - RBAC permission matrix (read-only)
  - LLM model catalog
  - Database table clear (emergency use)
  - Central repository management
  - Saved instructions CRUD
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.schemas.common import MessageResponse, PaginatedResponse

_log = logging.getLogger(__name__)
router = APIRouter()

# Tables allowed to be cleared via the legacy single-table endpoint.
_CLEARABLE_TABLES = {"system_logs", "audit_logs", "generation_jobs"}

# Streamlit parity — preset clears from analytics.py (users table never cleared).
_CLEAR_PRESETS: dict[str, dict] = {
    "cdds": {
        "label": "Clear Course Design Documents (CDDs)",
        "info": "Removes all CDD drafts and their version history. Style and Blueprint data is kept.",
        "tables": ["cdd_versions", "course_design_documents"],
    },
    "blueprints": {
        "label": "Clear Module Blueprints",
        "info": "Removes all blueprint drafts and their version history. CDDs and Lessons are kept.",
        "tables": ["blueprint_versions", "module_blueprints"],
    },
    "generations": {
        "label": "Clear Generated Lessons & Blocks",
        "info": "Removes all generated lesson content, individual blocks, and block-level comments.",
        "tables": ["block_comments", "blocks", "generations"],
    },
    "styles": {
        "label": "Clear Styles & Style Documents",
        "info": "Removes all instructional styles and their linked document associations. Uploaded documents themselves are kept.",
        "tables": ["style_documents", "styles"],
    },
    "documents": {
        "label": "Clear Uploaded Documents",
        "info": "Removes all uploaded reference documents (PDF, DOCX, etc.) from the library.",
        "tables": ["documents"],
    },
    "prompts": {
        "label": "Clear Prompts & Prompt Versions",
        "info": "Removes all prompt records and every version stored under them.",
        "tables": ["prompt_versions", "prompts"],
    },
    "reviews": {
        "label": "Clear Reviews & Feedback",
        "info": "Removes all review records and user feedback signals collected during the review workflow.",
        "tables": ["reviews", "feedback_signals"],
    },
    "logs": {
        "label": "Clear Activity Logs",
        "info": "Removes system event logs and workflow audit trail. Does not affect any content.",
        "tables": ["workflow_events", "system_logs"],
    },
    "everything": {
        "label": "Clear Everything (all data except users)",
        "info": "Wipes all CDDs, Blueprints, Lessons, Styles, Documents, Prompts, Reviews, and Logs in one shot. User accounts are preserved.",
        "tables": [
            "block_comments", "blocks", "generations",
            "blueprint_versions", "module_blueprints",
            "cdd_versions", "course_design_documents",
            "style_documents", "styles",
            "documents",
            "prompt_versions", "prompts",
            "reviews", "feedback_signals",
            "workflow_events", "system_logs",
        ],
    },
}


@router.get(
    "/permissions",
    summary="Get the full RBAC permission matrix",
    description="Returns the complete permission catalog with role assignments. Used to render the permissions table.",
)
def get_permission_matrix(
    current_user=Depends(require_permission("users.view")),
) -> list[dict]:
    """
    Return the RBAC permission matrix.

    Replicates get_matrix_rows() from auth/permission_matrix.py.
    The React frontend uses this to render the permissions reference table.
    """
    from promptops_app.auth.permission_matrix import get_matrix_rows
    return get_matrix_rows()


@router.get(
    "/permissions/overview",
    summary="Role permission summary for the current user",
)
def get_permissions_overview(
    current_user=Depends(get_current_user),
) -> dict:
    """My-permissions card + optional full matrix for Admin/Lead."""
    from promptops_app.auth.permission_matrix import PERMISSION_CATALOG, get_matrix_rows
    from promptops_app.auth.permissions import rbac_check, role_label

    role = current_user.role
    categories = []
    for section in PERMISSION_CATALOG:
        allowed = [
            {"key": p["key"], "label": p["label"]}
            for p in section["permissions"]
            if rbac_check(role, p["key"])
        ]
        categories.append({
            "category": section["category"],
            "icon": section["icon"],
            "has_access": bool(allowed),
            "permissions": allowed,
        })

    overview = {
        "role": role,
        "role_display": role_label(role),
        "categories": categories,
    }
    if rbac_check(role, "users.view"):
        overview["matrix"] = get_matrix_rows()
    return overview


@router.get(
    "/clear-presets",
    summary="List database clear presets (admin)",
)
def list_clear_presets(
    current_user=Depends(require_permission("system.clear_db")),
) -> list[dict]:
    """Return clear-database options shown in the Streamlit Analytics tab."""
    return [
        {
            "tag": tag,
            "label": preset["label"],
            "info": preset.get("info", ""),
            "tables": preset["tables"],
        }
        for tag, preset in _CLEAR_PRESETS.items()
    ]


@router.post(
    "/clear/{tag}",
    response_model=MessageResponse,
    summary="Clear a preset group of database tables",
)
def clear_preset(
    tag: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("system.clear_db")),
) -> MessageResponse:
    """
    Delete all rows from tables in a named preset.

    Replicates the Clear Database section in pages/analytics.py.
    User accounts are never affected.
    """
    preset = _CLEAR_PRESETS.get(tag)
    if not preset:
        raise ValidationError(
            f"Unknown clear preset '{tag}'. "
            f"Valid: {', '.join(sorted(_CLEAR_PRESETS))}"
        )

    from sqlalchemy import text

    total_deleted = 0
    for tbl in preset["tables"]:
        result = db.execute(text(f"DELETE FROM {tbl}"))
        total_deleted += result.rowcount or 0
    db.commit()

    _log.warning(
        "db_preset_cleared  by=%s  tag=%s  tables=%s  rows=%d",
        current_user.username, tag, preset["tables"], total_deleted,
    )
    return MessageResponse(message=f"{preset['label']} cleared successfully.")


@router.get(
    "/model-catalog",
    summary="Get available LLM models",
    description="Returns all configured models and the default. Used to populate the model selector.",
)
def get_model_catalog(current_user=Depends(get_current_user)) -> dict:
    """Return the LLM model catalog from core/models.py."""
    from promptops_app.core.models import MODEL_CATALOG, DEFAULT_MODEL_NAME

    models = [
        {
            "name":         m.display_name,
            "display_name": m.display_name,
            "provider":     m.provider,
        }
        for m in MODEL_CATALOG
    ]
    return {"models": models, "default": DEFAULT_MODEL_NAME}


@router.delete(
    "/data/{table_name}",
    response_model=MessageResponse,
    summary="Clear a database table",
    description=(
        f"Emergency admin tool. Allowed tables: {', '.join(sorted(_CLEARABLE_TABLES))}. "
        "Core data tables (users, projects, blocks, etc.) cannot be cleared via this endpoint."
    ),
)
def clear_table(
    table_name: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("system.clear_db")),
) -> MessageResponse:
    """
    Delete all rows from an allowed table.

    Replicates the "Clear Database Table" admin action in the Streamlit Analytics tab.
    Only tables in _CLEARABLE_TABLES are accessible to prevent accidental data loss.
    """
    if table_name not in _CLEARABLE_TABLES:
        raise ValidationError(
            f"Table '{table_name}' cannot be cleared via the API. "
            f"Allowed: {', '.join(sorted(_CLEARABLE_TABLES))}"
        )

    from sqlalchemy import text
    result = db.execute(text(f"DELETE FROM {table_name}"))
    db.commit()

    rows_deleted = result.rowcount
    _log.warning("table_cleared  by=%s  table=%s  rows=%d",
                 current_user.username, table_name, rows_deleted)

    return MessageResponse(message=f"Cleared {rows_deleted} rows from '{table_name}'.")


# ---------------------------------------------------------------------------
# Saved instructions (user prompt history)
# ---------------------------------------------------------------------------

@router.get(
    "/instructions",
    summary="List saved instruction snippets",
    description="Returns instructions saved for a given component (cdd | blueprint | generate).",
)
def list_instructions(
    component: str = Query(..., description="Component type: cdd | blueprint | generate"),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[dict]:
    """
    List saved instruction snippets for a component.

    Replicates list_latest_for_component() from user_prompt_history_repository.py.
    Used to populate the "Load saved instructions" dropdown in CDD/Blueprint/Generate pages.
    """
    from promptops_app.repositories.user_prompt_history_repository import list_latest_for_component

    records = list_latest_for_component(
        db, component,
        project_id=project_id,
        course_id=course_id,
    )
    return [{"id": r.id, "name": r.name, "content": r.content, "created_at": str(r.created_at)} for r in records]


@router.post(
    "/instructions",
    response_model=MessageResponse,
    status_code=201,
    summary="Save an instruction snippet for reuse",
)
def save_instruction(
    request: dict,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> MessageResponse:
    """
    Save a reusable instruction snippet.

    Replicates save_prompt() from user_prompt_history_repository.py.
    Called after generation to save the extra_instructions for future reuse.
    """
    from promptops_app.repositories.user_prompt_history_repository import save_prompt

    save_prompt(
        db,
        name=request.get("name", "").strip(),
        component=request.get("component", ""),
        content=request.get("content", "").strip(),
        project_id=request.get("project_id"),
        cluster_id=request.get("cluster_id"),
        course_id=request.get("course_id"),
        created_by=current_user.username,
        as_new_version=request.get("as_new_version", False),
    )
    return MessageResponse(message="Instruction saved.")


# ---------------------------------------------------------------------------
# Central repository
# ---------------------------------------------------------------------------

@router.get(
    "/central",
    summary="List Central Repository items",
    description="Admin-curated reusable prompts and assets. Admin only.",
)
def list_central_items(
    project_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("central.view")),
) -> dict:
    """Return Central Repository items with optional filters."""
    from promptops_app.repositories import central_repository

    items = central_repository.list_items(db, project_id=project_id, status=status, search=search)
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": [
            {"id": i.id, "title": i.title, "tags": i.tags, "status": i.status, "created_at": str(i.created_at)}
            for i in items[start: start + page_size]
        ],
        "total": total, "page": page, "page_size": page_size,
    }


@router.post(
    "/central",
    response_model=MessageResponse,
    status_code=201,
    summary="Create a Central Repository item",
)
def create_central_item(
    request: dict,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("central.create")),
) -> MessageResponse:
    """Create a new curated item in the Central Repository."""
    from promptops_app.repositories import central_repository

    central_repository.create_item(
        db,
        title=request.get("title", ""),
        item_type=request.get("item_type", "Prompt"),
        content=request.get("content", ""),
        project_id=request.get("project_id"),
        course_id=request.get("course_id"),
        tags=request.get("tags", ""),
        created_by=current_user.username,
    )
    _log.info("central_item_created  user=%s", current_user.username)
    return MessageResponse(message="Central Repository item created.")


@router.post(
    "/central/{item_id}/archive",
    response_model=MessageResponse,
    summary="Archive a Central Repository item",
)
def archive_central_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("central.delete")),
) -> MessageResponse:
    """Archive (soft-delete) a Central Repository item."""
    from promptops_app.repositories import central_repository

    ok = central_repository.archive_item(db, item_id)
    if not ok:
        raise NotFoundError("CentralRepositoryItem", item_id)

    _log.info("central_item_archived  user=%s  item_id=%d", current_user.username, item_id)
    return MessageResponse(message=f"Item {item_id} archived.")
