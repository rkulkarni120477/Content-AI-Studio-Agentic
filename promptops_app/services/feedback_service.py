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
import json
import logging
import re
from datetime import datetime

from promptops_app.core.llm_client import safe_json_loads
from promptops_app.core.models import DEFAULT_MODEL_NAME
from promptops_app.repositories import feedback_repository
from promptops_app.services.llm_service import generate_with_metadata
from promptops_app.services.usage_service import UsageLogContext

_log = logging.getLogger(__name__)

# Max characters of extracted text sent to the LLM. Guards against pathological
# uploads blowing the context window / cost; feedback docs are far smaller.
# Advisory size only — nothing is cut at it (see analyze_and_store). Named
# _LARGE_ rather than _MAX_ because a constant called MAX asserts a bound,
# and this one no longer enforces anything.
_LARGE_TEXT_CHARS = 120_000

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
    blueprint_id: int | None = None,
):
    """Full pipeline for one upload. Returns (FeedbackDocument, list[FeedbackItem]).

    Adds rows to the session and flushes; the caller commits. Raises
    ``FeedbackExtractionError`` on parse or LLM failure (so nothing is persisted).
    ``blueprint_id`` scopes the document and its items to a module (None = course).
    """
    course_id = getattr(course, "id", None) if course else None
    cluster_id = getattr(course, "cluster_id", None) if course else None
    model_choice = resolve_model_choice(course)

    # 1. Parse to text.
    document_text = parse_file_bytes(raw_bytes, filename)
    # Sent whole. This used to cut at _LARGE_TEXT_CHARS and log an INFO line — but
    # the model reads a truncated document as a complete one, so feedback on a
    # long design document silently stopped applying past the cut while the
    # response still read as a review of the whole thing. A log line the reviewer
    # never sees is not a disclosure. If a document genuinely exceeds the model's
    # window the call fails loudly, which is the correct outcome: a partial review
    # presented as a complete one is worse than no review.
    if len(document_text) > _LARGE_TEXT_CHARS:
        _log.info("feedback document is %d chars for %r — sent whole (over the "
                  "%d-char advisory size); expect a larger prompt",
                  len(document_text), filename, _LARGE_TEXT_CHARS)

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
        "feedback_extracted file=%s model=%s raw=%d kept=%d blueprint_id=%s",
        filename, model_choice, len(raw_items), len(items_data), blueprint_id,
    )

    # 5. Persist document + items (caller commits).
    doc = feedback_repository.create_document(
        db,
        project_id=project_id,
        course_id=course_id,
        blueprint_id=blueprint_id,
        filename=filename,
        file_type=(filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else None,
        content=document_text,
        model_used=model_choice,
        created_by=created_by,
    )
    items = feedback_repository.create_items(db, doc, items_data)
    return doc, items


# =============================================================================
# AI recommendations
# =============================================================================
#
# For a feedback item we load the item's course's *generated* content blocks
# from Postgres, pick the blocks most relevant to the feedback (there is no
# vector index over generated content — see the feedback design notes), and ask
# the LLM to recommend a concrete revision. The recommendation and the blocks it
# referenced are stored back on the item.
#
# NOTE (temporary/demo scope): today the only course wired with rich generated
# content for this flow is "Principles of Marketing" (course_id 56). The code
# path itself is course-agnostic — it loads blocks by the item's own course_id —
# so generalising later means only swapping the block-selection step (e.g. for a
# semantic retriever), not this orchestration.

#: Total course-content budget per recommendation. A SELECTION budget — it decides
#: how many whole blocks are sent, never how much of one.
#:
#: Raised from 45,000 (~11k tokens) once blocks stopped being pre-cut to 6,000
#: chars. At the old value a single large block could spend the whole budget on
#: its own and leave every smaller relevant block unselected — the same
#: crowding-out the per-block cut existed to prevent, reappearing as a selection
#: problem instead of a truncation one. 400,000 chars is ~100k tokens, well inside
#: the 200k-token window of every model this product targets, which makes
#: crowding-out unreachable for realistic course content rather than merely rarer.
_REC_CONTEXT_CHARS = 400_000
_REC_MAX_BLOCKS = 12          # cap on number of blocks included as context
# A per-block cut of 6,000 chars used to sit here. It sliced each block before the
# model read it, so a recommendation about a long block was drawn from its opening
# and presented as a recommendation about the block. Domination is a SELECTION
# problem, and _REC_CONTEXT_CHARS above already solves it by selection: a block is
# included whole or not at all, and one huge block simply spends the budget and
# stops the loop.
_REC_MAX_REFS = 8             # cap on stored referenced-block labels

_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "your", "with", "this",
    "that", "from", "have", "has", "was", "were", "would", "could", "should",
    "into", "than", "then", "them", "they", "will", "what", "when", "where",
    "which", "while", "about", "there", "their", "more", "some", "such", "can",
    "does", "did", "too", "very", "also", "just", "only", "how", "why", "who",
}


def _tokens(text: str) -> set[str]:
    """Lowercase content words (len >= 3, non-stopword) for lightweight matching."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) >= 3 and w not in _STOPWORDS}


def _block_label(block, index: int) -> str:
    """A stable, human-readable label the LLM can cite a block by."""
    label = (getattr(block, "block_label", None) or "").strip()
    if label:
        return label
    btype = (getattr(block, "block_type", None) or "block").strip()
    return f"{btype.title()} #{index + 1}"


def _load_course_blocks(db, course_id: int) -> list[tuple[str, str]]:
    """Return [(label, content), ...] for every non-empty generated block of a course."""
    if course_id is None:
        return []
    from promptops_app.repositories.generation_repository import (
        list_blocks_for_gen_ids,
        list_course_generations,
    )

    gens = list_course_generations(db, course_id=course_id, limit=1000)
    gen_ids = [g.id for g in gens]
    if not gen_ids:
        return []
    blocks = list_blocks_for_gen_ids(db, gen_ids, limit=2000)
    out: list[tuple[str, str]] = []
    for i, b in enumerate(blocks):
        content = (getattr(b, "content", None) or "").strip()
        if content:
            out.append((_block_label(b, i), content))
    return out


def _load_module_blocks(db, blueprint_id: int) -> list[tuple[str, str]]:
    """Return [(label, content), ...] for the blocks of ONE module (blueprint).

    Uses the current version of each lesson in the module (latest generation per
    topic), mirroring how the module is assembled for export, so a feedback item
    mapped to a module is grounded only in that module's content.
    """
    if blueprint_id is None:
        return []
    from promptops_app.repositories.generation_repository import (
        list_blocks_for_gen_ids,
        list_latest_generations_for_blueprint,
    )

    gens = list_latest_generations_for_blueprint(db, blueprint_id)
    gen_ids = [g.id for g in gens]
    if not gen_ids:
        return []
    blocks = list_blocks_for_gen_ids(db, gen_ids, limit=2000)
    out: list[tuple[str, str]] = []
    for i, b in enumerate(blocks):
        content = (getattr(b, "content", None) or "").strip()
        if content:
            out.append((_block_label(b, i), content))
    return out


def _load_blocks_for_item(db, course_id, blueprint_id) -> list[tuple[str, str]]:
    """Blocks used to ground a feedback item's recommendation.

    When the item is mapped to a module (``blueprint_id`` set), restrict the
    context to that module's blocks so the recommendation stays relevant to the
    target module. Fall back to the whole course when the item has no module
    mapping, or the module has no usable blocks yet — scoping must never leave the
    model with nothing to work from.
    """
    if blueprint_id is not None:
        module_blocks = _load_module_blocks(db, blueprint_id)
        if module_blocks:
            return module_blocks
    return _load_course_blocks(db, course_id)


def _select_relevant_blocks(
    blocks: list[tuple[str, str]], feedback_item, *, max_chars: int = _REC_CONTEXT_CHARS,
) -> list[tuple[str, str]]:
    """Pick the blocks most relevant to a feedback item, within a char budget.

    Scores each block by token overlap between the feedback (text + theme +
    location) and the block (label + content). Falls back to the first blocks
    when nothing overlaps, so the model always gets *some* course context.
    """
    if not blocks:
        return []

    query_tokens = (
        _tokens(getattr(feedback_item, "feedback_text", ""))
        | _tokens(getattr(feedback_item, "theme", "") or "")
        | _tokens(getattr(feedback_item, "source_location", "") or "")
    )

    scored: list[tuple[int, int, str, str]] = []
    for idx, (label, content) in enumerate(blocks):
        overlap = len(query_tokens & _tokens(f"{label} {content[:2000]}")) if query_tokens else 0
        scored.append((overlap, idx, label, content))

    # Highest overlap first; original order breaks ties (stable, deterministic).
    scored.sort(key=lambda t: (-t[0], t[1]))

    selected: list[tuple[str, str]] = []
    used = 0
    for _overlap, _idx, label, content in scored:
        if len(selected) >= _REC_MAX_BLOCKS:
            break
        # Whole block, or none of it. `and selected` keeps the first regardless,
        # so an oversized top-ranked block still informs the answer rather than
        # leaving the model with nothing.
        #
        # `continue`, not `break`. While every block was pre-cut to 6,000 chars a
        # block rarely overran the budget, so ending the loop at the first one
        # that did was near enough to harmless. Now that blocks are whole, one
        # 40,000-char block early in the ranking would end selection and discard
        # every smaller, still-relevant block behind it. Skip the one that does
        # not fit and keep going — the same include-whole-or-skip rule the
        # document assembler uses.
        cost = len(content) + len(label) + 32
        if used + cost > max_chars and selected:
            continue
        selected.append((label, content))
        used += cost
    return selected


def _format_course_content(selected: list[tuple[str, str]]) -> str:
    """Render selected blocks as a labelled, LLM-friendly context string."""
    if not selected:
        return ""
    parts = [f"### {label}\n{content}" for label, content in selected]
    return "\n\n".join(parts)


_REC_FALLBACK_SYSTEM = (
    "You are an instructional-design editor. Given ONE reviewer feedback item and "
    "labelled excerpts of a course's content, produce a concrete, actionable "
    "recommendation for revising the content. Address only this feedback item. "
    "Write the recommendation as rich Markdown with three labelled sections in "
    "order: a `**Summary**` one-liner starting with an action verb; a "
    "`**What to change**` heading followed by 1-3 distinct `-` bullets naming what "
    "to change and where (no duplicates or contradictions); a `**Why**` heading "
    "followed by 1-2 `-` bullets on how it resolves the feedback; then, when a "
    "concrete rewrite helps, a `> **Suggested revision:**` blockquote. "
    'Return ONLY a JSON object {"recommendation": "string", "referenced_blocks": '
    '["label", ...]} with "\\n" for line breaks. Cite only labels that appear in '
    "the provided content; use an empty array if none apply."
)


def _format_guidance(guidance: str | None) -> str:
    """Turn an optional reviewer instruction into a labelled prompt block (or '')."""
    text = (guidance or "").strip()
    if not text:
        return ""
    return (
        "\nADDITIONAL INSTRUCTIONS FROM THE REVIEWER (prioritise these while still "
        f"following the rules above):\n{text[:2000]}\n"
    )


def _actual_model_display(requested_display: str, result) -> str:
    """Display name of the model that actually produced the text.

    On success/retry the requested model served. On a cross-provider fallback
    (the requested model failed), map the provider model id the service reports
    back to its catalog display name so the stored provenance is truthful.
    """
    if getattr(result, "status", "success") in ("success", "retry_success"):
        return requested_display
    from promptops_app.core.models import MODEL_CATALOG
    for m in MODEL_CATALOG:
        if m.api_model_id == getattr(result, "model", ""):
            return m.display_name
    _log.info(
        "feedback recommendation: fallback model id %r not in catalog; "
        "labelling provenance as requested %r",
        getattr(result, "model", ""), requested_display,
    )
    return requested_display  # best effort — unknown fallback id


def _resolve_requested_model(model_override: str | None, course) -> str:
    """Model for this run: a valid explicit override, else the course/project model.

    An unknown override never errors — it silently falls back (matching the
    'use the project's model by default' rule; the override is opt-in).
    """
    if model_override:
        try:
            from promptops_app.core.models import validate_model
            return validate_model(model_override).display_name
        except ValueError:
            _log.warning("feedback recommendation: unknown model_override %r; using default", model_override)
    return resolve_model_choice(course)


def _build_recommendation_prompt(
    *, feedback_item, course_name: str, course_content: str, guidance: str | None,
    db, project_id, cluster_id, course_id,
):
    """Render the feedback_recommendation prompt; fall back to inline constants."""
    from promptops_app.prompts.prompt_builder import build_prompt

    variables = {
        "feedback_text": getattr(feedback_item, "feedback_text", "") or "",
        "feedback_theme": getattr(feedback_item, "theme", "") or "n/a",
        "feedback_sentiment": getattr(feedback_item, "sentiment", "") or "n/a",
        "feedback_location": getattr(feedback_item, "source_location", "") or "n/a",
        "course_name": course_name or "this course",
        "course_content": course_content or "(no course content available)",
        "extra_instructions": _format_guidance(guidance),
    }
    try:
        system_prompt, user_prompt, _n, _v = build_prompt(
            "feedback_recommendation", variables,
            db=db, project_id=project_id, cluster_id=cluster_id, course_id=course_id,
        )
        return system_prompt, user_prompt
    except Exception as exc:  # template misconfig / loader failure — degrade gracefully
        _log.warning("feedback recommendation prompt build failed, using inline fallback: %s", exc)
        user = (
            f"COURSE: {variables['course_name']}\n\nREVIEWER FEEDBACK\n"
            f"- Feedback: {variables['feedback_text']}\n- Theme: {variables['feedback_theme']}\n"
            f"- Sentiment: {variables['feedback_sentiment']}\n- Location: {variables['feedback_location']}\n\n"
            f"--- COURSE CONTENT START ---\n{variables['course_content']}\n--- COURSE CONTENT END ---\n"
            f"{variables['extra_instructions']}\n"
            "Return the JSON object only."
        )
        return _REC_FALLBACK_SYSTEM, user


def _parse_recommendation(raw_text: str, allowed_labels: list[str]) -> tuple[str, list[str]]:
    """Extract (recommendation, referenced_blocks) from the LLM JSON, defensively.

    referenced_blocks are filtered to labels we actually supplied (case-insensitive),
    so the model cannot invent citations.
    """
    parsed = safe_json_loads(raw_text)

    rec_text = ""
    refs_raw = []
    if isinstance(parsed, dict) and ("recommendation" in parsed or "text" in parsed):
        # A genuine structured response — trust it even when the recommendation
        # field is empty (empty → the caller marks the item 'error'). We must
        # NOT fall back to the raw JSON here, or the literal '{...}' blob would
        # be shown to the user as the recommendation.
        rec_text = str(parsed.get("recommendation") or parsed.get("text") or "").strip()
        maybe_refs = parsed.get("referenced_blocks") or parsed.get("references") or []
        if isinstance(maybe_refs, list):
            refs_raw = maybe_refs
    elif isinstance(parsed, str) and parsed.strip():
        rec_text = parsed.strip()
    else:
        # JSON parsing produced nothing usable (non-dict, or a dict missing the
        # expected key) — salvage the raw model text as a last resort.
        rec_text = (raw_text or "").strip()

    allowed_lc = {lbl.lower(): lbl for lbl in allowed_labels}
    refs: list[str] = []
    for r in refs_raw:
        key = str(r).strip().lower()
        if key in allowed_lc and allowed_lc[key] not in refs:
            refs.append(allowed_lc[key])
        if len(refs) >= _REC_MAX_REFS:
            break

    # The recommendation text is a MODEL RESULT — cutting it at 8000 chars
    # delivered a recommendation that stops mid-sentence and reads as finished.
    return rec_text, refs


def recommend_for_items(
    db, *, items: list, created_by: str,
    guidance: str | None = None, model_override: str | None = None,
) -> list:
    """Generate an AI recommendation for each feedback item (one per item).

    Loads each item's course's generated blocks once (cached per course),
    selects the blocks most relevant to the item, and calls the LLM through the
    shared ``generate_with_metadata`` path. By default it uses the course's
    configured model (falling back to the catalog default) — the same model rule
    as extraction; ``model_override`` lets the caller pick a different catalog
    model for this run, and ``guidance`` is an optional reviewer instruction
    threaded into the prompt.

    Sets ``recommendation`` / ``recommendation_refs`` / ``recommendation_model`` /
    ``recommendation_status`` / ``recommended_at`` / ``recommended_by`` on each
    item. A single item's LLM failure is isolated (that item's status becomes
    ``"error"``) and never aborts the batch. The caller owns the commit.
    """
    from promptops_app.database import Course

    course_cache: dict[int, object] = {}
    # Blocks are cached per (course, module) — items in the same course but
    # different modules must not share a scoped block set.
    blocks_cache: dict[tuple, list[tuple[str, str]]] = {}

    for item in items:
        course_id = getattr(item, "course_id", None)
        blueprint_id = getattr(item, "blueprint_id", None)
        course = None
        blocks: list[tuple[str, str]] = []
        if course_id is not None:
            if course_id not in course_cache:
                course_cache[course_id] = (
                    db.query(Course).filter(Course.id == course_id).first()
                )
            course = course_cache[course_id]
            cache_key = (course_id, blueprint_id)
            if cache_key not in blocks_cache:
                blocks_cache[cache_key] = _load_blocks_for_item(db, course_id, blueprint_id)
            blocks = blocks_cache[cache_key]

        model_choice = _resolve_requested_model(model_override, course)
        selected = _select_relevant_blocks(blocks, item)
        course_content = _format_course_content(selected)
        allowed_labels = [label for label, _c in selected]

        system_prompt, user_prompt = _build_recommendation_prompt(
            feedback_item=item,
            course_name=getattr(course, "name", "") if course else "",
            course_content=course_content,
            guidance=guidance,
            db=db,
            project_id=getattr(item, "project_id", None),
            cluster_id=getattr(course, "cluster_id", None) if course else None,
            course_id=course_id,
        )

        usage_ctx = UsageLogContext(
            user_name=created_by,
            project_id=getattr(item, "project_id", None),
            course_id=course_id,
            entity_type="feedback_recommendation",
        )
        result = generate_with_metadata(model_choice, system_prompt, user_prompt, usage_ctx=usage_ctx)

        if result.is_error:
            _log.error(
                "feedback_recommendation_llm_failed item=%s error_type=%s",
                getattr(item, "id", "?"), result.error_type,
            )
            # Clear any stale recommendation so the row stays consistent
            # (status 'error' must never sit next to old recommendation text —
            # e.g. a failed *regenerate* over a previously-good recommendation).
            item.recommendation = None
            item.recommendation_refs = None
            item.recommendation_status = "error"
            item.recommendation_model = model_choice
            item.recommended_at = datetime.utcnow()
            item.recommended_by = created_by
            continue

        rec_text, refs = _parse_recommendation(result.text, allowed_labels)
        actual_model = _actual_model_display(model_choice, result)
        item.recommendation = rec_text or None
        item.recommendation_refs = json.dumps(refs) if refs else None
        item.recommendation_model = actual_model
        item.recommendation_status = "ready" if rec_text else "error"
        item.recommended_at = datetime.utcnow()
        item.recommended_by = created_by
        _log.info(
            "feedback_recommended item=%s course=%s requested=%s actual=%s status=%s blocks=%d refs=%d",
            getattr(item, "id", "?"), course_id, model_choice, actual_model,
            result.status, len(selected), len(refs),
        )

    return items


_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def compile_feedback_instruction(items: list) -> str:
    """Build a regenerate instruction string from feedback item rows."""
    if not items:
        return ""
    sorted_items = sorted(
        items,
        key=lambda i: (_PRIORITY_ORDER.get((i.priority or "").lower(), 9), i.id or 0),
    )
    lines = ["Reviewer feedback to apply:"]
    for item in sorted_items:
        priority = (item.priority or "medium").upper()
        sentiment = (item.sentiment or "neutral").lower()
        theme = (item.theme or "").strip()
        theme_part = f" {theme}:" if theme else ""
        lines.append(f"[{priority}][{sentiment}]{theme_part} {item.feedback_text}".rstrip())
    return "\n".join(lines)


def resolve_apply_blueprint_id(items: list, override_blueprint_id: int | None) -> int | None:
    """Resolve the target module for Apply.

    Returns a blueprint id when unambiguous from the selection, or when the
    caller provides an override. Returns None when the caller must supply one
    (course-wide or mixed module selection without override).
    """
    if override_blueprint_id is not None:
        return override_blueprint_id
    ids = {getattr(i, "blueprint_id", None) for i in items}
    if len(ids) == 1:
        only = next(iter(ids))
        if only is not None:
            return only
    return None


def apply_feedback_to_module(
    db,
    *,
    items: list,
    blueprint,
    course,
    created_by: str,
) -> tuple[str, list[dict], int]:
    """Compile selected feedback and regenerate latest blocks for a module.

    Returns ``(instruction, regenerated[{block_id, block_label}], skipped)``.
    Caller owns the commit. Skips blocks that fail LLM regenerate without
    aborting the whole batch.
    """
    from promptops_app.core.constants import ChangeSource
    from promptops_app.core.content_utils import build_regeneration_context
    from promptops_app.prompt_templates import IMPROVISE_BLOCK_PROMPT_TEMPLATE, IMPROVISE_BLOCK_SYSTEM
    from promptops_app.repositories import generation_repository
    from promptops_app.repositories.block_repo import save_block_version
    from promptops_app.services.llm_service import generate_with_metadata
    from promptops_app.services.usage_service import UsageLogContext

    instruction = compile_feedback_instruction(items)
    if not instruction.strip():
        return instruction, [], 0

    model_choice = resolve_model_choice(course)
    gens = generation_repository.list_latest_generations_for_blueprint(db, blueprint.id)
    gen_ids = [g.id for g in gens]
    blocks = generation_repository.list_blocks_for_gen_ids(db, gen_ids) if gen_ids else []

    regenerated: list[dict] = []
    skipped = 0
    system_prompt = IMPROVISE_BLOCK_SYSTEM

    for block in blocks:
        gen = getattr(block, "generation", None)
        topic = (getattr(gen, "topic", None) if gen else None) or block.block_label or ""
        # Rebuild the same CDD + Blueprint + Style context the generator used,
        # so feedback-applied content honours the pinned Blueprint.
        context_preamble = build_regeneration_context(db, gen)
        improvise_prompt = IMPROVISE_BLOCK_PROMPT_TEMPLATE.format(
            topic=topic,
            block_type=block.block_type or "lesson",
            improvise_instruction=instruction,
            original_content=block.content or "",
        )
        user_prompt = (
            f"{context_preamble}\n\n{improvise_prompt}" if context_preamble else improvise_prompt
        )
        usage_ctx = UsageLogContext(
            user_name=created_by,
            project_id=getattr(course, "project_id", None),
            course_id=getattr(course, "id", None),
            entity_type="block",
            entity_id=str(block.id),
        )
        llm_result = generate_with_metadata(
            model_choice, system_prompt, user_prompt, usage_ctx=usage_ctx,
        )
        if getattr(llm_result, "status", None) == "error" or getattr(llm_result, "is_error", False):
            _log.warning(
                "feedback_apply_block_failed block_id=%s error=%s",
                block.id, getattr(llm_result, "error_type", None),
            )
            skipped += 1
            continue

        save_block_version(
            db, block, change_source=ChangeSource.REGENERATION, created_by=created_by,
        )
        block.content = llm_result.text
        regenerated.append({
            "block_id": block.id,
            "block_label": block.block_label,
        })

    return instruction, regenerated, skipped
