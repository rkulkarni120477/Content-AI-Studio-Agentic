"""Schemas for the async block-wide digest pipeline (CDD / Block Blueprint).

These back the `POST /cdd/generate-block` and `POST /blueprints/generate-block`
endpoints, which enqueue a background job and return a handle to poll via
`GET /api/v1/jobs/{job_id}` (block-wide generation is long → never synchronous).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class BlockWideGenerateRequest(BaseModel):
    """Submit a block-wide CDD or Block Blueprint for background generation."""

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
    model_choice: str = Field(default="GPT-5.4")
    extra_instructions: str = Field(default="", max_length=5000)
    cdd_id: Optional[int] = Field(default=None, description="Optional CDD context (blueprint).")
    target_audience: str = Field(default="", max_length=200)
    expert_domain: str = Field(default="", max_length=200)
    estimated_duration_hours: Optional[int] = Field(default=None, ge=1, le=500)
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
