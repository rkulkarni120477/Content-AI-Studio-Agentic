"""CAS proxy routes for DIS Source Library.

Frontend never calls DIS directly. CAS validates its own JWT/RBAC first, resolves
DIS tenant/client from the logged-in user, then calls DIS with the internal
service token.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.core.dis_access import (
    CLIENT_NAME_ALIASES,
    build_dis_profile,
    get_dis_access_for_user,
    load_dis_access_config,
    normalize_client_name,
    save_dis_access_config,
)
from app.core.dis_client import dis_client
from app.core.source_library_filter_forward import build_cas_documents_library_params
from app.core.upload_formats import load_upload_formats, unsupported_reasons

log = logging.getLogger(__name__)

router = APIRouter()


def _normalize_client_id(value: str | None) -> str:
    # Delegates to the single source of truth in app.core.dis_access (was a
    # duplicated alias map here — kept identical, so behavior is unchanged).
    return normalize_client_name(value)


def _reject_unsupported(filenames: list[str]) -> None:
    """Refuse an upload this pipeline cannot turn into retrievable content.

    The policy itself is config, not code — see config/upload_formats.json and
    app.core.upload_formats. Accepting an unreadable file and failing quietly
    downstream is what this replaces: it was stored, listed as "Processed", and
    contributed nothing to any generation. A refusal at the door is recoverable
    in seconds; a silent empty ingestion was not noticed for weeks.
    """
    reasons = unsupported_reasons(filenames)
    if reasons:
        raise HTTPException(400, " | ".join(reasons))


#: Sentinel course id meaning "visible in every course of this client" — set by
#: the Source Library's "Upload as Global" toggle. A global upload belongs to no
#: single block, so block derivation must never fire for it.
_GLOBAL_COURSE_ID = -1


def _block_from_course(db: Session, course_id: int | None) -> str:
    """The block label the upload is scoped to, or "" when there isn't one.

    WHY THIS EXISTS. DIS derives a document's ``block`` by regex over its path,
    filename and first 1,500 characters (AIMClientProfile._extract_block_day). A
    file whose folder is named for its subject rather than its block — e.g.
    ``Landing Gear Projects/Landing Gear Systems Project 4 A52.docx`` — matches
    nothing, is stored with ``block: ""``, and is then invisible to every
    block-scoped retrieval while the Source Library still reports it "Processed".
    Measured 2026-08-27 on the AIM corpus: 148 documents lost this way across six
    courses, 59-79% of each — which is why Block 9's CDD reported "Total Projects:
    0" while eleven landing-gear project files sat in the store, and why Blocks 8
    and 10 had no reachable teaching content at all.

    The upload already knows the answer and always did: the user picked a course,
    and that course is named for its block. Sending it makes the tag a fact about
    the upload rather than a guess about the filename, so a document's
    retrievability no longer depends on whether someone typed "Block N" into a
    folder name. DIS treats caller hints as authoritative over its own inference
    (metadata_tagging_agent applies metadata_hints AFTER enrich_metadata), and
    skips empty ones, so "" here preserves exactly the previous behaviour.

    Returns "" — never a guess — for a global upload, a missing course, or a
    course whose name carries no block label (non-AIM tenants land here, which is
    correct: they have no blocks and must be left untouched).
    """
    if course_id is None or course_id == _GLOBAL_COURSE_ID:
        return ""
    try:
        from app.core.dis_day_context import infer_block_label
        from promptops_app.database import Course

        course = db.query(Course).filter(Course.id == course_id).first()
        if course is None:
            return ""
        return infer_block_label(getattr(course, "name", "")) or ""
    except Exception:  # noqa: BLE001 — tagging must never break an upload
        log.warning("source_library: block derivation failed for course_id=%s", course_id, exc_info=True)
        return ""


def _client_from_scope(db: Session, *, project_id: int | None = None, course_id: int | None = None) -> str:
    from promptops_app.database import Course, Project
    if course_id:
        course = db.query(Course).filter(Course.id == course_id).first()
        if course:
            project = db.query(Project).filter(Project.id == course.project_id).first()
            client = _normalize_client_id(getattr(project, "client_name", "") if project else "")
            if client:
                return client
    if project_id:
        project = db.query(Project).filter(Project.id == project_id).first()
        client = _normalize_client_id(getattr(project, "client_name", "") if project else "")
        if client:
            return client
    return ""


def _resolved_client(current_user: Any, db: Session, *, client_id: str = "", project_id: int | None = None, course_id: int | None = None) -> str:
    scoped_client = _client_from_scope(db, project_id=project_id, course_id=course_id)
    if scoped_client:
        return _allowed_client_for_user(current_user, scoped_client)
    return _allowed_client_for_user(current_user, client_id)

async def _resolved_client_async(current_user: Any, db: Session, *, client_id: str = "", project_id: int | None = None, course_id: int | None = None) -> str:
    """Async wrapper for :func:`_resolved_client` (P4.2/F6).

    The DIS proxy routes below are genuinely async (they ``await`` httpx calls
    into DIS), but ``_resolved_client`` runs blocking sync DB queries
    (``_client_from_scope``). Calling it inline would block the event loop.
    Offload it to FastAPI's worker threadpool instead so the loop stays free.
    """
    return await run_in_threadpool(
        _resolved_client, current_user, db,
        client_id=client_id, project_id=project_id, course_id=course_id,
    )

def _allowed_client_for_user(current_user: Any, client_id: str = "") -> str:
    access = get_dis_access_for_user(current_user, requested_client_id=client_id or None)
    requested = _normalize_client_id(client_id)
    # A project/course scoped client is not a UI switch; allow it when it matches
    # the user's resolved DIS client. Super admins may access all configured clients.
    if requested and not access.is_super_admin and requested != _normalize_client_id(access.client_id):
        raise HTTPException(403, "You do not have access to this project client")
    return requested or access.client_id


def _require_dis_super_admin(current_user: Any) -> None:
    access = get_dis_access_for_user(current_user)
    if not access.is_super_admin:
        raise HTTPException(403, "Only DIS super admin can manage Source Library client access")


@router.get("/admin/access-config")
async def get_dis_access_config(current_user=Depends(get_current_user)) -> Dict[str, Any]:
    """Return Source Library client/user access mapping for super admins.

    This is CAS-side configuration, not DIS secret configuration. Passwords are
    still managed by the existing CAS users table/login flow.
    """
    _require_dis_super_admin(current_user)
    return {"access_config": load_dis_access_config(force_reload=True)}


@router.put("/admin/access-config")
async def update_dis_access_config(
    payload: Dict[str, Any],
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Persist Source Library client access mapping.

    Expected payload example:
    {
      "default_client_id": "cengage",
      "default_tenant_id": "cengage",
      "available_clients": ["aim", "cengage"],
      "super_admin_usernames": ["admin"],
      "client_admin_map": {"cengage_admin": "cengage"},
      "user_client_map": {"cengage_user": "cengage"}
    }
    """
    _require_dis_super_admin(current_user)
    return {"access_config": save_dis_access_config(payload)}


@router.get("/me")
async def get_source_library_profile(current_user=Depends(get_current_user)) -> Dict[str, Any]:
    return {"access": build_dis_profile(current_user)}


@router.get("/ui-config")
async def get_source_ui_config(
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    cfg = await dis_client.ui_config(current_user=current_user, client_id=resolved_client)
    # Publish the canonical client-alias map so the frontend uses it instead of
    # keeping its own copy (kills the drift risk). Additive + defensive.
    if isinstance(cfg, dict):
        cfg.setdefault("client_aliases", dict(CLIENT_NAME_ALIASES))
    return cfg


@router.get("/documents")
async def list_source_documents(
    request: Request,
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    all_courses: bool = Query(False, description="Admin override: bypass course scoping and return every document for the resolved client."),
    purpose: str = Query(""),
    document_type: str = Query(""),
    visibility: str = Query(""),
    status: str = Query(""),
    search: str = Query(""),
    block: str = Query(""),
    day: str = Query(""),
    chapter: str = Query(""),
    module_name: str = Query(""),
    learning_objective: str = Query(""),
    course_name: str = Query(""),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    """List Source Library documents.

    Named filter query params remain stable. Extra query keys are forwarded to
    DIS, which authorizes them against the tenant Field Registry filter_options
    promote (Phase 3). CAS does not maintain a per-field allowlist.
    """
    if all_courses and str(getattr(current_user, "role", "") or "").lower() != "admin":
        raise HTTPException(403, "Only admins can view all courses' documents")
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    params = build_cas_documents_library_params(
        purpose=purpose,
        document_type=document_type,
        visibility=visibility,
        status=status,
        search=search,
        block=block,
        day=day,
        chapter=chapter,
        module_name=module_name,
        learning_objective=learning_objective,
        course_name=course_name,
        course_id="all" if all_courses else (str(course_id) if course_id is not None else ""),
        limit=limit,
        offset=offset,
        query_params=request.query_params,
    )
    return await dis_client.documents_library(params=params, current_user=current_user, client_id=resolved_client)


@router.get("/documents/{job_id}/structure")
async def get_source_structure(
    job_id: str,
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.source_structure(job_id=job_id, current_user=current_user, client_id=resolved_client)


@router.delete("/documents/{job_id}")
async def delete_source_document(
    job_id: str,
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Permanently delete one Source Library document (S3 + OpenSearch + index). Irreversible."""
    if str(getattr(current_user, "role", "") or "").lower() != "admin":
        raise HTTPException(403, "Only admins can delete Source Library documents")
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.delete_source(job_id=job_id, current_user=current_user, client_id=resolved_client)


@router.get("/upload-policy")
async def get_upload_policy(current_user=Depends(get_current_user)) -> Dict[str, Any]:
    """What the Source Library will accept, and why it refuses the rest.

    Served rather than duplicated in the frontend so the browser-side filter and
    the server-side rule cannot drift — a drifting pair is how style_id and
    prompt_id went missing between a page and its own API client.
    """
    policy = load_upload_formats()
    return {
        "supported_extensions": sorted(policy["supported_extensions"]),
        "blocked_extensions": policy["blocked_extensions"],
    }


@router.post("/documents/upload")
async def upload_source_document(
    files: List[UploadFile] = File(..., alias="files"),
    client_id: str = Form(default=""),
    project_id: int | None = Form(default=None),
    course_id: int | None = Form(default=None),
    purpose: str = Form(default="general_reference"),
    document_type: str = Form(default=""),
    visibility: str = Form(default=""),
    course_name: str = Form(default=""),
    block: str = Form(default=""),
    day: str = Form(default=""),
    chapter: str = Form(default=""),
    module_name: str = Form(default=""),
    learning_objective: str = Form(default=""),
    # Folder upload: the browser folder picker gives each file a relative path
    # (e.g. "MyFolder/Quizzes/Quiz 4.pdf"). The frontend uploads one file per
    # request, so one value per request is correct. DIS already accepts these
    # fields, preserves the structure in S3, and uses the path for auto-tagging.
    # Defaults are empty, so plain file uploads behave exactly as before.
    source_relative_path: str = Form(default=""),
    source_root: str = Form(default=""),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    # Before anything is stored or a DIS job is created: a type this pipeline
    # cannot read must be refused here, not accepted and quietly lost downstream.
    _reject_unsupported([f.filename or "" for f in files])
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    # An explicit block from the caller always wins; this only fills the blank the
    # form has always left. See _block_from_course for why a blank is not benign.
    if not (block or "").strip():
        block = await run_in_threadpool(_block_from_course, db, course_id)
        if not block and course_id not in (None, _GLOBAL_COURSE_ID):
            # Not fatal — non-block tenants legitimately land here — but for a
            # block-organised course this is the moment a document becomes
            # unretrievable, and it used to happen in complete silence.
            log.warning(
                "source_library upload: no block resolved (course_id=%s client=%s files=%s) — "
                "documents will be stored block-less and excluded from block-scoped retrieval",
                course_id, resolved_client, [f.filename for f in files],
            )
    form_fields = {
        "client_id": resolved_client,
        "purpose": purpose,
        "document_type": document_type,
        "project_id": project_id or "",
        "course_id": course_id or "",
        "visibility": "internal",
        "course_name": course_name,
        "block": block,
        "day": day,
        "chapter": chapter,
        "module_name": module_name,
        "learning_objective": learning_objective,
        "source_relative_path": source_relative_path,
        "source_root": source_root,
    }
    return await dis_client.upload_documents(files=files, form_fields=form_fields, current_user=current_user, client_id=resolved_client)


@router.post("/folder-scan")
async def scan_source_folder(
    payload: Dict[str, Any] = Body(...),
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id or str(payload.get("client_id", "")), project_id=project_id, course_id=course_id)
    payload = dict(payload)
    payload["client_id"] = resolved_client
    payload["course_id"] = str(course_id) if course_id is not None else ""
    # Same reasoning as the upload path: a scanned folder is named for whatever the
    # content team called it, so the block must come from the course, not the path.
    if not str(payload.get("block") or "").strip():
        payload["block"] = await run_in_threadpool(_block_from_course, db, course_id)
    return await dis_client.folder_scan(payload=payload, current_user=current_user, client_id=resolved_client)


@router.post("/retrieve/{purpose}")
async def retrieve_source_context(
    purpose: str,
    payload: Dict[str, Any],
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.retrieve_context(purpose=purpose, payload=payload, current_user=current_user, client_id=resolved_client)


@router.get("/generated-documents")
async def list_generated_documents(
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    generated_type: str = Query(""),
    search: str = Query(""),
    active: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.generated_list(
        params={"generated_type": generated_type, "search": search, "active": active, "limit": limit, "offset": offset},
        current_user=current_user,
        client_id=resolved_client,
    )


@router.get("/generated-documents/{generated_doc_id}")
async def get_generated_document(
    generated_doc_id: str,
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.generated_get(generated_doc_id, current_user=current_user, client_id=resolved_client)


@router.post("/generated-documents/{generated_doc_id}/activate")
async def activate_generated_document(
    generated_doc_id: str,
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.generated_activate(generated_doc_id, current_user=current_user, client_id=resolved_client)


@router.delete("/generated-documents/{generated_doc_id}")
async def delete_generated_document(
    generated_doc_id: str,
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.generated_delete(generated_doc_id, current_user=current_user, client_id=resolved_client)

@router.get("/documents/{job_id}/metadata")
async def get_document_metadata_route(
    job_id: str,
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.source_metadata(job_id=job_id, current_user=current_user, client_id=resolved_client)


@router.patch("/documents/{job_id}/metadata")
async def patch_document_metadata_route(
    job_id: str,
    body: Dict[str, Any] = Body(...),
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.patch_source_metadata(job_id=job_id, payload=body, current_user=current_user, client_id=resolved_client)


@router.post("/documents/{job_id}/metadata/revert-ai")
async def revert_document_metadata_route(
    job_id: str,
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.revert_source_metadata(job_id=job_id, current_user=current_user, client_id=resolved_client)


@router.get("/documents/{job_id}/overview")
async def get_source_overview(
    job_id: str,
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.source_overview(job_id=job_id, current_user=current_user, client_id=resolved_client)


@router.get("/documents/{job_id}/content/pages")
async def get_source_pages(
    job_id: str,
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.source_pages(job_id=job_id, params={"page": page, "page_size": page_size}, current_user=current_user, client_id=resolved_client)


@router.get("/documents/{job_id}/content/units")
async def get_source_units(
    job_id: str,
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.source_units(job_id=job_id, current_user=current_user, client_id=resolved_client)


@router.get("/documents/{job_id}/content/units/{unit_id}")
async def get_source_unit_detail(
    job_id: str,
    unit_id: str,
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.source_unit_detail(job_id=job_id, unit_id=unit_id, current_user=current_user, client_id=resolved_client)


@router.post("/documents/{job_id}/content/retag")
async def retag_source_content(
    job_id: str,
    body: Dict[str, Any] = Body(default_factory=dict),
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Re-run per-page LLM content tagging for failed/selected ebook pages."""
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.source_content_retag(
        job_id=job_id, payload=body or {}, current_user=current_user, client_id=resolved_client,
    )


@router.get("/documents/{job_id}/search")
async def search_source_document(
    job_id: str,
    q: str = Query(""),
    limit: int = Query(20, ge=1, le=50),
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = await _resolved_client_async(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.source_search(job_id=job_id, params={"q": q, "limit": limit}, current_user=current_user, client_id=resolved_client)
