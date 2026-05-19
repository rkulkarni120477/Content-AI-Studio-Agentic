"""
CDD (Course Design Document) request and response schemas.

These Pydantic models define the exact shape of data flowing into and out of
the CDD endpoints.  Every field is typed and described so the auto-generated
OpenAPI docs at /docs are self-explanatory for frontend developers.

Naming convention used throughout all schema files
---------------------------------------------------
  <Resource>CreateRequest  → body for POST (create new resource)
  <Resource>UpdateRequest  → body for PUT/PATCH (update existing resource)
  <Resource>Read           → response for GET (single item)
  <Resource>ListItem       → lightweight version used inside paginated lists
  <Resource>GenerateRequest → body for AI generation endpoints
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# CDD Generation — POST /api/v1/cdd/generate
# ---------------------------------------------------------------------------

class CDDGenerateRequest(BaseModel):
    """
    All parameters required to generate a Course Design Document with AI.

    The LLM uses ``course_title``, ``estimated_duration_hours``, and the
    active style (``style_id``) to produce a structured CDD.
    ``extra_instructions`` lets the user guide the AI for this specific generation.

    ``system_prompt_override`` and ``user_prompt_override`` are advanced options
    that allow the user to customise the prompt directly from the UI (matching
    the inline prompt controls in the Streamlit version).
    """

    # ── Scope ─────────────────────────────────────────────────────────────────
    course_id: int = Field(description="ID of the course this CDD belongs to.")
    project_id: int = Field(description="ID of the parent project.")

    # ── Course metadata (drives LLM generation) ───────────────────────────────
    course_title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="The course title used as the primary input for AI generation.",
        examples=["Foundations of Clinical Nursing"],
    )
    document_title: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Optional label for this CDD document. Defaults to '<course_title> — CDD'.",
        examples=["Nursing Foundations CDD v1"],
    )
    estimated_duration_hours: int = Field(
        ...,
        ge=1,
        le=500,
        description="Total course duration in hours.",
        examples=[8],
    )
    extra_instructions: str = Field(
        default="",
        max_length=5_000,
        description=(
            "Additional guidance for the AI. "
            "E.g. 'Focus on clinical simulation. Include DEI examples.'"
        ),
    )

    # ── Style and context ─────────────────────────────────────────────────────
    style_id: Optional[int] = Field(
        default=None,
        description=(
            "ID of the Style to inject into generation. "
            "If None, the CDD is generated without style constraints."
        ),
    )

    # ── Sidebar config (passed from the React workspace state) ────────────────
    model_choice: str = Field(
        default="GPT-5.4",
        description="LLM model to use. Must be a valid entry from /api/v1/admin/model-catalog.",
        examples=["GPT-5.4", "claude-sonnet-4-5"],
    )
    target_audience: str = Field(
        default="",
        max_length=200,
        description="Audience description. Injected into the prompt.",
        examples=["Nursing Students Year 2"],
    )
    expert_domain: str = Field(
        default="",
        max_length=200,
        description="Subject matter domain. Injected into the prompt.",
        examples=["Clinical Nursing"],
    )
    audience_category: str = Field(
        default="Professional/Corporate",
        description="Broad audience category used for tone calibration.",
        examples=["Professional/Corporate", "Academic/Higher Ed", "K-12"],
    )

    # ── Advanced prompt overrides ─────────────────────────────────────────────
    system_prompt_override: Optional[str] = Field(
        default=None,
        description=(
            "If provided, replaces the default system prompt for this generation only. "
            "Used when the user edits the prompt directly in the inline prompt panel."
        ),
    )
    user_prompt_override: Optional[str] = Field(
        default=None,
        description=(
            "If provided, replaces the default user prompt template. "
            "The override should already have all variables interpolated."
        ),
    )


# ---------------------------------------------------------------------------
# CDD Version — POST /api/v1/cdd/{cdd_id}/versions
# ---------------------------------------------------------------------------

class CDDVersionCreateRequest(BaseModel):
    """Body for committing a manually edited CDD as a new version."""

    version_tag: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Version label, e.g. 'v3' or '2026-05-18-draft'.",
        examples=["v3"],
    )
    full_content: str = Field(
        ...,
        min_length=1,
        description="The complete edited CDD content in Markdown.",
    )
    sections: dict = Field(
        default_factory=dict,
        description="Parsed section dict. Keys are section names, values are Markdown content.",
    )
    change_reason: str = Field(
        default="",
        max_length=500,
        description="Brief description of what changed in this version.",
        examples=["Updated module count to 6", "Added clinical simulation section"],
    )


# ---------------------------------------------------------------------------
# CDD Pin — POST /api/v1/cdd/{cdd_id}/pin
# ---------------------------------------------------------------------------

class CDDPinRequest(BaseModel):
    """Pin a CDD as the active CDD for generation on a specific course."""

    course_id: int = Field(description="ID of the course to pin this CDD to.")


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class CDDVersionRead(BaseModel):
    """Full version details, returned by GET /api/v1/cdd/{id}/versions/{version}."""

    version: str
    is_active: bool
    full_content: Optional[str] = None
    sections: Optional[dict] = None
    generation_params: Optional[dict] = None
    change_reason: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CDDVersionListItem(BaseModel):
    """Lightweight version summary used in the version list endpoint."""

    version: str
    is_active: bool
    change_reason: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CDDRead(BaseModel):
    """
    Full CDD response, including the active version content.
    Returned by GET /api/v1/cdd/{cdd_id}.
    """

    id: int
    title: str
    course_title: Optional[str] = None
    description: Optional[str] = None
    active_version: Optional[str] = None
    workflow_state: str
    project_id: Optional[int] = None
    course_id: Optional[int] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Embedded active version content (avoids a second request)
    active_content: Optional[CDDVersionRead] = None

    model_config = ConfigDict(from_attributes=True)


class CDDListItem(BaseModel):
    """
    Lightweight CDD summary for paginated list responses.
    Returned by GET /api/v1/cdd.
    """

    id: int
    title: str
    course_title: Optional[str] = None
    active_version: Optional[str] = None
    workflow_state: str
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CDDGenerateResponse(BaseModel):
    """
    Returned immediately after a successful CDD generation.

    Includes enough information for the React frontend to display the result
    without a follow-up GET request.
    """

    cdd_id: int = Field(description="ID of the newly created CDD.")
    title: str = Field(description="Title of the CDD document.")
    version: str = Field(description="Version label of the first version (always 'v1').")
    sections_count: int = Field(description="Number of sections parsed from the AI output.")
    full_content: str = Field(description="Complete AI-generated CDD content in Markdown.")
    sections: dict = Field(description="Parsed sections dict for structured display.")
    model_used: str = Field(description="Actual LLM model that generated the content.")
    tokens_used: Optional[int] = Field(
        default=None,
        description="Total token count (prompt + completion). None if not reported by the provider.",
    )
    auto_pinned: bool = Field(
        default=True,
        description="True if this CDD was automatically set as active for the course.",
    )


class CDDActivateVersionResponse(BaseModel):
    """Returned after activating a specific CDD version."""

    cdd_id: int
    active_version: str


class CDDPinResponse(BaseModel):
    """Returned after pinning a CDD to a course."""

    cdd_id: int
    course_id: int
    pinned: bool = True
