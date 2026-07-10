from __future__ import annotations
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.middleware.auth import get_current_tenant
from services.generated_documents import GeneratedDocumentService

router = APIRouter(prefix="/generated-documents", tags=["Generated Documents"])


class GeneratedDocumentUpsert(BaseModel):
    generated_doc_id: str | None = None
    generated_type: str = "style"
    title: str = ""
    content: str = ""
    summary: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source_documents_used: list[Any] = Field(default_factory=list)
    active: bool = False
    archived: bool = False
    cas_ref: Dict[str, Any] = Field(default_factory=dict)
    created_by: str = ""


@router.post("/upsert")
async def upsert_generated_document(request: Request, body: GeneratedDocumentUpsert):
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    payload = body.model_dump()
    if not payload.get("created_by"):
        payload["created_by"] = getattr(request.state, "user_id", "")
    return GeneratedDocumentService(tenant).upsert(client_id, payload)


@router.get("/list")
async def list_generated_documents(
    request: Request,
    generated_type: str = Query(""),
    search: str = Query(""),
    active: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    return GeneratedDocumentService(tenant).list(client_id, generated_type=generated_type, search=search, active=active, limit=limit, offset=offset)


@router.get("/{generated_doc_id}")
async def get_generated_document(generated_doc_id: str, request: Request):
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    try:
        return GeneratedDocumentService(tenant).get(client_id, generated_doc_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{generated_doc_id}/activate")
async def activate_generated_document(generated_doc_id: str, request: Request):
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    try:
        return GeneratedDocumentService(tenant).activate(client_id, generated_doc_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


@router.delete("/{generated_doc_id}")
async def archive_generated_document(generated_doc_id: str, request: Request):
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    try:
        return GeneratedDocumentService(tenant).archive(client_id, generated_doc_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
