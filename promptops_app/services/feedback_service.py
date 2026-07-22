"""Feedback extraction service.

Orchestrates the reviewer-feedback pipeline:
  1. Parse the uploaded file to text (reuses ``parsers.file_parser``).
  2. Build the ``feedback_extraction`` prompt from the Prompt Library.
  3. Call the LLM through ``llm_service.generate_with_metadata`` — using the
     course's configured model (falling back to the catalog default), i.e. the
     same model the rest of the project already uses.
  4. Parse the JSON response defensively and normalise each item.
  5. Persist a ``FeedbackDocument`` + its ``FeedbackItem`` rows (via the
     repository). The caller owns the commit.

All LLM access goes through llm_service — never call the Bedrock/OpenAI client
directly (see llm_service module docstring).
"""

from __future__ import annotations

import io
import logging

from promptops_app.core.llm_client import safe_json_loads
from promptops_app.core.models import DEFAULT_MODEL_NAME
from promptops_app.repositories import feedback_repository
from promptops_app.services.llm_service import generate_with_metadata
from promptops_app.services.usage_service import UsageLogContext

_log = logging.getLogger(__name__)

# Max characters of extracted text sent to the LLM. Guards against pathological
# uploads blowing the context window / cost; feedback docs are far smaller.
_MAX_TEXT_CHARS = 120_000

_VALID_SENTIMENTS = {"suggestion", "concern", "praise", "neutral"}
_VALID_PRIORITIES = {"high", "medium", "low"}

# Inline fallback used only if the Prompt Library template is unavailable.
_FALLBACK_SYSTEM = (
    "You extract distinct, actionable reviewer feedback from a document into a "
    'JSON object {"items": [...]}. Each item has feedback_text, source_location, '
    "theme, sentiment (suggestion|concern|praise|neutral), and priority "
    "(high|medium|low). Return only the JSON object, no prose."
)
_FALLBACK_USER = (
    "Document name: {name}\n\n--- DOCUMENT TEXT START ---\n{text}\n"
    "--- DOCUMENT TEXT END ---\n\nReturn the JSON object only."
)


class FeedbackExtractionError(Exception):
    """Raised when a document cannot be parsed or the LLM extraction fails."""


class _NamedBytesIO(io.BytesIO):
    """BytesIO with a ``.name`` — file_parser keys extension off the name."""

    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


def parse_file_bytes(raw_bytes: bytes, filename: str) -> str:
    """Extract plain text from uploaded file bytes. Raises on unsupported/failed parse."""
    from promptops_app.parsers.file_parser import _parse_uploaded_file

    _name, content, err = _parse_uploaded_file(_NamedBytesIO(raw_bytes, filename))
    if err:
        raise FeedbackExtractionError(f"Could not parse file '{filename}': {err}")
    text = (content or "").strip()
    if not text:
        raise FeedbackExtractionError(
            f"No readable text found in '{filename}'. The file may be empty, "
            "image-only, or an unsupported format."
        )
    return text


def resolve_model_choice(course) -> str:
    """Return the model the project already uses: the course's configured model,
    else the catalog default."""
    configured = getattr(course, "config_model_choice", None) if course else None
    return configured or DEFAULT_MODEL_NAME


def _build_prompts(document_text: str, document_name: str, *, db, project_id, cluster_id, course_id):
    """Render the feedback_extraction prompt; fall back to inline constants."""
    from promptops_app.prompts.prompt_builder import build_prompt

    try:
        system_prompt, user_prompt, _name, _version = build_prompt(
            "feedback_extraction",
            {"document_text": document_text, "document_name": document_name},
            db=db,
            project_id=project_id,
            cluster_id=cluster_id,
            course_id=course_id,
        )
        return system_prompt, user_prompt
    except Exception as exc:  # template misconfig / loader failure — degrade gracefully
        _log.warning("feedback prompt build failed, using inline fallback: %s", exc)
        return _FALLBACK_SYSTEM, _FALLBACK_USER.format(name=document_name, text=document_text)


def _coerce_items(raw) -> list[dict]:
    """Pull the items list out of the (repaired) LLM JSON, tolerating shapes."""
    if isinstance(raw, list):
        candidate = raw
    elif isinstance(raw, dict):
        candidate = raw.get("items") or raw.get("feedback") or raw.get("feedback_items") or []
    else:
        candidate = []
    return candidate if isinstance(candidate, list) else []


def _normalise_item(entry) -> dict | None:
    """Validate + clean one raw item dict. Returns None to drop invalid entries."""
    if not isinstance(entry, dict):
        return None
    text = str(entry.get("feedback_text") or entry.get("text") or "").strip()
    if not text:
        return None

    sentiment = str(entry.get("sentiment") or "").strip().lower()
    if sentiment not in _VALID_SENTIMENTS:
        sentiment = "neutral"

    priority = str(entry.get("priority") or "").strip().lower()
    if priority in ("med", "moderate"):
        priority = "medium"
    if priority not in _VALID_PRIORITIES:
        priority = "medium"

    return {
        "feedback_text": text[:5000],
        "source_location": str(entry.get("source_location") or "").strip()[:500] or None,
        "theme": str(entry.get("theme") or "").strip()[:255] or None,
        "sentiment": sentiment,
        "priority": priority,
    }


def analyze_and_store(
    db,
    *,
    raw_bytes: bytes,
    filename: str,
    project_id: int,
    course,
    created_by: str,
):
    """Full pipeline for one upload. Returns (FeedbackDocument, list[FeedbackItem]).

    Adds rows to the session and flushes; the caller commits. Raises
    ``FeedbackExtractionError`` on parse or LLM failure (so nothing is persisted).
    """
    course_id = getattr(course, "id", None) if course else None
    cluster_id = getattr(course, "cluster_id", None) if course else None
    model_choice = resolve_model_choice(course)

    # 1. Parse to text.
    document_text = parse_file_bytes(raw_bytes, filename)
    if len(document_text) > _MAX_TEXT_CHARS:
        document_text = document_text[:_MAX_TEXT_CHARS]
        _log.info("feedback text truncated to %d chars for '%s'", _MAX_TEXT_CHARS, filename)

    # 2. Build prompt.
    system_prompt, user_prompt = _build_prompts(
        document_text, filename,
        db=db, project_id=project_id, cluster_id=cluster_id, course_id=course_id,
    )

    # 3. LLM call (never raises — returns an LLMResult).
    usage_ctx = UsageLogContext(
        user_name=created_by,
        project_id=project_id,
        course_id=course_id,
        entity_type="feedback",
    )
    llm_result = generate_with_metadata(model_choice, system_prompt, user_prompt, usage_ctx=usage_ctx)
    if llm_result.is_error:
        _log.error("feedback_extraction_llm_failed error_type=%s", llm_result.error_type)
        raise FeedbackExtractionError(
            "AI analysis failed. Please try again or switch models. "
            f"(error: {llm_result.error_type})"
        )

    # 4. Parse + normalise.
    parsed = safe_json_loads(llm_result.text)
    raw_items = _coerce_items(parsed)
    items_data = [it for it in (_normalise_item(e) for e in raw_items) if it]
    _log.info(
        "feedback_extracted file=%s model=%s raw=%d kept=%d",
        filename, model_choice, len(raw_items), len(items_data),
    )

    # 5. Persist document + items (caller commits).
    doc = feedback_repository.create_document(
        db,
        project_id=project_id,
        course_id=course_id,
        filename=filename,
        file_type=(filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else None,
        content=document_text,
        model_used=model_choice,
        created_by=created_by,
    )
    items = feedback_repository.create_items(db, doc, items_data)
    return doc, items
