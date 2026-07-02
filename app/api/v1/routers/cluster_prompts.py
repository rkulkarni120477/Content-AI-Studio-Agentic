"""
Cluster Prompts router — reusable prompt library scoped (optionally) to a Cluster.

Streamlit equivalent: "Cluster Prompt Manager" expander in
``core/shared.py`` ``cluster_selection_page()``.

A ClusterPrompt's ``cluster_id`` is nullable — prompts can exist unassigned
(a reusable library item) before being attached to a cluster. Active prompts
for a cluster are auto-injected into the Style context for every course in
that cluster via ``build_style_context()``.

RBAC:
  Create/Delete/AI-generate → cluster_prompt.create / cluster_prompt.delete (Admin only)
  List                      → All authenticated users
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db, require_permission
from app.core.exceptions import NotFoundError, ValidationError
from app.schemas.cluster_prompt import (
    ClusterPromptAIGenerateRequest,
    ClusterPromptAIGenerateResponse,
    ClusterPromptCreateRequest,
    ClusterPromptRead,
)
from app.schemas.common import MessageResponse, PaginatedResponse

_log = logging.getLogger(__name__)
router = APIRouter()


def _paginate(items: list, page: int, page_size: int) -> PaginatedResponse[ClusterPromptRead]:
    total = len(items)
    start = (page - 1) * page_size
    return PaginatedResponse.create(
        items=[ClusterPromptRead.model_validate(p) for p in items[start: start + page_size]],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/cluster-prompts",
    response_model=PaginatedResponse[ClusterPromptRead],
    summary="List all active cluster prompts",
    description="Returns every active ClusterPrompt across all clusters — used for the 'New Cluster' multiselect.",
)
def list_cluster_prompts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[ClusterPromptRead]:
    from promptops_app.database import ClusterPrompt

    prompts = (
        db.query(ClusterPrompt)
        .filter(ClusterPrompt.is_active == True)  # noqa: E712
        .order_by(ClusterPrompt.created_at.asc())
        .all()
    )
    return _paginate(prompts, page, page_size)


@router.get(
    "/cluster-prompts/unassigned",
    response_model=PaginatedResponse[ClusterPromptRead],
    summary="List unassigned (library) cluster prompts",
    description="Returns active ClusterPrompts with no cluster_id — the reusable prompt library.",
)
def list_unassigned_cluster_prompts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[ClusterPromptRead]:
    from promptops_app.database import ClusterPrompt

    prompts = (
        db.query(ClusterPrompt)
        .filter(ClusterPrompt.cluster_id == None, ClusterPrompt.is_active == True)  # noqa: E711,E712
        .order_by(ClusterPrompt.created_at.asc())
        .all()
    )
    return _paginate(prompts, page, page_size)


@router.get(
    "/clusters/{cluster_id}/cluster-prompts",
    response_model=PaginatedResponse[ClusterPromptRead],
    summary="List prompts assigned to a cluster",
    description="Returns active ClusterPrompts for this cluster, oldest first — the set auto-injected into Style context.",
)
def list_prompts_for_cluster(
    cluster_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> PaginatedResponse[ClusterPromptRead]:
    from promptops_app.database import Cluster, get_cluster_prompts

    cluster = db.query(Cluster).filter(Cluster.id == cluster_id, Cluster.is_active == True).first()  # noqa: E712
    if cluster is None:
        raise NotFoundError("Cluster", cluster_id)

    prompts = get_cluster_prompts(db, cluster_id)
    return _paginate(prompts, page, page_size)


@router.post(
    "/cluster-prompts",
    response_model=ClusterPromptRead,
    status_code=201,
    summary="Create a cluster prompt",
    description="Creates a ClusterPrompt, optionally assigned to a cluster. Leave cluster_id unset to save as unassigned (library).",
)
def create_cluster_prompt_endpoint(
    request_body: ClusterPromptCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cluster_prompt.create")),
) -> ClusterPromptRead:
    from promptops_app.database import Cluster, create_cluster_prompt

    if not (request_body.system_prompt or "").strip() and not (request_body.user_prompt_template or "").strip():
        raise ValidationError("Provide at least a System Prompt or User Prompt Template.")

    if request_body.cluster_id is not None:
        cluster = db.query(Cluster).filter(
            Cluster.id == request_body.cluster_id, Cluster.is_active == True,  # noqa: E712
        ).first()
        if cluster is None:
            raise NotFoundError("Cluster", request_body.cluster_id)

    prompt = create_cluster_prompt(
        db,
        request_body.cluster_id,
        name=request_body.name.strip(),
        description=(request_body.description or "").strip() or None,
        system_prompt=(request_body.system_prompt or "").strip() or None,
        user_prompt_template=(request_body.user_prompt_template or "").strip() or None,
        created_by=current_user.username,
    )

    _log.info("cluster_prompt_created  user=%s  prompt_id=%d  cluster_id=%s  name=%s",
              current_user.username, prompt.id, request_body.cluster_id, prompt.name)
    return ClusterPromptRead.model_validate(prompt)


@router.delete(
    "/cluster-prompts/{prompt_id}",
    response_model=MessageResponse,
    summary="Soft-delete a cluster prompt",
    description="Sets is_active=False. The prompt no longer appears in lists or gets injected into Style context.",
)
def delete_cluster_prompt(
    prompt_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("cluster_prompt.delete")),
) -> MessageResponse:
    from promptops_app.database import ClusterPrompt

    prompt = db.query(ClusterPrompt).filter(
        ClusterPrompt.id == prompt_id, ClusterPrompt.is_active == True,  # noqa: E712
    ).first()
    if prompt is None:
        raise NotFoundError("ClusterPrompt", prompt_id)

    prompt.is_active = False
    db.commit()

    _log.info("cluster_prompt_deleted  user=%s  prompt_id=%d", current_user.username, prompt_id)
    return MessageResponse(message=f"Cluster prompt {prompt_id} deleted.")


@router.post(
    "/cluster-prompts/ai-generate",
    response_model=ClusterPromptAIGenerateResponse,
    summary="Generate or refine a cluster prompt draft with AI",
    description=(
        "Meta-LLM call that drafts a {system_prompt, user_prompt_template} pair from free-text context. "
        "In 'refine' mode it improves the supplied draft instead of writing from scratch. "
        "Does not persist anything — the caller saves via POST /cluster-prompts once happy with the draft."
    ),
)
def ai_generate_cluster_prompt(
    request_body: ClusterPromptAIGenerateRequest,
    current_user=Depends(require_permission("cluster_prompt.create")),
) -> ClusterPromptAIGenerateResponse:
    from promptops_app.core.llm_client import safe_json_loads
    from promptops_app.services.llm_service import generate_text

    if request_body.mode == "refine":
        system_prompt = (
            "You are an expert prompt engineer for eLearning. "
            "Refine the provided system and user prompts based on the instructions. "
            "Respond with ONLY a raw JSON object — no markdown, no code fences, no extra text. "
            "Format: {\"system_prompt\": \"...\", \"user_prompt_template\": \"...\"}"
        )
        user_prompt = (
            f"Instructions: {request_body.context}\n\n"
            f"Current System Prompt:\n{request_body.draft_system_prompt or '(none)'}\n\n"
            f"Current User Prompt:\n{request_body.draft_user_prompt_template or '(none)'}\n\n"
            "Return ONLY the JSON object with refined prompts."
        )
    else:
        system_prompt = (
            "You are an expert prompt engineer for eLearning content generation. "
            "Create a cluster-level system prompt and user prompt template based on the context. "
            "These prompts will be auto-injected into AI generation for all courses in a cluster. "
            "Respond with ONLY a raw JSON object — no markdown, no code fences, no extra text. "
            "Format: {\"system_prompt\": \"...\", \"user_prompt_template\": \"...\"}"
        )
        user_prompt = (
            f"Context / Instructions: {request_body.context}\n\n"
            "Return ONLY the JSON object with system_prompt and user_prompt_template."
        )

    raw = generate_text(request_body.model_choice, system_prompt, user_prompt)
    if raw.startswith("ERROR"):
        raise ValidationError(f"LLM error: {raw}")

    parsed = safe_json_loads(raw)
    if isinstance(parsed, dict) and ("system_prompt" in parsed or "user_prompt_template" in parsed):
        return ClusterPromptAIGenerateResponse(
            system_prompt=parsed.get("system_prompt", "") or "",
            user_prompt_template=parsed.get("user_prompt_template", "") or "",
            fallback_used=False,
        )

    # LLM returned plain text, not JSON — fall back to using it as the system prompt.
    return ClusterPromptAIGenerateResponse(
        system_prompt=raw.strip(),
        user_prompt_template="",
        fallback_used=True,
    )
