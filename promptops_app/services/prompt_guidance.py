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

Fidelity
--------
The distillation is the ONE place where an admin's DB-maintained prompt reaches
the pipeline, so losing input here loses the feature. Two rules follow:

* **Never silently truncate.** A prompt longer than the input cap is processed as
  overlapping WINDOWS (bounded by ``prompt_guidance_max_windows``), each distilled
  and merged, instead of having its tail dropped. If even the windows can't cover
  it, that is logged at WARNING with the exact character counts — never swallowed.
* **Caps are configuration, not constants.** See ``AppSettings.prompt_guidance_*``.
  The historical 6000/2500/12 values silently discarded ~60% of AIM's own ~15.7k
  char Block 2 CDD template.

Cost
----
One distillation call per block-wide generation request (not per day), memoized in
process by a content hash of the prompt text — so repeat generations of the same
block reuse it, and an edited prompt misses the memo naturally (no invalidation
bookkeeping). The resulting guidance text is itself folded into the per-day digest
cache key by the MAP side, so editing the prompt correctly rebuilds digests.

Every failure mode (no prompt configured, resolution error, digestion call error)
degrades to "" so the pipeline behaves exactly as it did before this feature
existed. This module never raises.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from typing import Any

log = logging.getLogger(__name__)

# Overlap between windows when a prompt exceeds the single-call input cap, so an
# instruction straddling a window boundary is seen whole by at least one window.
_WINDOW_OVERLAP_CHARS = 500

# Sonnet 5, not Haiku, even though Haiku is now invokable and cheaper: the system
# prompt below was calibrated against AIM's real Block 2 template and a weaker model
# returned exactly "NONE" for it (see the calibration note). One call per generation,
# so the cost difference is small and the fidelity matters more.
_FALLBACK_MODEL = "Claude Sonnet 5 (Bedrock)"

# Calibration note — this wording was tuned against AIM's real ~15.5k-char Block 2
# CDD template, and the earlier version FAILED on it: told to "ignore output-format
# mechanics ... and generic boilerplate", the distiller classified the template's
# governance rules, review-flag vocabulary, and per-worksheet cell criteria as
# format mechanics and returned exactly "NONE" — silently yielding zero guidance
# for a prompt dense with real judgment criteria. The fix has three parts, all
# load-bearing: enumerate the categories to extract (rather than naming one
# category and a broad exclusion), state explicitly that a rule is not boilerplate
# merely because it is phrased as a rule or concerns document structure, and make
# NONE a narrow escape hatch instead of an easy default. Same prompt now yields 24
# grounded items. Re-verify against a real course prompt before editing.
_DIGEST_SYSTEM_TEMPLATE = (
    "You are given a prompt template used to produce instructional content. Extract "
    "the SUBSTANTIVE guidance a writer would need in order to exercise the same "
    "judgment the template asks for. Include: how to decide between categories or "
    "labels (and any controlled vocabulary of flags/statuses, with the criterion "
    "that distinguishes each one); what counts as sufficient evidence or sourcing; "
    "when to mark something unknown/missing rather than infer it; required level of "
    "detail; tone and audience; and stated priorities or non-negotiables. Exclude "
    "ONLY: literal JSON/schema syntax, {{variable}} placeholders, and filler with no "
    "bearing on content decisions. Do NOT dismiss a rule as boilerplate merely "
    "because it is stated as a rule or concerns document structure - if it "
    "constrains what the writer may say, it is substantive. Respond with a numbered "
    "list, at most {max_items} items, one short imperative sentence each - nothing "
    "else. Reserve the single word NONE for a template that is genuinely only schema "
    "and placeholders."
)

_TEMPLATE_NAME = {"cdd": "cdd_generation", "blueprint": "blueprint_generation"}


# --------------------------------------------------------------------------- #
# Distillation memo — content-keyed, bounded, thread-safe
# --------------------------------------------------------------------------- #
_CACHE: "OrderedDict[str, str]" = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _cache_key(prompt_text: str, model_choice: str, max_items: int, max_chars: int) -> str:
    """Hash every input that changes the distillation output, so a change to any
    of them (including the caps) misses rather than serving a stale digest."""
    raw = f"{model_choice}|{max_items}|{max_chars}|{prompt_text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> str | None:
    with _CACHE_LOCK:
        if key not in _CACHE:
            return None
        _CACHE.move_to_end(key)          # LRU touch
        return _CACHE[key]


def _cache_put(key: str, value: str, max_size: int) -> None:
    if max_size <= 0:
        return
    with _CACHE_LOCK:
        _CACHE[key] = value
        _CACHE.move_to_end(key)
        while len(_CACHE) > max_size:
            _CACHE.popitem(last=False)   # evict least-recently-used


def reset_cache() -> None:
    """Clear the distillation memo. For tests and for an admin-triggered flush."""
    with _CACHE_LOCK:
        _CACHE.clear()


# --------------------------------------------------------------------------- #
# Prompt resolution
# --------------------------------------------------------------------------- #
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
        system, user, name, version = build_prompt(
            _TEMPLATE_NAME.get(deliverable, "cdd_generation"), {}, db=db,
            project_id=getattr(request_body, "project_id", None) or (course.project_id if course else None),
            cluster_id=course.cluster_id if course else None,
            course_id=getattr(request_body, "course_id", None),
            prompt_id=getattr(request_body, "prompt_id", None),
            strict=False,
        )
        log.info("prompt_guidance_resolved deliverable=%s template=%s version=%s chars=%d",
                 deliverable, name, version, len(system or "") + len(user or "") + 2)
        return f"{system}\n\n{user}"
    except Exception as exc:
        log.debug("prompt_guidance: prompt resolution failed for %s: %s", deliverable, exc)
        return ""


# --------------------------------------------------------------------------- #
# Distillation
# --------------------------------------------------------------------------- #
def _windows(text: str, window: int, max_windows: int) -> tuple[list[str], int]:
    """Split *text* into at most *max_windows* overlapping windows of *window*
    chars. Returns ``(windows, chars_covered)``.

    One window when it already fits — the overwhelmingly common case, and
    byte-identical to the pre-windowing single call.

    The overlap means an instruction spanning a boundary is seen intact by at
    least one window; without it, windowing would introduce its own subtle
    fidelity loss while claiming to prevent one. It is clamped to half the window
    so a small configured window can't invert the step and stall progress.

    ``chars_covered`` is returned rather than inferred by the caller because the
    only honest source of it is the actual last window's end offset: with a low
    ``max_windows`` the windows may stop short of the text, and a caller that
    guessed would under-report exactly the truncation this function exists to
    make visible.
    """
    if len(text) <= window:
        return [text], len(text)
    overlap = min(_WINDOW_OVERLAP_CHARS, window // 2)
    step = max(1, window - overlap)
    out: list[str] = []
    last_start = 0
    for start in range(0, len(text), step):
        out.append(text[start:start + window])
        last_start = start
        if len(out) >= max_windows:
            break
    return out, min(len(text), last_start + window)


def _digest_one(chunk: str, system: str, model_choice: str, usage_ctx: Any = None) -> str:
    """One distillation LLM call. Returns "" on empty/failed/"NONE" output.

    ``usage_ctx`` attributes the call's cost to the requesting project/course/user.
    Without it the call still logs, but under entity_type="unattributed" with NULL
    project and course — so a block-wide generation's total spend silently excluded
    its own first LLM call. Optional because ``_digest_prompt`` is also called
    directly by tests with no request behind it.
    """
    from promptops_app.services.llm_service import generate_text

    result = generate_text(model_choice, system, f"GENERATION PROMPT TEMPLATE:\n{chunk}",
                           usage_ctx=usage_ctx)
    if not result or result.startswith("ERROR"):
        return ""
    result = result.strip()
    if not result or result.upper() == "NONE":
        return ""
    return result


def _renumber(lines: list[str]) -> str:
    """Merge windows' numbered lists into one consistently-numbered list.

    Each window numbers from 1, so concatenating them yields duplicate indices
    that read as a malformed list to the downstream model. Deduplicates on the
    text after the numeric prefix, preserving first-seen order.
    """
    import re
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        body = re.sub(r"^\s*\d+[.)]\s*", "", line).strip()
        if not body:
            continue
        fingerprint = body.lower()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.append(body)
    return "\n".join(f"{i}. {body}" for i, body in enumerate(out, start=1))


def _digest_prompt(prompt_text: str, model_choice: str, *,
                   max_prompt_chars: int | None = None,
                   max_chars: int | None = None,
                   max_items: int | None = None,
                   max_windows: int | None = None,
                   usage_ctx: Any = None) -> str:
    """Distill prompt_text's substantive instructions into a short checklist.

    Windows the input rather than truncating it, then merges and renumbers. Any
    over-cap loss is logged with exact counts — never silent.

    Each cap defaults to its configured value when omitted, so this is callable
    with just ``(prompt_text, model_choice)``.
    """
    from app.core.config import settings

    max_prompt_chars = max_prompt_chars or settings.prompt_guidance_max_prompt_chars
    max_chars = max_chars or settings.prompt_guidance_max_chars
    max_items = max_items or settings.prompt_guidance_max_items
    max_windows = max_windows or settings.prompt_guidance_max_windows

    text = prompt_text.strip()
    if not text:
        return ""

    system = _DIGEST_SYSTEM_TEMPLATE.format(max_items=max_items)
    chunks, covered = _windows(text, max_prompt_chars, max_windows)
    if len(chunks) > 1:
        log.info("prompt_guidance_windowed chars=%d windows=%d window_size=%d",
                 len(text), len(chunks), max_prompt_chars)
    # Checked independently of the window count: a max_windows of 1 truncates just
    # as surely as a too-small window does, and that case must not slip past
    # silently — silent input loss is the exact defect this function replaced.
    if covered < len(text):
        log.warning(
            "prompt_guidance_input_capped chars=%d covered=%d dropped=%d windows=%d — "
            "raise PROMPT_GUIDANCE_MAX_PROMPT_CHARS or PROMPT_GUIDANCE_MAX_WINDOWS to "
            "cover the whole prompt",
            len(text), covered, len(text) - covered, len(chunks),
        )

    collected: list[str] = []
    for chunk in chunks:
        piece = _digest_one(chunk, system, model_choice, usage_ctx)
        if piece:
            collected.extend(piece.splitlines())
    if not collected:
        return ""

    merged = _renumber(collected) if len(chunks) > 1 else "\n".join(collected).strip()
    if len(merged) > max_chars:
        log.warning("prompt_guidance_output_truncated chars=%d cap=%d — raise "
                    "PROMPT_GUIDANCE_MAX_CHARS to keep the whole checklist",
                    len(merged), max_chars)
        merged = merged[:max_chars]
    return merged


def _usage_ctx(request_body: Any, deliverable: str, current_user: Any) -> Any:
    """Cost attribution for the distillation call, mirroring the REDUCE stage's
    context (BlockWideGenerator._build_usage_ctx) so both halves of a block-wide
    generation group under the same project/course/entity in llm_usage_logs.

    ``entity_id`` is the block label for the same reason it is there: no deliverable
    row exists yet. Returns None on any failure — accounting must never be able to
    break a generation, which is also why the caller runs inside a broad try.

    Known and accepted: the distillation is memoized on prompt text, so only the
    FIRST generation to use a given prompt pays, and later ones — possibly on another
    course — get it free and log no row. That makes this an attribution of who paid,
    not a per-run cost. At roughly a cent a call it is not worth defeating the memo
    to even out; it is written down here so a cost review does not read the gaps as
    missing data.
    """
    try:
        from promptops_app.services.usage_service import UsageLogContext
        return UsageLogContext(
            user_name=getattr(current_user, "username", "") or "",
            project_id=getattr(request_body, "project_id", None),
            course_id=getattr(request_body, "course_id", None),
            entity_type=deliverable,
            entity_id=str(getattr(request_body, "block", "") or ""),
            prompt_template="prompt_guidance_distill",
        )
    except Exception as exc:   # noqa: BLE001 — accounting, never load-bearing
        log.debug("prompt-guidance usage context unavailable: %s", exc)
        return None


def resolve_prompt_guidance(db: Any, request_body: Any, deliverable: str, current_user: Any) -> str:
    """Distill the course's selected CDD/Blueprint prompt into a short
    generation-guidance checklist for the block-wide digest pipeline's
    MAP/REDUCE calls.

    Best-effort and additive only: any failure (no prompt configured, resolution
    error, LLM error) returns "" and the caller's pipeline runs exactly as it did
    before this feature existed.

    ONE exception propagates: ``BudgetExceededError``. Attributing this call (so its
    cost stops landing in the unattributed bucket) also brought it under budget
    enforcement, because ``check_budget`` keys off the very project/course/user ids
    the usage context supplies — before, ``_levels_for(None)`` returned no levels and
    the call was never checked. Left to the catch-all below, a quota breach would be
    swallowed into "" and the block would generate WITHOUT its guidance, quietly
    worse, while the user saw success. ``generate_with_metadata`` re-raises this one
    exception for exactly that reason: "a quota breach is not 'try again later'" — it
    is a real 402 with a dedicated handler in app/main.py, and it must reach it.
    """
    from promptops_app.services.budget_service import BudgetExceededError

    try:
        from app.core.config import settings

        prompt_text = _resolve_prompt_text(db, request_body, deliverable)
        if not prompt_text.strip():
            return ""
        model_choice = getattr(request_body, "model_choice", None) or _FALLBACK_MODEL

        max_prompt_chars = settings.prompt_guidance_max_prompt_chars
        max_chars = settings.prompt_guidance_max_chars
        max_items = settings.prompt_guidance_max_items

        key = _cache_key(prompt_text, model_choice, max_items, max_chars)
        cached = _cache_get(key)
        if cached is not None:
            log.info("prompt_guidance_cache_hit deliverable=%s chars=%d", deliverable, len(cached))
            return cached

        guidance = _digest_prompt(
            prompt_text, model_choice,
            max_prompt_chars=max_prompt_chars,
            max_chars=max_chars,
            max_items=max_items,
            max_windows=settings.prompt_guidance_max_windows,
            usage_ctx=_usage_ctx(request_body, deliverable, current_user),
        )
        # Memoize even an empty result: a prompt with no substantive guidance
        # would otherwise pay for the same "NONE" call on every generation.
        _cache_put(key, guidance, settings.prompt_guidance_cache_size)
        log.info("prompt_guidance_built deliverable=%s prompt_chars=%d guidance_chars=%d",
                 deliverable, len(prompt_text), len(guidance))
        return guidance
    except BudgetExceededError:
        raise
    except Exception as exc:
        log.warning("prompt_guidance_resolution_failed deliverable=%s error=%s", deliverable, exc)
        return ""
