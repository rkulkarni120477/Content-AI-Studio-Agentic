"""
Domain exception hierarchy for the Content AI Studio API.

Design principles
-----------------
- Every exception the application can raise intentionally inherits from
  ``AppError``.
- The global handler in ``app/main.py`` catches ``AppError`` and converts it
  into a consistent JSON error envelope:
      { "error": { "code": "...", "message": "...", "detail": {...} } }
- Unhandled Python exceptions fall through to FastAPI's default 500 handler.
- Each subclass carries a meaningful ``code`` string (SCREAMING_SNAKE_CASE)
  that the React frontend uses to decide how to display the error.

Usage
-----
    from app.core.exceptions import NotFoundError, WorkflowError

    cdd = cdd_repository.get_by_id(db, cdd_id)
    if not cdd:
        raise NotFoundError("CDD", cdd_id)

    ok, reason = workflow_service.approve_block(db, block, actor)
    if not ok:
        raise WorkflowError(reason)
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """
    Base class for all intentional application errors.

    Every subclass must set a unique ``code`` and appropriate ``status_code``.
    The ``detail`` dict can carry structured data the frontend may need
    (e.g. which field failed validation, which block_id caused a conflict).
    """

    status_code: int = 400
    code: str = "APP_ERROR"

    def __init__(
        self,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class NotFoundError(AppError):
    """
    Raised when a requested resource does not exist in the database.

    Example:
        raise NotFoundError("CDD", 42)
        # → 404 { "code": "NOT_FOUND", "message": "CDD with id 42 not found." }
    """

    status_code = 404
    code = "NOT_FOUND"

    def __init__(self, resource_name: str, resource_id: int | str) -> None:
        super().__init__(
            message=f"{resource_name} with id {resource_id} not found.",
            detail={"resource": resource_name, "id": resource_id},
        )


class AuthenticationError(AppError):
    """
    Raised when a JWT token is missing, expired, or invalid.

    Results in a 401 response, which tells the React app to redirect to login.
    """

    status_code = 401
    code = "AUTHENTICATION_REQUIRED"


class PermissionDeniedError(AppError):
    """
    Raised when the authenticated user lacks the required RBAC permission.

    Carries the permission key so the frontend can show a meaningful message.

    Example:
        raise PermissionDeniedError("workflow.bulk_approve", user_role="author")
    """

    status_code = 403
    code = "PERMISSION_DENIED"

    def __init__(self, permission: str, user_role: str = "") -> None:
        super().__init__(
            message=(
                f"Your role '{user_role}' does not have the '{permission}' permission. "
                "Contact an Admin to request access."
            ),
            detail={"required_permission": permission, "user_role": user_role},
        )


class WorkflowError(AppError):
    """
    Raised when a workflow state transition is not allowed.

    Corresponds to HTTP 409 Conflict — the request is valid but conflicts
    with the current resource state.

    Example:
        raise WorkflowError("Block must be 'In Review' to approve.")
    """

    status_code = 409
    code = "WORKFLOW_VIOLATION"


class ValidationError(AppError):
    """
    Raised when business-rule validation fails (not Pydantic schema validation).

    Use for domain-level rules that Pydantic cannot express, e.g.
    "A CDD must have at least one section before generating a blueprint."
    """

    status_code = 422
    code = "VALIDATION_ERROR"


class LLMGenerationError(AppError):
    """
    Raised when all LLM attempts fail (primary + retry + fallback).

    The message is always safe to show to the user — it never contains
    raw API error details or stack traces.
    """

    status_code = 502
    code = "LLM_GENERATION_FAILED"


class PromptConfigurationError(AppError):
    """
    Raised when the resolved prompt template is misconfigured — its declared
    required variables cannot be supplied by the generation call.

    Deliberately NOT swallowed into the constant-fallback tier: a declaration
    violation must surface to the admin who owns the template, not silently
    change which prompt generation uses.
    """

    status_code = 500
    code = "PROMPT_MISCONFIGURED"


class ExportError(AppError):
    """Raised when a file export operation fails (DOCX, PDF, HTML, etc.)."""

    status_code = 500
    code = "EXPORT_FAILED"


class JobNotFoundError(NotFoundError):
    """Raised when a background job ID does not exist."""

    def __init__(self, job_id: str) -> None:
        super().__init__("GenerationJob", job_id)


class DuplicateResourceError(AppError):
    """
    Raised when a create operation would produce a duplicate (e.g. prompt name collision).

    HTTP 409 Conflict.
    """

    status_code = 409
    code = "DUPLICATE_RESOURCE"

    def __init__(self, resource_name: str, identifier: str) -> None:
        super().__init__(
            message=f"A {resource_name} with identifier '{identifier}' already exists.",
            detail={"resource": resource_name, "identifier": identifier},
        )


class ResourceInUseError(AppError):
    """
    Raised when a destructive operation is refused because other rows depend on
    the target — e.g. permanently deleting a CDD that blueprints were derived
    from, which would cascade those blueprints away with it.

    Distinct from ValidationError (422): the request is well-formed and would be
    valid at a different time, so the caller can act on it (detach the
    dependents, or archive instead). ``detail["blockers"]`` lists what is in the
    way, so the UI can say *why* rather than just refusing.

    HTTP 409 Conflict.
    """

    status_code = 409
    code = "RESOURCE_IN_USE"

    def __init__(
        self,
        message: str,
        *,
        blockers: list[str] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, {**(detail or {}), "blockers": blockers or []})


class RetrievalUnavailableError(AppError):
    """
    Raised when regeneration needed to consult the source library and could not
    reach it — DIS down, S3 unreachable, the search endpoint erroring.

    Deliberately distinct from SourceUnavailableError, which says the material
    genuinely is not there. Collapsing the two would be the worst possible
    outcome for a curriculum document: an infrastructure blip would be reported
    as a hole in the source library, and a designer would act on it.

    Also distinct from returning the section unchanged, which is what happened
    before this existed. A silent no-op is indistinguishable from "there was
    nothing to fix", so the user retries, sees nothing again, and concludes the
    feature is broken rather than that a dependency is down.

    HTTP 503 — transient by nature and worth retrying.
    """

    status_code = 503
    code = "RETRIEVAL_UNAVAILABLE"

    def __init__(
        self,
        message: str,
        *,
        levels_tried: list[str] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, {**(detail or {}), "levels_tried": levels_tried or []})


class SourceUnavailableError(AppError):
    """
    Raised when regeneration was asked to FILL an empty cell and no source for
    it exists anywhere the system can reach — not the worksheet, not the day
    digest, not the raw ingested units, not a search of the library.

    This is the refusal that matters most. Every other failure mode leaves the
    document as it was; this one is the moment a model would happily invent a
    plausible ACS mapping, a page range, or an exam item count, and nothing
    downstream could tell the difference. An empty cell is information — it says
    the source library has a hole — and overwriting it with fluent prose
    destroys that signal permanently.

    ``detail["levels_tried"]`` names what was searched, so the answer is
    actionable: a gap because the digest failed is fixable by rebuilding it, a
    gap because nothing was ever ingested is fixable only by ingesting it.

    HTTP 409 Conflict — the request is well-formed and would succeed once the
    source exists.
    """

    status_code = 409
    code = "SOURCE_UNAVAILABLE"

    def __init__(
        self,
        message: str,
        *,
        levels_tried: list[str] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, {**(detail or {}), "levels_tried": levels_tried or []})


class ProtectedFieldError(AppError):
    """
    Raised when an instruction asks regeneration to rewrite a field it must not
    author — a day number, an ACS code, a source filename, a handbook citation.

    Those values are joins against the calendar and the ACS registry, not
    judgements a language model is entitled to make. Inventing an ACS code is
    the single worst thing this pipeline can do: it is plausible, unverifiable
    by eye, and it propagates into every downstream artifact built from the CDD.

    Refusing beats silently ignoring the request. Protecting the field while
    reporting success would leave the user believing an edit landed when it
    never did — the same class of silent failure as a truncated table.

    HTTP 409 Conflict — well-formed, but not something regeneration may do.
    """

    status_code = 409
    code = "REGENERATION_FIELD_PROTECTED"

    def __init__(
        self,
        message: str,
        *,
        fields: list[str] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, {**(detail or {}), "fields": fields or []})


class RegenerationTooLargeError(AppError):
    """
    Raised when a regeneration is refused because the model could not return the
    content even if it wanted to: the text to be rewritten is larger than the
    chosen model's output ceiling.

    This exists because the failure it replaces was silent. A 20-day CDD day
    table measures ~18,110 tokens against a 16,384-token cap, so the model
    emitted a truncated table, ``patch_item_in_section`` spliced the truncation
    back in, and the browser committed it as a new version — trailing days
    vanished with no error and a success toast.

    Refusing is strictly better than a partial answer here, because a partial
    answer is indistinguishable from a complete one once it has been saved.
    ``detail`` carries the measured size and the ceiling so the UI can say how
    far over the request is rather than just declining it.

    HTTP 409 Conflict — the request is well-formed and would succeed against a
    smaller target or a larger-context model.
    """

    status_code = 409
    code = "REGENERATION_TOO_LARGE"

    def __init__(
        self,
        message: str,
        *,
        tokens: int,
        max_output_tokens: int,
        model_choice: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, {
            **(detail or {}),
            "tokens": tokens,
            "max_output_tokens": max_output_tokens,
            "model_choice": model_choice,
        })
