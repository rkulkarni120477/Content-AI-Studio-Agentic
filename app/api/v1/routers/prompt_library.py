"""
Prompt Library router — ported from the standalone ``prompt-library`` Flask API.

Mounted at ``/api/v1/prompt-library``. Preserves the standalone request/response
shapes so the ported React frontend works unchanged, but:
  * uses host JWT auth + RBAC (``require_permission("prompt_library.*")``),
  * has no tenant scoping,
  * drops SMTP notifications (out of scope for this integration).

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
from promptops_app.pl_models import (
    PLAttachment,
    PLAuditEvent,
    PLPrompt,
    PLPromptRequest,
    PLPromptTag,
    PLPromptTeam,
    PLPromptVariable,
    PLPromptVersion,
    PLReview,
    PLTeam,
)
from promptops_app.services import prompt_library_service as svc

router = APIRouter()

# ── Attachment storage (filesystem; path recorded in pl_attachments.stored_name) ──
_ATTACH_DIR = os.environ.get(
    "PL_ATTACHMENTS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))), "pl_attachments"),
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


# ==============================================================================
# Prompts
# ==============================================================================

@router.get("/prompts")
def list_prompts(request: Request, db: Session = Depends(get_db),
                 user=Depends(require_permission("prompt_library.view"))):
    q = svc.apply_visibility_filter(svc.base_prompt_query(db), user.role, None)
    q = svc.apply_list_filters(q, dict(request.query_params))
    items, total, page, limit = _paginate_optin(request, q)
    return _paginated_body(_enrich_prompts(db, items), total, page, limit)


@router.get("/prompts/search")
def search_prompts(request: Request, db: Session = Depends(get_db),
                   user=Depends(require_permission("prompt_library.view"))):
    q_text = (request.query_params.get("q") or request.query_params.get("query") or "").strip()
    if not q_text:
        raise HTTPException(status_code=400, detail="q query parameter required")
    q = svc.apply_visibility_filter(svc.base_prompt_query(db), user.role, None)
    q = svc.apply_list_filters(q, dict(request.query_params))
    items, total, page, limit = _paginate_forced(request, q)
    return _paginated_body(_enrich_prompts(db, items), total, page, limit, extra={"q": q_text})


@router.get("/meta")
def meta(db: Session = Depends(get_db), user=Depends(require_permission("prompt_library.view"))):
    return {
        "categories": svc.list_distinct_categories(db, user.role, None),
        "tags": svc.list_distinct_tags(db, user.role, None),
    }


@router.get("/categories")
def list_categories(db: Session = Depends(get_db), user=Depends(require_permission("prompt_library.view"))):
    return {"categories": svc.list_distinct_categories(db, user.role, None)}


@router.get("/tags")
def list_tags(category: str | None = Query(default=None), db: Session = Depends(get_db),
              user=Depends(require_permission("prompt_library.view"))):
    cat = (category or "").strip() or None
    return {"tags": svc.list_distinct_tags(db, user.role, None, category=cat)}


@router.get("/prompts/{pid}")
def get_prompt(pid: str, db: Session = Depends(get_db),
               user=Depends(require_permission("prompt_library.view"))):
    p = svc.base_prompt_query(db).filter_by(id=pid).first()
    if not p or not svc.can_access_prompt(p, user.role, None):
        raise HTTPException(status_code=404, detail="Not found")
    stats = svc.review_stats_batch(db, [p.id])
    return svc.enrich(db, p, stats)


@router.post("/prompts/{pid}/use")
def mark_used(pid: str, db: Session = Depends(get_db),
              user=Depends(require_permission("prompt_library.view"))):
    p = svc.base_prompt_query(db).filter_by(id=pid).first()
    if not p or not svc.can_access_prompt(p, user.role, None):
        raise HTTPException(status_code=404, detail="Not found")
    p.last_used_at = svc.now_utc()
    db.commit()
    return {"ok": True}


@router.post("/prompts/{pid}/render")
def render_prompt(pid: str, payload: dict = Body(default=None), db: Session = Depends(get_db),
                  user=Depends(require_permission("prompt_library.view"))):
    p = svc.base_prompt_query(db).filter_by(id=pid).first()
    if not p or not svc.can_access_prompt(p, user.role, None):
        raise HTTPException(status_code=404, detail="Not found")
    variables = (payload or {}).get("variables") or {}
    if not isinstance(variables, dict):
        raise HTTPException(status_code=400, detail="variables must be an object")
    rendered = svc.render_prompt_content(p.content, {str(k): str(v) for k, v in variables.items()})
    return {"id": p.id, "title": p.title, "content": rendered, "variables": variables}


@router.post("/prompts", status_code=201)
def create_prompt(request: Request, payload: dict = Body(...), db: Session = Depends(get_db),
                  user=Depends(require_permission("prompt_library.manage"))):
    data = payload or {}
    if not data.get("title") or not data.get("content"):
        raise HTTPException(status_code=400, detail="title and content required")
    vis = data.get("visibility", "draft")
    team_ids = svc.parse_teams_from_data(data) or []
    if vis == "team" and not team_ids:
        raise HTTPException(status_code=400, detail="At least one team is required for team-based visibility")
    try:
        parent_id = svc.resolve_parent_id(db, data.get("parent_id"))
    except svc.HierarchyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    content = data["content"].strip()
    now = svc.now_utc()
    actor = user.username
    p = PLPrompt(
        id=str(uuid.uuid4()),
        parent_id=parent_id,
        title=data["title"].strip(),
        content=content,
        description=(data.get("description") or "").strip(),
        category=(data.get("category") or "General").strip(),
        visibility=vis,
        team_id=team_ids[0] if vis == "team" and team_ids else None,
        created_by=actor,
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
            db.add(PLPromptTag(prompt_id=p.id, tag=tag))
    for idx, v in enumerate(data.get("variables", [])):
        db.add(PLPromptVariable(prompt_id=p.id, name=v["name"], label=v.get("label", ""),
                                hint=v.get("hint", ""), sort_order=idx))
    db.add(PLPromptVersion(id=str(uuid.uuid4()), prompt_id=p.id, version_number=1, content=content,
                           note="Initial version", created_by=actor, created_at=now))
    db.commit()
    db.refresh(p)
    svc.log_event(db, "prompt.create", "create", entity_type="prompt", entity_id=p.id,
                  summary=f"Created prompt '{p.title}'", actor_username=actor, actor_role=user.role,
                  ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.commit()
    return svc.prompt_to_dict(db, p)


@router.put("/prompts/{pid}")
def update_prompt(pid: str, request: Request, payload: dict = Body(...), db: Session = Depends(get_db),
                  user=Depends(require_permission("prompt_library.manage"))):
    p = svc.base_prompt_query(db).filter_by(id=pid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    data = dict(payload or {})
    create_version = data.pop("create_version", False)
    version_note = data.pop("version_note", "")
    before_snapshot = svc.build_prompt_snapshot(db, p)
    actor = user.username

    if "parent_id" in data:
        try:
            p.parent_id = svc.resolve_parent_id(db, data.get("parent_id"), prompt_id=pid)
        except svc.HierarchyError as e:
            raise HTTPException(status_code=400, detail=str(e))

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
        for t in list(p.tags):
            db.delete(t)
        db.flush()
        for tag in data["tags"]:
            tag = str(tag).strip()
            if tag:
                db.add(PLPromptTag(prompt_id=pid, tag=tag))

    if "variables" in data:
        for v in list(p.variables):
            db.delete(v)
        db.flush()
        for idx, v in enumerate(data["variables"]):
            db.add(PLPromptVariable(prompt_id=pid, name=v["name"], label=v.get("label", ""),
                                    hint=v.get("hint", ""), sort_order=idx))

    if "content" in data:
        new_content = (data["content"] or "").strip()
        if create_version and new_content != p.content:
            max_ver = db.query(func.max(PLPromptVersion.version_number)).filter_by(prompt_id=pid).scalar() or 0
            db.add(PLPromptVersion(id=str(uuid.uuid4()), prompt_id=pid, version_number=max_ver + 1,
                                   content=new_content, note=version_note, created_by=actor,
                                   created_at=svc.now_utc()))
        p.content = new_content

    p.updated_at = svc.now_utc()
    db.commit()
    p_fresh = svc.base_prompt_query(db).filter_by(id=pid).first()
    changes = svc.compute_prompt_changes(before_snapshot, svc.build_prompt_snapshot(db, p_fresh))
    svc.log_event(db, "prompt.update", "update", entity_type="prompt", entity_id=pid,
                  summary=f"Updated prompt '{p_fresh.title}'", changes=changes,
                  actor_username=actor, actor_role=user.role,
                  ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.commit()
    return svc.prompt_to_dict(db, p_fresh)


@router.delete("/prompts/{pid}")
def delete_prompt(pid: str, request: Request, db: Session = Depends(get_db),
                  user=Depends(require_permission("prompt_library.manage"))):
    p = svc.base_prompt_query(db).filter_by(id=pid).first()
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
def duplicate_prompt(pid: str, request: Request, db: Session = Depends(get_db),
                     user=Depends(require_permission("prompt_library.manage"))):
    src = svc.base_prompt_query(db).filter_by(id=pid).first()
    if not src:
        raise HTTPException(status_code=404, detail="Not found")
    now = svc.now_utc()
    new_id = str(uuid.uuid4())
    actor = user.username
    cp = PLPrompt(id=new_id, parent_id=None, title="Copy of " + src.title, content=src.content,
                  description=src.description, category=src.category, visibility=src.visibility,
                  team_id=src.team_id, created_by=actor, created_at=now, updated_at=now)
    db.add(cp)
    db.flush()
    for pt in src.prompt_teams or []:
        db.add(PLPromptTeam(prompt_id=new_id, team_id=pt.team_id))
    if not src.prompt_teams and src.team_id:
        db.add(PLPromptTeam(prompt_id=new_id, team_id=src.team_id))
    for t in src.tags:
        db.add(PLPromptTag(prompt_id=new_id, tag=t.tag))
    for v in src.variables:
        db.add(PLPromptVariable(prompt_id=new_id, name=v.name, label=v.label, hint=v.hint, sort_order=v.sort_order))
    db.add(PLPromptVersion(id=str(uuid.uuid4()), prompt_id=new_id, version_number=1, content=src.content,
                           note="Duplicated", created_by=actor, created_at=now))
    db.commit()
    db.refresh(cp)
    svc.log_event(db, "prompt.duplicate", "create", entity_type="prompt", entity_id=new_id,
                  summary=f"Duplicated prompt '{src.title}' -> '{cp.title}'",
                  changes={"source_id": {"old": None, "new": pid}},
                  actor_username=actor, actor_role=user.role,
                  ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.commit()
    return svc.prompt_to_dict(db, cp)


@router.post("/prompts/{pid}/versions", status_code=201)
def create_version(pid: str, request: Request, payload: dict = Body(...), db: Session = Depends(get_db),
                   user=Depends(require_permission("prompt_library.manage"))):
    data = payload or {}
    if not data.get("content"):
        raise HTTPException(status_code=400, detail="content required")
    p = svc.base_prompt_query(db).filter_by(id=pid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    actor = user.username
    max_ver = db.query(func.max(PLPromptVersion.version_number)).filter_by(prompt_id=pid).scalar() or 0
    new_v = PLPromptVersion(id=str(uuid.uuid4()), prompt_id=pid, version_number=max_ver + 1,
                            content=data["content"].strip(), note=data.get("note", ""),
                            created_by=actor, created_at=svc.now_utc())
    db.add(new_v)
    p.content = new_v.content
    p.updated_at = svc.now_utc()
    db.commit()
    db.refresh(p)
    svc.log_event(db, "prompt.version", "create", entity_type="prompt", entity_id=pid,
                  summary=f"Created version {new_v.version_number} for prompt '{p.title}'",
                  actor_username=actor, actor_role=user.role,
                  ip_address=_client_ip(request), user_agent=request.headers.get("User-Agent"))
    db.commit()
    return {
        "version": new_v.version_number, "content": new_v.content, "note": new_v.note,
        "created_by": new_v.created_by, "created_at": svc.fmt_dt(new_v.created_at),
    }


# ==============================================================================
# Reviews (per prompt)
# ==============================================================================

@router.get("/prompts/{pid}/reviews")
def get_reviews(pid: str, db: Session = Depends(get_db),
                user=Depends(require_permission("prompt_library.view"))):
    p = svc.base_prompt_query(db).filter_by(id=pid).first()
    if not p or not svc.can_access_prompt(p, user.role, None):
        raise HTTPException(status_code=404, detail="Not found")
    if not svc.is_manager(user.role):
        return []
    reviews = db.query(PLReview).filter_by(prompt_id=pid).all()
    return [svc.review_to_dict(r) for r in reviews]


@router.post("/prompts/{pid}/reviews")
def submit_review(pid: str, payload: dict = Body(...), db: Session = Depends(get_db),
                  user=Depends(require_permission("prompt_library.review"))):
    p = svc.base_prompt_query(db).filter_by(id=pid).first()
    if not p or not svc.can_access_prompt(p, user.role, None):
        raise HTTPException(status_code=404, detail="Not found")
    rating = (payload or {}).get("rating")
    if not isinstance(rating, (int, float)) or not (1 <= rating <= 5):
        raise HTTPException(status_code=400, detail="rating must be 1-5")
    now = svc.now_utc()
    existing = db.query(PLReview).filter_by(prompt_id=pid, username=user.username).first()
    if existing:
        existing.rating = int(rating)
        existing.feedback = (payload.get("feedback") or "").strip()
        existing.updated_at = now
        db.commit()
        r = existing
    else:
        r = PLReview(id=str(uuid.uuid4()), prompt_id=pid, username=user.username, rating=int(rating),
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
        .join(PLReview, PLReview.prompt_id == PLPrompt.id)
        .with_entities(PLReview, PLPrompt.title)
        .order_by(PLReview.updated_at.desc())
    )
    has_feedback = (request.query_params.get("has_feedback") or "").strip().lower()
    if has_feedback in ("1", "true", "yes"):
        q = q.filter(PLReview.feedback.isnot(None), PLReview.feedback != "")
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
    q = db.query(PLPromptRequest).order_by(PLPromptRequest.created_at.desc())
    if not svc.is_manager(user.role):
        q = q.filter_by(requested_by=user.username)
    status = (request.query_params.get("status") or "").strip()
    if status:
        q = q.filter(PLPromptRequest.status == status)
    items, total, page, limit = _paginate_optin(request, q)
    return _paginated_body([svc.request_to_dict(r) for r in items], total, page, limit)


@router.post("/requests", status_code=201)
def create_request(payload: dict = Body(...), db: Session = Depends(get_db),
                   user=Depends(require_permission("prompt_library.request"))):
    data = payload or {}
    if not data.get("title"):
        raise HTTPException(status_code=400, detail="title required")
    now = svc.now_utc()
    r = PLPromptRequest(
        id=str(uuid.uuid4()), title=data["title"].strip(),
        description=(data.get("description") or "").strip(), type=data.get("type", "new"),
        prompt_id=data.get("prompt_id") or None, requested_by=user.username, status="open",
        admin_notes="", created_at=now, updated_at=now,
    )
    db.add(r)
    db.commit()
    return svc.request_to_dict(r)


@router.put("/requests/{rid}")
def update_request(rid: str, request: Request, payload: dict = Body(...), db: Session = Depends(get_db),
                   user=Depends(require_permission("prompt_library.request_manage"))):
    r = db.get(PLPromptRequest, rid)
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
def upload_attachment(pid: str, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db),
                      user=Depends(require_permission("prompt_library.manage"))):
    p = svc.base_prompt_query(db).filter_by(id=pid).first()
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
    att = PLAttachment(id=str(uuid.uuid4()), prompt_id=pid, original_name=orig, stored_name=stored,
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
def download_attachment(pid: str, aid: str, db: Session = Depends(get_db),
                        user=Depends(require_permission("prompt_library.view"))):
    p = svc.base_prompt_query(db).filter_by(id=pid).first()
    if not p or not svc.can_access_prompt(p, user.role, None):
        raise HTTPException(status_code=404, detail="Not found")
    att = db.query(PLAttachment).filter_by(id=aid, prompt_id=pid).first()
    if not att:
        raise HTTPException(status_code=404, detail="Not found")
    path = os.path.join(_attachments_dir(), att.stored_name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File missing")
    return FileResponse(path, filename=att.original_name)


@router.delete("/prompts/{pid}/attachments/{aid}")
def delete_attachment(pid: str, aid: str, request: Request, db: Session = Depends(get_db),
                      user=Depends(require_permission("prompt_library.manage"))):
    p = svc.base_prompt_query(db).filter_by(id=pid).first()
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    att = db.query(PLAttachment).filter_by(id=aid, prompt_id=pid).first()
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
# Audit
# ==============================================================================

_AUDIT_EXPORT_MAX_ROWS = int(os.environ.get("PL_AUDIT_EXPORT_MAX_ROWS", "10000"))


def _audit_query(db: Session, req: Request):
    from datetime import datetime, timezone

    def _parse_dt(value):
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            return None

    q = db.query(PLAuditEvent)
    a = req.query_params
    if a.get("actor"):
        q = q.filter(PLAuditEvent.actor_username == a["actor"].strip())
    if a.get("entity_type"):
        q = q.filter(PLAuditEvent.entity_type == a["entity_type"].strip())
    if a.get("entity_id"):
        q = q.filter(PLAuditEvent.entity_id == a["entity_id"].strip())
    if a.get("event_type"):
        q = q.filter(PLAuditEvent.event_type == a["event_type"].strip())
    if a.get("action"):
        q = q.filter(PLAuditEvent.action == a["action"].strip())
    if (dt_from := _parse_dt(a.get("from"))):
        q = q.filter(PLAuditEvent.created_at >= dt_from)
    if (dt_to := _parse_dt(a.get("to"))):
        q = q.filter(PLAuditEvent.created_at <= dt_to)
    return q


@router.get("/audit")
def list_audit_events(request: Request, db: Session = Depends(get_db),
                      user=Depends(require_permission("prompt_library.audit"))):
    page = max(1, _int_arg(request, "page", 1))
    limit = min(100, max(1, _int_arg(request, "limit", 25)))
    q = _audit_query(db, request)
    total = q.count()
    events = q.order_by(PLAuditEvent.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
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
    events = q.order_by(PLAuditEvent.created_at.desc()).limit(_AUDIT_EXPORT_MAX_ROWS).all()
    return Response(content=svc.events_to_csv(events), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=audit-log.csv"})


# ==============================================================================
# Teams (Prompt Library sharing scope)
# ==============================================================================

@router.get("/teams")
def list_teams(request: Request, db: Session = Depends(get_db),
               user=Depends(require_permission("prompt_library.view"))):
    q = db.query(PLTeam).order_by(PLTeam.id)
    items, total, page, limit = _paginate_optin(request, q)
    result = []
    for t in items:
        p_count = (
            db.query(func.count(func.distinct(PLPromptTeam.prompt_id)))
            .join(PLPrompt, PLPrompt.id == PLPromptTeam.prompt_id)
            .filter(PLPromptTeam.team_id == t.id, PLPrompt.deleted_at.is_(None))
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
    tid = str(data.get("id", name)).strip()
    if db.get(PLTeam, tid):
        raise HTTPException(status_code=409, detail="Team already exists")
    if db.query(PLTeam).filter(func.lower(PLTeam.name) == name.lower()).first():
        raise HTTPException(status_code=409, detail="Team name already exists")
    team = PLTeam(id=tid, name=name, created_at=svc.now_utc(), created_by=user.username)
    db.add(team)
    db.commit()
    svc.log_event(db, "team.create", "create", entity_type="team", entity_id=team.id,
                  summary=f"Created team '{team.name}'", actor_username=user.username, actor_role=user.role)
    db.commit()
    return {"id": team.id, "name": team.name, "created_at": svc.fmt_dt(team.created_at),
            "created_by": team.created_by, "_user_count": 0, "_prompt_count": 0}


@router.put("/teams/{tid}")
def update_team(tid: str, payload: dict = Body(...), db: Session = Depends(get_db),
                user=Depends(require_permission("prompt_library.manage"))):
    team = db.get(PLTeam, tid)
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
def delete_team(tid: str, db: Session = Depends(get_db),
                user=Depends(require_permission("prompt_library.manage"))):
    team = db.get(PLTeam, tid)
    if not team:
        raise HTTPException(status_code=404, detail="Not found")
    p_count = (
        db.query(func.count(func.distinct(PLPromptTeam.prompt_id)))
        .join(PLPrompt, PLPrompt.id == PLPromptTeam.prompt_id)
        .filter(PLPromptTeam.team_id == tid, PLPrompt.deleted_at.is_(None))
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
