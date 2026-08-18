"""
Prompt Library router — backed by the NATIVE prompt tables (consolidation
Phase 4/5; formerly pl_*).

Mounted at ``/api/v1/prompt-library``. Preserves the ported React frontend's
request/response shapes, with the planned cutover changes:
  * ids are integers (were UUID strings); path params are typed ``int``
  * teams get autoincrement integer ids — a caller-supplied slug id is
    ignored; the slug lives in ``name`` (Phase 1 remap decision)
  * audit reads/writes the unified ``audit_logs`` table, scoped to the PL
    action families
  * attachments live under ``PROMPT_ATTACHMENTS_DIR``

Kind separation (Decision 1): list/search accept ``kind=library|pipeline|all``
but non-pipeline-managers are silently stripped to library rows server-side;
every WRITE endpoint resolves its target through the library-only base query,
so pipeline rows are untouchable here (they stay on /api/v1/prompts until the
Phase 8 approval gate).

"Manager" access (create/edit/delete/review-read-all) = host roles admin + reviewer.
"""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, require_permission
from promptops_app.repositories.prompt_repository import visible_to_tenant
from promptops_app.database import (
    AuditLog,
    Prompt,
    PromptAttachment,
    PromptRequest,
    PromptReview,
    PromptTag,
    PromptTeamLink,
    PromptVariable,
    Team,
)
from promptops_app.services import prompt_library_service as svc

router = APIRouter()

# ── Attachment storage (filesystem; path recorded in prompt_attachments.stored_name) ──
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
_ATTACH_DIR = (
    os.environ.get("PROMPT_ATTACHMENTS_DIR")
    or os.path.join(_REPO_ROOT, "prompt_attachments")
)
_ALLOWED_EXT = frozenset({
    "pdf", "doc", "docx", "txt", "png", "jpg", "jpeg", "gif", "xlsx", "pptx", "csv", "zip",
})
_MAX_FILE_MB = int(os.environ.get("PL_MAX_FILE_MB", "10"))


def _attachments_dir() -> str:
    os.makedirs(_ATTACH_DIR, exist_ok=True)
    return _ATTACH_DIR


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in _ALLOWED_EXT


# ── Pagination helpers (mirror standalone opt-in / forced pagination) ──────────
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def _int_arg(req: Request, name: str, default: int) -> int:
    try:
        return int(req.query_params.get(name, default))
    except (TypeError, ValueError):
        return default


def _paginate_optin(req: Request, q):
    """page absent → bare list; page present → {items,total,page,limit}."""
    page_raw = req.query_params.get("page")
    if page_raw is None or str(page_raw).strip() == "":
        return q.all(), None, None, None
    page = max(1, _int_arg(req, "page", 1))
    limit = min(_MAX_LIMIT, max(1, _int_arg(req, "limit", _DEFAULT_LIMIT)))
    total = q.order_by(None).count()
    items = q.offset((page - 1) * limit).limit(limit).all()
    return items, total, page, limit


def _paginate_forced(req: Request, q):
    page = max(1, _int_arg(req, "page", 1))
    limit = min(_MAX_LIMIT, max(1, _int_arg(req, "limit", _DEFAULT_LIMIT)))
    total = q.order_by(None).count()
    items = q.offset((page - 1) * limit).limit(limit).all()
    return items, total, page, limit


def _paginated_body(items, total, page, limit, extra: dict | None = None):
    if page is None:
        return items
    body = {"items": items, "total": total or 0, "page": page, "limit": limit or _DEFAULT_LIMIT}
    if extra:
        body.update(extra)
    return body


def _enrich_prompts(db: Session, prompts: list) -> list:
    stats = svc.review_stats_batch(db, [p.id for p in prompts])
    parent_ids = [p.id for p in prompts if p.parent_id is None]
    child_counts = svc.child_count_batch(db, parent_ids)
    return [svc.enrich(db, p, stats, child_counts) for p in prompts]


def _client_ip(req: Request) -> str | None:
    fwd = (req.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return fwd or (req.client.host if req.client else None)


def _tenant_kwargs(user) -> dict:
    return {
        "project_id": getattr(user, "_project_id", None),
        "is_platform_admin": getattr(user, "_is_platform_admin", False),
    }


def _get_library_prompt(db: Session, pid: int, *, project_id: int | None = None,
                        is_platform_admin: bool = False) -> Prompt | None:
    """Resolve a WRITE/render target — library rows only, by construction.

    Same 404-for-both-cases contract as a read: a prompt that exists but
    belongs to another tenant returns None here exactly like a truly
    nonexistent id, so callers can't distinguish "not yours" from "doesn't
    exist" (no enumeration oracle).
    """
    p = svc.base_prompt_query(db).filter_by(id=pid).first()
    if p is None:
        return None
    if not visible_to_tenant(p.project_id, project_id, is_platform_admin):
        return None
    return p


# ==============================================================================
# Prompts
# ==============================================================================

def _include_archived(request: Request) -> bool:
    return (request.query_params.get("include_archived") or "").strip().lower() in ("1", "true", "yes")


@router.get("/prompts")
def list_prompts(request: Request, db: Session = Depends(get_db),
                 user=Depends(require_permission("prompt_library.view"))):
    kind = request.query_params.get("kind")
    q = svc.browse_prompts_query(db, user.role, None, kind=kind,
                                 include_deleted=_include_archived(request), **_tenant_kwargs(user))
    q = svc.apply_list_filters(q, dict(request.query_params))
    items, total, page, limit = _paginate_optin(request, q)
    return _paginated_body(_enrich_prompts(db, items), total, page, limit)


@router.get("/prompts/search")
def search_prompts(request: Request, db: Session = Depends(get_db),
                   user=Depends(require_permission("prompt_library.view"))):
    q_text = (request.query_params.get("q") or request.query_params.get("query") or "").strip()
    if not q_text:
        raise HTTPException(status_code=400, detail="q query parameter required")
    kind = request.query_params.get("kind")
    q = svc.browse_prompts_query(db, user.role, None, kind=kind,
                                 include_deleted=_include_archived(request), **_tenant_kwargs(user))
    q = svc.apply_list_filters(q, dict(request.query_params))
    items, total, page, limit = _paginate_forced(request, q)
    return _paginated_body(_enrich_prompts(db, items), total, page, limit, extra={"q": q_text})


@router.get("/meta")
def meta(db: Session = Depends(get_db), user=Depends(require_permission("prompt_library.view"))):
    # The console list surfaces only CAS pipeline prompts (Phase 12b), so its
    # tag filter needs pipeline tags for pipeline managers. Categories stay
    # library-derived — they feed the (now promote-path-only) create form.
    tag_kind = "pipeline" if svc.can_manage_pipeline_prompts(user.role) else "library"
    tenant = _tenant_kwargs(user)
    return {
        "categories": svc.list_distinct_categories(db, user.role, None, **tenant),
        "tags": svc.list_distinct_tags(db, user.role, None, kind=tag_kind, **tenant),
    }


@router.get("/categories")
def list_categories(db: Session = Depends(get_db), user=Depends(require_permission("prompt_library.view"))):
    return {"categories": svc.list_distinct_categories(db, user.role, None, **_tenant_kwargs(user))}


@router.get("/tags")
def list_tags(category: str | None = Query(default=None), db: Session = Depends(get_db),
              user=Depends(require_permission("prompt_library.view"))):
    cat = (category or "").strip() or None
    return {"tags": svc.list_distinct_tags(db, user.role, None, category=cat, **_tenant_kwargs(user))}


@router.get("/prompts/{pid}")
def get_prompt(pid: int, db: Session = Depends(get_db),
               user=Depends(require_permission("prompt_library.view"))):
    # Reads span kinds so pipeline managers can open pipeline rows (Phase 7b);
    # can_access_prompt hides them from everyone else with the same 404.
    # Pipeline managers may also open ARCHIVED rows (doc §9 "unless explicitly
    # enabled" — needed to inspect/restore); everyone else keeps the 404.
    p = db.query(Prompt).filter(Prompt.id == pid).first()
    if not p or not svc.can_access_prompt(p, user.role, None, **_tenant_kwargs(user)):
        raise HTTPException(status_code=404, detail="Not found")
    if p.deleted_at is not None and not svc.can_manage_pipeline_prompts(user.role):
        raise HTTPException(status_code=404, detail="Not found")
    stats = svc.review_stats_batch(db, [p.id])
    return svc.enrich(db, p, stats)


@router.post("/prompts/{pid}/restore")
def restore_prompt(pid: int, request: Request, db: Session = Depends(get_db),
                   user=Depends(require_permission("prompt_library.manage"))):
    """Un-archive a soft-deleted prompt (doc §9 "unless explicitly enabled").
    The same visibility rule as reads applies: a caller who could not see the
    row gets the same 404 as a nonexistent id."""
    p = (db.query(Prompt)
         .filter(Prompt.id == pid, Prompt.deleted_at.isnot(None))
         .first())
    # Archived rows are visible to pipeline managers only (see get_prompt) —
    # nobody may restore what they cannot see.
    if (not p or not svc.can_access_prompt(p, user.role, None, **_tenant_kwargs(user))
            or not svc.can_manage_pipeline_prompts(user.role)):
        raise HTTPException(status_code=404, detail="Not found")
    p.deleted_at = None
    p.updated_at = svc.now_utc()
    db.commit()
    svc.log_event(db, "prompt.restore", "update", entity_type="prompt", entity_id=pid,
                  summary=f"Restored prompt '{p.title or p.name}' from archive",
                  actor_username=user.username, actor_role=user.role,
                  ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.commit()
    return svc.prompt_to_dict(db, p)


@router.post("/prompts/{pid}/use")
def mark_used(pid: int, db: Session = Depends(get_db),
              user=Depends(require_permission("prompt_library.view"))):
    p = _get_library_prompt(db, pid, **_tenant_kwargs(user))
    if not p or not svc.can_access_prompt(p, user.role, None, **_tenant_kwargs(user)):
        raise HTTPException(status_code=404, detail="Not found")
    p.last_used_at = svc.now_utc()
    db.commit()
    return {"ok": True}


@router.post("/prompts/{pid}/render")
def render_prompt(pid: int, payload: dict = Body(default=None), db: Session = Depends(get_db),
                  user=Depends(require_permission("prompt_library.view"))):
    # Library rows only: the {{var}} engine must never touch a pipeline
    # template (Decision 2) — pipeline rows 404 here.
    p = _get_library_prompt(db, pid, **_tenant_kwargs(user))
    if not p or not svc.can_access_prompt(p, user.role, None, **_tenant_kwargs(user)):
        raise HTTPException(status_code=404, detail="Not found")
    variables = (payload or {}).get("variables") or {}
    if not isinstance(variables, dict):
        raise HTTPException(status_code=400, detail="variables must be an object")
    rendered = svc.render_prompt_content(
        svc.get_prompt_content(p), {str(k): str(v) for k, v in variables.items()})
    return {"id": p.id, "title": p.title, "content": rendered, "variables": variables}


@router.post("/prompts/duplicate-check")
def duplicate_check(payload: dict = Body(...), db: Session = Depends(get_db),
                    user=Depends(require_permission("prompt_library.manage"))):
    """Advisory dedup probe (doc §10): does a live prompt already carry this
    exact content (whitespace/case-normalized)? The create form warns on a
    hit — it never blocks; structural dedup for defaults is unaffected."""
    data = payload or {}
    kind = (data.get("kind") or "library").strip().lower()
    if kind not in ("library", "pipeline") or (
        kind == "pipeline" and not svc.can_manage_pipeline_prompts(user.role)
    ):
        kind = "library"
    exclude_id = data.get("exclude_id")
    try:
        exclude_id = int(exclude_id) if exclude_id is not None else None
    except (TypeError, ValueError):
        exclude_id = None
    dup = svc.find_duplicate_prompt(db, data.get("content") or "", kind=kind,
                                    exclude_id=exclude_id)
    if dup is None:
        return {"duplicate_of": None}
    return {"duplicate_of": {
        "id": dup.id,
        "title": dup.title or dup.name,
        "kind": dup.prompt_kind,
        "category": dup.category,
    }}


@router.post("/prompts/{pid}/promote")
def promote_prompt(pid: int, request: Request, payload: dict = Body(...), db: Session = Depends(get_db),
                   user=Depends(require_permission("prompt_library.manage"))):
    """Promote a library prompt to an admin-managed pipeline prompt (Phase 12,
    doc §5.1 spirit). The kind flips IN PLACE — id and version history
    survive, so there is still exactly one master record (doc §6). Promotion
    is inert for generation: the row merely becomes eligible; nothing fires
    until an admin makes it a component default or binds a scope lock."""
    if not svc.can_manage_pipeline_prompts(user.role):
        raise HTTPException(status_code=403, detail="Pipeline manager role required")
    p = _get_library_prompt(db, pid, **_tenant_kwargs(user))
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    data = payload or {}
    component = (data.get("component_type") or "").strip().lower()
    if component not in svc.VALID_PIPELINE_COMPONENTS:
        raise HTTPException(status_code=400, detail=(
            f"component_type must be one of {', '.join(svc.VALID_PIPELINE_COMPONENTS)}"))
    variant = (data.get("variant") or "").strip().lower() or None
    if variant and len(variant) > 50:
        raise HTTPException(status_code=400, detail="variant too long (max 50)")
    # Pipeline rows live outside the library follow-up hierarchy.
    if p.parent_id is not None or svc.child_count(db, pid):
        raise HTTPException(status_code=400, detail=(
            "Prompts in a follow-up hierarchy cannot be promoted — detach the parent/children first"))
    if not svc.get_prompt_content(p).strip():
        raise HTTPException(status_code=400, detail="Prompt has no content to promote")
    name = svc.unique_prompt_name(
        db, (data.get("name") or "").strip() or svc.slugify_prompt_name(p.title or ""))
    old_category = p.category
    p.prompt_kind = "pipeline"
    p.name = name
    p.component_type = component
    p.variant = variant
    p.is_default = False
    # Classification is (component_type, variant) from here on; a leftover
    # freeform category would shadow it in category filters.
    p.category = None
    p.tags = ",".join(t.tag for t in (p.tag_rows or []))  # legacy mirror on pipeline writes
    p.updated_at = svc.now_utc()
    db.commit()
    svc.log_event(db, "prompt.promote", "update", entity_type="prompt", entity_id=pid,
                  summary=(f"Promoted prompt '{p.title}' to pipeline "
                           f"({component}{'/' + variant if variant else ''}) as '{name}'"),
                  changes={"prompt_kind": {"old": "library", "new": "pipeline"},
                           "component_type": {"old": None, "new": component},
                           "variant": {"old": None, "new": variant},
                           "category": {"old": old_category, "new": None},
                           "name": {"old": None, "new": name}},
                  actor_username=user.username, actor_role=user.role,
                  ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.commit()
    return svc.prompt_to_dict(db, p)


@router.post("/prompts", status_code=201)
def create_prompt(request: Request, payload: dict = Body(...), db: Session = Depends(get_db),
                  user=Depends(require_permission("prompt_library.manage"))):
    data = payload or {}
    if not data.get("title") or not data.get("content"):
        raise HTTPException(status_code=400, detail="title and content required")
    # Category is mandatory (requirements doc §10) — no more silent "General".
    if not (data.get("category") or "").strip():
        raise HTTPException(status_code=400, detail="category required")
    vis = data.get("visibility", "draft")
    team_ids = svc.parse_teams_from_data(data) or []
    if vis == "team" and not team_ids:
        raise HTTPException(status_code=400, detail="At least one team is required for team-based visibility")
    try:
        parent_id = svc.resolve_parent_id(db, data.get("parent_id"), **_tenant_kwargs(user))
    except svc.HierarchyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    content = data["content"].strip()
    now = svc.now_utc()
    actor = user.username
    p = Prompt(
        prompt_kind="library",
        parent_id=parent_id,
        title=data["title"].strip(),
        description=(data.get("description") or "").strip(),
        category=data["category"].strip(),
        visibility=vis,
        owner=actor,
        # A platform admin has no single tenant (_project_id is None), so
        # this naturally creates a shared/global prompt for them and a
        # tenant-owned one for everyone else — no extra branch needed.
        project_id=getattr(user, "_project_id", None),
        created_at=now,
        updated_at=now,
    )
    db.add(p)
    db.flush()
    if vis == "team":
        svc.set_prompt_teams(db, p, team_ids)
    for tag in data.get("tags", []):
        tag = str(tag).strip()
        if tag:
            db.add(PromptTag(prompt_id=p.id, tag=tag))
    for idx, v in enumerate(data.get("variables", [])):
        db.add(PromptVariable(prompt_id=p.id, name=v["name"], label=v.get("label", ""),
                              hint=v.get("hint", ""), sort_order=idx))
    svc.set_prompt_content(db, p, content, create_version=True,
                           note="Initial version", created_by=actor)
    db.commit()
    db.refresh(p)
    svc.log_event(db, "prompt.create", "create", entity_type="prompt", entity_id=p.id,
                  summary=f"Created prompt '{p.title}'", actor_username=actor, actor_role=user.role,
                  ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.commit()
    return svc.prompt_to_dict(db, p)


@router.put("/prompts/{pid}")
def update_prompt(pid: int, request: Request, payload: dict = Body(...), db: Session = Depends(get_db),
                  user=Depends(require_permission("prompt_library.manage"))):
    p = _get_library_prompt(db, pid, **_tenant_kwargs(user))
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    data = dict(payload or {})
    create_version = data.pop("create_version", False)
    version_note = data.pop("version_note", "")
    before_snapshot = svc.build_prompt_snapshot(db, p)
    actor = user.username

    if "parent_id" in data:
        try:
            p.parent_id = svc.resolve_parent_id(db, data.get("parent_id"), prompt_id=pid, **_tenant_kwargs(user))
        except svc.HierarchyError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Category stays mandatory on update too (doc §10) — an explicit blank is
    # rejected rather than silently clearing the classification.
    if "category" in data and not (data.get("category") or "").strip():
        raise HTTPException(status_code=400, detail="category required")
    for field in ("title", "description", "category"):
        if field in data:
            setattr(p, field, (data[field] or "").strip())

    team_payload = svc.parse_teams_from_data(data)
    if "visibility" in data:
        p.visibility = data["visibility"]
        if data["visibility"] == "team":
            ids = team_payload if team_payload is not None else []
            if not ids:
                raise HTTPException(status_code=400, detail="At least one team is required for team-based visibility")
            svc.set_prompt_teams(db, p, ids)
        else:
            svc.set_prompt_teams(db, p, [])
    elif team_payload is not None and p.visibility == "team":
        if not team_payload:
            raise HTTPException(status_code=400, detail="At least one team is required for team-based visibility")
        svc.set_prompt_teams(db, p, team_payload)

    if "tags" in data:
        for t in list(p.tag_rows):
            db.delete(t)
        db.flush()
        for tag in data["tags"]:
            tag = str(tag).strip()
            if tag:
                db.add(PromptTag(prompt_id=pid, tag=tag))

    if "variables" in data:
        for v in list(p.variables):
            db.delete(v)
        db.flush()
        for idx, v in enumerate(data["variables"]):
            db.add(PromptVariable(prompt_id=pid, name=v["name"], label=v.get("label", ""),
                                  hint=v.get("hint", ""), sort_order=idx))

    if "content" in data:
        new_content = (data["content"] or "").strip()
        if new_content != svc.get_prompt_content(p):
            # create_version → new instant-publish version; otherwise the
            # active version's snapshot is overwritten in place ("silent
            # edit" — preserves the old pl_prompts.content semantics).
            svc.set_prompt_content(db, p, new_content, create_version=bool(create_version),
                                   note=version_note, created_by=actor)

    p.updated_at = svc.now_utc()
    db.commit()
    db.refresh(p)
    p_fresh = p
    changes = svc.compute_prompt_changes(before_snapshot, svc.build_prompt_snapshot(db, p_fresh))
    svc.log_event(db, "prompt.update", "update", entity_type="prompt", entity_id=pid,
                  summary=f"Updated prompt '{p_fresh.title}'", changes=changes,
                  actor_username=actor, actor_role=user.role,
                  ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.commit()
    return svc.prompt_to_dict(db, p_fresh)


@router.delete("/prompts/{pid}")
def delete_prompt(pid: int, request: Request, db: Session = Depends(get_db),
                  user=Depends(require_permission("prompt_library.manage"))):
    p = _get_library_prompt(db, pid, **_tenant_kwargs(user))
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    now = svc.now_utc()
    title = p.title
    for child in svc.list_children(db, pid):
        child.deleted_at = now
    p.deleted_at = now
    db.commit()
    svc.log_event(db, "prompt.delete", "delete", entity_type="prompt", entity_id=pid,
                  summary=f"Deleted prompt '{title}'", actor_username=user.username, actor_role=user.role,
                  ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.commit()
    return {"deleted": pid}


@router.post("/prompts/{pid}/duplicate", status_code=201)
def duplicate_prompt(pid: int, request: Request, db: Session = Depends(get_db),
                     user=Depends(require_permission("prompt_library.manage"))):
    src = _get_library_prompt(db, pid, **_tenant_kwargs(user))
    if not src:
        raise HTTPException(status_code=404, detail="Not found")
    now = svc.now_utc()
    actor = user.username
    # Owned by the duplicating user's own tenant, not copied from src —
    # duplicating a shared/global prompt makes a private copy, same as
    # creating one fresh (create_prompt's identical project_id logic).
    cp = Prompt(prompt_kind="library", parent_id=None, title="Copy of " + src.title,
                description=src.description, category=src.category, visibility=src.visibility,
                owner=actor, project_id=getattr(user, "_project_id", None),
                created_at=now, updated_at=now)
    db.add(cp)
    db.flush()
    for link in src.team_links or []:
        db.add(PromptTeamLink(prompt_id=cp.id, team_id=link.team_id))
    for t in src.tag_rows:
        db.add(PromptTag(prompt_id=cp.id, tag=t.tag))
    for v in src.variables:
        db.add(PromptVariable(prompt_id=cp.id, name=v.name, label=v.label, hint=v.hint, sort_order=v.sort_order))
    svc.set_prompt_content(db, cp, svc.get_prompt_content(src), create_version=True,
                           note="Duplicated", created_by=actor)
    db.commit()
    db.refresh(cp)
    svc.log_event(db, "prompt.duplicate", "create", entity_type="prompt", entity_id=cp.id,
                  summary=f"Duplicated prompt '{src.title}' -> '{cp.title}'",
                  changes={"source_id": {"old": None, "new": pid}},
                  actor_username=actor, actor_role=user.role,
                  ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.commit()
    return svc.prompt_to_dict(db, cp)


@router.post("/prompts/{pid}/versions", status_code=201)
def create_version(pid: int, request: Request, payload: dict = Body(...), db: Session = Depends(get_db),
                   user=Depends(require_permission("prompt_library.manage"))):
    data = payload or {}
    if not data.get("content"):
        raise HTTPException(status_code=400, detail="content required")
    p = _get_library_prompt(db, pid, **_tenant_kwargs(user))
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    actor = user.username
    new_v = svc.set_prompt_content(db, p, data["content"].strip(), create_version=True,
                                   note=data.get("note", ""), created_by=actor)
    p.updated_at = svc.now_utc()
    db.commit()
    db.refresh(p)
    svc.log_event(db, "prompt.version", "create", entity_type="prompt", entity_id=pid,
                  summary=f"Created version {new_v.version_number} for prompt '{p.title}'",
                  actor_username=actor, actor_role=user.role,
                  ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.commit()
    return {
        "version": new_v.version_number, "content": new_v.user_prompt_template,
        "note": new_v.change_reason or "", "created_by": new_v.created_by,
        "created_at": svc.fmt_dt(new_v.created_at),
    }


# ==============================================================================
# Reviews (per prompt)
# ==============================================================================

@router.get("/prompts/{pid}/reviews")
def get_reviews(pid: int, db: Session = Depends(get_db),
                user=Depends(require_permission("prompt_library.view"))):
    p = _get_library_prompt(db, pid, **_tenant_kwargs(user))
    if not p or not svc.can_access_prompt(p, user.role, None, **_tenant_kwargs(user)):
        raise HTTPException(status_code=404, detail="Not found")
    if not svc.is_manager(user.role):
        return []
    reviews = db.query(PromptReview).filter_by(prompt_id=pid).all()
    return [svc.review_to_dict(r) for r in reviews]


@router.post("/prompts/{pid}/reviews")
def submit_review(pid: int, payload: dict = Body(...), db: Session = Depends(get_db),
                  user=Depends(require_permission("prompt_library.review"))):
    p = _get_library_prompt(db, pid, **_tenant_kwargs(user))
    if not p or not svc.can_access_prompt(p, user.role, None, **_tenant_kwargs(user)):
        raise HTTPException(status_code=404, detail="Not found")
    rating = (payload or {}).get("rating")
    if not isinstance(rating, (int, float)) or not (1 <= rating <= 5):
        raise HTTPException(status_code=400, detail="rating must be 1-5")
    now = svc.now_utc()
    existing = db.query(PromptReview).filter_by(prompt_id=pid, username=user.username).first()
    if existing:
        existing.rating = int(rating)
        existing.feedback = (payload.get("feedback") or "").strip()
        existing.updated_at = now
        db.commit()
        r = existing
    else:
        r = PromptReview(prompt_id=pid, username=user.username, rating=int(rating),
                         feedback=(payload.get("feedback") or "").strip(), created_at=now, updated_at=now)
        db.add(r)
        db.commit()
    return svc.review_to_dict(r)


# ==============================================================================
# Staff reviews (all reviews, managers only)
# ==============================================================================

@router.get("/reviews")
def list_staff_reviews(request: Request, db: Session = Depends(get_db),
                       user=Depends(require_permission("prompt_library.review_read_all"))):
    q = (
        svc.base_prompt_query(db)
        .join(PromptReview, PromptReview.prompt_id == Prompt.id)
        .with_entities(PromptReview, Prompt.title)
        .order_by(PromptReview.updated_at.desc())
    )
    has_feedback = (request.query_params.get("has_feedback") or "").strip().lower()
    if has_feedback in ("1", "true", "yes"):
        q = q.filter(PromptReview.feedback.isnot(None), PromptReview.feedback != "")
    rows = q.all()
    return [
        {**svc.review_to_dict(r), "prompt_title": title or ""}
        for r, title in rows
    ]


# ==============================================================================
# Requests
# ==============================================================================

@router.get("/requests")
def list_requests(request: Request, db: Session = Depends(get_db),
                  user=Depends(require_permission("prompt_library.view"))):
    q = db.query(PromptRequest).order_by(PromptRequest.created_at.desc())
    if not svc.is_manager(user.role):
        q = q.filter_by(requested_by=user.username)
    status = (request.query_params.get("status") or "").strip()
    if status:
        q = q.filter(PromptRequest.status == status)
    items, total, page, limit = _paginate_optin(request, q)
    return _paginated_body([svc.request_to_dict(r) for r in items], total, page, limit)


@router.post("/requests", status_code=201)
def create_request(payload: dict = Body(...), db: Session = Depends(get_db),
                   user=Depends(require_permission("prompt_library.request"))):
    data = payload or {}
    if not data.get("title"):
        raise HTTPException(status_code=400, detail="title required")
    now = svc.now_utc()
    prompt_id = data.get("prompt_id") or None
    if prompt_id is not None:
        try:
            prompt_id = int(prompt_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="prompt_id must be an integer")
    r = PromptRequest(
        title=data["title"].strip(),
        description=(data.get("description") or "").strip(), type=data.get("type", "new"),
        prompt_id=prompt_id, requested_by=user.username, status="open",
        admin_notes="", created_at=now, updated_at=now,
    )
    db.add(r)
    db.commit()
    return svc.request_to_dict(r)


@router.put("/requests/{rid}")
def update_request(rid: int, request: Request, payload: dict = Body(...), db: Session = Depends(get_db),
                   user=Depends(require_permission("prompt_library.request_manage"))):
    r = db.get(PromptRequest, rid)
    if not r:
        raise HTTPException(status_code=404, detail="Not found")
    data = payload or {}
    changes: dict = {}
    if "status" in data:
        changes["status"] = {"old": r.status, "new": data["status"]}
        r.status = data["status"]
    if "admin_notes" in data:
        changes["admin_notes"] = {"old": r.admin_notes, "new": (data["admin_notes"] or "").strip()}
        r.admin_notes = (data["admin_notes"] or "").strip()
    r.updated_at = svc.now_utc()
    db.commit()
    if changes:
        svc.log_event(db, "request.status_change", "update", entity_type="request", entity_id=rid,
                      summary=f"Updated request '{r.title}'", changes=changes,
                      actor_username=user.username, actor_role=user.role,
                      ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
        db.commit()
    return {"id": r.id, "title": r.title, "status": r.status, "admin_notes": r.admin_notes,
            "updated_at": svc.fmt_dt(r.updated_at)}


# ==============================================================================
# Attachments
# ==============================================================================

@router.post("/prompts/{pid}/attachments", status_code=201)
def upload_attachment(pid: int, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db),
                      user=Depends(require_permission("prompt_library.manage"))):
    p = _get_library_prompt(db, pid, **_tenant_kwargs(user))
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    if not file.filename:
        raise HTTPException(status_code=400, detail="No selected file")
    if not _allowed_file(file.filename):
        raise HTTPException(status_code=400, detail="File type not allowed")
    contents = file.file.read()
    size = len(contents)
    if size > _MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"File too large. Max {_MAX_FILE_MB} MB")
    orig = os.path.basename(file.filename)
    stored = f"{uuid.uuid4()}_{orig}"
    with open(os.path.join(_attachments_dir(), stored), "wb") as fh:
        fh.write(contents)
    att = PromptAttachment(prompt_id=pid, original_name=orig, stored_name=stored,
                           size_bytes=size, uploaded_by=user.username, uploaded_at=svc.now_utc())
    db.add(att)
    p.updated_at = svc.now_utc()
    db.commit()
    svc.log_event(db, "attachment.upload", "create", entity_type="attachment", entity_id=att.id,
                  summary=f"Uploaded attachment '{orig}' to prompt {pid}", changes={"prompt_id": {"new": pid}},
                  actor_username=user.username, actor_role=user.role,
                  ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.commit()
    return {"id": att.id, "original_name": att.original_name, "stored_name": att.stored_name,
            "size": att.size_bytes, "uploaded_by": att.uploaded_by, "uploaded_at": svc.fmt_dt(att.uploaded_at)}


@router.get("/prompts/{pid}/attachments/{aid}/download")
def download_attachment(pid: int, aid: int, db: Session = Depends(get_db),
                        user=Depends(require_permission("prompt_library.view"))):
    p = _get_library_prompt(db, pid, **_tenant_kwargs(user))
    if not p or not svc.can_access_prompt(p, user.role, None, **_tenant_kwargs(user)):
        raise HTTPException(status_code=404, detail="Not found")
    att = db.query(PromptAttachment).filter_by(id=aid, prompt_id=pid).first()
    if not att:
        raise HTTPException(status_code=404, detail="Not found")
    path = os.path.join(_attachments_dir(), att.stored_name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File missing")
    return FileResponse(path, filename=att.original_name)


@router.delete("/prompts/{pid}/attachments/{aid}")
def delete_attachment(pid: int, aid: int, request: Request, db: Session = Depends(get_db),
                      user=Depends(require_permission("prompt_library.manage"))):
    p = _get_library_prompt(db, pid, **_tenant_kwargs(user))
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    att = db.query(PromptAttachment).filter_by(id=aid, prompt_id=pid).first()
    if not att:
        raise HTTPException(status_code=404, detail="Not found")
    stored = os.path.join(_attachments_dir(), att.stored_name)
    if os.path.exists(stored):
        os.remove(stored)
    orig_name = att.original_name
    db.delete(att)
    p.updated_at = svc.now_utc()
    db.commit()
    svc.log_event(db, "attachment.delete", "delete", entity_type="attachment", entity_id=aid,
                  summary=f"Deleted attachment '{orig_name}' from prompt {pid}",
                  actor_username=user.username, actor_role=user.role,
                  ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.commit()
    return {"deleted": aid}


# ==============================================================================
# Audit (unified audit_logs table, scoped to the PL action families)
# ==============================================================================

_AUDIT_EXPORT_MAX_ROWS = int(os.environ.get("PL_AUDIT_EXPORT_MAX_ROWS", "10000"))


def _audit_query(db: Session, req: Request):
    from datetime import datetime, timezone

    def _parse_dt(value):
        """ISO input → naive UTC (audit_logs.created_at is naive-UTC)."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            return None

    q = svc.pl_audit_scope(db.query(AuditLog))
    a = req.query_params
    if a.get("actor"):
        q = q.filter(AuditLog.user_id == a["actor"].strip())
    if a.get("entity_type"):
        q = q.filter(AuditLog.entity_type == a["entity_type"].strip())
    if a.get("entity_id"):
        q = q.filter(AuditLog.entity_id == a["entity_id"].strip())
    if a.get("event_type"):
        q = q.filter(AuditLog.action == a["event_type"].strip())
    if a.get("action"):
        short = a["action"].strip()
        q = q.filter(AuditLog.action.like(f"%.{short}"))
    if (dt_from := _parse_dt(a.get("from"))):
        q = q.filter(AuditLog.created_at >= dt_from)
    if (dt_to := _parse_dt(a.get("to"))):
        q = q.filter(AuditLog.created_at <= dt_to)
    return q


@router.get("/audit")
def list_audit_events(request: Request, db: Session = Depends(get_db),
                      user=Depends(require_permission("prompt_library.audit"))):
    page = max(1, _int_arg(request, "page", 1))
    limit = min(100, max(1, _int_arg(request, "limit", 25)))
    q = _audit_query(db, request)
    total = q.count()
    events = q.order_by(AuditLog.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "items": [svc.audit_event_to_dict(e) for e in events],
        "total": total, "page": page, "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
    }


@router.get("/audit/export")
def export_audit_events(request: Request, db: Session = Depends(get_db),
                        user=Depends(require_permission("prompt_library.audit"))):
    q = _audit_query(db, request)
    total = q.count()
    if total > _AUDIT_EXPORT_MAX_ROWS:
        raise HTTPException(status_code=413, detail=(
            f"Export would return {total} rows (max {_AUDIT_EXPORT_MAX_ROWS}). Narrow your date range or filters."))
    events = q.order_by(AuditLog.created_at.desc()).limit(_AUDIT_EXPORT_MAX_ROWS).all()
    return Response(content=svc.events_to_csv(events), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=audit-log.csv"})


# ==============================================================================
# Teams (Prompt Library sharing scope)
# ==============================================================================

@router.get("/teams")
def list_teams(request: Request, db: Session = Depends(get_db),
               user=Depends(require_permission("prompt_library.view"))):
    q = db.query(Team).order_by(Team.id)
    items, total, page, limit = _paginate_optin(request, q)
    result = []
    for t in items:
        p_count = (
            db.query(func.count(func.distinct(PromptTeamLink.prompt_id)))
            .join(Prompt, Prompt.id == PromptTeamLink.prompt_id)
            .filter(PromptTeamLink.team_id == t.id, Prompt.deleted_at.is_(None))
            .scalar()
        )
        result.append({
            "id": t.id, "name": t.name, "created_at": svc.fmt_dt(t.created_at),
            "created_by": t.created_by, "_user_count": 0, "_prompt_count": p_count or 0,
        })
    return _paginated_body(result, total, page, limit)


@router.post("/teams", status_code=201)
def create_team(payload: dict = Body(...), db: Session = Depends(get_db),
                user=Depends(require_permission("prompt_library.manage"))):
    data = payload or {}
    if not data.get("name"):
        raise HTTPException(status_code=400, detail="name required")
    name = data["name"].strip()
    # ids are autoincrement integers now — a caller-supplied slug id is
    # ignored; uniqueness (and the 409) is on the name.
    if db.query(Team).filter(func.lower(Team.name) == name.lower()).first():
        raise HTTPException(status_code=409, detail="Team name already exists")
    team = Team(name=name, created_at=svc.now_utc(), created_by=user.username)
    db.add(team)
    db.commit()
    svc.log_event(db, "team.create", "create", entity_type="team", entity_id=team.id,
                  summary=f"Created team '{team.name}'", actor_username=user.username, actor_role=user.role)
    db.commit()
    return {"id": team.id, "name": team.name, "created_at": svc.fmt_dt(team.created_at),
            "created_by": team.created_by, "_user_count": 0, "_prompt_count": 0}


@router.put("/teams/{tid}")
def update_team(tid: int, payload: dict = Body(...), db: Session = Depends(get_db),
                user=Depends(require_permission("prompt_library.manage"))):
    team = db.get(Team, tid)
    if not team:
        raise HTTPException(status_code=404, detail="Not found")
    data = payload or {}
    old_name = team.name
    if "name" in data:
        team.name = data["name"].strip()
    db.commit()
    svc.log_event(db, "team.update", "update", entity_type="team", entity_id=tid,
                  summary=f"Updated team '{team.name}'",
                  changes={"name": {"old": old_name, "new": team.name}} if old_name != team.name else None,
                  actor_username=user.username, actor_role=user.role)
    db.commit()
    return {"id": team.id, "name": team.name, "created_at": svc.fmt_dt(team.created_at)}


@router.delete("/teams/{tid}")
def delete_team(tid: int, db: Session = Depends(get_db),
                user=Depends(require_permission("prompt_library.manage"))):
    team = db.get(Team, tid)
    if not team:
        raise HTTPException(status_code=404, detail="Not found")
    p_count = (
        db.query(func.count(func.distinct(PromptTeamLink.prompt_id)))
        .join(Prompt, Prompt.id == PromptTeamLink.prompt_id)
        .filter(PromptTeamLink.team_id == tid, Prompt.deleted_at.is_(None))
        .scalar()
    ) or 0
    if p_count:
        raise HTTPException(status_code=409, detail=f"Cannot delete — {p_count} prompt(s) are assigned to this team.")
    name = team.name
    db.delete(team)
    db.commit()
    svc.log_event(db, "team.delete", "delete", entity_type="team", entity_id=tid,
                  summary=f"Deleted team '{name}'", actor_username=user.username, actor_role=user.role)
    db.commit()
    return {"deleted": tid}
