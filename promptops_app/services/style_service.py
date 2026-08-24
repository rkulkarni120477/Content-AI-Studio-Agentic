"""Style Intelligence service — generates and refines Style Understanding documents.

Calls go through llm_client. No Streamlit dependency.

Prompt source (load order):
  1. DB — admin-editable via Prompts page (prompt name: "style_understanding")
  2. promptops_app/prompts/templates/style_understanding.md
  3. Inline constant _STYLE_UNDERSTANDING_SYSTEM (backward-compat fallback)
"""

import logging

from promptops_app.database import _build_unified_style_docs
from promptops_app.core.logging import log_duration
from promptops_app.services.llm_service import generate_with_metadata as _llm_meta
from promptops_app.services.usage_service import UsageLogContext

_log = logging.getLogger(__name__)


class _SafeFmtDict(dict):
    """dict subclass returning '' for any missing key (safe .format_map)."""
    def __missing__(self, key: str) -> str:
        return ""


def _safe_format(template: str, **kwargs) -> str:
    """Format *template*, leaving unknown ``{placeholders}`` empty. Never raises."""
    try:
        return (template or "").format_map(_SafeFmtDict(**kwargs))
    except Exception:
        return template or ""

# ── Inline fallback (original prompt — unchanged) ─────────────────────────────
_STYLE_UNDERSTANDING_SYSTEM = """You are analyzing a set of instructional design documents to understand the style, tone, and writing rules they define.

STRICT RULES:
- Read all provided documents fully as a unified whole.
- Use ONLY what is explicitly stated in the documents. Do not use prior knowledge, assumptions, or external context.
- If something is not in the documents, it does not exist.
- Use exact terminology, names, labels, and phrases from the documents. Do not substitute terms.
- All outputs must be fully traceable to the documents.
- Do NOT produce file-by-file summaries. Synthesize everything into ONE unified output.

YOUR OUTPUT MUST FOLLOW THIS EXACT FORMAT — NO DEVIATIONS:

WHAT THIS IS
[State the purpose and problem using exact document terms. Do not generalize.]

WHAT I LEARNED
[Provide a unified synthesis of all documents. Not file-by-file. Use exact framework, model, and principle names from the documents.]

HOW I WILL WORK
[State governing principles. Name frameworks, models, checklists, and standards. Explain how they are applied before, during, and after writing.]

WHAT I WILL NOT DO
[List prohibited actions. Map each to specific rules, standards, or principles using exact document terms.]"""


def _load_system_prompt(db=None) -> tuple[str, str, str]:
    """Return (system_prompt, template_name, template_version).

    Tries the prompt library first; falls back to the inline constant
    so existing behaviour is never broken.
    """
    try:
        from promptops_app.prompts.prompt_loader import load_template
        tmpl = load_template("style_understanding", db=db)
        return tmpl.system_template or _STYLE_UNDERSTANDING_SYSTEM, tmpl.name, tmpl.version
    except Exception:
        return _STYLE_UNDERSTANDING_SYSTEM, "style_understanding", "v1-inline"


def generate_style_understanding(
    db,
    style,
    model_choice: str,
    extra_instructions: str = "",
    system_prompt: str | None = None,
    user_prompt_template: str | None = None,
    audit_capture: dict | None = None,
) -> str:
    """Generate a unified Style Intelligence Layer from linked documents + custom instructions.

    Uses the strict WHAT THIS IS / WHAT I LEARNED / HOW I WILL WORK / WHAT I WILL NOT DO format.
    If *system_prompt* is provided it overrides the DB/file/inline fallback chain.
    If *user_prompt_template* is provided (a prompt selected in the library
    dropdown), its text becomes the instruction body; the style documents and any
    extra instructions are always appended so the analysis stays grounded.
    """
    unified_docs = _build_unified_style_docs(db, style)
    if not unified_docs.strip() and not (extra_instructions or "").strip():
        _log.warning(
            "style_service.generate SKIPPED  style_id=%r  reason=no_documents",
            style.id,
        )
        return (
            "ERROR: No documents or instructions found for this style. "
            "Add documents or custom instructions first."
        )

    if system_prompt:
        _tpl_name, _tpl_ver = "selected_style_prompt", "active"
    else:
        system_prompt, _tpl_name, _tpl_ver = _load_system_prompt(db=db)

    if user_prompt_template:
        # A prompt was selected in the library dropdown — use its user template as
        # the instruction body. Unknown placeholders resolve to empty; the
        # documents and extra instructions are appended below so grounding and
        # existing behaviour are preserved regardless of the template's shape.
        user_p = _safe_format(user_prompt_template).strip()
        if unified_docs.strip():
            user_p += ("\n\n" if user_p else "") + unified_docs
        if extra_instructions:
            user_p += f"\n\n[DIS SOURCE CONTEXT / ADDITIONAL INSTRUCTIONS]\n{extra_instructions}"
    else:
        user_p = (
            "Here are the documents and instructions that define this instructional style. "
            "Read them fully and produce the Style Understanding output.\n\n"
            + (f"{unified_docs}" if unified_docs.strip() else "")
            + (
                f"\n\n[DIS SOURCE CONTEXT / ADDITIONAL INSTRUCTIONS]\n{extra_instructions}"
                if extra_instructions
                else ""
            )
        )

    if audit_capture is not None:
        audit_capture["system_prompt"] = system_prompt
        audit_capture["user_prompt"] = user_p

    with log_duration(
        "style_service.generate_style_understanding",
        extra={"style_id": style.id, "model": model_choice, "prompt_template": _tpl_name},
    ):
        # usage_ctx passed straight into the call — the choke point (llm_client.py's
        # _log_and_trace) logs it now; a separate log_llm_usage() call here would
        # double-count this call's cost, since every attempt logs automatically.
        _result = _llm_meta(model_choice, system_prompt, user_p,
                             UsageLogContext(entity_type="style", entity_id=str(style.id)))

    if _result.is_error:
        _log.error(
            "style_service.generate FAILED  style_id=%r  error_type=%r",
            style.id, _result.error_type,
        )
        return f"ERROR: {_result.text}"

    return _result.text


def regenerate_style_understanding(
    db,
    style,
    model_choice: str,
    correction_instructions: str,
    system_prompt: str | None = None,
    audit_capture: dict | None = None,
) -> str:
    """Refine the Style Intelligence Layer using previous output + user corrections."""
    unified_docs = _build_unified_style_docs(db, style)
    prev_summary = style.generated_summary or "(no previous understanding generated)"

    if not system_prompt:
        system_prompt, _tpl_name, _tpl_ver = _load_system_prompt(db=db)

    user_p = (
        "TASK: Refine the Style Understanding based on correction instructions.\n\n"
        f"PREVIOUS STYLE UNDERSTANDING:\n{prev_summary}\n\n"
        f"CORRECTION INSTRUCTIONS:\n{correction_instructions}\n\n"
        "DOCUMENTS (use for verification and grounding):\n"
        f"{unified_docs}\n\n"
        "Produce an updated Style Understanding in the same WHAT THIS IS / WHAT I LEARNED / "
        "HOW I WILL WORK / WHAT I WILL NOT DO format, fully incorporating the corrections."
    )

    if audit_capture is not None:
        audit_capture["system_prompt"] = system_prompt
        audit_capture["user_prompt"] = user_p

    with log_duration(
        "style_service.regenerate_style_understanding",
        extra={"style_id": style.id, "model": model_choice},
    ):
        _result = _llm_meta(model_choice, system_prompt, user_p,
                             UsageLogContext(entity_type="style", entity_id=str(style.id)))

    if _result.is_error:
        _log.error(
            "style_service.regenerate FAILED  style_id=%r  error_type=%r",
            style.id, _result.error_type,
        )
        return f"ERROR: {_result.text}"

    return _result.text
