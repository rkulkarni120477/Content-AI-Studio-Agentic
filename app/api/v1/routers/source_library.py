"""CAS proxy routes for DIS Source Library.

Frontend never calls DIS directly. CAS validates its own JWT/RBAC first, resolves
DIS tenant/client from the logged-in user, then calls DIS with the internal
service token.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.core.dis_access import (
    build_dis_profile,
    get_dis_access_for_user,
    load_dis_access_config,
    save_dis_access_config,
)
from app.core.dis_client import dis_client

router = APIRouter()


def _normalize_client_id(value: str | None) -> str:
    v = str(value or "").strip().lower().replace(" ", "_")
    aliases = {"cengage_learning": "cengage", "cengage": "cengage", "aim": "aim", "academian": "academian", "demo": "demo"}
    return aliases.get(v, v)


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
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.ui_config(current_user=current_user, client_id=resolved_client)


@router.get("/documents")
async def list_source_documents(
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
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
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    params = {
        "purpose": purpose,
        "document_type": document_type,
        "visibility": visibility,
        "status": status,
        "search": search,
        "block": block,
        "day": day,
        "chapter": chapter,
        "module_name": module_name,
        "learning_objective": learning_objective,
        "course_name": course_name,
        "limit": limit,
        "offset": offset,
    }
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
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.source_structure(job_id=job_id, current_user=current_user, client_id=resolved_client)


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
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
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
    resolved_client = _resolved_client(current_user, db, client_id=client_id or str(payload.get("client_id", "")), project_id=project_id, course_id=course_id)
    payload = dict(payload)
    payload["client_id"] = resolved_client
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
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
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
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
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
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
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
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
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
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.generated_delete(generated_doc_id, current_user=current_user, client_id=resolved_client)

@router.get("/documents/{job_id}/overview")
async def get_source_overview(
    job_id: str,
    client_id: str = Query("", description="Optional fallback only. Project/course client is preferred."),
    project_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
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
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
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
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
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
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.source_unit_detail(job_id=job_id, unit_id=unit_id, current_user=current_user, client_id=resolved_client)


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
    resolved_client = _resolved_client(current_user, db, client_id=client_id, project_id=project_id, course_id=course_id)
    return await dis_client.source_search(job_id=job_id, params={"q": q, "limit": limit}, current_user=current_user, client_id=resolved_client)
