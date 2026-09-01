"""Prompt template registry schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _validate_tag_lengths(raw: str | None) -> str | None:
    """Each comma-separated tag must fit the prompt_tags.tag column (100)."""
    if raw:
        too_long = [t.strip() for t in raw.split(",") if len(t.strip()) > 100]
        if too_long:
            raise ValueError(
                f"Each tag must be 100 characters or fewer; got {len(too_long)} longer one(s)."
            )
    return raw


class PromptCreateRequest(BaseModel):
    """Body for POST /api/v1/prompts — create asset; optional v1 when prompts are supplied."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Unique identifier slug, e.g. 'adaptive_tutor_v1'.",
        examples=["quiz_builder_clinical"],
    )
    description: str = Field(default="", max_length=2000)
    tags: str = Field(default="", max_length=500, description="Comma-separated tags.")

    @field_validator("tags")
    @classmethod
    def _tag_lengths(cls, v: str) -> str:
        return _validate_tag_lengths(v)
    component_type: Optional[str] = Field(
        default=None,
        description="Pipeline component: style, cdd, blueprint, generate, or quiz.",
    )
    variant: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Pipeline variant within the component, e.g. teacher/student "
                    "(blueprint) or interactive (generate). NULL = the component's "
                    "base slot.",
    )
    system_prompt: Optional[str] = Field(default=None, description="Initial system prompt (creates v1).")
    user_prompt_template: Optional[str] = Field(default=None, description="Initial user template (creates v1).")
    change_reason: str = Field(default="Initial commit.", max_length=500)
    project_id: Optional[int] = Field(
        default=None,
        description=(
            "Platform admins only — stamp the new prompt as owned by this "
            "project instead of shared/global. Ignored for a tenant caller, "
            "whose own project is always used regardless of this value."
        ),
    )


class PromptCreateFromTemplateRequest(BaseModel):
    """Body for POST /api/v1/prompts/from-template."""

    template_name: str = Field(
        ...,
        description="Key from the PROMPT_TEMPLATES dict in prompt_templates.py.",
    )


class PromptFromGenerationRequest(BaseModel):
    """Body for POST /api/v1/prompts/from-generation — promote the prompt
    override captured in a CDD/Blueprint version's generation_params (11.9
    provenance) into a real registry prompt (doc §5.2, opt-in)."""

    source_type: str = Field(..., description="'cdd' or 'blueprint'.")
    artifact_id: int = Field(..., description="The CDD / Blueprint id the version belongs to.")
    version: str = Field(..., description="Version label the override was captured on, e.g. 'v2'.")
    name: str | None = Field(default=None, max_length=200,
                             description="Registry name; defaults to a slug from the source.")
    description: str = Field(default="", max_length=2000)
    project_id: int | None = Field(
        default=None,
        description=(
            "Platform admins only — stamp the promoted prompt as owned by "
            "this project instead of shared/global. Ignored for a tenant "
            "caller, whose own project is always used regardless of this "
            "value."
        ),
    )


class PromptAIGenerateRequest(BaseModel):
    """Body for POST /api/v1/prompts/generate — AI-generate a prompt from a description."""

    description: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Plain-language description of the prompt's purpose.",
        examples=["I need a prompt that creates interactive coding exercises with hints."],
    )
    model_choice: str = Field(default="GPT-5.4")


class PromptAISuggestResponse(BaseModel):
    """AI suggestion preview — does not persist to the registry."""

    system_prompt: str
    user_prompt_template: str
    description: Optional[str] = None


class PromptUpdateRequest(BaseModel):
    description: Optional[str] = Field(default=None, max_length=2000)
    tags: Optional[str] = Field(default=None, max_length=500)

    @field_validator("tags")
    @classmethod
    def _tag_lengths(cls, v: Optional[str]) -> Optional[str]:
        return _validate_tag_lengths(v)
    # Re-keying fields — change what generation resolves, so the endpoint
    # accepts them only from prompt.pipeline.edit holders (403 otherwise).
    # Omit/None = untouched; empty string = clear to NULL.
    component_type: Optional[str] = Field(default=None, max_length=50)
    variant: Optional[str] = Field(default=None, max_length=50)


class PromptVersionCreateRequest(BaseModel):
    """Body for POST /api/v1/prompts/{id}/versions — commit a new version."""

    version: str = Field(..., min_length=1, max_length=50, examples=["v3"])
    system_prompt: str = Field(..., min_length=1)
    user_prompt_template: str = Field(..., min_length=1)
    change_reason: str = Field(default="", max_length=500)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class PromptVersionRead(BaseModel):
    id: int
    version: str
    is_active: bool
    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None
    change_reason: Optional[str] = None
    created_at: Optional[datetime] = None
    workflow_state: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PromptVersionListItem(BaseModel):
    id: int
    version: str
    is_active: bool
    change_reason: Optional[str] = None
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None
    workflow_state: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class _NameFromTitleMixin:
    @model_validator(mode="before")
    @classmethod
    def _name_falls_back_to_title(cls, data):
        # Carried-over library rows have a title but no registry name (name is
        # pipeline-only) — without this, one such row 500s the whole response.
        if not isinstance(data, dict) and not getattr(data, "name", None):
            title = getattr(data, "title", None)
            if title:
                copied = {f: getattr(data, f, None) for f in cls.model_fields}
                copied["name"] = title
                return copied
        return data


class PromptRead(_NameFromTitleMixin, BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    tags: Optional[str] = None
    owner: Optional[str] = None
    active_version: Optional[str] = None
    component_type: Optional[str] = None
    variant: Optional[str] = None
    is_default: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PromptCapabilityNotice(BaseModel):
    """One thing a requester should know before choosing this prompt."""

    severity: str = Field(description="error | warning | info")
    scope: str = Field(
        default="both",
        description="Which generation path this notice is true of: 'block' (the "
                    "block-wide digest pipeline, which owns the day table), 'single' "
                    "(the legacy single-call path), or 'both'. The prompt picker is "
                    "shared by both Generate buttons, so the UI places each notice "
                    "beside the button it actually describes.")
    message: str = Field(description="Plain sentence, ready to display as-is.")


class PromptCapabilityRead(BaseModel):
    """How this prompt compares with what the platform can actually produce.

    Computed by ``promptops_app.services.prompt_capability`` from the active
    version's text — deterministic, no model call, so it is safe to attach to a read
    endpoint. Present only for the ``cdd``/``blueprint`` component types (the two
    that drive CDD and Blueprint generation); ``null`` elsewhere, which the UI must
    treat as "not applicable" rather than "no problems".
    """

    assessed: bool = Field(description="False when the prompt has no text to assess.")
    has_findings: bool = Field(
        description="Whether anything diverges from what the pipeline emits.")
    would_refuse_single_call: bool = Field(
        description="Whether the single-call path would refuse this prompt outright. The "
                    "block-wide pipeline falls back to that path on a DIS or reduce "
                    "failure, so a true here predicts a refusal after the pipeline ran.")
    has_source_context_slot: bool = Field(
        description="Whether the prompt has an {{extra_instructions_block}} slot. Without "
                    "one, single-call generation runs source-blind.")
    requested_day_columns: int = 0
    matched_columns: int = 0
    #: Names are capped for a response served on every prompt selection; the paired
    #: ``*_total`` says how many there really were, so a capped list is visibly capped
    #: rather than quietly short.
    unmatched_columns: list[str] = Field(default_factory=list)
    unmatched_columns_total: int = 0
    unknown_variables: list[str] = Field(default_factory=list)
    unknown_variables_total: int = 0
    blocking_demands: list[str] = Field(default_factory=list)
    notices: list[PromptCapabilityNotice] = Field(default_factory=list)


class PromptDetailRead(PromptRead):
    """Prompt metadata plus the active version's prompt text."""

    system_prompt: Optional[str] = None
    user_prompt_template: Optional[str] = None
    version_created_by: Optional[str] = None
    version_created_at: Optional[datetime] = None
    version_change_reason: Optional[str] = None
    #: Additive and optional: every existing consumer ignores it, and a computation
    #: failure yields null rather than failing the read.
    capability: Optional[PromptCapabilityRead] = None


class PromptListItem(_NameFromTitleMixin, BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    tags: Optional[str] = None
    owner: Optional[str] = None
    active_version: Optional[str] = None
    component_type: Optional[str] = None
    variant: Optional[str] = None
    is_default: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PromptDeployResponse(BaseModel):
    prompt_id: int
    active_version: str


# ---------------------------------------------------------------------------
# Course-grouped view (Phase 11) — per-course effective prompt sets
# ---------------------------------------------------------------------------

class CoursePromptSlot(BaseModel):
    """One resolution slot for a course: which prompt a component resolves to.

    ``source`` mirrors the real resolution order (scope lock → component
    default → shipped file / bespoke builder):
    course_lock | cluster_lock | project_lock | global_lock | default | file | builtin
    """

    component: str
    variant: Optional[str] = None
    source: str
    scope_level: Optional[str] = None
    prompt: Optional[PromptListItem] = None


class CoursePromptGroup(BaseModel):
    course_id: int
    course_name: str
    cluster_id: Optional[int] = None
    cluster_name: Optional[str] = None
    project_id: int
    project_name: Optional[str] = None
    prompts: list[CoursePromptSlot]


class PromptsByCourseResponse(BaseModel):
    courses: list[CoursePromptGroup]


# ---------------------------------------------------------------------------
# Pipeline management (Phase 8) — default flag, workflow state, scope locks
# ---------------------------------------------------------------------------

class PromptDefaultRequest(BaseModel):
    """Body for PUT /api/v1/prompts/{id}/default."""

    is_default: bool = Field(
        default=True,
        description="True → make this row the default for its (component_type, "
                    "variant), demoting any current default. False → clear the flag.",
    )


class PromptWorkflowStateRequest(BaseModel):
    """Body for POST /api/v1/prompts/{id}/versions/{version}/state."""

    state: str = Field(
        ...,
        description="Target workflow state: draft, in_review, approved, or active. "
                    "Transitioning to 'active' deploys the version.",
        examples=["in_review"],
    )


class PromptFixingSetRequest(BaseModel):
    """Body for PUT /api/v1/prompts/fixings — bind a prompt to a scope."""

    component: str = Field(..., description="Pipeline component: style, cdd, blueprint, or generate.")
    scope_level: str = Field(..., description="global, project, cluster, or course.")
    project_id: Optional[int] = None
    cluster_id: Optional[int] = None
    course_id: Optional[int] = None
    prompt_id: int = Field(..., description="The pipeline prompt to bind (by reference).")


class PromptFixingRead(BaseModel):
    id: int
    component: str
    scope_level: str
    project_id: Optional[int] = None
    cluster_id: Optional[int] = None
    course_id: Optional[int] = None
    prompt_id: Optional[int] = None
    fixed_by: Optional[str] = None
    fixed_by_role: Optional[str] = None
    fixed_at: Optional[datetime] = None
    # Human-readable name of the scoped entity (course/cluster/project name;
    # None for global locks). Populated by list endpoints only — ORM
    # validation leaves it None.
    scope_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PromptFragmentRead(BaseModel):
    """A shared prompt fragment with its active text (Phase 9)."""

    fragment_key: str
    description: Optional[str] = None
    active_version: Optional[str] = None
    content: Optional[str] = None  # active version's text; None if never authored
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PromptFragmentSetRequest(BaseModel):
    """Author/bump a fragment: appends the next version and activates it."""

    content: str = Field(..., min_length=1, max_length=20000)
    change_reason: str = Field(default="", max_length=2000)
    description: Optional[str] = Field(default=None, max_length=2000)


class PromptVariableItem(BaseModel):
    """One declared template variable ({{name}})."""

    name: str = Field(
        ...,
        max_length=100,
        pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$",
        description="Template placeholder identifier (rendered as {{name}}).",
        examples=["course_name"],
    )
    label: str = Field(default="", max_length=200)
    hint: str = Field(default="", max_length=2000)


class PromptVariablesSetRequest(BaseModel):
    """Body for PUT /api/v1/prompts/{id}/variables — replace-all declarations.

    Declaring variables ARMS strict enforcement for the row (when component
    resolution is enabled): a generation call that fails to supply a declared
    variable raises instead of silently falling back. Names must therefore
    appear as {{placeholders}} in the active version's templates.
    """

    variables: list[PromptVariableItem] = Field(default_factory=list, max_length=100)


class PromptVariablesRead(BaseModel):
    variables: list[PromptVariableItem]
