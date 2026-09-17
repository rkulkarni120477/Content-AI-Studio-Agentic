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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.archive import DocumentReferences
from app.schemas.budget import UsageSummary
from app.schemas.json_fields import parse_optional_json_dict


# ---------------------------------------------------------------------------
# CDD Generation — POST /api/v1/cdd/generate
# ---------------------------------------------------------------------------

class CDDGenerateRequest(BaseModel):
    """
    All parameters required to generate a Course Design Document with AI.

    The LLM uses ``course_title``, ``estimated_duration_hours`` (optional) and
    the active style (``style_id``) to produce a structured CDD.
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
    estimated_duration_hours: Optional[int] = Field(
        default=None,
        ge=1,
        le=500,
        description=(
            "Total course duration in hours. Optional: when omitted the prompt "
            "carries no duration at all, rather than a default the requester "
            "never chose."
        ),
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
    reference_document_ids: list[str] = Field(
        default_factory=list,
        description=(
            "DIS Source Library document/job IDs picked in the 'Reference Documents' "
            "selector. Retrieved as an extra, explicitly-pinned context block on top "
            "of the automatic purpose=cdd retrieval. Per-generation only — not stored "
            "on the CDD."
        ),
    )

    # ── Sidebar config (passed from the React workspace state) ────────────────
    model_choice: str = Field(
        default="GPT-5.6 Terra",
        description="LLM model to use. Must be a valid entry from /api/v1/admin/model-catalog.",
        examples=["GPT-5.6 Terra", "claude-sonnet-4-5"],
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

    # ── Selected prompt template ──────────────────────────────────────────────
    prompt_id: Optional[int] = Field(
        default=None,
        description=(
            "Id of the pipeline prompt selected in the 'Prompt Template' dropdown. "
            "When set (and not a system default), that prompt's active version drives "
            "generation instead of the scope/component-default resolution. Rendered "
            "through the normal variable path, so context injection still applies."
        ),
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

    # ── Digest pipeline (block-wide CDD) ──────────────────────────────────────
    block: Optional[str] = Field(
        default=None,
        description=(
            "Block label (e.g. 'Block 2') for the block-wide digest pipeline. "
            "Only used when the digest pipeline is enabled for the course's client; "
            "when omitted the legacy single-call CDD path runs unchanged."
        ),
        examples=["Block 2"],
    )
    quality_tier: Optional[str] = Field(
        default=None,
        description=(
            "Quality tier for the block-wide REDUCE model: 'draft' | 'standard' | "
            "'premium'. Defaults to 'standard'. Ignored by the legacy path."
        ),
        examples=["standard", "premium"],
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

    @field_validator("sections", "generation_params", mode="before")
    @classmethod
    def _coerce_json_columns(cls, value: object) -> Optional[dict]:
        return parse_optional_json_dict(value)

    @field_validator("is_active", mode="before")
    @classmethod
    def _default_is_active(cls, value: object) -> bool:
        return bool(value) if value is not None else False


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
    workflow_state: str = "draft"
    project_id: Optional[int] = None
    course_id: Optional[int] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Archive state — an archived CDD is still readable (that is how it gets
    # inspected before a restore), so the detail view has to be able to say so.
    is_archived: bool = False
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[str] = None

    # Embedded active version content (avoids a second request)
    active_content: Optional[CDDVersionRead] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("title", mode="before")
    @classmethod
    def _default_title(cls, value: object) -> str:
        return value if value else "Untitled"

    @field_validator("workflow_state", mode="before")
    @classmethod
    def _default_workflow_state(cls, value: object) -> str:
        return value if value else "draft"


class CDDListItem(BaseModel):
    """
    Lightweight CDD summary for paginated list responses.
    Returned by GET /api/v1/cdd.

    ``created_by`` and ``references.version_count`` are here because titles alone
    do not identify a row: a course can hold dozens of CDDs whose titles are
    character-for-character identical, and the picker has to be able to tell
    them apart.
    """

    id: int
    title: str
    course_title: Optional[str] = None
    active_version: Optional[str] = None
    workflow_state: str = "draft"
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None

    # ── Archive state ────────────────────────────────────────────────────────
    is_archived: bool = False
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[str] = None

    # What points at this CDD — populated in one batched pass per page, so the
    # list can show "safe to delete" per row without a query per row.
    references: DocumentReferences = Field(default_factory=DocumentReferences)

    model_config = ConfigDict(from_attributes=True)

    @field_validator("title", mode="before")
    @classmethod
    def _default_title(cls, value: object) -> str:
        return value if value else "Untitled"

    @field_validator("workflow_state", mode="before")
    @classmethod
    def _default_workflow_state(cls, value: object) -> str:
        return value if value else "draft"


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
    source_context_unavailable: Optional[str] = Field(
        default=None,
        description=(
            "Set when the Source Library could not be reached during generation "
            "(the exception class name, e.g. 'RuntimeError') -- None when it "
            "answered, including when it had nothing to add. Degrading to "
            "CDD-and-style grounding is deliberate; this is what makes the "
            "degraded case distinguishable from a fully grounded one after the "
            "fact, same contract the Blueprint route already keeps."
        ),
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


# ---------------------------------------------------------------------------
# Regeneration (AI) — ports the Streamlit CDD "🔄 Regenerate Section" button
# and the per-item "⟳" regeneration into the React UI.
# ---------------------------------------------------------------------------

class CDDRegenerateItemRequest(BaseModel):
    """Body for POST /cdd/{id}/regenerate-item — regenerate one item in a section."""

    section_key: str = Field(description="Section the item belongs to, e.g. 'Course Details'.")
    section_content: str = Field(description="Current markdown content of that section.")
    item_index: int = Field(..., ge=0, description="Index of the item to regenerate.")
    feedback: str = Field(default="", max_length=2000, description="Optional regeneration instruction.")
    model_choice: str = Field(default="GPT-5.6 Terra")
    use_sources: Optional[bool] = Field(
        default=None,
        description=(
            "Whether to search the DIS source library for this item. None (the "
            "default) infers it from the instruction, which is what a client "
            "that does not send the field gets. True forces the lookup, False "
            "skips it — a caller offering the user a checkbox sends the box's "
            "state so the user's explicit choice beats the inference."
        ),
    )


class CDDRegenerateItemResponse(BaseModel):
    """New section content with only the targeted item replaced."""

    updated_content: str
    patched_item: str
    usage_summary: Optional[UsageSummary] = None
    #: False when the model returned the item unchanged. Defaults to True so an
    #: older client keeps its current behaviour; a caller that understands the
    #: field should decline to commit a version identical to the one before it.
    changed: bool = True
    #: Why nothing changed, in terms the user can act on. Set only when
    #: ``changed`` is False.
    note: Optional[str] = None


class CDDRegenerateSectionRequest(BaseModel):
    """Body for POST /cdd/{id}/regenerate-section — regenerate a whole section."""

    section_key: str = Field(description="Section to regenerate, e.g. 'Course Structure'.")
    feedback: str = Field(default="", max_length=2000, description="Optional regeneration instruction.")
    model_choice: str = Field(default="GPT-5.6 Terra")


class CDDDigestRepairResponse(BaseModel):
    """Outcome of retrying the day digests behind a block-wide CDD.

    The one gap regeneration cannot close by itself. When a day's MAP call
    failed — CDD 169 lost two to ``TransportError(504)`` — there is no digest to
    read and no worksheet row derived from one, so every level of context
    escalation finds nothing. The day has to be rebuilt before it can be
    regenerated.

    Cheap to run: ``day_is_cached`` requires ``digest_status == "ok"``, so days
    that already succeeded are reused and only the failures re-run through MAP.
    """

    block: str = Field(description="Block whose digests were rebuilt.")
    built: int = Field(default=0, description="Days rebuilt on this run.")
    cached: int = Field(default=0, description="Days reused from cache, not re-run.")
    failed: int = Field(default=0, description="Days that failed again.")
    map_calls: int = Field(default=0, description="MAP calls made.")
    reasons: list[str] = Field(default_factory=list,
                               description="Failure reasons, when any day failed.")
    repaired: bool = Field(default=False,
                           description="True when this run rebuilt at least one day.")


class CDDRegenerateSectionResponse(BaseModel):
    """Freshly generated content for the section."""

    updated_content: str
    usage_summary: Optional[UsageSummary] = None
    changed: bool = Field(
        default=True,
        description=(
            "False when the model returned the section byte-identical. The "
            "grounded prompt instructs it to leave content alone when the "
            "instruction cannot be satisfied from the context it was given, so "
            "an unchanged answer is a legitimate outcome — but it is "
            "indistinguishable from a broken feature unless it is said out loud. "
            "Callers should skip committing a version and tell the user why "
            "rather than saving a no-op."
        ),
    )
    note: Optional[str] = Field(
        default=None,
        description="Human-readable explanation when `changed` is False.",
    )
