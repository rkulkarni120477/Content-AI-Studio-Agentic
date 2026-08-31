"""
CDD (Course Design Document) router.

This router handles the complete CDD pipeline:
  - Generating a new CDD with AI
  - Listing and viewing existing CDDs
  - Managing CDD versions (create, list, activate)
  - Pinning a CDD as active for generation
  - Exporting a CDD as DOCX

How this maps to the Streamlit UI
-----------------------------------
  Streamlit page : promptops_app/pages/cdd.py  render_page(db, ctx)
  The entire page becomes these HTTP endpoints — each button/form becomes
  one endpoint.

  "🤖 Generate CDD with AI"        → POST   /api/v1/cdd/generate
  "📂 Your Course Design Documents" → GET    /api/v1/cdd
  "Select CDD to View"             → GET    /api/v1/cdd/{cdd_id}
  "View Version" selector          → GET    /api/v1/cdd/{cdd_id}/versions/{version}
  "Set as Active Version"          → POST   /api/v1/cdd/{cdd_id}/versions/{version}/activate
  "Save as New Version"            → POST   /api/v1/cdd/{cdd_id}/versions
  "📌 Set as Active CDD"           → POST   /api/v1/cdd/{cdd_id}/pin
  "⬇️ Word (.docx)"                → GET    /api/v1/cdd/{cdd_id}/export
  "🗄️ Archive"                     → DELETE /api/v1/cdd/{cdd_id}
  "♻️ Restore"                     → POST   /api/v1/cdd/{cdd_id}/restore
  "🗑️ Delete permanently"          → DELETE /api/v1/cdd/{cdd_id}/permanent

RBAC permissions used
---------------------
  cdd.generate  → Generate a new CDD with AI
  cdd.pin       → Pin a CDD for generation
  cdd.version   → Create or activate a CDD version
  cdd.archive   → Archive or restore a CDD (reversible)
  cdd.purge     → Permanently delete an archived CDD (admin only)
  export.course → Download a CDD as a file
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.http import content_disposition
from app.core.exceptions import (
    LLMGenerationError,
    NotFoundError,
    PromptConfigurationError,
    RetrievalUnavailableError,
    SourceUnavailableError,
    WorkflowError,
)
from app.schemas.archive import (
    ArchiveResponse,
    BulkArchiveRequest,
    BulkArchiveResponse,
    DocumentReferences,
    PurgeResponse,
)
from app.schemas.cdd import (
    CDDActivateVersionResponse,
    CDDGenerateRequest,
    CDDGenerateResponse,
    CDDListItem,
    CDDPinRequest,
    CDDPinResponse,
    CDDRead,
    CDDRegenerateItemRequest,
    CDDRegenerateItemResponse,
    CDDRegenerateSectionRequest,
    CDDDigestRepairResponse,
    CDDRegenerateSectionResponse,
    CDDVersionCreateRequest,
    CDDVersionListItem,
    CDDVersionRead,
)
from app.schemas.common import PaginatedResponse
from app.schemas.block_wide import BlockWideGenerateRequest, BlockWideJobResponse
from app.api.v1.cdd_response import build_cdd_read
from app.core.dis_client import dis_client
from app.core.dis_access import resolve_course_dis_client
from app.core.config import settings

_log = logging.getLogger(__name__)

router = APIRouter()


def _dis_context_block(purpose: str, payload: dict, current_user, label: str, client_id: str = "") -> tuple[str, list]:
    try:
        result = dis_client.retrieve_context_sync(purpose, payload, current_user=current_user, client_id=client_id)
        ctx = str(result.get("combined_context") or "").strip()
        units = result.get("source_units") or result.get("sources") or []
        if ctx:
            return (
                f"\n\n---\n{label} FROM DIS SOURCE LIBRARY\n"
                "Use this as supporting source context only. Follow CAS style, structure, and user instructions first. "
                "Do not expose internal DIS metadata.\n\n"
                f"{ctx}\n---\n",
                units,
            )
    except Exception as exc:
        _log.warning("dis_%s_context_unavailable error=%s", purpose, exc)
    return "", []


def _merge_source_units(primary: list, extra: list) -> list:
    """Append `extra` source units to `primary`, dropping ones already present.

    Units are DIS payloads (usually dicts, occasionally plain ids), so identity is
    keyed off whichever id-ish field is available and falls back to the repr —
    provenance must never raise and break a generation that already succeeded.
    """
    def key(unit):
        if isinstance(unit, dict):
            for field in ("unit_id", "chunk_id", "id", "document_id", "job_id"):
                if unit.get(field):
                    return f"{field}:{unit[field]}"
        return repr(unit)

    merged = list(primary or [])
    seen = {key(u) for u in merged}
    for unit in extra or []:
        unit_key = key(unit)
        if unit_key not in seen:
            seen.add(unit_key)
            merged.append(unit)
    return merged


# Generic source-retrieval intent appended to every CDD retrieval query.
#
# A CDD / Block Blueprint is built from the course's planning documents, but a
# title-only query ("<course title> <extra instructions>") is too thin to match
# them semantically. Investigation (course "Aircraft maintenance Test 3"): the
# title-only query returned 3 sparse calendar rows and the model flagged most
# cells MISSING_SOURCE; adding the document kinds a blueprint actually needs
# surfaced the syllabus (score 1.0) and the full day-by-day calendar (~18 units).
#
# The terms are domain-neutral, so they help BOTH the standard CDD pipeline and
# the DLU (worksheet/day-based) pipeline without biasing toward any one course —
# irrelevant kinds simply don't match and are not retrieved.
_CDD_RETRIEVAL_INTENT = (
    "course syllabus course calendar day-by-day schedule learning objectives "
    "topics and modules projects and activities assessments and quizzes "
    "instructor guide reference materials"
)


def _is_dlu_prompt(*texts: str) -> bool:
    """True when the selected CDD prompt is a DLU (day/worksheet) Block Blueprint.

    Detection is content-based and fail-safe toward "standard": a normal CDD
    prompt contains none of these markers, so it never triggers the extra
    course-generation retrieval pass below. Checked against the prompt's name,
    system prompt, and user template (or the inline overrides).
    """
    blob = " ".join(t or "" for t in texts).upper()
    # "WORKSHEET" is the DLU output contract's boundary marker (## WORKSHEET N:) and
    # appears in every block-blueprint prompt's system text; the standard CDD prompt
    # (Course -> Module -> Lesson) never uses it. "DLU" / "BLOCK BLUEPRINT" catch the
    # short user templates / prompt names. NB: we deliberately do NOT match bare
    # "BLUEPRINT" — the standard CDD prompt mentions it in passing.
    return (
        "DLU" in blob
        or "BLOCK BLUEPRINT" in blob
        or "WORKSHEET" in blob
        or ("DAY-BY-DAY" in blob and "INSTRUCTIONAL" in blob)
    )


# ---------------------------------------------------------------------------
# Block-wide digest pipeline (flag-gated) — enumerate → map → reduce → verify
# ---------------------------------------------------------------------------
# The pipeline + persistence live in promptops_app.services.block_wide_service so
# the async worker (jobs/block_wide_jobs.py) can share them without a router↔jobs
# import cycle. These aliases keep the local names used by generate_cdd() and the
# coverage eval; persist_cdd_and_respond is also the legacy path's shared tail.
from promptops_app.services.block_wide_service import (  # noqa: E402
    generate_cdd_via_digests as _generate_cdd_via_digests,
    persist_cdd_and_respond as _persist_and_respond,
    run_block_wide_sync,
)


# ---------------------------------------------------------------------------
# Helper — refuse a reply the output cap cut short
# ---------------------------------------------------------------------------

def _reject_if_truncated(result, what: str) -> None:
    """Raise rather than let a fragment overwrite stored content.

    The model's own output cap is the one bound content is allowed to hit, but a
    reply that hit it is a fragment with nothing to say so: the prose reads as
    finished, the last markdown construct on the line is left unclosed (this is
    one of the ways a label ends up rendering as ``*Label:**``), and every
    version saved afterwards carries the damage forward untouched.

    Committing it loses the tail silently, which is strictly worse than failing:
    a failure the user can see, they can act on by narrowing the request or
    switching to a model with more output range.
    """
    if not getattr(result, "truncated", False):
        return
    _log.error(
        "cdd_llm_output_truncated  what=%s  model=%s  stop_reason=%s  completion_tokens=%s",
        what, getattr(result, "model", "?"), getattr(result, "stop_reason", None),
        getattr(result, "completion_tokens", None),
    )
    raise LLMGenerationError(
        f"The model ran out of output room part-way through {what}, so the reply "
        f"is incomplete and was not applied — nothing was changed. Narrow the "
        f"request (name fewer days or one column), or pick a model with a larger "
        f"output range."
    )


# ---------------------------------------------------------------------------
# Helper — load CDD or raise 404
# ---------------------------------------------------------------------------

def _get_cdd_or_404(db: Session, cdd_id: int, current_user):
    """
    Fetch a CDD by ID from the database, scoped to the caller's tenant.

    Raises ``NotFoundError`` (HTTP 404) if no CDD with that ID exists, OR if
    it exists but belongs to a different tenant — a cross-tenant CDD 404s
    exactly like a nonexistent one (no enumeration oracle), same contract as
    prompts._get_prompt_or_404. ``get_cdd_by_id`` is itself unfiltered, so
    without this every one of this helper's ~14 callers (get/update/archive/
    restore/versions/pin/export/…) would let any authenticated user act on
    any tenant's CDD just by knowing its id.
    """
    from promptops_app.repositories import cdd_repository
    from app.core.tenant_context import visible_to_tenant

    cdd = cdd_repository.get_cdd_by_id(db, cdd_id)
    is_platform_admin = getattr(current_user, "_is_platform_admin", False)
    project_id = getattr(current_user, "_project_id", None)
    if cdd is None or not visible_to_tenant(cdd.project_id, project_id, is_platform_admin):
        raise NotFoundError("CDD", cdd_id)
    return cdd


# ---------------------------------------------------------------------------
# List CDDs
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=PaginatedResponse[CDDListItem],
    summary="List Course Design Documents",
    description=(
        "Returns a paginated list of CDDs scoped to the given project. "
        "Admin users can omit project_id to see all CDDs across all projects. "
        "Archived CDDs are excluded unless include_archived=true."
    ),
)
def list_cdds(
    project_id: int | None = Query(default=None, description="Filter by project ID."),
    course_id: int | None = Query(default=None, description="Optional course ID for legacy row matching."),
    include_archived: bool = Query(
        default=False,
        description="Include archived CDDs. Off by default — the archive is a bin, not the list.",
    ),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)."),
    page_size: int = Query(default=100, ge=1, le=200, description="Items per page."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[CDDListItem]:
    """
    Return CDDs the current user has access to.

    Non-admin users are automatically scoped to their assigned project.
    Admin users can pass any project_id or omit it to see all CDDs.

    Each row carries what references it, resolved in one batched pass over the
    page rather than a query per row, so a 100-row page costs the same handful
    of queries as a 1-row page.
    """
    from promptops_app.repositories import cdd_repository
    from app.services import design_doc_archive as archive_svc

    # ``current_user.role == "admin"`` is a per-tenant membership role, not
    # platform-admin — a tenant's own admin used to be able to pass another
    # tenant's project_id (or omit project_id/course_id entirely and fall
    # through to list_all_cdds, every tenant, unfiltered) and see it. Only a
    # genuine platform admin may choose an arbitrary/omitted project_id; a
    # tenant caller is always scoped to their own project regardless of what
    # they pass, same rule as prompts.list_prompts.
    is_platform_admin = getattr(current_user, "_is_platform_admin", False)
    effective_project_id = project_id if is_platform_admin else getattr(current_user, "_project_id", None)

    if effective_project_id:
        all_cdds = cdd_repository.list_cdds_for_scope(
            db, project_id=effective_project_id, course_id=course_id,
            include_archived=include_archived,
        )
    elif is_platform_admin and course_id:
        all_cdds = cdd_repository.list_cdds_for_scope(
            db, course_id=course_id, include_archived=include_archived,
        )
    elif is_platform_admin:
        all_cdds = cdd_repository.list_all_cdds(db, include_archived=include_archived)
    else:
        # Non-platform-admin with no project assigned at all — nothing to
        # scope to, so nothing shown rather than every tenant's rows (this
        # also closes off using a bare course_id to sidestep the boundary).
        all_cdds = []

    total = len(all_cdds)
    start = (page - 1) * page_size
    page_items = all_cdds[start : start + page_size]

    refs = archive_svc.reference_counts(db, archive_svc.CDD, [c.id for c in page_items])

    items = []
    for c in page_items:
        items.append(CDDListItem(
            id=c.id,
            title=c.title or "",
            course_title=c.course_title,
            active_version=c.active_version,
            workflow_state=c.workflow_state or "draft",
            created_by=c.created_by,
            created_at=c.created_at,
            is_archived=archive_svc.is_archived(c),
            deleted_at=getattr(c, "deleted_at", None),
            deleted_by=getattr(c, "deleted_by", None),
            references=DocumentReferences.from_refs(
                refs.get(c.id, archive_svc.DocReferences())
            ),
        ))

    return PaginatedResponse.create(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# Generate a new CDD with AI
# ---------------------------------------------------------------------------

@router.post(
    "/generate",
    response_model=CDDGenerateResponse,
    status_code=201,
    summary="Generate a new CDD with AI",
    description=(
        "Runs the full CDD generation pipeline: builds the prompt from the "
        "active style and course metadata, calls the LLM, parses the output "
        "into sections, saves the CDD and version to the database, and "
        "automatically pins the new CDD to the specified course."
    ),
    responses={
        201: {"description": "CDD created and pinned successfully."},
        502: {"description": "All LLM attempts failed (primary + retry + fallback)."},
        403: {"description": "User does not have the cdd.generate permission."},
    },
)
def generate_cdd(
    request_body: CDDGenerateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.generate")),
) -> CDDGenerateResponse:
    """
    Generate a Course Design Document using AI.

    This endpoint mirrors the "🤖 Generate CDD with AI" button in the Streamlit app.
    The full pipeline is:
      1. Build the prompt (active style + course metadata + extra instructions)
      2. Call the LLM via llm_service.generate_text() (primary → retry → fallback)
      3. Parse the output into sections using cdd_parser.parse_sections_from_text()
      4. Persist the CDD and version v1 to the database
      5. Auto-pin the new CDD to the course via course_repository.set_active_cdd()

    All steps are identical to the Streamlit implementation — only the delivery
    mechanism (HTTP response vs st.rerun) has changed.
    """
    from promptops_app.database import (
        CDDVersion, CourseDesignDocument, build_style_context,
    )
    from promptops_app.parsers.cdd_parser import parse_cdd_flat, parse_sections_from_text
    from promptops_app.repositories import cdd_repository, style_repository
    from promptops_app.repositories.course_repository import get_course_by_id, set_active_cdd
    from promptops_app.services.audit_service import log_audit_event
    from promptops_app.services.llm_service import generate_with_metadata
    from promptops_app.services.usage_service import UsageLogContext
    from promptops_app.prompts.prompt_builder import PromptVariableError, build_prompt

    _log.info(
        "cdd_generate_start  user=%s  course=%d  title=%s  model=%s",
        current_user.username, request_body.course_id,
        request_body.course_title, request_body.model_choice,
    )

    # ── Block-wide digest pipeline (flag-gated) ────────────────────────────────
    # Engages ONLY when a block is supplied AND the digest pipeline is enabled for
    # the course's client (both checked inside run_block_wide_sync). No caller
    # sends `block` today, so this is a no-op and the legacy single-call path below
    # runs byte-for-byte unchanged. A DIS or reduce failure returns None and falls
    # through to that same legacy path.
    _block_resp = run_block_wide_sync(db, "cdd", request_body, current_user)
    if _block_resp is not None:
        return _block_resp

    # Fold the selected (or inline-overridden) prompt's own USER template into the
    # retrieval query. That template names the exact source documents the CDD is
    # built from — "day-by-day course calendar", "syllabus", "ACS codes", "SME
    # review notes", etc. — which is far stronger retrieval signal than the course
    # title alone: with it the search returns the syllabus + the full calendar,
    # without it just a stray day. Read-only and best-effort; any lookup failure
    # leaves the base query unchanged so generation never breaks.
    prompt_query_text = request_body.user_prompt_override or ""
    prompt_sys_text = request_body.system_prompt_override or ""
    prompt_name = ""
    if not prompt_query_text and getattr(request_body, "prompt_id", None):
        try:
            from promptops_app.repositories.prompt_repository import get_active_version
            from promptops_app.database import Prompt
            _pv = get_active_version(db, int(request_body.prompt_id))
            if _pv:
                prompt_query_text = _pv.user_prompt_template or ""
                prompt_sys_text = prompt_sys_text or (_pv.system_prompt or "")
            _p = db.get(Prompt, int(request_body.prompt_id))
            prompt_name = getattr(_p, "name", "") or ""
        except Exception:
            pass

    # Is this a DLU (day/worksheet) Block Blueprint? Standard CDDs return False and
    # keep the original single-pass retrieval unchanged.
    is_dlu = _is_dlu_prompt(prompt_name, prompt_sys_text, prompt_query_text)

    dis_query = " ".join(str(x or "") for x in [
        request_body.course_title,
        request_body.document_title,
        request_body.target_audience,
        request_body.expert_domain,
        request_body.audience_category,
        request_body.extra_instructions,
        # The selected prompt's own wording (names the source docs it consumes).
        # Capped: a very long template blurs the query embedding and starves the
        # tiny per-day calendar rows — 800 chars keeps the strong signal (course/
        # source-kind vocabulary) without diluting day-level matches.
        prompt_query_text[:800],
        # Name the planning-document kinds a CDD/blueprint is built from so
        # semantic search surfaces the syllabus + full calendar, not just a
        # title match. Applies to standard and DLU alike (see constant above).
        _CDD_RETRIEVAL_INTENT,
    ])
    # Scope retrieval to the COURSE's own Source Library (its project's
    # client), independent of who runs the generation.
    dis_client_id = resolve_course_dis_client(
        db, course_id=request_body.course_id,
        project_id=getattr(request_body, "project_id", None),
    )

    dis_context_block, dis_source_units = _dis_context_block(
        "cdd",
        {
            "purpose": "cdd",
            "query": dis_query,
            "filters": {
                "purpose": "cdd",
                # No hard document_types filter: fixed type names did not match real
                # stored doc types (e.g. Cengage docs are typed "pdf"), silently
                # returning zero. Retrieval relies on purpose + semantic ranking;
                # the security allow-set still applies.
            },
            # A blueprint needs the whole picture — a 20-day calendar plus the
            # syllabus is already ~18 units. top_k=12 truncated that; 24 fits the
            # full schedule + syllabus while token_budget caps total size so the
            # prompt never bloats. Calendar rows are tiny (~80 tokens), so 24000
            # comfortably holds them alongside the larger syllabus sections.
            "retrieval": {"top_k": 24, "token_budget": 24000},
        },
        current_user,
        "CDD CONTEXT",
        client_id=dis_client_id,
    )

    # DLU Block Blueprints need far more than the calendar/syllabus that the `cdd`
    # purpose returns — they draw on the per-day content, study questions, projects
    # and quizzes that live under the `course-generation` purpose (the same source
    # set lesson generation uses). Run a SECOND retrieval there with the same query
    # and merge it in, so the model sees structure (cdd) + detail (course content).
    #
    # Gated to DLU prompts only: a standard CDD leaves is_dlu False and this block
    # is skipped entirely, so its retrieval is byte-for-byte unchanged. Best-effort
    # — _dis_context_block swallows any DIS error and returns "", so a failure here
    # degrades to today's cdd-only behaviour rather than breaking generation.
    if is_dlu:
        gen_block, gen_units = _dis_context_block(
            "course-generation",
            {
                "purpose": "course-generation",
                "query": dis_query,
                # include_restricted stays False: the rich student-facing content
                # (per-day material, study questions, projects, quizzes) is returned
                # without it, and requesting restricted content as a non-admin would
                # 403 and drop this whole pass.
                "filters": {"include_restricted": False},
                "retrieval": {"top_k": 24, "token_budget": 24000},
            },
            current_user,
            "COURSE CONTENT (per-day source material)",
            client_id=dis_client_id,
        )
        if gen_block:
            dis_context_block = f"{dis_context_block}{gen_block}"
            dis_source_units = _merge_source_units(dis_source_units, gen_units)
        _log.info(
            "cdd_generate_dlu_course_gen_pass  user=%s  course=%d  merged=%s  added_units=%d",
            current_user.username, request_body.course_id,
            bool(gen_block), len(gen_units or []),
        )

    # Documents the user explicitly picked in "Reference Documents" on the Create
    # New CDD form. Retrieved as an ADDITIONAL block pinned to those ids, on top
    # of the automatic purpose=cdd retrieval above — when nothing is selected the
    # pipeline is byte-for-byte what it was before.
    selected_ref_ids = [
        str(x).strip() for x in (request_body.reference_document_ids or []) if str(x).strip()
    ]
    if selected_ref_ids:
        pinned_block, pinned_units = _dis_context_block(
            "cdd",
            {
                "purpose": "cdd",
                "query": dis_query,
                "document_ids": selected_ref_ids,
                # Only pin by id here. The selector lists every processed document,
                # not just purpose=cdd ones, so re-applying the purpose filter would
                # silently drop documents the user deliberately attached.
                "filters": {"document_ids": selected_ref_ids},
                # The pool is already narrowed to the user's picks, so pull a deeper
                # slice than the broad auto-retrieval above.
                "retrieval": {"top_k": 24, "token_budget": 20000},
            },
            current_user,
            "USER-SELECTED REFERENCE DOCUMENTS",
            client_id=dis_client_id,
        )
        if pinned_block:
            dis_context_block = f"{dis_context_block}{pinned_block}"
            dis_source_units = _merge_source_units(dis_source_units, pinned_units)
        _log.info(
            "cdd_generate_reference_docs  user=%s  course=%d  selected=%d  retrieved=%s",
            current_user.username, request_body.course_id,
            len(selected_ref_ids), bool(pinned_block),
        )

    # ── Step 1: Build prompts ──────────────────────────────────────────────────
    # Use the custom override if the user edited the prompt in the UI,
    # otherwise build from the prompt library (falls back to inline constants).
    if request_body.system_prompt_override and request_body.user_prompt_override:
        system_prompt = request_body.system_prompt_override
        user_prompt = request_body.user_prompt_override
        if dis_context_block:
            user_prompt = f"{user_prompt}\n\n{dis_context_block}"
        # Persist the override with the artifact (PL↔CAS sync review, plan
        # Phase 11): a prompt authored inline in CAS must stay recoverable —
        # before this it drove the LLM call and was discarded.
        prompt_provenance = {
            "prompt_source": "override",
            "system_prompt_override": request_body.system_prompt_override,
            "user_prompt_override": request_body.user_prompt_override,
        }
    else:
        course = get_course_by_id(db, request_body.course_id)
        style_context = ""
        if request_body.style_id:
            style = style_repository.get_style_by_id(db, request_body.style_id)
            if style:
                style_context = build_style_context(db, style, cluster_id=course.cluster_id if course else None)

        extra_block = request_body.extra_instructions or ""
        if dis_context_block:
            extra_block = f"{extra_block}\n\n{dis_context_block}".strip()
        if style_context:
            extra_block = f"**ACTIVE STYLE — Apply throughout:**\n{style_context}\n\n{extra_block}"

        # Duration is optional on the form, so it can be absent here. "unspecified"
        # rather than a stand-in number: a template that prints
        # "**Estimated Duration:** {estimated_duration} hours" must not assert a
        # figure the requester never gave, and an empty string would read as a
        # rendering bug rather than as an answer.
        estimated_duration = (
            str(request_body.estimated_duration_hours)
            if request_body.estimated_duration_hours is not None else "unspecified"
        )

        variables = {
            "course_title":        request_body.course_title,
            "course_name":         request_body.course_title,
            "target_audience":     request_body.target_audience,
            "expert_domain":       request_body.expert_domain,
            "audience_level":      request_body.audience_category,
            "estimated_duration":  estimated_duration,
            "extra_instructions_block": extra_block,
            "extra_instructions":  extra_block,
            "style_guidelines":    style_context,
            "grade_level":         request_body.target_audience,
            # The block this request is scoped to. Absent until now, which made the
            # block un-injectable on the very route AIM's Block Blueprint prompts run
            # on (all four are component_type="cdd"), so such a prompt had to hardcode
            # "Block 2" in its text or name a blueprint-route variable and fail
            # render's strict check outright.
            "block":               getattr(request_body, "block", None) or "",
        }

        try:
            system_prompt, user_prompt, _tpl_name, _tpl_version = build_prompt(
                "cdd_generation", variables, db=db,
                project_id=course.project_id if course else None,
                cluster_id=course.cluster_id if course else None,
                course_id=request_body.course_id,
                prompt_id=request_body.prompt_id,
            )
            prompt_provenance = {
                "prompt_source": "selected" if request_body.prompt_id else "registry",
                "prompt_name": _tpl_name,
                "prompt_version": _tpl_version,
            }
        except PromptVariableError as exc:
            # A declared-variable violation is a template misconfiguration —
            # surface it to the admin; never silently swap in the constant
            # fallback (that would mask which prompt generation actually used).
            raise PromptConfigurationError(
                str(exc),
                detail={"template": exc.template, "missing": exc.missing},
            ) from exc
        except Exception:
            # Fall back to inline constants if the prompt library fails.
            from promptops_app.prompt_templates import (
                CDD_SYSTEM_PROMPT, CDD_USER_PROMPT_TEMPLATE,
            )
            system_prompt = CDD_SYSTEM_PROMPT
            user_prompt = CDD_USER_PROMPT_TEMPLATE.format(
                course_title=request_body.course_title,
                target_audience=request_body.target_audience,
                expert_domain=request_body.expert_domain,
                audience_level=request_body.audience_category,
                estimated_duration=estimated_duration,
                extra_instructions_block=extra_block,
            )
            prompt_provenance = {"prompt_source": "builtin_fallback"}

    # ── Step 1b: Refuse a prompt this path structurally cannot honour ─────────
    # Reached both by a direct single-call request and by the block-wide branch above
    # falling through after a DIS/reduce failure — the fallback is where an
    # xlsx-and-download-link prompt would otherwise persist a one-line CDD.
    from promptops_app.services.prompt_capability import (
        context_was_dropped, reject_if_unsatisfiable,
    )
    reject_if_unsatisfiable(system_prompt, user_prompt, what="generating this CDD")
    # Not fatal (see context_was_dropped), but it decides whether the flags in the
    # finished document mean "the source really lacks this" or "the prompt never
    # received the source", so it must be on the row rather than inferred later.
    if context_was_dropped(dis_context_block, system_prompt, user_prompt):
        _log.warning(
            "cdd_source_context_dropped  user=%s  course=%d  context_chars=%d  "
            "prompt_source=%s — the selected prompt has no slot for it",
            current_user.username, request_body.course_id, len(dis_context_block),
            prompt_provenance.get("prompt_source"),
        )
        prompt_provenance["source_context_dropped"] = True

    # ── Step 2: Call the LLM ───────────────────────────────────────────────────
    usage_context = UsageLogContext(
        user_name=current_user.username,
        project_id=request_body.project_id,
        course_id=request_body.course_id,
        entity_type="cdd",
    )

    llm_result = generate_with_metadata(
        request_body.model_choice,
        system_prompt,
        user_prompt,
        usage_ctx=usage_context,
    )

    _reject_if_truncated(llm_result, "generating this CDD")

    if llm_result.status == "error":
        _log.error(
            "cdd_generate_llm_failed  user=%s  error_type=%s",
            current_user.username, llm_result.error_type,
        )
        raise LLMGenerationError(
            f"Content generation failed after all retries. "
            f"Please try again or switch models. Error type: {llm_result.error_type}"
        )

    # ── Step 3: Parse the AI output into sections ──────────────────────────────
    raw_output = llm_result.text
    sections = parse_sections_from_text(raw_output)

    # Merge any additional flat-parsed keys (handles both parsing strategies).
    flat_parsed = parse_cdd_flat(raw_output)
    for key, value in flat_parsed.items():
        if not key.startswith("_") and value.strip():
            sections[key] = value

    # ── Step 4/5: Persist, mirror to DIS, auto-pin, audit, respond ─────────────
    # Shared tail with the digest pipeline. Legacy path passes coverage=None.
    return _persist_and_respond(
        db, request_body, current_user,
        raw_output=raw_output,
        sections=sections,
        dis_source_units=dis_source_units,
        prompt_provenance=prompt_provenance,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_used=(llm_result.model or request_body.model_choice),
        tokens_used=(
            (llm_result.prompt_tokens or 0) + (llm_result.completion_tokens or 0)
            if llm_result.prompt_tokens else None
        ),
        coverage=None,
    )


# ---------------------------------------------------------------------------
# Generate a block-wide CDD asynchronously (digest pipeline)
# ---------------------------------------------------------------------------

@router.post(
    "/generate-block",
    response_model=BlockWideJobResponse,
    status_code=202,
    summary="Generate a block-wide CDD via the digest pipeline (async)",
    description=(
        "Enqueues a background job that builds the block's day digests and reduces "
        "them into a full-coverage CDD. Block-wide generation is long, so this is "
        "always async: poll GET /api/v1/jobs/{job_id}; on completion the job's "
        "entity id is the new CDD id. Requires the digest pipeline to be enabled "
        "for the course's client."
    ),
    responses={
        202: {"description": "Job queued."},
        400: {"description": "Digest pipeline not enabled for this client."},
        403: {"description": "Requires the cdd.generate permission."},
    },
)
def generate_cdd_block(
    request_body: BlockWideGenerateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.generate")),
) -> BlockWideJobResponse:
    from promptops_app.repositories import job_repository
    from promptops_app.jobs import dispatch, block_wide_jobs

    dis_client_id = resolve_course_dis_client(
        db, course_id=request_body.course_id, project_id=request_body.project_id,
    )
    if not settings.digest_pipeline_on_for(dis_client_id):
        # Audited before raising. This refusal happens BEFORE create_job and before
        # the *.block_requested write below, so without this a rejected request left
        # no job row and no audit row — the user saw an error the system had no
        # record of. The UI now gates on the same question (courses carry
        # digest_pipeline_enabled), so reaching here means a stale client, a direct
        # API call, or the two checks drifting apart again; all three are worth
        # seeing.
        from promptops_app.services.audit_service import log_audit_event
        log_audit_event(
            db, current_user.username, "cdd.block_refused",
            entity_type="cdd", entity_id=None,
            project_id=request_body.project_id, course_id=request_body.course_id,
            metadata={"block": request_body.block, "dis_client_id": dis_client_id,
                      "reason": "digest_pipeline_disabled_for_client"},
        )
        raise HTTPException(400, "Digest pipeline is not enabled for this course's client.")

    params = request_body.model_dump()
    params["deliverable"] = "cdd"
    params["user_name"] = current_user.username
    # The id, not just the name: the worker rebuilds this caller to resolve DIS
    # access, and that resolution looks up tenant memberships by user id. Without
    # it the lookup fails and the caller is silently downgraded to the default DIS
    # client, which then builds a DIFFERENT client's block (see dis_access).
    params["user_id"] = getattr(current_user, "id", None)
    # Persist the role so the async worker reconstructs the caller's DIS
    # privilege (e.g. super_admin) instead of silently downgrading to 'user'.
    params["role"] = getattr(current_user, "role", "user")
    params["dis_client_id"] = dis_client_id
    job_id = job_repository.create_job(
        db, user_name=current_user.username, request_params=params,
        project_id=request_body.project_id, course_id=request_body.course_id,
        job_type="cdd_block",
    )
    # Written BEFORE submit so the request event can never be timestamped after
    # the outcome event a fast-failing job would write.
    # Audited at ENQUEUE, not only on completion: this path is always async, so a job
    # that fails would otherwise leave no audit trace that an expensive,
    # user-attributed generation was ever requested (the legacy path is synchronous,
    # where cdd.created covers both). Paired with cdd.block_failed in the worker.
    from promptops_app.services.audit_service import log_audit_event
    log_audit_event(
        db, current_user.username, "cdd.block_requested",
        entity_type="cdd", entity_id=None,
        project_id=request_body.project_id, course_id=request_body.course_id,
        metadata={
            "job_id": job_id, "block": request_body.block,
            "quality_tier": request_body.quality_tier or "standard",
            "model_choice": request_body.model_choice,
            "dis_client_id": dis_client_id,
            "prompt_id": request_body.prompt_id,
            "course_title": request_body.course_title,
            "extra_instructions": request_body.extra_instructions,
            # The other two user-steerable inputs, recorded at enqueue for the same
            # reason extra_instructions is: the audit row should show what the user
            # asked for, not just that they asked. Whether each one actually reached
            # a model is recorded separately on the version row's provenance
            # (user_directives), because the two can differ — a style id can point at
            # a deleted style.
            "style_id": request_body.style_id,
            "estimated_duration_hours": request_body.estimated_duration_hours,
        },
    )
    # dispatch, not job_runner: routes to Celery when enabled so a web-container
    # restart/OOM can't kill a long block-wide run (it falls back to the
    # threadpool automatically when Celery is off or the broker is unreachable).
    dispatch.submit(block_wide_jobs.run_block_wide_job, job_id)
    _log.info("cdd_generate_block_queued  user=%s  course=%d  block=%s  job=%s",
              current_user.username, request_body.course_id, request_body.block, job_id)
    return BlockWideJobResponse(
        job_id=job_id, status="queued", deliverable="cdd",
        block=request_body.block, poll_url=f"/api/v1/jobs/{job_id}",
    )


# ---------------------------------------------------------------------------
# Import an existing Blueprint / DLU CDD file
# ---------------------------------------------------------------------------

@router.post(
    "/import",
    response_model=CDDGenerateResponse,
    status_code=201,
    summary="Import an existing Blueprint / CDD file (Excel, DOCX, PDF)",
    description=(
        "Upload a Blueprint the user already has (Excel, Word or PDF). The file is "
        "extracted and normalized into the canonical worksheet shape, then saved as "
        "a normal CDD (same tables as a generated one), pinned as active, and shown "
        "in 'Your Title Design Documents' exactly like a generated blueprint. "
        "Excel is mapped deterministically (lossless); unstructured DOCX/PDF is "
        "reorganized by the LLM under a strict preserve-everything contract."
    ),
    responses={
        201: {"description": "Blueprint imported, pinned, and rendered as a CDD."},
        400: {"description": "Unsupported file type or unreadable content."},
        403: {"description": "User does not have the cdd.generate permission."},
    },
)
def import_cdd(
    file: UploadFile = File(..., description="Blueprint file (.xlsx, .xls, .docx, .pdf)."),
    course_id: int = Form(..., description="Course this imported CDD belongs to."),
    project_id: int = Form(..., description="Parent project id."),
    course_title: str = Form("", description="Block / course title. Blank → derived from the file."),
    document_title: str = Form("", description="Document label. Blank → '<block> — CDD'."),
    model_choice: str = Form("GPT-5.4", description="Model used only for the LLM restructure path."),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.generate")),
) -> CDDGenerateResponse:
    """Extract → normalize → persist an uploaded Blueprint as an active CDD.

    Reuses the exact persistence/pin/audit tail the generate paths use
    (``_persist_and_respond``), so an imported CDD is indistinguishable from a
    generated one everywhere downstream — only ``generation_params.prompt_source``
    records that it was imported.
    """
    from types import SimpleNamespace

    from promptops_app.services.cdd_import_service import normalize_import
    from promptops_app.services.usage_service import UsageLogContext

    raw = file.file.read()
    if not raw:
        raise HTTPException(400, "The uploaded file is empty.")

    usage_ctx = UsageLogContext(
        user_name=current_user.username,
        project_id=project_id,
        course_id=course_id,
        entity_type="cdd_import",
    )

    try:
        result = normalize_import(
            file.filename or "blueprint",
            raw,
            course_title=course_title,
            document_title=document_title,
            model_choice=model_choice,
            usage_ctx=usage_ctx,
        )
    except ValueError as exc:
        # Content/format problem the user can fix (wrong type, empty workbook) —
        # a clean 400, not a 500.
        raise HTTPException(400, str(exc)) from exc

    _log.info(
        "cdd_import  user=%s  course=%d  file=%r  method=%s  dlu=%s  sections=%d",
        current_user.username, course_id, file.filename,
        result.method, result.is_dlu, len(result.sections),
    )

    # A minimal request-body stand-in carrying exactly the fields
    # _persist_and_respond reads. Import has no style/duration/audience inputs, so
    # those are blank/None — the persist path already treats them as optional.
    request_body = SimpleNamespace(
        course_id=course_id,
        project_id=project_id,
        course_title=result.derived_block,
        document_title=result.derived_title,
        target_audience="",
        expert_domain="",
        estimated_duration_hours=None,
        extra_instructions="",
        model_choice=model_choice,
    )

    return _persist_and_respond(
        db, request_body, current_user,
        raw_output=result.raw_output,
        sections=result.sections,
        dis_source_units=[],
        prompt_provenance={
            "prompt_source": "imported",
            "import_method": result.method,
            "source_filename": file.filename,
            "is_dlu": result.is_dlu,
            "import_warnings": result.warnings,
        },
        system_prompt=f"[imported blueprint · method={result.method} · file={file.filename}]",
        user_prompt="[imported from an uploaded file — not a generated prompt]",
        model_used=(model_choice if "llm" in result.method else "import"),
        tokens_used=None,
        coverage=None,
    )


# ---------------------------------------------------------------------------
# Archive / restore / permanently delete
#
# Three endpoints rather than one because the two states differ in kind:
# archiving is an everyday tidy-up that anyone can undo, and purging destroys
# version history that cannot be recovered. The rules live in
# app/services/design_doc_archive.py so CDDs and Blueprints cannot drift apart.
# ---------------------------------------------------------------------------

@router.post(
    "/bulk-archive",
    response_model=BulkArchiveResponse,
    summary="Archive several CDDs at once",
    description=(
        "Archives every CDD named in `ids`, reporting each id's outcome. Takes "
        "explicit ids only — there is no predicate form, because a filter-driven "
        "mass delete is one mistake away from emptying a workspace. Ids that are "
        "missing, out of scope or pinned are skipped, not failed, so one bad id "
        "does not abandon the rest of the batch."
    ),
    responses={
        403: {"description": "Requires the cdd.archive permission."},
        422: {"description": "More ids than a single request may carry."},
    },
)
def bulk_archive_cdds(
    request_body: BulkArchiveRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.archive")),
) -> BulkArchiveResponse:
    """Archive a batch of CDDs, returning a row per id."""
    from app.services import design_doc_archive as archive_svc
    from promptops_app.services.audit_service import log_audit_event

    outcomes = archive_svc.bulk_archive(
        db, archive_svc.CDD, request_body.ids,
        actor=current_user.username,
        unpin=request_body.unpin,
        scope_course_id=request_body.course_id,
        scope_project_id=request_body.project_id,
    )
    response = BulkArchiveResponse.from_outcomes(outcomes)

    # One audit row for the batch, naming exactly which ids moved. A row per id
    # would bury the operation; a row saying "12 archived" would not survive the
    # question "which twelve?".
    #
    # The ids go in metadata, NOT entity_id: that column is VARCHAR(64) and would
    # overflow after about a dozen of them. log_audit_event swallows write
    # failures by design, so the overflow would not surface as an error — it
    # would silently drop the audit row for the very operation that most needs
    # one. entity_id is left unset because a batch is not one entity.
    archived_ids = [o.doc_id for o in outcomes if o.status == "archived"]
    if archived_ids:
        log_audit_event(
            db, current_user.username, "cdd.archived",
            entity_type="cdd",
            course_id=request_body.course_id, project_id=request_body.project_id,
            metadata={
                "bulk": True,
                "archived_ids": archived_ids,
                "archived": response.archived,
                "skipped": response.skipped,
                "already_archived": response.already_archived,
                "unpin": request_body.unpin,
            },
        )
    _log.info("cdd_bulk_archived  user=%s  archived=%d  skipped=%d",
              current_user.username, response.archived, response.skipped)
    return response


@router.get(
    "/{cdd_id}/references",
    response_model=DocumentReferences,
    summary="What currently references this CDD",
    description=(
        "Everything pointing at the CDD, and whether it can be permanently "
        "deleted. Use this to explain a refusal rather than just reporting one."
    ),
    responses={404: {"description": "CDD not found."}},
)
def get_cdd_references(
    cdd_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> DocumentReferences:
    """Return the reference/blocker snapshot for one CDD."""
    from app.services import design_doc_archive as archive_svc

    _get_cdd_or_404(db, cdd_id, current_user)
    return DocumentReferences.from_refs(
        archive_svc.references_for(db, archive_svc.CDD, cdd_id)
    )


@router.delete(
    "/{cdd_id}",
    response_model=ArchiveResponse,
    summary="Archive a CDD",
    description=(
        "Removes the CDD from the list without deleting anything — it can be "
        "restored. A CDD pinned as active on a course is refused unless "
        "`unpin=true`, so nobody silently removes the document the next "
        "generation depends on."
    ),
    responses={
        404: {"description": "CDD not found."},
        403: {"description": "Requires the cdd.archive permission."},
        409: {"description": "Pinned as active and unpin was not requested."},
    },
)
def archive_cdd(
    cdd_id: int,
    unpin: bool = Query(
        default=False,
        description="Clear the course's active-CDD pin so a pinned CDD can be archived.",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.archive")),
) -> ArchiveResponse:
    """Soft-delete one CDD."""
    from app.services import design_doc_archive as archive_svc
    from app.core.exceptions import ResourceInUseError
    from promptops_app.services.audit_service import log_audit_event

    cdd = _get_cdd_or_404(db, cdd_id, current_user)
    outcome = archive_svc.archive(
        db, archive_svc.CDD, cdd, actor=current_user.username, unpin=unpin,
    )
    if not outcome.ok:
        raise ResourceInUseError(outcome.reason, blockers=[outcome.reason], detail={"id": cdd_id})

    if outcome.status == "archived":
        log_audit_event(
            db, current_user.username, "cdd.archived",
            entity_type="cdd", entity_id=cdd_id,
            course_id=cdd.course_id, project_id=cdd.project_id,
            metadata={"title": cdd.title, "unpinned_courses": list(outcome.unpinned_courses)},
        )
        _log.info("cdd_archived  user=%s  cdd_id=%d  unpinned=%s",
                  current_user.username, cdd_id, outcome.unpinned_courses or "none")

    return ArchiveResponse(
        id=cdd_id,
        archived=True,
        unpinned_courses=list(outcome.unpinned_courses),
        message=(
            "Already archived." if outcome.status == "already_archived"
            else "Archived. Restore it any time from the archived list."
        ),
    )


@router.post(
    "/{cdd_id}/restore",
    response_model=ArchiveResponse,
    summary="Restore an archived CDD",
    description=(
        "Returns the CDD to the list. Deliberately does not re-pin it: which "
        "document a course generates from is an explicit decision."
    ),
    responses={
        404: {"description": "CDD not found."},
        403: {"description": "Requires the cdd.archive permission."},
    },
)
def restore_cdd(
    cdd_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.archive")),
) -> ArchiveResponse:
    """Undo an archive."""
    from app.services import design_doc_archive as archive_svc
    from promptops_app.services.audit_service import log_audit_event

    cdd = _get_cdd_or_404(db, cdd_id, current_user)
    changed = archive_svc.restore(db, archive_svc.CDD, cdd)
    if changed:
        log_audit_event(
            db, current_user.username, "cdd.restored",
            entity_type="cdd", entity_id=cdd_id,
            course_id=cdd.course_id, project_id=cdd.project_id,
            metadata={"title": cdd.title},
        )
        _log.info("cdd_restored  user=%s  cdd_id=%d", current_user.username, cdd_id)

    return ArchiveResponse(
        id=cdd_id,
        archived=False,
        message=(
            "Restored. Pin it if you want generation to use it."
            if changed else "That CDD was not archived."
        ),
    )


@router.delete(
    "/{cdd_id}/permanent",
    response_model=PurgeResponse,
    summary="Permanently delete an archived CDD",
    description=(
        "Irreversible. Refused unless the CDD is archived first and nothing "
        "references it — deleting a CDD cascades to every blueprint derived "
        "from it and their whole version history, so a referenced CDD stays "
        "archived instead. Admin only."
    ),
    responses={
        404: {"description": "CDD not found."},
        403: {"description": "Requires the cdd.purge permission (admin)."},
        409: {"description": "Something still references it; see detail.blockers."},
        422: {"description": "Not archived yet — archive it first."},
    },
)
def permanently_delete_cdd(
    cdd_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.purge")),
) -> PurgeResponse:
    """Hard-delete an archived, unreferenced CDD and its versions."""
    from app.services import design_doc_archive as archive_svc
    from promptops_app.services.audit_service import log_audit_event

    cdd = _get_cdd_or_404(db, cdd_id, current_user)
    title, course_id, project_id = cdd.title, cdd.course_id, cdd.project_id

    refs = archive_svc.purge(db, archive_svc.CDD, cdd)

    # Logged after the delete succeeds, with the version count it destroyed —
    # the row itself is gone, so the audit entry is the only remaining record
    # that it ever existed.
    log_audit_event(
        db, current_user.username, "cdd.purged",
        entity_type="cdd", entity_id=cdd_id,
        course_id=course_id, project_id=project_id,
        metadata={"title": title, "versions_deleted": refs.version_count},
    )
    _log.warning("cdd_purged  user=%s  cdd_id=%d  versions=%d",
                 current_user.username, cdd_id, refs.version_count)
    return PurgeResponse(
        id=cdd_id, deleted=True,
        message=f"Permanently deleted, along with {refs.version_count} saved version(s).",
    )


# ---------------------------------------------------------------------------
# Get a single CDD
# ---------------------------------------------------------------------------

@router.get(
    "/{cdd_id}",
    response_model=CDDRead,
    summary="Get a single CDD with its active version content",
    description=(
        "**cdd_id** is the primary key of `course_design_documents` — not the course id. "
        "To load the CDD pinned on a course, call `GET /api/v1/courses/{course_id}` "
        "and use the returned `active_cdd_id`, or use "
        "`GET /api/v1/courses/{course_id}/active-cdd`."
    ),
    responses={404: {"description": "CDD not found."}},
)
def get_cdd(
    cdd_id: int = Path(
        ...,
        description="CDD record id (course_design_documents.id). Not the course id.",
        examples=[12],
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> CDDRead:
    """
    Return full CDD metadata and the content of its currently active version.

    The active version content is embedded in the response to avoid a second
    API call, matching the Streamlit pattern of loading both simultaneously.
    """
    cdd = _get_cdd_or_404(db, cdd_id, current_user)
    return build_cdd_read(db, cdd)


# ---------------------------------------------------------------------------
# List CDD versions
# ---------------------------------------------------------------------------

@router.get(
    "/{cdd_id}/versions",
    response_model=list[CDDVersionListItem],
    summary="List all versions of a CDD",
    responses={404: {"description": "CDD not found."}},
)
def list_cdd_versions(
    cdd_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> list[CDDVersionListItem]:
    """Return all saved versions for a CDD, newest first."""
    from promptops_app.repositories import cdd_repository

    _get_cdd_or_404(db, cdd_id, current_user)  # validates existence
    versions = cdd_repository.list_cdd_versions(db, cdd_id)
    return [CDDVersionListItem.model_validate(v) for v in versions]


# ---------------------------------------------------------------------------
# Get a specific CDD version
# ---------------------------------------------------------------------------

@router.get(
    "/{cdd_id}/versions/{version}",
    response_model=CDDVersionRead,
    summary="Get the full content of a specific CDD version",
    responses={
        404: {"description": "CDD or version not found."},
    },
)
def get_cdd_version(
    cdd_id: int,
    version: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> CDDVersionRead:
    """
    Return the full Markdown content and parsed sections for a specific CDD version.

    Used when the user selects a version from the version dropdown in the CDD tab.
    """
    from promptops_app.repositories import cdd_repository

    _get_cdd_or_404(db, cdd_id, current_user)
    version_record = cdd_repository.get_cdd_version(db, cdd_id, version)

    if version_record is None:
        raise NotFoundError(f"CDD version '{version}'", cdd_id)

    return CDDVersionRead.model_validate(version_record)


# ---------------------------------------------------------------------------
# Activate a specific CDD version
# ---------------------------------------------------------------------------

@router.post(
    "/{cdd_id}/versions/{version}/activate",
    response_model=CDDActivateVersionResponse,
    summary="Set a specific version as the active version",
    description="Marks the given version as active. All other versions are deactivated.",
    responses={
        404: {"description": "CDD or version not found."},
        403: {"description": "Requires cdd.version permission."},
    },
)
def activate_cdd_version(
    cdd_id: int,
    version: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.version")),
) -> CDDActivateVersionResponse:
    """
    Set a CDD version as the active version.

    Replicates the "Set as Active Version" button in the Streamlit CDD tab.
    All other versions for this CDD are deactivated in the same transaction.
    """
    from promptops_app.database import CDDVersion

    cdd = _get_cdd_or_404(db, cdd_id, current_user)

    version_record = db.query(CDDVersion).filter(
        CDDVersion.cdd_id == cdd_id,
        CDDVersion.version == version,
    ).first()

    if version_record is None:
        raise NotFoundError(f"CDD version '{version}'", cdd_id)

    # Deactivate all versions, then activate the selected one.
    db.query(CDDVersion).filter(CDDVersion.cdd_id == cdd_id).update(
        {CDDVersion.is_active: False}
    )
    version_record.is_active = True
    cdd.active_version = version
    db.commit()

    _log.info(
        "cdd_version_activated  user=%s  cdd_id=%d  version=%s",
        current_user.username, cdd_id, version,
    )

    return CDDActivateVersionResponse(cdd_id=cdd_id, active_version=version)


# ---------------------------------------------------------------------------
# Commit a new manual CDD version
# ---------------------------------------------------------------------------

@router.post(
    "/{cdd_id}/versions",
    response_model=CDDVersionRead,
    status_code=201,
    summary="Commit edited content as a new CDD version",
    description=(
        "Saves the current editor content as a new named version. "
        "The new version is immediately set as active."
    ),
    responses={
        404: {"description": "CDD not found."},
        403: {"description": "Requires cdd.version permission."},
    },
)
def create_cdd_version(
    cdd_id: int,
    request_body: CDDVersionCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.version")),
) -> CDDVersionRead:
    """
    Commit a new CDD version from manually edited content.

    Replicates the "Save as New Version" expander in the Streamlit CDD tab.
    The new version is set as active and the CDD's ``active_version`` field
    is updated accordingly.
    """
    from promptops_app.database import CDDVersion
    from promptops_app.services.audit_service import log_audit_event

    cdd = _get_cdd_or_404(db, cdd_id, current_user)

    # Deactivate all existing versions before creating the new one.
    db.query(CDDVersion).filter(CDDVersion.cdd_id == cdd_id).update(
        {CDDVersion.is_active: False}
    )

    new_version = CDDVersion(
        cdd_id=cdd_id,
        version=request_body.version_tag,
        full_content=request_body.full_content,
        sections=json.dumps(request_body.sections),
        change_reason=request_body.change_reason,
        is_active=True,
        created_by=current_user.username,
    )
    db.add(new_version)
    cdd.active_version = request_body.version_tag
    db.commit()
    db.refresh(new_version)

    log_audit_event(
        db,
        current_user.username,
        "cdd.version_committed",
        entity_type="cdd",
        entity_id=cdd_id,
        metadata={"version": request_body.version_tag, "reason": request_body.change_reason},
    )

    _log.info(
        "cdd_version_committed  user=%s  cdd_id=%d  version=%s",
        current_user.username, cdd_id, request_body.version_tag,
    )

    return CDDVersionRead.model_validate(new_version)


# ---------------------------------------------------------------------------
# Regeneration (AI) — ports the Streamlit CDD regenerate controls
# ---------------------------------------------------------------------------

@router.post(
    "/{cdd_id}/regenerate-item",
    response_model=CDDRegenerateItemResponse,
    summary="Regenerate a single item within a CDD section",
    responses={
        404: {"description": "CDD or item not found."},
        403: {"description": "Requires cdd.version permission."},
    },
)
def regenerate_cdd_item(
    cdd_id: int,
    request_body: CDDRegenerateItemRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.version")),
) -> CDDRegenerateItemResponse:
    """
    Regenerate one bullet/line/paragraph inside a CDD section, preserving all
    siblings. Replicates the per-item "⟳" button in the Streamlit CDD tab.

    Stateless: returns the patched section content. The frontend commits a new
    CDD version, matching the existing "Save Edit" flow.
    """
    from app.services import cdd_deep_context as deep_svc
    from app.services import cdd_regen_context as regen_ctx_svc
    from promptops_app.parsers.blueprint_parser import (
        parse_items_from_section,
        patch_item_in_section,
        regen_single_item,
    )
    from promptops_app.services.usage_service import UsageLogContext

    cdd = _get_cdd_or_404(db, cdd_id, current_user)
    course_title = getattr(cdd, "course_title", None) or request_body.section_key

    original = request_body.section_content or ""
    item_index = request_body.item_index
    items = parse_items_from_section(original)
    if not items or item_index < 0 or item_index >= len(items):
        raise NotFoundError("CDD item", item_index)

    target = items[item_index]

    # Refuse before spending anything if the model could not return this item
    # whole. parse_items_from_section treats a markdown table as ONE item, so a
    # 20-day CDD day table arrives here as a single ~18k-token item against a
    # 16,384-token ceiling — the response came back truncated, was spliced in
    # below, and committed as a new version with rows missing and no error.
    target_label = f"Item {item_index + 1} of '{request_body.section_key}'"
    regen_ctx_svc.assert_can_emit(
        target["text"], model_choice=request_body.model_choice, label=target_label,
    )

    # Grounding from the CDD's own worksheets — no DIS call, no network.
    context = regen_ctx_svc.build_context(
        db, cdd,
        section_key=request_body.section_key,
        instruction=request_body.feedback or "",
        section_content=original,
    )

    # Source escalation, on the same terms as the section and row paths.
    #
    # This is the control a user actually reaches for to correct one field —
    # "the primary handbooks are incomplete, refer to the syllabus" is aimed at a
    # single line, not a whole worksheet. Without this the item button had
    # worksheet grounding only, so the one instruction most likely to be typed
    # into it was the one instruction it could not satisfy: the answer is in the
    # library, and this path never looked.
    instruction = request_body.feedback or ""
    # The user's explicit choice wins over the inference. wants_source reads the
    # instruction's wording, which is a good guess and only a guess — it cannot
    # know that "make this match what we teach" means the syllabus. When the
    # client offers a checkbox it sends the answer, and only an absent field
    # falls back to inferring.
    use_sources = (deep_svc.wants_source(instruction)
                   if request_body.use_sources is None else request_body.use_sources)
    deep = deep_svc.DeepContext()
    if use_sources:
        deep = deep_svc.deepen(
            db, cdd, scope=context.scope, instruction=instruction,
            current_user=current_user, course_title=course_title,
            section_key=request_body.section_key,
            # The item, not the whole section: anchors should describe the line
            # being corrected, not every other field around it.
            section_content=target["text"],
        )
        if deep.unavailable and not deep.found:
            raise RetrievalUnavailableError(
                "This instruction needs the source library, and it could not be "
                "reached — so the item was left untouched rather than rewritten "
                "without it. Nothing was changed or charged for. Try again once "
                "the source service is back.",
                levels_tried=list(deep.levels_tried),
                detail={"flags": list(deep.flags), "item_index": item_index},
            )

    context_block = context.text
    if deep.found:
        context_block = f"{context_block}\n\n{deep.text}" if context_block else deep.text

    usage_ctx = UsageLogContext(
        user_name=current_user.username, project_id=cdd.project_id, course_id=cdd.course_id,
        entity_type="cdd_item_regen", entity_id=str(cdd_id),
    )
    new_item_text = regen_single_item(
        section_title=request_body.section_key,
        section_content=original,
        item_index=item_index,
        item_text=target["text"],
        custom_instruction=instruction,
        model_choice=request_body.model_choice,
        usage_ctx=usage_ctx,
        context=context_block,
    )
    updated_content = patch_item_in_section(original, item_index, new_item_text)

    _log.info("cdd_item_regenerated  user=%s  cdd_id=%d  section=%s  item=%d  %s  %s",
              current_user.username, cdd_id, request_body.section_key, item_index,
              context.provenance(), deep.provenance())

    from promptops_app.services.budget_service import build_usage_summary

    # Same no-op visibility the section path has. Compared on the ITEM, because
    # patch_item_in_section rewrites one line — an unchanged item still yields a
    # section string that differs from nothing, and committing it would write a
    # new CDD version that reads identically to the last one.
    changed = (new_item_text or "").strip() != (target["text"] or "").strip()
    if not changed:
        _log.info("cdd_item_regen_noop  user=%s  cdd_id=%d  section=%s  item=%d  %s",
                  current_user.username, cdd_id, request_body.section_key, item_index,
                  deep.provenance())

    return CDDRegenerateItemResponse(
        updated_content=updated_content,
        patched_item=new_item_text or "",
        usage_summary=build_usage_summary(db, usage_ctx, "cdd_item_regen", str(cdd_id)),
        changed=changed,
        note=None if changed else _noop_note(deep, target_text=target["text"]),
    )


#: Documents named in a no-op note before it summarises the rest. Enough to
#: recognise what was consulted; not so many that the toast becomes a file list.
_NOTE_SOURCE_LIMIT = 4

#: Document types shown when explaining what a search was allowed to cover.
_NOTE_TYPE_LIMIT = 6


def _scope_hint(deep) -> str:
    """What the search was allowed to see — the one cause that IS knowable here.

    A CDD search covers the document types the tenant's `cdd` purpose admits,
    plus any the instruction named (see cdd_deep_context.document_type_hints).
    Everything else in the library is out of scope for the request, which is a
    different problem from a missing document and has a different fix: name the
    material in the instruction, or widen the purpose.
    """
    types = list(getattr(deep, "document_types", ()) or ())
    if types:
        shown = ", ".join(types[:_NOTE_TYPE_LIMIT])
        more = ", and others" if len(types) > _NOTE_TYPE_LIMIT else ""
        return (f"The search covered these document types: {shown}{more}. "
                "Material of any other type was out of scope for this request — "
                "naming it in the instruction brings it in.")
    if "doc_types_unopened" in (getattr(deep, "flags", ()) or ()):
        return ("The instruction named material outside the CDD source set, but the "
                "tenant's retrieval config could not be read, so the search stayed "
                "narrow. Retrying may widen it.")
    return ("The search covered the document types configured as this course's CDD "
            "sources. Other material in the Source Library is out of scope unless "
            "the instruction names it.")


def _completeness_hint(deep) -> str:
    """Name any document the model did not see in full.

    Nothing is ever truncated to fit a budget, but two kinds of loss survive that
    rule, and they need opposite responses — so they are reported separately rather
    than merged into one "incomplete" sentence:

    * ``omitted_documents`` — retrieved, never opened. None of it was read, so a
      "not covered" conclusion says nothing about it. Fix: name it in the
      instruction so it ranks ahead of what crowded it out.
    * ``incomplete_documents`` — opened, but DIS packed only part of it before
      answering. It DID inform the answer, partially, which is the more dangerous
      of the two: a conclusion drawn from it looks as well-sourced as any other.
      Fix: narrow the request so fewer documents compete.

    Returns "" when everything retrieved was read in full, which is the normal case
    — the note should not carry a caveat that does not apply.
    """
    parts: list[str] = []
    omitted = list(getattr(deep, "omitted_documents", ()) or ())
    incomplete = list(getattr(deep, "incomplete_documents", ()) or ())
    if omitted:
        shown = ", ".join(omitted[:_NOTE_SOURCE_LIMIT])
        more = ", and others" if len(omitted) > _NOTE_SOURCE_LIMIT else ""
        parts.append(f" These were found but not read, because higher-ranked documents "
                     f"filled the request first: {shown}{more}. Naming one in the "
                     "instruction moves it to the front.")
    if incomplete:
        shown = ", ".join(incomplete[:_NOTE_SOURCE_LIMIT])
        more = ", and others" if len(incomplete) > _NOTE_SOURCE_LIMIT else ""
        parts.append(f" These were read only in part: {shown}{more} — so nothing here "
                     "rules out that they cover it. Asking about one of them "
                     "specifically will retrieve more of it.")
    return "".join(parts)


def _noop_note(deep, *, target_text: str = "") -> str:
    """Explain an unchanged section in terms the user can act on.

    ``target_text`` is what was already in the cell, when the caller knows it.
    It changes which explanation is the honest one, and the difference is not
    cosmetic: on CDD 169 the Primary Handbooks line was already complete
    ("FAA-H-8083-30B — cited on Days 1-13, 17-19; …"), retrieval returned 25
    units of the correct block's calendars and syllabi, and the instruction
    reduced to the words "part check once". Leading with "the material does not
    answer the instruction" pointed at the source library, where the actual
    problem was that the instruction did not say what to change. A message that
    sends the user to look at the wrong thing costs them more than no message.

    Three outcomes look identical from the outside and need different answers,
    so they must not share one message. The previous wording claimed the lookup
    "found nothing" whenever any rung had run — which was wrong in the case that
    actually happens: on CDD 169 the search returned eight source units and the
    toast still reported that nothing had been found, pointing the user at a
    retrieval failure that had not occurred.

    Naming the documents matters more than the verdict. "These were read and
    none of them covers it" tells the user which document to upload; "nothing
    happened" tells them the feature is broken.

    What it must NOT do is guess at a cause. The earlier wording ended "it may
    not be ingested for this block — check the Source Library", and on the run
    that produced it the material WAS ingested: 992 of Block 2's 1,002 indexed
    units were simply outside the document types the CDD purpose admits. The
    advice was confident, actionable and pointed at the wrong place. Retrieval
    scope is knowable here; ingestion status is not, so only the first is stated.
    """
    from app.services.cdd_deep_context import is_placeholder

    already_filled = bool((target_text or "").strip()) and not is_placeholder(target_text)
    if already_filled:
        head = ("The model returned this unchanged. It already has content, and neither "
                "the instruction nor the material retrieved gave it a reason to change "
                "— it is told to leave content alone rather than guess. ")
        tail = (" If you meant something specific, say what should change and what it "
                "should become; an instruction that does not name a change reads as "
                "\"leave this alone\".")
    else:
        head = ("The model returned this section unchanged, because the material it was "
                "given does not answer the instruction — it is told to leave content "
                "alone rather than guess. ")
        tail = ""
    if deep.found:
        # `units` comes from the retrieval response and `sources` is parsed out of
        # the text, so either can be absent while the other is populated. Report
        # whichever is actually known rather than a confident "0 units".
        shown = deep.sources[:_NOTE_SOURCE_LIMIT]
        count = deep.units or len(deep.sources)
        return (
            head
            + "The source library was searched and "
            + (f"{count} unit(s) came back" if count else "material came back")
            + (f" ({', '.join(shown)}"
               + (", and others" if len(deep.sources) > _NOTE_SOURCE_LIMIT else "")
               + ")" if shown else "")
            + (", and nothing in it changed this. " if already_filled
               else ", but none of it covers what was asked. ")
            + _scope_hint(deep)
            # Only on this branch. The empty-lookup branch below retrieved nothing,
            # so it can have nothing omitted — and _pack_units always keeps at least
            # one unit, so "found nothing" and "dropped something" cannot co-occur.
            + _completeness_hint(deep)
            + " Nothing was saved."
            + tail
        )
    if deep.levels_tried:
        # Scope belongs here too, and arguably more than above: a lookup that
        # returned nothing at all is the case where the user is most likely to
        # conclude the library is empty, when the truthful reading is that this
        # request could only see part of it. A rung that could not be consulted
        # never reaches this note — the caller raises RetrievalUnavailableError
        # for that — so "came back empty" is a statement about the source.
        return (
            head
            + f"A {deep.level or 'source'} lookup ran and came back empty, so there "
              "is nothing to fill this from. "
            + _scope_hint(deep)
            + " Nothing was saved."
            + tail
        )
    return (head + "No source lookup was needed for this instruction. Nothing was saved."
            + tail)


def _regenerate_rows(db, cdd, *, plan, context, request_body, course_title, current_user):
    """Regenerate only the day rows a plan targets, then merge them back.

    Split out of the endpoint rather than inlined because the two paths differ
    in everything that matters — what is sent, what is asked for, and what is
    allowed to change — and interleaving them behind conditionals is how the
    stricter one ends up quietly sharing the looser one's rules.
    """
    from app.services import cdd_deep_context as deep_svc
    from app.services import cdd_regen_context as regen_ctx_svc
    from app.services import cdd_scoped_regen as scoped_regen
    from promptops_app.prompt_templates import CDD_ROW_REGENERATE_PROMPT, CDD_SYSTEM_PROMPT
    from promptops_app.services.budget_service import build_usage_summary
    from promptops_app.services.llm_service import generate_with_metadata
    from promptops_app.services.usage_service import UsageLogContext

    # Still checked, though a handful of rows will not come close: the guard is
    # about the request that was actually made, not about the shape we expect.
    regen_ctx_svc.assert_can_emit(
        plan.target_markdown,
        model_choice=request_body.model_choice,
        label=f"Days {', '.join(str(d) for d in plan.day_numbers)}",
    )

    # ── Source escalation ──────────────────────────────────────────────────
    # The worksheet can only support REVISING a cell. Filling an empty one needs
    # what is behind it, so when the targeted cells are blank — or the
    # instruction asks for content rather than improvement — climb to the day's
    # digest and units, then to a library search for material attributed to no
    # day at all.
    instruction = request_body.feedback or ""
    gaps = scoped_regen.unfilled_cells(plan)
    deep = deep_svc.DeepContext()
    if gaps or deep_svc.wants_source(instruction):
        deep = deep_svc.deepen(db, cdd, scope=context.scope, instruction=instruction,
                               current_user=current_user, course_title=course_title,
                               section_key=request_body.section_key,
                               section_content=plan.section_content)
        # See the section path: "could not look" must not be reported as "not
        # there", and must not become a silent no-op either.
        if deep.unavailable and not deep.found and (gaps or deep_svc.wants_source(instruction)):
            raise RetrievalUnavailableError(
                "These rows need the source library, and it could not be reached "
                "— so they were left untouched rather than rewritten without it. "
                "Nothing was changed or charged for. Try again once the source "
                "service is back.",
                levels_tried=list(deep.levels_tried),
                detail={"flags": list(deep.flags),
                        "days": [d for d in plan.day_numbers]},
            )

    # Nothing anywhere, for a request that was explicitly to fill a blank cell.
    # The empty cell is a true statement about the source library; a model given
    # this prompt would replace it with a fluent invention nobody downstream
    # could distinguish from a real value.
    if gaps and deep_svc.wants_source(instruction) and not deep.found:
        raise SourceUnavailableError(
            "There is no source for "
            + ", ".join(f"{col} on Day {day}" for day, col in gaps[:4])
            + (" and others" if len(gaps) > 4 else "")
            + ". The day's digest, its ingested units and a search of the source "
              "library all came back empty, so filling these cells would mean "
              "inventing them. Ingest the material and rebuild the digests, or "
              "leave the gap recorded.",
            levels_tried=list(deep.levels_tried),
            detail={"gaps": [{"day": d, "column": c} for d, c in gaps[:20]]},
        )

    context_block = context.text
    if deep.found:
        context_block = f"{context_block}\n\n{deep.text}" if context_block else deep.text

    prompt = CDD_ROW_REGENERATE_PROMPT.format(
        section_title=request_body.section_key,
        course_title=course_title,
        custom_instruction=instruction or "Improve these rows.",
        context_block=context_block,
        writable_columns=", ".join(plan.writable),
        protected_columns=", ".join(plan.protected) or "(none)",
        current_rows=plan.target_markdown,
    )

    usage_ctx = UsageLogContext(
        user_name=current_user.username, project_id=cdd.project_id, course_id=cdd.course_id,
        entity_type="cdd_section_regen", entity_id=str(cdd.id),
    )
    result = generate_with_metadata(
        request_body.model_choice, CDD_SYSTEM_PROMPT, prompt, usage_ctx,
        max_tokens=regen_ctx_svc.output_budget(request_body.model_choice),
    )
    if result.is_error or not result.text:
        raise LLMGenerationError("Row regeneration failed. Please try again.")
    # Before merge_rows, not after: a truncated table loses its trailing rows and
    # its last cell mid-word, and the merge would splice that in as if the model
    # had meant it.
    _reject_if_truncated(result, "regenerating these rows")

    merged = scoped_regen.merge_rows(plan, result.text)

    _log.info("cdd_rows_regenerated  user=%s  cdd_id=%d  section=%s  %s  %s  gaps=%d  %s  %s",
              current_user.username, cdd.id, request_body.section_key,
              plan.describe(), merged.describe(), len(gaps),
              context.provenance(), deep.provenance())

    # Same no-op visibility the section path has. A merge that carried every
    # original cell across — because the model returned the rows unchanged, or
    # returned only empty replacements — is a legitimate outcome, but committing
    # it writes a new version identical to the current one and tells the user
    # nothing about why.
    changed = merged.section_content.strip() != (plan.section_content or "").strip()
    return CDDRegenerateSectionResponse(
        updated_content=merged.section_content,
        usage_summary=build_usage_summary(db, usage_ctx, "cdd_section_regen", str(cdd.id)),
        changed=changed,
        note=None if changed else _noop_note(deep, target_text=plan.section_content or ""),
    )


@router.post(
    "/{cdd_id}/repair-digests",
    response_model=CDDDigestRepairResponse,
    summary="Rebuild the day digests behind a block-wide CDD",
    responses={
        404: {"description": "CDD not found."},
        409: {"description": "Block could not be resolved for this CDD."},
        502: {"description": "DIS was unreachable."},
    },
)
def repair_cdd_digests(
    cdd_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.version")),
) -> CDDDigestRepairResponse:
    """
    Retry the day digests for this CDD's block.

    The gap regeneration cannot close on its own. A day whose MAP call failed
    has no digest and no worksheet row derived from one, so every rung of the
    context ladder finds nothing — there is no source to escalate to until the
    day is rebuilt. CDD 169 lost two days this way to ``TransportError(504)``.

    Only failures re-run: ``day_is_cached`` requires ``digest_status == "ok"``,
    so days that already succeeded come from cache and cost no MAP tokens.
    Regenerate the affected rows afterwards to pull the recovered material into
    the document.
    """
    from app.core.dis_access import resolve_course_dis_client
    from app.services import cdd_deep_context as deep_svc

    cdd = _get_cdd_or_404(db, cdd_id, current_user)
    block = deep_svc.resolve_block(db, cdd)
    if not block:
        raise WorkflowError(
            "This CDD does not record which block it was built from, so its "
            "digests cannot be rebuilt. Block-wide CDDs state it in Worksheet 1; "
            "a CDD generated outside the digest pipeline has none to rebuild."
        )

    dis_client_id = resolve_course_dis_client(
        db, course_id=cdd.course_id, project_id=cdd.project_id,
    )
    try:
        report = dis_client.build_digests_sync(
            block, current_user=current_user, client_id=dis_client_id,
        ) or {}
    except Exception as exc:  # noqa: BLE001 — surfaced as a clean 502 below
        _log.warning("cdd_digest_repair_failed cdd_id=%s block=%s error=%s",
                     cdd_id, block, exc, exc_info=True)
        raise LLMGenerationError(
            f"Could not rebuild digests for {block}: {type(exc).__name__}. "
            "The document is unchanged."
        ) from exc

    built = int(report.get("built") or 0)
    _log.info("cdd_digests_repaired  user=%s  cdd_id=%d  block=%s  built=%d  cached=%d  failed=%d",
              current_user.username, cdd_id, block, built,
              int(report.get("cached") or 0), int(report.get("failed") or 0))

    return CDDDigestRepairResponse(
        block=block,
        built=built,
        cached=int(report.get("cached") or 0),
        failed=int(report.get("failed") or 0),
        map_calls=int(report.get("map_calls") or 0),
        reasons=[str(r) for r in (report.get("failure_reasons") or [])],
        repaired=built > 0,
    )


@router.post(
    "/{cdd_id}/regenerate-section",
    response_model=CDDRegenerateSectionResponse,
    summary="Regenerate an entire CDD section with AI",
    responses={
        404: {"description": "CDD not found."},
        403: {"description": "Requires cdd.version permission."},
    },
)
def regenerate_cdd_section(
    cdd_id: int,
    request_body: CDDRegenerateSectionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.version")),
) -> CDDRegenerateSectionResponse:
    """
    Regenerate a whole CDD section using the section-regeneration prompt.
    Replicates the "🔄 Regenerate Section" button in the Streamlit CDD tab.

    Stateless: returns the new section content; the frontend commits a version.
    """
    from app.services import cdd_deep_context as deep_svc
    from app.services import cdd_regen_context as regen_ctx_svc
    from app.services import cdd_scoped_regen as scoped_regen
    from promptops_app.prompt_templates import (
        CDD_SECTION_REGENERATE_GROUNDED_PROMPT,
        CDD_SECTION_REGENERATE_PROMPT,
        CDD_SYSTEM_PROMPT,
    )
    from promptops_app.services.llm_service import generate_with_metadata
    from promptops_app.services.usage_service import UsageLogContext

    cdd = _get_cdd_or_404(db, cdd_id, current_user)
    course_title = getattr(cdd, "course_title", None) or request_body.section_key

    # The request body carries no section content — the current text never left
    # the browser — so read it from the stored active version. Without it
    # "regenerate" meant "write a new section from its title", which is how a
    # 457-character prompt came to replace a 77,099-character worksheet.
    sections = regen_ctx_svc.load_sections(db, cdd)
    current_content = regen_ctx_svc.find_section(sections, request_body.section_key)

    context = regen_ctx_svc.build_context(
        db, cdd,
        section_key=request_body.section_key,
        instruction=request_body.feedback or "",
        section_content=current_content,
    )

    # ── Row-scoped path ────────────────────────────────────────────────────
    # When the instruction names days and the section is a day table, rewrite
    # only those rows. Everything else is carried across untouched by the merge
    # rather than re-emitted, which is what keeps a 20-day worksheet inside a
    # 16k output ceiling and what stops a model dropping rows it was never
    # asked about.
    plan = scoped_regen.plan_rows(current_content, context.scope) if current_content else None
    if plan is not None:
        return _regenerate_rows(
            db, cdd, plan=plan, context=context, request_body=request_body,
            course_title=course_title, current_user=current_user,
        )

    deep = deep_svc.DeepContext()
    if current_content.strip():
        # Revision, grounded in the section's own text plus the document's
        # other worksheets. Refuse rather than truncate (see assert_can_emit).
        regen_ctx_svc.assert_can_emit(
            current_content,
            model_choice=request_body.model_choice,
            label=f"Section '{request_body.section_key}'",
        )

        # A prose worksheet has no rows to scope, but it can still be asked for
        # something the worksheet does not contain — "the primary handbooks are
        # incomplete, refer to the syllabus" needs the syllabus, not a reread of
        # the list that is already wrong. Escalation is the same ladder the row
        # path uses; only the trigger differs, since there are no cells here to
        # inspect for emptiness.
        if deep_svc.wants_source(request_body.feedback or ""):
            deep = deep_svc.deepen(
                db, cdd, scope=context.scope, instruction=request_body.feedback or "",
                current_user=current_user, sections=sections,
                course_title=course_title, section_key=request_body.section_key,
                section_content=current_content,
            )
            # Could not look, as opposed to looked and found nothing. Proceeding
            # would send the model a prompt that cannot satisfy the instruction,
            # it would correctly return the section unchanged, and the UI would
            # commit a no-op — leaving the user to conclude the feature is broken
            # rather than that a dependency is down.
            if deep.unavailable and not deep.found:
                raise RetrievalUnavailableError(
                    "This instruction needs the source library, and it could not "
                    "be reached — so the section was left untouched rather than "
                    "rewritten without it. Nothing was changed or charged for. "
                    "Try again once the source service is back.",
                    levels_tried=list(deep.levels_tried),
                    detail={"flags": list(deep.flags)},
                )

        context_block = context.text
        if deep.found:
            context_block = f"{context_block}\n\n{deep.text}" if context_block else deep.text

        regen_prompt = CDD_SECTION_REGENERATE_GROUNDED_PROMPT.format(
            section_title=request_body.section_key,
            course_title=course_title,
            custom_instruction=request_body.feedback or "Improve and expand this section.",
            context_block=context_block,
            current_content=current_content,
        )
    elif sections:
        # The document has sections but not this one. Writing it from scratch
        # here is how a Worksheet 1 regeneration came back as a generic
        # "Module 1: Introduction to Aircraft Drawings" outline — 451 input
        # tokens, no grounding — which the UI would then have committed over the
        # real worksheet. Refuse: a section that exists somewhere and cannot be
        # located is a lookup failure, not an invitation to invent one.
        raise SourceUnavailableError(
            f"'{request_body.section_key}' could not be found in this CDD's "
            f"current version, so there is nothing to revise. Regenerating it "
            f"from the title alone would replace the section with invented "
            f"content. Reload the document and try again.",
            levels_tried=["stored_sections", "document_body"],
            detail={"section_key": request_body.section_key,
                    "available_sections": sorted(sections)[:20]},
        )
    else:
        # A genuinely empty document — nothing stored at all, e.g. a CDD being
        # authored before its first generation. The original from-scratch prompt
        # is the correct behaviour here and is unchanged.
        regen_prompt = CDD_SECTION_REGENERATE_PROMPT.format(
            section_title=request_body.section_key,
            course_title=course_title,
            custom_instruction=request_body.feedback or "Improve and expand this section.",
        )

    usage_ctx = UsageLogContext(
        user_name=current_user.username, project_id=cdd.project_id, course_id=cdd.course_id,
        entity_type="cdd_section_regen", entity_id=str(cdd_id),
    )
    # Ask for the model's real ceiling instead of inheriting the flat 16384
    # default, which capped a 64k-output model at a quarter of its range.
    result = generate_with_metadata(
        request_body.model_choice, CDD_SYSTEM_PROMPT, regen_prompt, usage_ctx,
        max_tokens=regen_ctx_svc.output_budget(request_body.model_choice),
    )
    new_content = f"ERROR: {result.text}" if result.is_error else result.text
    if not new_content or new_content.startswith("ERROR"):
        raise LLMGenerationError("Section regeneration failed. Please try again.")
    _reject_if_truncated(result, "regenerating this section")

    _log.info("cdd_section_regenerated  user=%s  cdd_id=%d  section=%s  grounded=%s  %s  %s",
              current_user.username, cdd_id, request_body.section_key,
              bool(current_content.strip()), context.provenance(), deep.provenance())

    from promptops_app.services.budget_service import build_usage_summary

    # An unchanged answer is legitimate — the grounded prompt tells the model to
    # leave content alone when the instruction cannot be satisfied from what it
    # was given — but it must be said out loud. Returned silently, it is
    # indistinguishable from a broken feature, and the caller commits a version
    # identical to the one before it.
    final = new_content.strip()
    changed = final != (current_content or "").strip()
    note = None
    if not changed:
        note = _noop_note(deep, target_text=current_content or "")
        _log.info("cdd_section_regen_noop  user=%s  cdd_id=%d  section=%s  %s",
                  current_user.username, cdd_id, request_body.section_key,
                  deep.provenance())

    return CDDRegenerateSectionResponse(
        updated_content=final,
        usage_summary=build_usage_summary(db, usage_ctx, "cdd_section_regen", str(cdd_id)),
        changed=changed,
        note=note,
    )


# ---------------------------------------------------------------------------
# Pin a CDD to a course
# ---------------------------------------------------------------------------

@router.post(
    "/{cdd_id}/pin",
    response_model=CDDPinResponse,
    summary="Set this CDD as the active CDD for generation",
    description=(
        "Persists the selection so the Generate page knows which CDD to inject "
        "into prompts. Equivalent to the '📌 Set as Active CDD' button."
    ),
    responses={
        404: {"description": "CDD not found."},
        403: {"description": "Requires cdd.pin permission."},
    },
)
def pin_cdd(
    cdd_id: int,
    request_body: CDDPinRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cdd.pin")),
) -> CDDPinResponse:
    """
    Pin a CDD as the active CDD for a course.

    Calls ``course_repository.set_active_cdd()`` — the same function used by
    the auto-pin after generation and the manual pin button in the Streamlit UI.
    """
    from promptops_app.repositories.course_repository import set_active_cdd
    from app.services import design_doc_archive as archive_svc

    cdd = _get_cdd_or_404(db, cdd_id, current_user)
    # Pinning an archive would quietly put a document someone deliberately
    # retired back in front of every generation for this course.
    archive_svc.assert_live(cdd, archive_svc.CDD)
    set_active_cdd(db, request_body.course_id, cdd_id)

    _log.info(
        "cdd_pinned  user=%s  cdd_id=%d  course_id=%d",
        current_user.username, cdd_id, request_body.course_id,
    )

    return CDDPinResponse(cdd_id=cdd_id, course_id=request_body.course_id, pinned=True)


# ---------------------------------------------------------------------------
# Export a CDD as a file
# ---------------------------------------------------------------------------

@router.get(
    "/{cdd_id}/export",
    summary="Download a CDD as a file (DOCX or Markdown)",
    description=(
        "Generates a formatted document from the CDD's active version content. "
        "Supports 'docx' (default) and 'md' formats."
    ),
    responses={
        200: {"description": "File download."},
        404: {"description": "CDD not found or has no active version."},
        403: {"description": "Requires export.course permission."},
    },
)
def export_cdd(
    cdd_id: int,
    format: str = Query(default="docx", description="Export format: docx | md"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("export.course")),
) -> Response:
    """
    Export the active CDD version as a downloadable file.

    Calls ``export_service.export_content()`` with the same parameters as the
    Streamlit "⬇️ Word (.docx)" download button.
    """
    from promptops_app.parsers.cdd_parser import (
        _strip_ui_hidden_text, parse_cdd_flat, is_dlu_cdd, split_cdd_worksheets,
    )
    from promptops_app.repositories import cdd_repository
    from promptops_app.services.export_service import ExportRequest, export_content

    cdd = _get_cdd_or_404(db, cdd_id, current_user)

    if not cdd.active_version:
        raise WorkflowError("This CDD has no active version to export.")

    version_record = cdd_repository.get_cdd_version(db, cdd_id, cdd.active_version)
    if not version_record:
        raise NotFoundError(f"CDD active version '{cdd.active_version}'", cdd_id)

    # Build export blocks — same logic as the Streamlit download button.
    parsed = parse_cdd_flat(version_record.full_content or "")

    # DLU CDD + XLSX → one sheet per worksheet (Overview + Worksheet 1..N).
    # Standard CDDs and every other format fall through to the shared export path
    # below, unchanged.
    if format == "xlsx" and is_dlu_cdd(version_record.full_content or ""):
        from promptops_app.exporters.xlsx_exporter import build_xlsx_worksheets

        cs_text = parsed.get("Course Structure", "") or (version_record.full_content or "")
        sheets = []
        for label, content in split_cdd_worksheets(cs_text):
            clean = _strip_ui_hidden_text(content)
            if (clean or "").strip():
                sheets.append((label, clean))
        if sheets:
            buf = build_xlsx_worksheets(cdd.title, sheets)
            fname = f"CDD_{cdd.title.replace(' ', '_')}_{cdd.active_version}.xlsx"
            _log.info(
                "cdd_exported_dlu_xlsx  user=%s  cdd_id=%d  sheets=%d",
                current_user.username, cdd_id, len(sheets),
            )
            # This DLU-worksheet branch builds its own file and returns early,
            # bypassing export_service.export_content() (and its _log_export
            # call) entirely — every other export path goes through that
            # shared function, so without this, DLU xlsx exports were
            # invisible to both the System Event Log and the Audit Trail.
            from promptops_app.database import log_event
            from promptops_app.services.audit_service import log_audit_event

            log_event(
                db, "export", current_user.username,
                f"Exported XLSX [DLU worksheets] — cdd #{cdd_id} — {cdd.title}",
            )
            log_audit_event(
                db, current_user.username, "export.course",
                entity_type="cdd", entity_id=cdd_id,
                project_id=cdd.project_id, course_id=cdd.course_id,
                metadata={"format": "xlsx", "template": "dlu_worksheets", "sheets": len(sheets)},
            )

            return Response(
                content=buf.read(),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": content_disposition(fname)},
            )
    export_blocks = []
    for section_key, section_label in [
        ("Course Details",          "Course Details"),
        ("Course Structure",        "Course Structure & Module Assessments"),
        ("Course Level Assessment", "Course Level Assessment"),
    ]:
        content = parsed.get(section_key, "").strip()
        if content:
            content = _strip_ui_hidden_text(content)
            if content:
                export_blocks.append((section_label, content))

    if not export_blocks:
        export_blocks = [("CDD Content", version_record.full_content or "")]

    export_request = ExportRequest(
        fmt=format,
        topic=cdd.title,
        blocks=export_blocks,
        user_name=current_user.username,
        is_admin=(current_user.role == "admin"),
        entity_type="cdd",
        entity_id=cdd.id,
        project_id=cdd.project_id,
        course_id=cdd.course_id,
        file_name=f"CDD_{cdd.title.replace(' ', '_')}_{cdd.active_version}.{format}",
    )

    result = export_content(db, export_request)

    if not result.success:
        raise WorkflowError(f"Export failed: {result.error_message}")

    _log.info(
        "cdd_exported  user=%s  cdd_id=%d  format=%s",
        current_user.username, cdd_id, format,
    )

    return Response(
        content=result.data,
        media_type=result.mime_type,
        headers={"Content-Disposition": content_disposition(result.file_name)},
    )
