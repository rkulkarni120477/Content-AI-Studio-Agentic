"""Studio context APIs used by Content AI Studio.

v8 rule: one client/workspace per tenant. `client_id` is optional and normally
comes from the JWT. Super admins can still use different tenant config by
getting a token for that tenant.
"""
from __future__ import annotations
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.middleware.auth import get_current_tenant
from services.context_retrieval import ContextRetrievalService
from config.settings import get_tenant_config
from storage.provider import get_storage_provider

router = APIRouter(prefix="/context", tags=["Studio Context"])


@router.get("/ui-config")
async def source_ui_config(request: Request):
    """Return client-specific Source Library filter config for CAS dynamic UI."""
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    return ContextRetrievalService(tenant, role=getattr(request.state, "role", "user")).ui_config(client_id)


@router.get("/documents/library")
async def documents_library(
    request: Request,
    purpose: str = Query("", description="style | cdd | blueprint | course_generation | blank for all"),
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
    course_id: str = Query("", description="CAS course ID. Isolates documents to the current course; only documents explicitly tagged with this course_id are returned."),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Document library endpoint for CAS Source Library and dropdowns.

    It is purpose-aware and client-config aware. The same endpoint supports AIM
    Block/Day filters and Cengage Chapter/Module/LO filters through metadata.
    """
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    filters = {
        "document_type": document_type,
        "visibility": visibility,
        "status": status,
        "search": search,
        "block": block,
        "day": day,
        "chapter": chapter,
        "module_name": module_name,
        "course_name": course_name,
        "course_id": course_id,
        "metadata_filters": {},
    }
    if learning_objective:
        filters["metadata_filters"]["learning_objective"] = learning_objective
    return ContextRetrievalService(tenant, role=getattr(request.state, "role", "user")).documents_library(
        client_id, purpose=purpose, filters=filters, limit=limit, offset=offset
    )


class DynamicContextRequest(BaseModel):
    request_id: str | None = None
    # Free-text query the caller (CAS) builds from the current step. This is the
    # primary signal for semantic retrieval; without it, retrieve() has no query
    # to embed and falls back to unranked keyword results. CAS sends it top-level.
    query: str | None = None
    generation: Dict[str, Any] = Field(default_factory=dict)
    context_input: Dict[str, Any] = Field(default_factory=dict)
    filters: Dict[str, Any] = Field(default_factory=dict)
    retrieval: Dict[str, Any] = Field(default_factory=dict)


@router.get("/sources")
async def list_sources(
    request: Request,
    client_id: str = Query("", description="Super admin only. Optional client/workspace id to inspect another client."),
    document_type: str = Query("", description="Optional. Use blank or all for no filter."),
    source_file_type: str = Query("", description="Optional. Example: pdf, docx, pptx. Use blank or all for no filter."),
    course_name: str = Query("", description="Optional. Use blank or all for no filter."),
    block: str = Query("", description="Optional. Example: Block 05. Use blank or all for no filter."),
    search: str = Query("", description="Optional filename/title search."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List processed source documents and dynamic filter dropdown values.

    This is the only source-list endpoint. If filters are blank/missing/`all`,
    DIS returns the full paginated source list for the current client. The response
    always includes `filter_options` so the dashboard can build dropdowns from
    actual ingested content instead of fixed values.
    """
    tenant = get_current_tenant(request)
    role = getattr(request.state, "role", "user")
    if client_id and role != "super_admin":
        current_client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
        if client_id != current_client_id:
            raise HTTPException(403, "Only super admin can pass client_id to inspect another client")
    if client_id and role == "super_admin" and client_id != tenant.tenant_id:
        try:
            tenant = get_tenant_config(client_id)
        except KeyError:
            raise HTTPException(404, f"Client '{client_id}' not found")
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        actual_client_id = tenant.effective_client_id("")
    else:
        actual_client_id = tenant.effective_client_id(client_id or getattr(request.state, "client_id", ""))
    filters = {
        "document_type": document_type,
        "source_file_type": source_file_type,
        "course_name": course_name,
        "block": block,
        "search": search,
    }
    return ContextRetrievalService(tenant, role=role).list_sources(actual_client_id, filters=filters, limit=limit, offset=offset)


@router.get("/sources/{job_id}/structure")
async def source_structure(job_id: str, request: Request):
    """Show extracted structure for one source file: calendar days, syllabus sections, project tasks, etc."""
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    try:
        return ContextRetrievalService(tenant, role=getattr(request.state, "role", "user")).get_structure(client_id, job_id)
    except Exception as exc:
        raise HTTPException(404, f"Source structure not found: {exc}")


@router.get("/sources/{job_id}/download-url")
async def source_download_url(
    job_id: str,
    request: Request,
    expires: int = Query(900, ge=60, le=3600, description="Link lifetime in seconds (default 15 min, max 1 hour)."),
):
    """Mint a short-lived presigned URL to download a source's original file.

    The original stays in a private bucket; this returns a temporary GET link on
    demand instead of exposing the S3 key. Restricted/instructor-only sources are
    blocked for normal users. Returns 404 if the source has no stored original
    (older ingests predate deep-link capture; re-ingest to enable).
    """
    tenant = get_current_tenant(request)
    role = getattr(request.state, "role", "user")
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    svc = ContextRetrievalService(tenant, role=role)
    try:
        ref = svc.resolve_source_raw_ref(client_id, job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    raw_key = ref.get("raw_key")
    if not raw_key:
        raise HTTPException(404, "No original file stored for this source (predates deep-link capture; re-ingest to enable download).")
    provider = get_storage_provider(tenant)
    try:
        url = await provider.presigned_download_url(raw_key, expires=expires, filename=ref.get("source_file_name") or "")
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Could not create download URL: {exc}")
    return {
        "job_id": job_id,
        "source_file_name": ref.get("source_file_name"),
        "source_file_type": ref.get("source_file_type"),
        "download_url": url,
        "expires_in": expires,
    }



@router.post("/retrieve/style")
async def retrieve_style_context(request: Request, body: DynamicContextRequest):
    """Retrieve style guide / approved sample context. Style docs are often kept as one content unit."""
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    data = body.model_dump()
    data.setdefault("generation", {})
    data["generation"].setdefault("type", "style")
    filters = data.setdefault("filters", {})
    filters.setdefault("purpose", "style")
    filters.setdefault("include_restricted", False)
    return ContextRetrievalService(tenant, role=getattr(request.state, "role", "user")).retrieve(client_id, data)


@router.post("/retrieve/cdd")
async def retrieve_cdd_context(request: Request, body: DynamicContextRequest):
    """Retrieve high-level syllabus/course-outline/program context for CDD generation."""
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    data = body.model_dump()
    data.setdefault("generation", {})
    data["generation"].setdefault("type", "cdd")
    filters = data.setdefault("filters", {})
    filters.setdefault("purpose", "cdd")
    filters.setdefault("include_restricted", False)
    return ContextRetrievalService(tenant, role=getattr(request.state, "role", "user")).retrieve(client_id, data)


@router.post("/retrieve/blueprint")
async def retrieve_blueprint_context(request: Request, body: DynamicContextRequest):
    """Retrieve safe context for blueprint generation.

    Default behavior is client-configurable but safe: calendar/syllabus only,
    no answer keys or instructor-only content.
    """
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    data = body.model_dump()
    data.setdefault("generation", {})
    data["generation"].setdefault("type", "blueprint")
    filters = data.setdefault("filters", {})
    filters.setdefault("content_types", ["course_calendar", "syllabus"])
    filters.setdefault("use_for_blueprint", True)
    filters.setdefault("include_restricted", False)
    return ContextRetrievalService(tenant, role=getattr(request.state, "role", "user")).retrieve(client_id, data)


@router.post("/retrieve/course-generation")
async def retrieve_course_generation_context(request: Request, body: DynamicContextRequest):
    """Retrieve safe context for course generation.

    Default behavior returns student-facing generation candidates only.
    Instructor-only context can be requested only by client_admin/super_admin with
    filters.include_restricted=true.
    """
    tenant = get_current_tenant(request)
    role = getattr(request.state, "role", "user")
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    data = body.model_dump()
    data.setdefault("generation", {})
    data["generation"].setdefault("type", "course_generation")
    filters = data.setdefault("filters", {})
    # Do NOT force visibility=student here. Course content is authored by content
    # teams, so generation must be able to use instructor/content-team-authored
    # SOURCE documents (e.g. Cengage manuscripts, AIM calendar). Genuinely
    # restricted material (answer keys, instructor guides, admin/internal-only) is
    # still blocked by the retrieval restricted gate (_is_hard_restricted).
    filters.setdefault("use_for_course_generation", True)
    filters.setdefault("include_restricted", False)
    if filters.get("include_restricted") and role not in {"client_admin", "super_admin"}:
        raise HTTPException(403, "Only client_admin or super_admin can retrieve instructor-only/restricted context")
    return ContextRetrievalService(tenant, role=role).retrieve(client_id, data)

@router.get("/sources/{job_id}/overview")
async def source_overview(job_id: str, request: Request):
    """Fast document overview for CAS View. Does not return full large-file text."""
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    try:
        return ContextRetrievalService(tenant, role=getattr(request.state, "role", "user")).get_source_overview(client_id, job_id)
    except Exception as exc:
        raise HTTPException(404, f"Source overview not found: {exc}")


@router.get("/sources/{job_id}/content/pages")
async def source_content_pages(
    job_id: str,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(5, ge=1, le=20),
):
    """Paged readable content for large files."""
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    try:
        return ContextRetrievalService(tenant, role=getattr(request.state, "role", "user")).get_source_pages(client_id, job_id, page=page, page_size=page_size)
    except Exception as exc:
        raise HTTPException(404, f"Source pages not found: {exc}")


@router.get("/sources/{job_id}/content/units")
async def source_content_units(job_id: str, request: Request):
    """Section/content-unit list for one source."""
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    try:
        return ContextRetrievalService(tenant, role=getattr(request.state, "role", "user")).get_source_units(client_id, job_id)
    except Exception as exc:
        raise HTTPException(404, f"Source units not found: {exc}")


@router.get("/sources/{job_id}/content/units/{unit_id}")
async def source_content_unit_detail(job_id: str, unit_id: str, request: Request):
    """Load one full source content unit on demand."""
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    try:
        return ContextRetrievalService(tenant, role=getattr(request.state, "role", "user")).get_source_unit_detail(client_id, job_id, unit_id=unit_id)
    except Exception as exc:
        raise HTTPException(404, f"Source unit not found: {exc}")


@router.get("/sources/{job_id}/search")
async def source_content_search(
    job_id: str,
    request: Request,
    q: str = Query(""),
    limit: int = Query(20, ge=1, le=50),
):
    """Search inside one extracted source document without loading the whole file in CAS."""
    tenant = get_current_tenant(request)
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    try:
        return ContextRetrievalService(tenant, role=getattr(request.state, "role", "user")).search_source_content(client_id, job_id, q=q, limit=limit)
    except Exception as exc:
        raise HTTPException(404, f"Source search failed: {exc}")
