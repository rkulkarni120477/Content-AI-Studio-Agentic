"""Studio context APIs used by Content AI Studio.

v8 rule: one client/workspace per tenant. `client_id` is optional and normally
comes from the JWT. Super admins can still use different tenant config by
getting a token for that tenant.
"""
from __future__ import annotations
import functools
import logging
import threading
from typing import Any, Dict

import anyio.to_thread
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.middleware.auth import get_current_tenant
from services.context_retrieval import ContextRetrievalService
from services.digests import progress as digest_progress
from services.digests.enumerate import enumerate_block
from services.digests.build import digest_status, context_bundle, run_tracked_build
from services.digests.day_scoped import day_context, DEFAULT_SUPPLEMENT_K
from services.source_library import delete_source_document
from config.settings import get_tenant_config
from storage.provider import get_storage_provider

router = APIRouter(prefix="/context", tags=["Studio Context"])

log = logging.getLogger(__name__)


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
    course_id: str = Query("", description="CAS course ID. Isolates documents to the current course; only documents tagged with this course_id or the global sentinel course_id=-1 are returned."),
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


@router.delete("/sources/{job_id}")
async def delete_source(job_id: str, request: Request):
    """Permanently delete one Source Library document: raw upload, all processed
    artifacts, OpenSearch chunks, and its source-index entry. Irreversible.
    Restricted to client_admin/super_admin — a normal user cannot delete sources.
    """
    tenant = get_current_tenant(request)
    role = getattr(request.state, "role", "user")
    if role == "user":
        raise HTTPException(403, "Only admins can delete Source Library documents")
    client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    try:
        return await delete_source_document(tenant, client_id, job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))



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

class EnumerateRequest(BaseModel):
    block: str = Field(..., description="Block label, e.g. 'Block 2'.")
    client_id: str | None = Field(None, description="Super admin only. Inspect another client/workspace.")
    include_units: bool = Field(False, description="Admin only. Include per-day unit descriptors (ids/types/attribution signal).")


def _resolve_block_scope(request: Request, body_client_id: str | None):
    """Resolve (tenant, client_id) for a block-wide request, honoring super-admin
    cross-client inspection (mirrors /sources). Returns (tenant, client_id, role)."""
    tenant = get_current_tenant(request)
    role = getattr(request.state, "role", "user")
    current_client_id = getattr(request.state, "client_id", tenant.effective_client_id(""))
    if body_client_id and role != "super_admin" and body_client_id != current_client_id:
        raise HTTPException(403, "Only super admin can pass client_id to inspect another client")
    if body_client_id and role == "super_admin" and body_client_id != tenant.tenant_id:
        try:
            tenant = get_tenant_config(body_client_id)
        except KeyError:
            raise HTTPException(404, f"Client '{body_client_id}' not found")
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        client_id = tenant.effective_client_id("")
    else:
        client_id = tenant.effective_client_id(body_client_id or current_client_id)
    return tenant, client_id, role


@router.post("/enumerate")
async def enumerate_block_context(request: Request, body: EnumerateRequest):
    """Deterministic block inventory for block-wide CDD / Blueprint coverage.

    Read-only. Enumerates the block's calendar days and content units, attributes
    units whose ``day_number`` is NULL to a day (ENUMERATE owns attribution), and
    returns the declared-ACS set with coverage flags (BLOCK_INCOMPLETE /
    DUPLICATE_CALENDAR / THIN_DAY / UNATTRIBUTED). Returns metadata only — day
    topics, ACS codes and counts — never source text.
    """
    tenant, client_id, role = _resolve_block_scope(request, body.client_id)
    # Unit-level detail can surface instructor-only unit titles; gate it.
    if body.include_units and role not in {"client_admin", "super_admin"}:
        raise HTTPException(403, "Only client_admin or super_admin can include unit-level detail")
    try:
        result = enumerate_block(tenant, body.block, client_id=client_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    return result.to_summary(include_units=body.include_units)


class DigestBuildRequest(BaseModel):
    block: str = Field(..., description="Block label, e.g. 'Block 2'.")
    client_id: str | None = Field(None, description="Super admin only. Build for another client/workspace.")
    force: bool = Field(False, description="Rebuild every day's digest, ignoring the cache.")
    use_graph: bool | None = Field(
        None,
        description="Override the fan-out strategy: true = LangGraph Send fan-out, "
                    "false = sequential. None ⇒ tenant/global config default (D4).",
    )
    map_guidance: str = Field(
        "", description="Optional judgment/emphasis guidance distilled from the course's "
                        "selected CDD/Blueprint prompt (see the CAS-side prompt_guidance "
                        "service) — appended to every day's MAP extraction call and folded "
                        "into the cache key. Empty string reproduces today's behavior exactly.",
    )
    wait: bool = Field(
        True,
        description="True (default) = hold this request until the build finishes and "
                    "return the report. False = start the build in the background and "
                    "return immediately; poll GET /context/digests/progress for state "
                    "and collect the report with include_result=true. Prefer False for "
                    "a cold block: a 5-20 minute request is at the mercy of every "
                    "timeout between caller and here, and losing it discards a build "
                    "that already ran and was billed.",
    )


@router.post("/digests/build")
async def build_block_digests(request: Request, body: DigestBuildRequest):
    """Build (or refresh) the per-day digest tier for a block (MAP + cache, D3).

    Lazy + content-addressed: days whose source units are unchanged are served
    from cache; only changed/missing/failed days re-run the extractor. This calls
    the LLM and writes the (idempotent, content-addressed) digest store.
    Authenticated-tenant access, mirroring /retrieve/cdd — the endpoint is reached
    server-to-server via the CAS service token, and the caller (CAS
    /cdd/generate-block, /blueprints/generate-block) is already permission-gated
    (cdd.generate / blueprint.generate), so this is not an unauthenticated
    spend/write surface. No restricted text is ever exposed; digests are
    instructor-facing and answer keys are excluded. Can be long for a full block;
    the app-side async job wraps end-to-end generation.

    Strategy (D4): the block's MAP step runs either sequentially or as a LangGraph
    ``Send`` fan-out (per-day checkpoint/resume + bounded concurrency). The default
    comes from ``pipeline.digest_fanout_enabled``; ``use_graph`` overrides per call.
    """
    tenant, client_id, role = _resolve_block_scope(request, body.client_id)
    pipe = tenant.pipeline
    use_graph = pipe.digest_fanout_enabled if body.use_graph is None else body.use_graph
    checkpointer = None
    if use_graph:
        from services.digests.graph import make_checkpointer
        checkpointer = make_checkpointer(getattr(pipe, "digest_fanout_checkpoint_dsn", ""))

    # Single-flight, before any work. Two users pressing Generate on the same block —
    # or a poller re-issuing a build whose original is still alive — would otherwise
    # run concurrent MAP passes over the same days: double the Bedrock spend for one
    # result, and racing writers on the same digest documents.
    if not digest_progress.reserve(client_id, body.block):
        if not body.wait:
            # Not an error on the async path: the caller wants the build to happen, and
            # it is happening. It polls for the same registry entry either way.
            return {"started": False, "already_running": True, "block": body.block}
        raise HTTPException(
            409, f"A digest build for '{body.block}' is already running. Poll "
                 f"GET /context/digests/progress?block={body.block} for its state.",
        )

    runner = functools.partial(
        run_tracked_build,
        tenant, body.block, client_id=client_id, force=body.force,
        use_graph=use_graph, checkpointer=checkpointer,
        max_concurrency=getattr(pipe, "digest_fanout_max_concurrency", 5),
        map_guidance=body.map_guidance,
    )

    if not body.wait:
        # A plain thread rather than a task/queue: the build is IO-bound (Bedrock +
        # OpenSearch), one runs at a time per block by the guard above, and the
        # registry already carries the state a queue would exist to provide. daemon so
        # it cannot wedge shutdown — an interrupted build leaves a terminal registry
        # entry (see run_tracked_build) and its days are cached, so a retry resumes.
        threading.Thread(
            target=_run_build_in_background, args=(runner, client_id, body.block),
            name=f"digest-build:{client_id}:{body.block}", daemon=True,
        ).start()
        return {"started": True, "block": body.block, "client_id": client_id}

    try:
        # to_thread, NOT a direct call: this coroutine runs on the single uvicorn event
        # loop, and calling the blocking build here froze that loop for the whole build
        # — starving health checks, every other tenant's requests, and above all the
        # progress endpoint below, which is the one thing able to report that the build
        # was alive. That is why a 10-minute cold build showed no day counter at all.
        return await anyio.to_thread.run_sync(runner)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


def _run_build_in_background(runner: Any, client_id: str, block: str) -> None:
    """Thread body for a non-blocking build.

    ``run_tracked_build`` has already recorded the failure in the registry by the time
    anything reaches here, so this only has to keep the exception from being lost to a
    dead thread's stack and make it greppable in the logs.
    """
    try:
        runner()
    except BaseException:
        log.exception("background digest build failed: client=%s block=%s", client_id, block)


@router.get("/digests/progress")
async def get_block_digest_progress(
    request: Request,
    block: str = Query(..., description="Block label, e.g. 'Block 2'."),
    client_id: str = Query("", description="Super admin only. Inspect another client/workspace."),
    include_result: bool = Query(
        False,
        description="Include the finished build's report. For the server-to-server "
                    "poller that collects it once at the end — the browser polls this "
                    "every 2 seconds and has no use for the report.",
    ),
):
    """State, per-day progress, and (optionally) the result of a block's digest build.

    This is both the liveness channel and the delivery channel for the report, which
    is what lets ``POST /digests/build?wait=false`` return immediately. A caller that
    loses its connection re-reads state here instead of losing the build.

    Progress is reported BY the build (see services/digests/progress.py), never
    inferred from the digest store: digests persist between attempts, so counting them
    would report a retry as complete before it began.

    Returns ``{"progress": null}`` when no build has been tracked for this block — an
    ordinary answer, not an error, since the usual case is that nothing is running. A
    poller that has already seen an entry must read ``null`` as "DIS restarted" rather
    than "finished", and re-issue the build; per-day digests are cached, so that is
    cheap and converges.
    """
    _tenant, resolved_client_id, _role = _resolve_block_scope(request, client_id or None)
    return {"progress": digest_progress.snapshot(resolved_client_id, block,
                                                 include_result=include_result)}


@router.get("/digests")
async def get_block_digests(
    request: Request,
    block: str = Query(..., description="Block label, e.g. 'Block 2'."),
    client_id: str = Query("", description="Super admin only. Inspect another client/workspace."),
):
    """Return the enumerate summary + persisted digest bodies for a block — the
    bundle the app-side REDUCE consumes. Read-only (does not build); call
    /digests/build first to ensure freshness. Authenticated-tenant access
    (mirrors /retrieve/cdd; digests carry instructor-facing narrative only, no
    answer keys)."""
    tenant, resolved_client_id, role = _resolve_block_scope(request, client_id or None)
    try:
        return context_bundle(tenant, block, client_id=resolved_client_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@router.get("/digests/status")
async def block_digest_status(
    request: Request,
    block: str = Query(..., description="Block label, e.g. 'Block 2'."),
    client_id: str = Query("", description="Super admin only. Inspect another client/workspace."),
):
    """Coverage + freshness of a block's digest store (fresh/stale/missing/failed
    per day) without building. Authenticated-tenant operational view."""
    tenant, resolved_client_id, role = _resolve_block_scope(request, client_id or None)
    try:
        return digest_status(tenant, block, client_id=resolved_client_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


class DayContextRequest(BaseModel):
    block: str = Field(..., description="Block label, e.g. 'Block 2'.")
    day: int = Field(..., ge=1, description="Day number within the block's calendar.")
    client_id: str | None = Field(None, description="Super admin only. Inspect another client/workspace.")
    audience: str = Field("instructor", description="'instructor' (default) or 'student' — drives the §8.4 text gate.")
    supplement_k: int = Field(DEFAULT_SUPPLEMENT_K, ge=0, le=20, description="Max related handbook pages to add beyond the day's own units (0 disables).")


@router.post("/retrieve/day")
async def retrieve_day_context(request: Request, body: DayContextRequest):
    """Structured-first day-scoped context (plan §7) for DLU / Learn It / Today's
    Mission generators.

    Returns *every* unit placed on ``block+day`` (complete, reusing ENUMERATE's
    attribution), the day's digest, and a bounded kNN supplement of related pages
    beyond the day's own sources. Read-only. The §8.4 text gate applies — pass
    ``audience='student'`` for student-facing deliverables to withhold
    instructor-only text (metadata is always returned for coverage). Authenticated
    tenant access, mirroring /retrieve/cdd; answer-key text is never returned.
    """
    tenant, client_id, role = _resolve_block_scope(request, body.client_id)
    audience = body.audience if body.audience in {"instructor", "student"} else "instructor"
    try:
        return day_context(
            tenant, body.block, body.day, client_id=client_id,
            audience=audience, supplement_k=body.supplement_k,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc))


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
