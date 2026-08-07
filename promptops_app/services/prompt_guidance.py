"""Digest the course's selected CDD/Blueprint prompt into short generation
guidance for the block-wide digest pipeline.

The digest pipeline's MAP (per-day extraction, dis_backend/services/digests/
mapper.py) and REDUCE (cross-day narrative fill, block_wide_generator.py) calls
are structured-extraction contracts — they return a fixed JSON schema that the
worksheet renderer reads by exact key name, so the selected prompt can never
be substituted in for them directly (an admin-edited prompt could drop/rename
a key the renderer depends on with no visible error). Instead, this module
distills ONLY the prompt's judgment/quality instructions (tone, emphasis, what
distinguishes one category from another) into a short checklist that gets
APPENDED to the fixed contract by the caller — refining how fields get filled,
never redefining what fields exist.

One LLM call per block-wide generation request (not per day) — cheap relative
to the per-day MAP calls it feeds into. Every failure mode (no prompt
configured, resolution error, digestion call error) degrades to "" so the
pipeline behaves exactly as it did before this feature existed.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_MAX_GUIDANCE_CHARS = 2500
_MAX_PROMPT_CHARS = 6000  # caps what we feed the (single) digestion call itself
_FALLBACK_MODEL = "Claude Haiku 4.5 (Bedrock)"

_DIGEST_SYSTEM = (
    "You read a generation prompt template used to produce instructional "
    "content and extract ONLY the substantive judgment/quality instructions "
    "relevant to content generation - tone, emphasis, what distinguishes one "
    "category from another, priorities, required level of detail. Ignore "
    "JSON/output-format mechanics, {{variable}} placeholders, and generic "
    "boilerplate unrelated to content quality. Respond with a concise "
    "numbered list, at most 12 items, one short sentence each - nothing else. "
    "If the template has no such substantive guidance beyond generic "
    "boilerplate, respond with exactly: NONE"
)

_TEMPLATE_NAME = {"cdd": "cdd_generation", "blueprint": "blueprint_generation"}


def _resolve_prompt_text(db: Any, request_body: Any, deliverable: str) -> str:
    """Best-effort raw text of whatever prompt would drive the legacy
    single-call path for this request: an inline override if the caller
    supplied one, else the scope/prompt_id-resolved registry template
    (placeholders deliberately left unresolved via strict=False - the
    digestion call only needs the instructional prose, not a fully rendered
    template). Returns "" on any failure or when nothing is configured.
    """
    override_sys = getattr(request_body, "system_prompt_override", None)
    override_user = getattr(request_body, "user_prompt_override", None)
    if override_sys and override_user:
        return f"{override_sys}\n\n{override_user}"

    course = None
    try:
        from promptops_app.repositories.course_repository import get_course_by_id
        course_id = getattr(request_body, "course_id", None)
        if course_id:
            course = get_course_by_id(db, course_id)
    except Exception:
        course = None

    try:
        from promptops_app.prompts.prompt_builder import build_prompt
        system, user, _name, _version = build_prompt(
            _TEMPLATE_NAME.get(deliverable, "cdd_generation"), {}, db=db,
            project_id=getattr(request_body, "project_id", None) or (course.project_id if course else None),
            cluster_id=course.cluster_id if course else None,
            course_id=getattr(request_body, "course_id", None),
            prompt_id=getattr(request_body, "prompt_id", None),
            strict=False,
        )
        return f"{system}\n\n{user}"
    except Exception as exc:
        log.debug("prompt_guidance: prompt resolution failed for %s: %s", deliverable, exc)
        return ""


def _digest_prompt(prompt_text: str, model_choice: str) -> str:
    """One LLM call: distill prompt_text's substantive instructions into a
    short checklist. Returns "" on empty/failed/"NONE" output."""
    from promptops_app.services.llm_service import generate_text

    text = prompt_text.strip()[:_MAX_PROMPT_CHARS]
    if not text:
        return ""
    user = f"GENERATION PROMPT TEMPLATE:\n{text}"
    result = generate_text(model_choice, _DIGEST_SYSTEM, user)
    if not result or result.startswith("ERROR"):
        return ""
    result = result.strip()
    if not result or result.upper() == "NONE":
        return ""
    return result[:_MAX_GUIDANCE_CHARS]


def resolve_prompt_guidance(db: Any, request_body: Any, deliverable: str, current_user: Any) -> str:
    """Distill the course's selected CDD/Blueprint prompt into a short
    generation-guidance checklist for the block-wide digest pipeline's
    MAP/REDUCE calls.

    Best-effort and additive only: any failure (no prompt configured,
    resolution error, LLM error) returns "" and the caller's pipeline runs
    exactly as it did before this feature existed. Never raises.
    """
    try:
        prompt_text = _resolve_prompt_text(db, request_body, deliverable)
        if not prompt_text.strip():
            return ""
        model_choice = getattr(request_body, "model_choice", None) or _FALLBACK_MODEL
        return _digest_prompt(prompt_text, model_choice)
    except Exception as exc:
        log.warning("prompt_guidance_resolution_failed deliverable=%s error=%s", deliverable, exc)
        return ""
