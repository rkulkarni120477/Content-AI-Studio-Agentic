"""
Prompts router — prompt template registry.

Streamlit equivalent: ``pages/prompts.py`` render_page()

Prompt templates drive all AI generation. This router manages the full
lifecycle: create, version, deploy, and delete prompt assets.

Three creation paths (matching the Streamlit UI):
  1. Manual creation (POST /prompts with empty versions)
  2. From a built-in template (POST /prompts/from-template)
  3. AI-generated from a plain-language description (POST /prompts/generate)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import (
    DuplicateResourceError,
    LLMGenerationError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    WorkflowError,
)
from app.core.permissions import effective_rbac_check
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.prompt import (
    CoursePromptGroup,
    CoursePromptSlot,
    PromptAIGenerateRequest,
    PromptAISuggestResponse,
    PromptCreateFromTemplateRequest,
    PromptCreateRequest,
    PromptDefaultRequest,
    PromptDeployResponse,
    PromptDetailRead,
    PromptFixingRead,
    PromptFixingSetRequest,
    PromptFragmentRead,
    PromptFragmentSetRequest,
    PromptFromGenerationRequest,
    PromptListItem,
    PromptRead,
    PromptsByCourseResponse,
    PromptUpdateRequest,
    PromptVariableItem,
    PromptVariablesRead,
    PromptVariablesSetRequest,
    PromptVersionCreateRequest,
    PromptVersionListItem,
    PromptVersionRead,
    PromptWorkflowStateRequest,
)

_log = logging.getLogger(__name__)
router = APIRouter()


def _get_prompt_or_404(db: Session, prompt_id: int):
    """Fetch a prompt by ID or raise HTTP 404."""
    from promptops_app.database import Prompt
    prompt = db.query(Prompt).filter(Prompt.id == prompt_id).first()
    if not prompt:
        raise NotFoundError("Prompt", prompt_id)
    return prompt


def _prompt_detail(db: Session, prompt) -> PromptDetailRead:
    """Build prompt metadata plus active version text."""
    from promptops_app.repositories import prompt_repository

    base = PromptRead.model_validate(prompt)
    detail = PromptDetailRead.model_validate(base.model_dump())
    active = prompt_repository.get_active_version(db, prompt.id)
    if active:
        detail.system_prompt = active.system_prompt
        detail.user_prompt_template = active.user_prompt_template
        detail.version_created_by = active.created_by
        detail.version_created_at = active.created_at
        detail.version_change_reason = active.change_reason
    return detail


@router.get(
    "",
    response_model=PaginatedResponse[PromptListItem],
    summary="List all prompt templates",
    description="Supports text search and component filter. Used for the Generate page template dropdown.",
)
def list_prompts(
    search: str | None = Query(default=None, description="Search by name, description, or tags."),
    component: str | None = Query(default=None, description="Filter by component scope, e.g. 'generate'."),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompts.view")),
) -> PaginatedResponse[PromptListItem]:
    """Return all prompt templates, optionally filtered."""
    from promptops_app.repositories import prompt_repository

    if component:
        prompts = prompt_repository.list_prompts_by_component(db, component)
    else:
        prompts = prompt_repository.list_all_prompts(db)

    if search:
        q = search.lower()
        prompts = [
            p for p in prompts
            if q in (p.name or "").lower()
            or q in (p.description or "").lower()
            or q in (p.tags or "").lower()
            or any(q in t.tag.lower() for t in (p.tag_rows or []))
        ]

    total = len(prompts)
    start = (page - 1) * page_size
    return PaginatedResponse.create(
        items=[PromptListItem.model_validate(p) for p in prompts[start: start + page_size]],
        total=total, page=page, page_size=page_size,
    )


@router.post(
    "",
    response_model=PromptDetailRead,
    status_code=201,
    summary="Create a new prompt asset",
    description="Creates metadata; when system/user prompts are supplied, commits and activates v1.",
)
def create_prompt(
    request_body: PromptCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompts.create")),
) -> PromptDetailRead:
    """Create a prompt metadata shell. No version is created here."""
    from promptops_app.database import Prompt
    from promptops_app.repositories import prompt_repository

    if prompt_repository.get_prompt_by_name(db, request_body.name):
        raise DuplicateResourceError("Prompt", request_body.name)

    from promptops_app.database import PromptVersion

    component = request_body.component_type or ""
    prompt = Prompt(
        name=request_body.name,
        description=request_body.description or None,
        owner=current_user.username,
        component_type=component or None,
        variant=request_body.variant or None,
        is_default=False,
        active_version="v1" if request_body.system_prompt and request_body.user_prompt_template else None,
    )
    prompt_repository.set_prompt_tags(db, prompt, request_body.tags or component)
    db.add(prompt)
    db.commit()
    db.refresh(prompt)

    if request_body.system_prompt and request_body.user_prompt_template:
        db.add(PromptVersion(
            prompt_id=prompt.id,
            version="v1",
            version_number=1,
            system_prompt=request_body.system_prompt,
            user_prompt_template=request_body.user_prompt_template,
            change_reason=request_body.change_reason or "Initial commit.",
            is_active=True,
            created_by=current_user.username,
        ))
        db.commit()
        db.refresh(prompt)

    _log.info("prompt_created  user=%s  name=%s", current_user.username, prompt.name)
    return _prompt_detail(db, prompt)


@router.post(
    "/from-template",
    response_model=PromptRead,
    status_code=201,
    summary="Create a prompt from a built-in template",
    description="Instantiates a prompt asset with version v1 from a predefined template in PROMPT_TEMPLATES.",
)
def create_from_template(
    request_body: PromptCreateFromTemplateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompts.create")),
) -> PromptRead:
    """
    Create a prompt from a built-in template.

    Replicates the "⚡ Create from Template" button in the Streamlit Prompt Registry.
    """
    from promptops_app.database import Prompt, PromptVersion
    from promptops_app.prompt_templates import PROMPT_TEMPLATES
    from promptops_app.repositories import prompt_repository

    if request_body.template_name not in PROMPT_TEMPLATES:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("PromptTemplate", request_body.template_name)

    tmpl = PROMPT_TEMPLATES[request_body.template_name]
    asset_id = request_body.template_name.lower().replace(" ", "_").replace("&", "and")

    if prompt_repository.get_prompt_by_name(db, asset_id):
        raise DuplicateResourceError("Prompt", asset_id)

    prompt = Prompt(
        name=asset_id,
        description=f"Auto-created from '{request_body.template_name}' template.",
        owner=current_user.username,
        active_version="v1",
    )
    prompt_repository.set_prompt_tags(db, prompt, tmpl.get("tags", ""))
    db.add(prompt)
    db.commit()
    db.refresh(prompt)

    db.add(PromptVersion(
        prompt_id=prompt.id,
        version="v1",
        version_number=1,
        system_prompt=tmpl["system"],
        user_prompt_template=tmpl["user"],
        change_reason="Created from template.",
        is_active=True,
    ))
    db.commit()

    _log.info("prompt_from_template  user=%s  template=%s  name=%s",
              current_user.username, request_body.template_name, asset_id)
    return PromptRead.model_validate(prompt)


@router.post(
    "/from-generation",
    response_model=PromptDetailRead,
    status_code=201,
    summary="Promote a captured generation override into a prompt asset",
    description="Turns the prompt override recorded in a CDD/Blueprint version's "
                "generation_params into a registry prompt (doc §5.2, opt-in).",
)
def create_from_generation(
    request_body: PromptFromGenerationRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompt.pipeline.edit")),
) -> PromptDetailRead:
    """Phase 12e — the §5.2 loop closer. An override typed in a CAS tab is
    captured on the artifact version (11.9 provenance); this endpoint turns
    that capture into a real registry prompt on explicit request, so ad-hoc
    experiments never auto-pollute the registry. The new prompt is INERT for
    generation until an admin makes it a component default or scope-locks it.
    """
    import json as _json

    from promptops_app.database import Prompt, PromptVersion
    from promptops_app.repositories import blueprint_repository, cdd_repository, prompt_repository
    from promptops_app.services.prompt_library_service import (
        slugify_prompt_name,
        unique_prompt_name,
    )

    source = (request_body.source_type or "").strip().lower()
    if source not in ("cdd", "blueprint"):
        raise ValidationError("source_type must be 'cdd' or 'blueprint'.")
    getter = (cdd_repository.get_cdd_version if source == "cdd"
              else blueprint_repository.get_blueprint_version)
    row = getter(db, request_body.artifact_id, request_body.version)
    if row is None:
        raise NotFoundError(
            "GenerationVersion", f"{source} {request_body.artifact_id} {request_body.version}")
    try:
        params = _json.loads(row.generation_params or "{}")
    except ValueError:
        params = {}
    if params.get("prompt_source") != "override":
        raise ValidationError(
            "This version was not generated with a prompt override — nothing to promote.")
    system_prompt = (params.get("system_prompt_override") or "").strip()
    user_prompt = (params.get("user_prompt_override") or "").strip()
    if not system_prompt or not user_prompt:
        raise ValidationError("The captured override is incomplete (missing system or user prompt).")

    base = slugify_prompt_name(
        request_body.name or f"{source}_override_{request_body.artifact_id}_{row.version}")
    name = unique_prompt_name(db, base)
    prompt = Prompt(
        name=name,
        description=(request_body.description or (
            f"Promoted from {source} #{request_body.artifact_id} version {row.version} "
            "(override captured at generation time)."
        )),
        owner=current_user.username,
        component_type=source,
        is_default=False,
        active_version="v1",
    )
    prompt_repository.set_prompt_tags(db, prompt, source)
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    db.add(PromptVersion(
        prompt_id=prompt.id,
        version="v1",
        version_number=1,
        system_prompt=system_prompt,
        user_prompt_template=user_prompt,
        change_reason=f"Promoted from {source} #{request_body.artifact_id} {row.version} override.",
        is_active=True,
        created_by=current_user.username,
    ))
    db.commit()
    db.refresh(prompt)
    _log.info("prompt_from_generation  user=%s  source=%s#%s/%s  name=%s",
              current_user.username, source, request_body.artifact_id,
              request_body.version, name)
    return _prompt_detail(db, prompt)


@router.post(
    "/generate",
    response_model=PromptRead,
    status_code=201,
    summary="AI-generate a prompt from a plain-language description",
)
def ai_generate_prompt(
    request_body: PromptAIGenerateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompts.create")),
) -> PromptRead:
    """
    Use AI to generate a prompt template from a plain-language description.

    Replicates the "✨ Generate Prompt with AI" expander in the Streamlit Prompt Registry.
    Calls evaluation_service.generate_prompt_template_with_llm().
    """
    from promptops_app.database import Prompt, PromptVersion
    from promptops_app.repositories import prompt_repository
    from promptops_app.services.evaluation_service import generate_prompt_template_with_llm

    tmpl = generate_prompt_template_with_llm(request_body.description)
    if not tmpl or "system_prompt" not in tmpl:
        raise LLMGenerationError("AI prompt generation failed. Please try again with a clearer description.")

    asset_id = tmpl.get("name", "custom_prompt")

    if prompt_repository.get_prompt_by_name(db, asset_id):
        asset_id = f"{asset_id}_{current_user.username[:8]}"

    prompt = Prompt(
        name=asset_id,
        description=tmpl.get("description", request_body.description),
        owner=current_user.username,
        active_version="v1",
    )
    prompt_repository.set_prompt_tags(db, prompt, tmpl.get("tags", "custom"))
    db.add(prompt)
    db.commit()
    db.refresh(prompt)

    db.add(PromptVersion(
        prompt_id=prompt.id,
        version="v1",
        version_number=1,
        system_prompt=tmpl["system_prompt"],
        user_prompt_template=tmpl["user_prompt_template"],
        change_reason="AI-generated from description.",
        is_active=True,
    ))
    db.commit()

    _log.info("prompt_ai_generated  user=%s  name=%s", current_user.username, asset_id)
    return PromptRead.model_validate(prompt)


@router.post(
    "/suggest",
    response_model=PromptAISuggestResponse,
    summary="AI-suggest prompt text (preview only)",
    description="Returns revised system/user prompts without saving to the registry.",
)
def suggest_prompt(
    request_body: PromptAIGenerateRequest,
    current_user=Depends(require_permission("prompts.view")),
) -> PromptAISuggestResponse:
    """Preview AI-improved prompt text — matches Streamlit Improve with AI tab."""
    from promptops_app.services.evaluation_service import generate_prompt_template_with_llm

    tmpl = generate_prompt_template_with_llm(request_body.description)
    if not tmpl or "system_prompt" not in tmpl:
        raise LLMGenerationError("AI prompt generation failed. Please try again with a clearer description.")
    return PromptAISuggestResponse(
        system_prompt=tmpl.get("system_prompt", ""),
        user_prompt_template=tmpl.get("user_prompt_template", ""),
        description=tmpl.get("description"),
    )


# ── Scope locks (prompt fixings) — Phase 8 management endpoints ──────────────
#
# Declared BEFORE the /{prompt_id} routes: FastAPI matches in declaration
# order and "/fixings" would otherwise 422 against the int path param.

_SCOPE_ID_FIELD = {"project": "project_id", "cluster": "cluster_id", "course": "course_id"}
_VALID_SCOPES = ("global", "project", "cluster", "course")


def _scope_ids(scope_level: str, project_id, cluster_id, course_id) -> dict:
    """Validate scope_level + its id and normalize the id tuple.

    Only the id matching the scope level is kept — the repository upserts on
    the exact (component, scope_level, ids) tuple, so stray ids would create
    unreachable duplicate rows.
    """
    if scope_level not in _VALID_SCOPES:
        raise ValidationError(f"scope_level must be one of {', '.join(_VALID_SCOPES)}.")
    given = {"project_id": project_id, "cluster_id": cluster_id, "course_id": course_id}
    ids = {"project_id": None, "cluster_id": None, "course_id": None}
    field = _SCOPE_ID_FIELD.get(scope_level)
    if field:
        if given[field] is None:
            raise ValidationError(f"{field} is required for scope_level='{scope_level}'.")
        ids[field] = given[field]
    return ids


@router.get(
    "/fixings/resolve",
    response_model=PromptFixingRead | None,
    summary="Resolve the effective scope lock for a component",
    description="Returns the most specific PromptFixing (course → cluster → project → global) or null.",
)
def resolve_fixing(
    component: str = Query(...),
    project_id: int | None = Query(default=None),
    cluster_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompts.view")),
) -> PromptFixingRead | None:
    from promptops_app.repositories import prompt_repository

    fixing = prompt_repository.resolve_fixed_prompt(
        db, component,
        project_id=project_id, cluster_id=cluster_id, course_id=course_id,
    )
    return PromptFixingRead.model_validate(fixing) if fixing else None


@router.put(
    "/fixings",
    response_model=PromptFixingRead,
    summary="Bind a pipeline prompt to a scope (set a scope lock)",
    description=(
        "Reuse-by-reference: admins may bind any pipeline prompt; other roles may "
        "bind only prompts whose active version is approved, and only to the "
        "prompt's own component (phase-appropriateness is enforced for everyone)."
    ),
)
def set_fixing(
    request_body: PromptFixingSetRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompts.view")),
) -> PromptFixingRead:
    from promptops_app.repositories import prompt_repository

    target = _get_prompt_or_404(db, request_body.prompt_id)
    if target.prompt_kind != "pipeline":
        raise ValidationError("Only pipeline prompts can be bound to a generation scope.")
    if target.deleted_at is not None:
        raise NotFoundError("Prompt", request_body.prompt_id)
    if target.component_type != request_body.component:
        raise ValidationError(
            f"Prompt {target.id} is a '{target.component_type}' prompt and cannot "
            f"be bound to the '{request_body.component}' phase."
        )

    if not effective_rbac_check(current_user, "prompt.pipeline.edit"):
        active = prompt_repository.get_active_version(db, target.id)
        if active is None or active.workflow_state not in ("approved", "active"):
            # Binding a not-yet-approved prompt requires the pipeline-edit tier.
            raise PermissionDeniedError("prompt.pipeline.edit", current_user.role)

    ids = _scope_ids(request_body.scope_level, request_body.project_id,
                     request_body.cluster_id, request_body.course_id)
    fixing = prompt_repository.set_fixed_prompt(
        db,
        component=request_body.component,
        scope_level=request_body.scope_level,
        prompt_id=target.id,
        fixed_by=current_user.username,
        fixed_by_role=current_user.role,
        **ids,
    )
    _log.info("prompt_fixing_set  user=%s  component=%s  scope=%s  prompt_id=%d",
              current_user.username, request_body.component, request_body.scope_level, target.id)
    return PromptFixingRead.model_validate(fixing)


@router.delete(
    "/fixings",
    status_code=204,
    summary="Remove a scope lock",
    description="Unbind reverts the scope to component-default resolution. Same roles as binding.",
)
def unset_fixing(
    component: str = Query(...),
    scope_level: str = Query(...),
    project_id: int | None = Query(default=None),
    cluster_id: int | None = Query(default=None),
    course_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompts.view")),
) -> None:
    from promptops_app.repositories import prompt_repository

    ids = _scope_ids(scope_level, project_id, cluster_id, course_id)
    removed = prompt_repository.unset_fixed_prompt(
        db, component=component, scope_level=scope_level, **ids,
    )
    if not removed:
        raise NotFoundError("PromptFixing", f"{component}/{scope_level}")
    _log.info("prompt_fixing_unset  user=%s  component=%s  scope=%s",
              current_user.username, component, scope_level)


@router.get(
    "/fixings",
    response_model=list[PromptFixingRead],
    summary="List scope locks",
    description=(
        "All scope locks, optionally filtered by prompt and/or component. "
        "Powers the detail page's 'used by' facets. scope_name carries the "
        "bound course/cluster/project name (null for global locks)."
    ),
)
def list_fixings(
    prompt_id: int | None = Query(default=None),
    component: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompts.view")),
) -> list[PromptFixingRead]:
    from promptops_app.database import Cluster, Course, Project, PromptFixing

    q = db.query(PromptFixing)
    if prompt_id is not None:
        q = q.filter(PromptFixing.prompt_id == prompt_id)
    if component:
        q = q.filter(PromptFixing.component == component)
    rows = q.order_by(PromptFixing.component.asc(), PromptFixing.id.asc()).all()

    def _names(model, ids):
        if not ids:
            return {}
        return dict(db.query(model.id, model.name).filter(model.id.in_(ids)).all())

    course_names = _names(Course, {r.course_id for r in rows if r.course_id})
    cluster_names = _names(Cluster, {r.cluster_id for r in rows if r.cluster_id})
    project_names = _names(Project, {r.project_id for r in rows if r.project_id})
    out = []
    for r in rows:
        item = PromptFixingRead.model_validate(r)
        if r.scope_level == "course":
            item.scope_name = course_names.get(r.course_id)
        elif r.scope_level == "cluster":
            item.scope_name = cluster_names.get(r.cluster_id)
        elif r.scope_level == "project":
            item.scope_name = project_names.get(r.project_id)
        out.append(item)
    return out


# ── Course-grouped view (Phase 11) ───────────────────────────────────────────
#
# One batch call for the "prompts by course" library view: every course with
# the prompt each pipeline slot resolves to, mirroring the live resolution
# order (scope lock course→cluster→project→global → component default →
# shipped file) without N×components /fixings/resolve calls. Declared before
# the /{prompt_id} routes for the same match-order reason as /fixings.

# The requirements-doc categories as (component, variant) slots. The
# (generate, interactive) slot is exact-variant-only at generation time
# (require_variant) — it never falls back to the NULL-variant lesson default.
_COURSE_VIEW_SLOTS: tuple[tuple[str, str | None], ...] = (
    ("style", None),
    ("cdd", None),
    ("blueprint", None),
    ("generate", None),
    ("quiz", None),
    ("generate", "interactive"),
)

# Slots whose fallback tier is a shipped file template. The interactive slot
# has none — unresolved means the bespoke component builder stays in control.
_FILE_BACKED_SLOTS = frozenset(_COURSE_VIEW_SLOTS) - {("generate", "interactive")}


@router.get(
    "/by-course",
    response_model=PromptsByCourseResponse,
    summary="Course-grouped effective prompt sets",
    description=(
        "Every course with the prompt each pipeline component resolves to "
        "(scope locks → component defaults → file fallback). Read-only; "
        "powers the Prompt Library's course-grouped view."
    ),
)
def prompts_by_course(
    project_id: int | None = Query(default=None, description="Limit to one project. Platform admins only — tenant users are always scoped to their own tenant."),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompts.view")),
) -> PromptsByCourseResponse:
    from promptops_app.database import Cluster, Course, Project, Prompt, PromptFixing

    # Tenant isolation: non-platform-admin users only ever see their own
    # tenant's courses, regardless of what project_id (if any) they pass.
    # Platform admins may optionally filter to one project, or see all.
    if not getattr(current_user, "_is_platform_admin", False):
        project_id = getattr(current_user, "_project_id", None)

    course_q = (
        db.query(Course, Cluster.name, Project.name)
        .join(Project, Course.project_id == Project.id)
        .outerjoin(Cluster, Course.cluster_id == Cluster.id)
        .order_by(Project.name.asc(), Course.name.asc())
    )
    if project_id is not None:
        course_q = course_q.filter(Course.project_id == project_id)
    else:
        # No tenant context at all (e.g. platform admin who hasn't chosen a
        # project) and not a platform admin — return nothing rather than
        # leaking every tenant's courses.
        if not getattr(current_user, "_is_platform_admin", False):
            course_q = course_q.filter(False)
    course_rows = course_q.all()

    # Whole tables up front — fixings and defaults are tiny; resolution runs
    # in Python with the exact semantics of resolve_fixed_prompt (own-ID scope
    # match, variant-incompatible fixings fall through to broader scopes).
    fixings = db.query(PromptFixing).filter(PromptFixing.prompt_id.isnot(None)).all()
    fix_map: dict[tuple[str, str, int | None], PromptFixing] = {}
    for f in fixings:
        sid = {"course": f.course_id, "cluster": f.cluster_id,
               "project": f.project_id, "global": None}.get(f.scope_level)
        fix_map.setdefault((f.component, f.scope_level, sid), f)
    bound_ids = {f.prompt_id for f in fixings}
    bound_by_id = (
        {p.id: p for p in db.query(Prompt)
         .filter(Prompt.id.in_(bound_ids), Prompt.deleted_at.is_(None)).all()}
        if bound_ids else {}
    )
    default_map = {
        (p.component_type, p.variant): p
        for p in db.query(Prompt)
        .filter(Prompt.is_default.is_(True), Prompt.prompt_kind == "pipeline",
                Prompt.deleted_at.is_(None))
        .all()
    }

    # Fixed slots plus a dynamic slot for any authored variant default the
    # static list doesn't cover (e.g. a future (blueprint, teacher) row).
    slots = list(_COURSE_VIEW_SLOTS) + sorted(
        (k for k in default_map if k not in set(_COURSE_VIEW_SLOTS)),
        key=lambda k: (k[0], k[1] or ""),
    )

    def resolve_slot(course: Course, component: str, variant: str | None):
        # Mirror the loader's _acceptable_variants exactly, so the display
        # never shows a lock that live generation would skip: a NULL-variant
        # request accepts only NULL-variant rows; the interactive slot is
        # exact-only (require_variant); other variant slots fall back to the
        # NULL-variant row, never sideways.
        if variant is None:
            acceptable = (None,)
        elif (component, variant) == ("generate", "interactive"):
            acceptable = ("interactive",)
        else:
            acceptable = (variant, None)
        for scope, sid in (("course", course.id), ("cluster", course.cluster_id),
                           ("project", course.project_id), ("global", None)):
            if scope != "global" and sid is None:
                continue
            f = fix_map.get((component, scope, sid))
            if f is None:
                continue
            p = bound_by_id.get(f.prompt_id)
            if p is None:
                continue
            if p.prompt_kind != "pipeline" or p.variant not in acceptable:
                continue
            return f"{scope}_lock", scope, p
        p = default_map.get((component, variant))
        if p is not None:
            return "default", None, p
        source = "file" if (component, variant) in _FILE_BACKED_SLOTS else "builtin"
        return source, None, None

    groups = []
    for course, cluster_name, project_name in course_rows:
        slot_items = []
        for component, variant in slots:
            source, scope, p = resolve_slot(course, component, variant)
            slot_items.append(CoursePromptSlot(
                component=component,
                variant=variant,
                source=source,
                scope_level=scope,
                prompt=PromptListItem.model_validate(p) if p is not None else None,
            ))
        groups.append(CoursePromptGroup(
            course_id=course.id,
            course_name=course.name,
            cluster_id=course.cluster_id,
            cluster_name=cluster_name,
            project_id=course.project_id,
            project_name=project_name,
            prompts=slot_items,
        ))
    return PromptsByCourseResponse(courses=groups)


# ── Shared prompt fragments — Phase 9 ────────────────────────────────────────
# Declared before the /{prompt_id} routes for the same match-order reason as
# /fixings ("/fragments" would otherwise 422 against the int path param).

def _fragment_read(fragment, content: str | None) -> PromptFragmentRead:
    return PromptFragmentRead(
        fragment_key=fragment.fragment_key,
        description=fragment.description,
        active_version=fragment.active_version,
        content=content,
        updated_at=fragment.updated_at,
    )


@router.get(
    "/fragments",
    response_model=list[PromptFragmentRead],
    summary="List shared prompt fragments",
    description=(
        "The reusable fragment blocks (persona_tone, style_guide, guardrails, "
        "…) with their active text. Fragments override the hardcoded constants "
        "when component-keyed resolution is enabled."
    ),
)
def list_prompt_fragments(
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompt.pipeline.edit")),
) -> list[PromptFragmentRead]:
    from promptops_app.repositories import fragment_repository

    result = []
    for fragment in fragment_repository.list_fragments(db):
        content = fragment_repository.get_active_fragment_text(
            db, fragment.fragment_key
        )
        result.append(_fragment_read(fragment, content))
    return result


@router.put(
    "/fragments/{fragment_key}",
    response_model=PromptFragmentRead,
    summary="Author or bump a shared prompt fragment",
    description=(
        "Appends the next version of the fragment and activates it (append-only, "
        "like registry prompt versions). The key must be one of the known "
        "fragment slots. Text is {{double}}-brace."
    ),
)
def set_prompt_fragment(
    fragment_key: str,
    request_body: PromptFragmentSetRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompt.pipeline.edit")),
) -> PromptFragmentRead:
    from promptops_app.prompts.brace_conversion import find_single_brace_vars
    from promptops_app.repositories import fragment_repository

    if fragment_key not in fragment_repository.KNOWN_FRAGMENT_KEYS:
        raise ValidationError(
            f"Unknown fragment key '{fragment_key}'. "
            f"Known keys: {', '.join(fragment_repository.KNOWN_FRAGMENT_KEYS)}"
        )
    stray = find_single_brace_vars(request_body.content)
    if stray:
        raise ValidationError(
            "Fragment text uses legacy {single} placeholders "
            f"({', '.join(sorted(set(stray)))}) — use {{{{double}}}} braces."
        )
    version = fragment_repository.set_fragment_text(
        db,
        fragment_key,
        request_body.content,
        created_by=current_user.username,
        change_reason=request_body.change_reason,
        description=request_body.description,
    )
    _log.info(
        "prompt_fragment_set  user=%s  key=%s  version=%s",
        current_user.username, fragment_key, version.version,
    )
    fragment = fragment_repository.get_fragment(db, fragment_key)
    return _fragment_read(fragment, version.content)


@router.get("/{prompt_id}", response_model=PromptDetailRead, summary="Get a prompt with active version")
def get_prompt(prompt_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission("prompts.view"))) -> PromptDetailRead:
    """Return prompt metadata and active version prompt text."""
    return _prompt_detail(db, _get_prompt_or_404(db, prompt_id))


@router.put("/{prompt_id}", response_model=PromptRead, summary="Update prompt metadata")
def update_prompt(
    prompt_id: int,
    request_body: PromptUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompts.manage")),
) -> PromptRead:
    """Update description or tags. Does not affect versions.

    ``component_type``/``variant`` re-key what generation resolves, so they are
    accepted only from ``prompt.pipeline.edit`` holders (admin). Omit/None =
    untouched; empty string = clear to NULL.
    """
    prompt = _get_prompt_or_404(db, prompt_id)
    if request_body.component_type is not None or request_body.variant is not None:
        if not effective_rbac_check(current_user, "prompt.pipeline.edit"):
            raise PermissionDeniedError("prompt.pipeline.edit", user_role=current_user.role)
        if prompt.is_default:
            raise ValidationError(
                "This prompt is a component default — clear the default flag "
                "before re-keying its component_type/variant."
            )
        if request_body.component_type is not None:
            prompt.component_type = request_body.component_type or None
        if request_body.variant is not None:
            prompt.variant = request_body.variant or None
    if request_body.description is not None:
        prompt.description = request_body.description
    if request_body.tags is not None:
        from promptops_app.repositories import prompt_repository

        prompt_repository.set_prompt_tags(db, prompt, request_body.tags)
    db.commit()
    db.refresh(prompt)
    return PromptRead.model_validate(prompt)


@router.delete("/{prompt_id}", status_code=204, summary="Delete a prompt asset")
def delete_prompt(prompt_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission("prompts.manage"))) -> None:
    """Hard-delete a prompt and all its versions. Admin or Lead only."""
    prompt = _get_prompt_or_404(db, prompt_id)
    db.delete(prompt)
    db.commit()
    _log.info("prompt_deleted  user=%s  prompt_id=%d", current_user.username, prompt_id)


@router.get("/{prompt_id}/versions", response_model=list[PromptVersionListItem], summary="List prompt versions")
def list_prompt_versions(prompt_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission("prompts.view"))) -> list[PromptVersionListItem]:
    """List all versions for a prompt."""
    from promptops_app.repositories import prompt_repository
    _get_prompt_or_404(db, prompt_id)
    versions = prompt_repository.list_versions_for_prompt(db, prompt_id)
    return [PromptVersionListItem.model_validate(v) for v in versions]


@router.post(
    "/{prompt_id}/versions",
    response_model=PromptVersionRead,
    status_code=201,
    summary="Commit a new prompt version",
)
def create_prompt_version(
    prompt_id: int,
    request_body: PromptVersionCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompts.manage")),
) -> PromptVersionRead:
    """Commit a new version.

    Approval gate (Phase 8): callers with ``prompt.pipeline.edit`` (admin)
    keep the historical instant-deploy — the new version activates and prior
    versions retire. Anyone else commits a ``draft`` that leaves the deployed
    version untouched; activation then goes through the workflow-state
    transition endpoint.
    """
    from promptops_app.database import PromptVersion
    from promptops_app.repositories import prompt_repository

    prompt = _get_prompt_or_404(db, prompt_id)
    duplicate = db.query(PromptVersion).filter(
        PromptVersion.prompt_id == prompt_id,
        PromptVersion.version == request_body.version,
    ).first()
    if duplicate:
        raise DuplicateResourceError("PromptVersion", request_body.version)

    write = (
        prompt_repository.deploy_new_version
        if effective_rbac_check(current_user, "prompt.pipeline.edit")
        else prompt_repository.commit_draft_version
    )
    version = write(
        db,
        prompt=prompt,
        system_prompt=request_body.system_prompt,
        user_prompt_template=request_body.user_prompt_template,
        version_tag=request_body.version,
        change_reason=request_body.change_reason or "Manual edit.",
        created_by=current_user.username,
    )

    _log.info("prompt_version_committed  user=%s  prompt_id=%d  version=%s  state=%s",
              current_user.username, prompt_id, request_body.version, version.workflow_state)
    return PromptVersionRead.model_validate(version)


@router.post(
    "/{prompt_id}/versions/{version}/deploy",
    response_model=PromptDeployResponse,
    summary="Deploy a version (set it as active)",
)
def deploy_prompt_version(
    prompt_id: int,
    version: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompt.pipeline.edit")),
) -> PromptDeployResponse:
    """
    Set a specific version as the active (deployed) version.

    Replicates the "Deploy" button in the Streamlit Prompt Registry.
    All other versions are deactivated. Activation is pipeline-tier — admin
    only (the approval gate); non-admins route through the workflow-state
    transitions instead.
    """
    from promptops_app.database import Prompt, PromptVersion

    prompt = _get_prompt_or_404(db, prompt_id)
    ver = db.query(PromptVersion).filter(
        PromptVersion.prompt_id == prompt_id,
        PromptVersion.version == version,
    ).first()

    if not ver:
        raise NotFoundError(f"Prompt version '{version}'", prompt_id)

    db.query(PromptVersion).filter(PromptVersion.prompt_id == prompt_id).update({PromptVersion.is_active: False})
    db.query(PromptVersion).filter(
        PromptVersion.prompt_id == prompt_id,
        PromptVersion.workflow_state == "active",
    ).update({PromptVersion.workflow_state: "approved"})
    ver.is_active = True
    ver.workflow_state = "active"
    prompt.active_version = version
    db.commit()

    _log.info("prompt_version_deployed  user=%s  prompt_id=%d  version=%s",
              current_user.username, prompt_id, version)
    return PromptDeployResponse(prompt_id=prompt_id, active_version=version)


# ── Default flag + approval workflow — Phase 8 management endpoints ───────────

@router.put(
    "/{prompt_id}/default",
    response_model=PromptRead,
    summary="Set or clear the default flag for a component",
    description=(
        "Setting demotes the current default for the same (component_type, variant) "
        "so exactly one default exists per pipeline stage."
    ),
)
def set_default_flag(
    prompt_id: int,
    request_body: PromptDefaultRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompt.pipeline.edit")),
) -> PromptRead:
    from promptops_app.database import Prompt

    prompt = _get_prompt_or_404(db, prompt_id)
    if prompt.prompt_kind != "pipeline":
        raise ValidationError("Only pipeline prompts can be a component default.")

    if request_body.is_default:
        if not prompt.component_type:
            raise ValidationError("Prompt has no component_type; set one before making it a default.")
        # Demote the current default for this (component_type, variant) first —
        # the partial unique index allows exactly one.
        db.query(Prompt).filter(
            Prompt.component_type == prompt.component_type,
            Prompt.variant == prompt.variant,
            Prompt.is_default == True,  # noqa: E712
            Prompt.id != prompt.id,
        ).update({Prompt.is_default: False}, synchronize_session="fetch")
        prompt.is_default = True
    else:
        prompt.is_default = False

    db.commit()
    db.refresh(prompt)
    _log.info("prompt_default_%s  user=%s  prompt_id=%d  component=%s  variant=%s",
              "set" if request_body.is_default else "cleared",
              current_user.username, prompt_id, prompt.component_type, prompt.variant)
    return PromptRead.model_validate(prompt)


@router.put(
    "/{prompt_id}/variables",
    response_model=PromptVariablesRead,
    summary="Replace a pipeline prompt's declared variables",
    description=(
        "Replace-all semantics. Declared variables are what strict enforcement "
        "checks at generation time (component resolution only): a call that "
        "fails to supply one raises PROMPT_MISCONFIGURED instead of silently "
        "falling back. Every name must appear as a {{placeholder}} in the "
        "active version's templates — a declaration the template never uses "
        "would only make generations fail."
    ),
)
def set_declared_variables(
    prompt_id: int,
    request_body: PromptVariablesSetRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompt.pipeline.edit")),
) -> PromptVariablesRead:
    from promptops_app.database import PromptVariable
    from promptops_app.prompts.prompt_builder import extract_variables
    from promptops_app.repositories import prompt_repository

    prompt = _get_prompt_or_404(db, prompt_id)
    if prompt.prompt_kind != "pipeline":
        raise ValidationError("Variable declarations apply to pipeline prompts only.")

    names = [v.name for v in request_body.variables]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValidationError(f"Duplicate variable declarations: {', '.join(dupes)}")

    if names:
        active = prompt_repository.get_active_version(db, prompt.id)
        if active is None:
            raise ValidationError(
                "Prompt has no active version; commit one before declaring variables."
            )
        placeholders = set(extract_variables(active.system_prompt or "")) | set(
            extract_variables(active.user_prompt_template or "")
        )
        unknown = sorted(n for n in names if n not in placeholders)
        if unknown:
            raise ValidationError(
                f"Not {{{{placeholders}}}} in the active version ({active.version}): "
                f"{', '.join(unknown)}. Declaring a variable the template never "
                "uses would only make generation calls fail."
            )

    prompt.variables = [
        PromptVariable(name=v.name, label=v.label or "", hint=v.hint or "", sort_order=i)
        for i, v in enumerate(request_body.variables)
    ]
    db.commit()
    db.refresh(prompt)
    _log.info("prompt_variables_set  user=%s  prompt_id=%d  names=%s",
              current_user.username, prompt_id, ",".join(names))
    return PromptVariablesRead(variables=[
        PromptVariableItem(name=v.name, label=v.label or "", hint=v.hint or "")
        for v in sorted(prompt.variables, key=lambda v: (v.sort_order or 0, v.id))
    ])


# Allowed workflow_state transitions (Decision 3: pipeline activation is gated).
_STATE_TRANSITIONS: dict[str, set[str]] = {
    "draft":     {"in_review"},
    "in_review": {"approved", "draft"},
    "approved":  {"active", "draft"},
    "active":    set(),
}


@router.post(
    "/{prompt_id}/versions/{version}/state",
    response_model=PromptVersionRead,
    summary="Transition a version's workflow state",
    description=(
        "draft → in_review → approved → active (with rejection back to draft). "
        "Transitioning to 'active' deploys the version: all other versions are "
        "deactivated and demoted from 'active' to 'approved'."
    ),
)
def transition_workflow_state(
    prompt_id: int,
    version: str,
    request_body: PromptWorkflowStateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompt.pipeline.edit")),
) -> PromptVersionRead:
    from datetime import datetime

    from promptops_app.database import PromptVersion

    prompt = _get_prompt_or_404(db, prompt_id)
    ver = db.query(PromptVersion).filter(
        PromptVersion.prompt_id == prompt_id,
        PromptVersion.version == version,
    ).first()
    if not ver:
        raise NotFoundError(f"Prompt version '{version}'", prompt_id)

    target = request_body.state
    if target not in _STATE_TRANSITIONS:
        raise ValidationError(
            f"Unknown workflow state '{target}'. "
            f"Valid states: {', '.join(_STATE_TRANSITIONS)}."
        )
    current = ver.workflow_state or "active"
    if target not in _STATE_TRANSITIONS[current]:
        raise WorkflowError(
            f"Cannot transition version '{version}' from '{current}' to '{target}'."
        )

    if target == "active":
        others = db.query(PromptVersion).filter(
            PromptVersion.prompt_id == prompt_id,
            PromptVersion.id != ver.id,
        )
        others.update({PromptVersion.is_active: False}, synchronize_session="fetch")
        others.filter(PromptVersion.workflow_state == "active").update(
            {PromptVersion.workflow_state: "approved"}, synchronize_session="fetch"
        )
        ver.is_active = True
        prompt.active_version = ver.version
        prompt.updated_at = datetime.utcnow()

    ver.workflow_state = target
    db.commit()
    db.refresh(ver)
    _log.info("prompt_workflow_state  user=%s  prompt_id=%d  version=%s  %s->%s",
              current_user.username, prompt_id, version, current, target)
    return PromptVersionRead.model_validate(ver)
