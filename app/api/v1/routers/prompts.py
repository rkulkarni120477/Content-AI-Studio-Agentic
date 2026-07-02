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

from app.core.dependencies import get_current_user, get_db, require_permission, get_tenant_context
from app.core.exceptions import DuplicateResourceError, LLMGenerationError, NotFoundError
from app.core.tenant_context import apply_tenant_filter, effective_tenant_id_for_write
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.prompt import (
    PromptAIGenerateRequest,
    PromptAISuggestResponse,
    PromptCreateFromTemplateRequest,
    PromptCreateRequest,
    PromptDeployResponse,
    PromptDetailRead,
    PromptListItem,
    PromptRead,
    PromptUpdateRequest,
    PromptVersionCreateRequest,
    PromptVersionListItem,
    PromptVersionRead,
)

_log = logging.getLogger(__name__)
router = APIRouter()


def _get_prompt_or_404(db: Session, prompt_id: int, tenant_id=None, is_platform_admin=False):
    """Fetch a prompt by ID or raise HTTP 404, scoped to the caller's tenant."""
    from promptops_app.database import Prompt
    q = apply_tenant_filter(db.query(Prompt), Prompt, tenant_id, is_platform_admin)
    prompt = q.filter(Prompt.id == prompt_id).first()
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
    tenant_ctx=Depends(get_tenant_context),
) -> PaginatedResponse[PromptListItem]:
    """Return all prompt templates, optionally filtered."""
    from promptops_app.repositories import prompt_repository

    tid, is_admin = tenant_ctx
    if component:
        prompts = prompt_repository.list_prompts_by_component(db, component, tenant_id=tid, is_platform_admin=is_admin)
    else:
        prompts = prompt_repository.list_all_prompts(db, tenant_id=tid, is_platform_admin=is_admin)

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
    tenant_ctx=Depends(get_tenant_context),
) -> PromptDetailRead:
    """Create a prompt metadata shell. No version is created here."""
    from promptops_app.database import Prompt
    from promptops_app.repositories import prompt_repository

    tid, is_admin = tenant_ctx
    if prompt_repository.get_prompt_by_name(db, request_body.name, tenant_id=tid, is_platform_admin=is_admin):
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
        tenant_id=effective_tenant_id_for_write(current_user),
    )
    db.add(prompt)
    db.commit()
    db.refresh(prompt)

    if request_body.system_prompt and request_body.user_prompt_template:
        db.add(PromptVersion(
            prompt_id=prompt.id,
            version="v1",
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
    tenant_ctx=Depends(get_tenant_context),
) -> PromptRead:
    """
    Create a prompt from a built-in template.

    Replicates the "⚡ Create from Template" button in the Streamlit Prompt Registry.
    """
    from promptops_app.database import Prompt, PromptVersion
    from promptops_app.prompt_templates import PROMPT_TEMPLATES
    from promptops_app.repositories import prompt_repository

    tid, is_admin = tenant_ctx
    if request_body.template_name not in PROMPT_TEMPLATES:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("PromptTemplate", request_body.template_name)

    tmpl = PROMPT_TEMPLATES[request_body.template_name]
    asset_id = request_body.template_name.lower().replace(" ", "_").replace("&", "and")

    if prompt_repository.get_prompt_by_name(db, asset_id, tenant_id=tid, is_platform_admin=is_admin):
        raise DuplicateResourceError("Prompt", asset_id)

    prompt = Prompt(
        name=asset_id,
        description=f"Auto-created from '{request_body.template_name}' template.",
        owner=current_user.username,
        active_version="v1",
        tags=tmpl.get("tags", ""),
        tenant_id=effective_tenant_id_for_write(current_user),
    )
    db.add(prompt)
    db.commit()
    db.refresh(prompt)

    db.add(PromptVersion(
        prompt_id=prompt.id,
        version="v1",
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
    tenant_ctx=Depends(get_tenant_context),
) -> PromptRead:
    """
    Use AI to generate a prompt template from a plain-language description.

    Replicates the "✨ Generate Prompt with AI" expander in the Streamlit Prompt Registry.
    Calls evaluation_service.generate_prompt_template_with_llm().
    """
    from promptops_app.database import Prompt, PromptVersion
    from promptops_app.repositories import prompt_repository
    from promptops_app.services.evaluation_service import generate_prompt_template_with_llm

    tid, is_admin = tenant_ctx
    tmpl = generate_prompt_template_with_llm(request_body.description)
    if not tmpl or "system_prompt" not in tmpl:
        raise LLMGenerationError("AI prompt generation failed. Please try again with a clearer description.")

    asset_id = tmpl.get("name", "custom_prompt")

    if prompt_repository.get_prompt_by_name(db, asset_id, tenant_id=tid, is_platform_admin=is_admin):
        asset_id = f"{asset_id}_{current_user.username[:8]}"

    prompt = Prompt(
        name=asset_id,
        description=tmpl.get("description", request_body.description),
        owner=current_user.username,
        active_version="v1",
        tags=tmpl.get("tags", "custom"),
        tenant_id=effective_tenant_id_for_write(current_user),
    )
    db.add(prompt)
    db.commit()
    db.refresh(prompt)

    db.add(PromptVersion(
        prompt_id=prompt.id,
        version="v1",
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


@router.get("/{prompt_id}", response_model=PromptDetailRead, summary="Get a prompt with active version")
def get_prompt(prompt_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission("prompts.view")), tenant_ctx=Depends(get_tenant_context)) -> PromptDetailRead:
    """Return prompt metadata and active version prompt text."""
    tid, is_admin = tenant_ctx
    return _prompt_detail(db, _get_prompt_or_404(db, prompt_id, tid, is_admin))


@router.put("/{prompt_id}", response_model=PromptRead, summary="Update prompt metadata")
def update_prompt(
    prompt_id: int,
    request_body: PromptUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("prompts.manage")),
    tenant_ctx=Depends(get_tenant_context),
) -> PromptRead:
    """Update description or tags. Does not affect versions."""
    tid, is_admin = tenant_ctx
    prompt = _get_prompt_or_404(db, prompt_id, tid, is_admin)
    if request_body.description is not None:
        prompt.description = request_body.description
    if request_body.tags is not None:
        prompt.tags = request_body.tags
    db.commit()
    db.refresh(prompt)
    return PromptRead.model_validate(prompt)


@router.delete("/{prompt_id}", status_code=204, summary="Delete a prompt asset")
def delete_prompt(prompt_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission("prompts.manage")), tenant_ctx=Depends(get_tenant_context)) -> None:
    """Hard-delete a prompt and all its versions. Admin or Lead only."""
    tid, is_admin = tenant_ctx
    prompt = _get_prompt_or_404(db, prompt_id, tid, is_admin)
    db.delete(prompt)
    db.commit()
    _log.info("prompt_deleted  user=%s  prompt_id=%d", current_user.username, prompt_id)


@router.get("/{prompt_id}/versions", response_model=list[PromptVersionListItem], summary="List prompt versions")
def list_prompt_versions(prompt_id: int, db: Session = Depends(get_db), current_user=Depends(require_permission("prompts.view")), tenant_ctx=Depends(get_tenant_context)) -> list[PromptVersionListItem]:
    """List all versions for a prompt."""
    from promptops_app.repositories import prompt_repository
    tid, is_admin = tenant_ctx
    _get_prompt_or_404(db, prompt_id, tid, is_admin)
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
    tenant_ctx=Depends(get_tenant_context),
) -> PromptVersionRead:
    """Deploy a new active version (deactivates prior versions)."""
    from promptops_app.repositories import prompt_repository

    tid, is_admin = tenant_ctx
    prompt = _get_prompt_or_404(db, prompt_id, tid, is_admin)
    version = prompt_repository.deploy_new_version(
        db,
        prompt=prompt,
        system_prompt=request_body.system_prompt,
        user_prompt_template=request_body.user_prompt_template,
        version_tag=request_body.version,
        change_reason=request_body.change_reason or "Manual edit.",
        created_by=current_user.username,
    )

    _log.info("prompt_version_committed  user=%s  prompt_id=%d  version=%s",
              current_user.username, prompt_id, request_body.version)
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
    current_user=Depends(require_permission("prompts.manage")),
    tenant_ctx=Depends(get_tenant_context),
) -> PromptDeployResponse:
    """
    Set a specific version as the active (deployed) version.

    Replicates the "Deploy" button in the Streamlit Prompt Registry.
    All other versions are deactivated.
    """
    from promptops_app.database import Prompt, PromptVersion

    tid, is_admin = tenant_ctx
    prompt = _get_prompt_or_404(db, prompt_id, tid, is_admin)
    ver = db.query(PromptVersion).filter(
        PromptVersion.prompt_id == prompt_id,
        PromptVersion.version == version,
    ).first()

    if not ver:
        raise NotFoundError(f"Prompt version '{version}'", prompt_id)

    db.query(PromptVersion).filter(PromptVersion.prompt_id == prompt_id).update({PromptVersion.is_active: False})
    ver.is_active = True
    prompt.active_version = version
    db.commit()

    _log.info("prompt_version_deployed  user=%s  prompt_id=%d  version=%s",
              current_user.username, prompt_id, version)
    return PromptDeployResponse(prompt_id=prompt_id, active_version=version)
