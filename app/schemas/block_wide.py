"""Schemas for the async block-wide digest pipeline (CDD / Block Blueprint).

These back the `POST /cdd/generate-block` and `POST /blueprints/generate-block`
endpoints, which enqueue a background job and return a handle to poll via
`GET /api/v1/jobs/{job_id}` (block-wide generation is long → never synchronous).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class BlockWideGenerateRequest(BaseModel):
    """Submit a block-wide CDD or Block Blueprint for background generation.

    Every field here that the generation form exposes as a control now reaches a
    model: ``prompt_id`` (distilled to guidance), ``style_id``, ``extra_instructions``
    and ``estimated_duration_hours`` (see
    ``promptops_app.services.user_directives``), ``quality_tier`` (model + output
    cap), ``block`` (pipeline scope). Two are metadata only and say so here rather
    than looking live: ``target_audience``/``expert_domain`` are recorded on the
    version row, and ``model_choice`` selects only the guidance-distillation model —
    the generation model comes from ``quality_tier``.

    ``reference_document_ids`` is ADDITIVE here, and only additive: the digest
    pipeline still enumerates every ingested unit in the block, and these ids are
    pinned on top of that set. They are never a filter and never a replacement —
    a block-wide deliverable built from a document subset would silently drop days
    the user can see in the panel, which is the failure this path exists to avoid.
    Empty (the default) is byte-identical to the pre-existing request.
    """

    deliverable: Literal["cdd", "blueprint"] = Field(
        default="cdd", description="Which block-wide deliverable to produce."
    )
    block: str = Field(..., min_length=1, description="Block label, e.g. 'Block 2'.")
    course_id: int
    project_id: int
    course_title: str = Field(default="", max_length=500)
    document_title: Optional[str] = Field(default=None, max_length=500)
    quality_tier: Optional[str] = Field(
        default=None, description="'draft' | 'standard' | 'premium' (default standard)."
    )
    model_choice: str = Field(default="GPT-5.6 Terra")
    extra_instructions: str = Field(default="", max_length=5000)
    cdd_id: Optional[int] = Field(default=None, description="Optional CDD context (blueprint).")
    target_audience: str = Field(default="", max_length=200)
    expert_domain: str = Field(default="", max_length=200)
    estimated_duration_hours: Optional[int] = Field(default=None, ge=1, le=500)
    reference_document_ids: list[str] = Field(
        default_factory=list,
        description="DIS Source Library document/job IDs picked in the 'Reference "
                    "Documents' selector, pinned ON TOP of the block's enumerated "
                    "units — never instead of them. Same field name and same "
                    "meaning as the sync path (app.schemas.cdd), so a form that "
                    "fills one fills the other; the block path differs only in that "
                    "the automatic half is the whole block rather than a purpose "
                    "retrieval. Per-generation only — not stored on the deliverable.",
    )
    style_id: Optional[int] = Field(
        default=None,
        # Rejected at the boundary rather than coerced away later, matching
        # estimated_duration_hours: a 0 or negative id is a caller bug, and a 422 names
        # it where a silent None would look like "no style was selected" and produce a
        # document quietly missing the style the caller thought they asked for.
        # user_directives still re-checks, because the async worker rebuilds the request
        # from job-row JSON and that path is never re-validated.
        ge=1,
        description="The style selected on the generation form. Applied as a "
                    "voice/convention layer to the digest pipeline's REDUCE stage "
                    "(full context) and MAP stage (compact form) — see "
                    "promptops_app.services.user_directives. Only an explicit id is "
                    "honoured; there is no server-side fallback to the course's "
                    "active style, so the applied style is always the one the form "
                    "displayed. Omit for no style.",
    )
    prompt_id: Optional[int] = Field(
        default=None,
        description="Optional specific pipeline-prompt id (the 'Prompt Template' "
                    "dropdown). Omit to use the project/cluster/course-scoped default "
                    "cdd_generation/blueprint_generation prompt — that default is what "
                    "the digest pipeline distills judgment/emphasis guidance from "
                    "(see promptops_app.services.prompt_guidance), so editing it in the "
                    "prompt admin UI already lets an admin steer block-wide generation "
                    "without this field.",
    )


class BlockWideJobResponse(BaseModel):
    """Handle for a queued block-wide job. Poll ``poll_url`` until terminal."""

    job_id: str
    status: str = Field(default="queued", description="queued | running | completed | failed")
    deliverable: str
    block: str
    poll_url: str = Field(description="GET this for status; on completion its entity id is the CDD/Blueprint id.")
