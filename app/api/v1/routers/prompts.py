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
from app.core.permissions import rbac_check
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.prompt import (
    PromptAIGenerateRequest,
    PromptAISuggestResponse,
    PromptCreateFromTemplateRequest,
    PromptCreateRequest,
    PromptDefaultRequest,
    PromptDeployResponse,
    PromptDetailRead,
    PromptFixingRead,
    PromptFixingSetRequest,
    PromptListItem,
    PromptRead,
    PromptUpdateRequest,
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
        tags=request_body.tags or component,
        owner=current_user.username,
        component_type=component or None,
        is_default=False,
        active_version="v1" if request_body.system_prompt and request_body.user_prompt_template else None,
    )
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
        tags=tmpl.get("tags", ""),
    )
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
        tags=tmpl.get("tags", "custom"),
    )
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

    if not rbac_check(current_user.role, "prompt.pipeline.edit"):
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
    """Update description or tags. Does not affect versions."""
    prompt = _get_prompt_or_404(db, prompt_id)
    if request_body.description is not None:
        prompt.description = request_body.description
    if request_body.tags is not None:
        prompt.tags = request_body.tags
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
        if rbac_check(current_user.role, "prompt.pipeline.edit")
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
