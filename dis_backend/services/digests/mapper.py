"""MAP — build one compact per-day digest from a day's content units.

A digest is a ~300-500 token structured JSON summary of a single course day. The
factual fields (day, topic, ACS, source availability) are lifted from the STRUCTURE
STORE metadata and are never invented by the model; only the narrative fields
(objective, misconceptions, salient excerpts, concept type) come from the LLM.

Restricted-content gate (§8.4) is deliverable-aware and text-only:
  * METADATA (ACS, topic, availability) is read from ALL units, always — it is
    curriculum bookkeeping, not restricted content.
  * The LLM PROMPT is built only from units whose TEXT is allowed for the target
    audience. Answer keys never enter any digest. Instructor-only text (e.g.
    instructor guides) IS allowed for instructor-facing deliverables (CDD /
    Blueprint) and withheld for student-facing ones.

Proven end-to-end in the Phase-0 spike; this promotes it to product code.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, List, Optional

from services.digests import attribution
from services.digests.prompt_template import render as prompt_template_render

log = logging.getLogger(__name__)

DIGEST_SCHEMA_VERSION = "v8"
# Every bump here is a structural change to what a digest CONTAINS, which cache_key
# cannot infer on its own — so the version must move for existing digests to rebuild.
#   v8: added assigned_reading (label/unit_count/files) and made THIN_DAY /
#       MISSING_SOURCE account for it — a day taught from its assigned handbook
#       reading is no longer reported as having no source material.
#   v7: added concept_scope — the AIM reference's Worksheet 4 "Concept Scope" column:
#       the specific sub-topics/tools/materials covered that day, grounded in that
#       day's own sources rather than a restatement of the Day Title.
#   v6: added concept_type_explanation, interactive_scope, job_aid_source_reference
#       (LLM fields) and storyline_source_asset_status (mechanical, from unit types)
#       to close the AIM-reference Day-by-Day Map column gap.
PROMPT_VERSION_BASE = "map-v6"  # bumped: the extraction rubric + concept_type taxonomy moved
# out of this module's f-string literal into the editable templates/digest_map.md (the JSON
# schema below stays code-owned and is injected as {{schema}}). The effective prompt version
# is BASE + the template's content hash — see current_prompt_version() — so editing the
# template invalidates exactly the digests it changes, with no manual bump needed.

#: The reply contract. Code-owned and injected into the template as ``{{schema}}`` so an
#: admin can rewrite the entire rubric without being able to drop a key the parser reads.
#: Keep in sync with the ``fields`` dict at the end of ``_llm_extract``.
MAP_SCHEMA = (
    '{"derived_objective": str, "misconceptions": [str],\n'
    '"salient_excerpts": [str], "concept_type": str, "concept_type_explanation": str,\n'
    '"concept_scope": str,\n'
    '"interactive_candidate": bool, "interactive_type": str,\n'
    '"interactive_content": str, "interactive_rationale": str, "interactive_scope": str,\n'
    '"job_aid_candidate": bool, "job_aid_type": str, "job_aid_description": str,\n'
    '"job_aid_source_reference": str}'
)

#: Every key ``_llm_extract`` reads out of the reply. A template that fails to mention one
#: is rejected in favour of the built-in (see prompt_template.resolve).
MAP_REPLY_KEYS = (
    "derived_objective", "misconceptions", "salient_excerpts", "concept_type",
    "concept_type_explanation", "concept_scope", "interactive_candidate", "interactive_type",
    "interactive_content", "interactive_rationale", "interactive_scope",
    "job_aid_candidate", "job_aid_type", "job_aid_description", "job_aid_source_reference",
)

MAP_TEMPLATE_NAME = "digest_map"


class MapExtractionError(RuntimeError):
    """The MAP reply did not contain the digest fields.

    Raised rather than defaulting, so ``build_digest``'s per-day isolation marks the
    day ``digest_status="failed"`` and it surfaces as "REVIEW NEEDED" plus a
    ``coverage.failed_days`` entry. A silently-defaulted day is indistinguishable
    downstream from a genuinely extracted one, which is how an entire block once
    rendered with every LLM field at its default while reporting success.

    Carries the token counts and the model of the call that produced the bad reply.
    A rejected reply was still generated and still billed, and ``build_digest``'s
    ``except`` branch is the only place that spend can be recorded — the success
    path's counter is never reached. Without this, prod's 2026-08-13 Block 2 build
    reported ``map_calls=0`` and $0 spent for 20 days that had each made a real
    Bedrock call. ``model`` matters just as much: ``_llm_extract`` may ESCALATE a
    content-rich day to a larger-context model, so the model that failed is often
    not the one configured, and the failed digest is the only record of which.
    """

    def __init__(self, message: str, *, tokens_in: int = 0, tokens_out: int = 0,
                 model: str = "") -> None:
        super().__init__(message)
        self.tokens_in = int(tokens_in or 0)
        self.tokens_out = int(tokens_out or 0)
        self.model = model or ""

#: Last-resort fallback if ``templates/digest_map.md`` is missing, empty, unreadable, or
#: fails the reply-key contract check. Deliberately a COMPACT prompt rather than a
#: duplicate of the shipped template: two copies of a 60-line rubric would drift, and
#: the shipped file is the source of truth. This keeps the reply contract and the core
#: "never invent" discipline intact, so a bad deploy degrades digest QUALITY (a terser
#: rubric, no concept_type taxonomy) rather than CORRECTNESS — and says so in the log.
#: Its own hash feeds the cache key too, so fallback-built digests never masquerade as
#: template-built ones.
_BUILTIN_MAP_PROMPT = (
    "You are extracting a compact JSON digest of ONE course day for a Block Blueprint /\n"
    "Course Design Document. Extract ONLY the fields in the schema below. Do not invent a\n"
    "source type, citation, or fact that is absent from the SOURCES. Be concise.\n\n"
    "List fields (misconceptions, salient_excerpts) must be genuine JSON arrays — use []\n"
    "when the day has nothing to document. NEVER put a placeholder string like \"no\",\n"
    "\"N/A\", or \"none\" inside an array as if it were a real item.\n\n"
    "Use the \"no\"/false/\"N/A\" values where a field genuinely doesn't apply. The\n"
    "*_type/*_content/*_description fields are \"\" when their *_candidate is false.\n\n"
    "concept_type is a short label for the day's dominant mode of learning (e.g.\n"
    "Conceptual, Procedural, Skill, Factual, Metacognitive, Summative Assessment), and\n"
    "concept_type_explanation grounds that choice in what the SOURCES actually contain.\n\n"
    "Respond with ONLY the JSON object below — no preamble, no markdown fence.\n\n"
    "{{schema}}\n"
    "{{guidance_block}}"
    "DAY {{day_number}}: {{topic}}\n"
    "LESSON: {{lesson_title}}\n"
    "SOURCES:\n"
    "{{sources}}\n"
)


def map_prompt() -> tuple[str, str]:
    """Return ``(template_text, content_hash)`` for the MAP prompt."""
    from services.digests import prompt_template
    return prompt_template.resolve(MAP_TEMPLATE_NAME, _BUILTIN_MAP_PROMPT, MAP_REPLY_KEYS,
                                   schema=MAP_SCHEMA)


def current_prompt_version() -> str:
    """Effective MAP prompt version: base marker + the template's content hash.

    Resolved at CALL time, not import time, so an edit to the template is picked up
    by a running process (the loader is mtime-aware) and immediately invalidates the
    digests that edit would change. Import-time resolution would let a long-lived
    worker keep serving cached digests under a stale version after an edit — the same
    stale-process failure mode that has bitten this pipeline before.
    """
    try:
        return f"{PROMPT_VERSION_BASE}+{map_prompt()[1]}"
    except Exception as exc:   # never let versioning break a build
        log.warning("map prompt version resolution failed (%s) — using base marker", exc)
        return PROMPT_VERSION_BASE


#: Backwards-compatible module attribute. Prefer ``current_prompt_version()``: this is a
#: snapshot taken at import and does not reflect a later template edit.
PROMPT_VERSION = PROMPT_VERSION_BASE
def _env_int_or_none(name: str) -> Optional[int]:
    """Operator override for a derived limit: an int (0 = no limit), or None if unset.

    Junk and negative values resolve to None — i.e. "fall back to the default/derived
    budget" — never to a number. Returning a sentinel like -1 here looks harmless but
    is not: -1 is truthy, so it flowed straight into the trim comparison and silently
    kept only the FIRST source unit of every day, leaving nothing but a log line to
    say so. A malformed limit must never be quieter, or more destructive, than an
    unset one.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        log.warning("%s=%r is not an integer — ignoring it and using the default "
                    "(model-derived) budget instead", name, raw)
        return None
    if value < 0:
        log.warning("%s=%d is negative — ignoring it and using the default "
                    "(model-derived) budget instead (use 0 for 'no limit')", name, value)
        return None
    return value


def _env_int(name: str, default: int) -> int:
    """Read a non-negative int limit from the environment. 0 means "no limit".

    Thin wrapper over :func:`_env_int_or_none` so the parsing and the
    junk-value-is-not-a-number rule live in exactly one place.
    """
    value = _env_int_or_none(name)
    return default if value is None else value


#: Fallback MAP output ceiling for a model this module has no entry for. Any cap
#: below the model's real limit risks a digest truncated mid-JSON (which MAP does
#: now catch — the day fails rather than storing defaults — but a failed day is
#: still a day nobody got an answer for). 4096 matched Claude 3 Sonnet's actual
#: maximum; the modern family is far higher, so a single flat number is wrong in
#: BOTH directions: it throttles capable models and it exceeds what legacy models
#: will accept. Per-model values live in ``_MAX_OUTPUT_TOKENS``; this is only what
#: an unrecognised id gets. Set DIS_MAP_MAX_TOKENS to override everything.
MAP_MAX_TOKENS = _env_int("DIS_MAP_MAX_TOKENS", 32_000)

# One canonical "teachable substance" set, shared with ENUMERATE's THIN_DAY logic
# so the two never disagree about what makes a day thin (was previously a divergent
# copy here that also counted "chunk").
SUBSTANTIVE_UNIT_TYPES = attribution.SUBSTANTIVE
# Answer-key material must never enter a digest, regardless of audience.
_ANSWER_KEY_UNIT_TYPES = {"answer_key_item"}
# Visibility/access values that mark a unit's TEXT as restricted for student-facing
# use — kept in sync with ContextRetrievalService._is_hard_restricted so the digest
# gate and the retrieval gate agree.
_RESTRICTED_VISIBILITY = {"instructor_only", "internal", "internal_only",
                          "admin_only", "restricted_admin"}

# Source types VERIFY / coverage reconciles per day (§4.2 source_availability).
TRACKED_SOURCES = ["calendar", "slide", "guide_section", "project_task"]
_UNIT_TO_SOURCE = {"calendar_day": "calendar", "slide": "slide",
                   "guide_section": "guide_section", "project_task": "project_task"}

# Unit types that reliably carry a visual/diagram asset (vs. plain narrative text) —
# used to give storyline_source_asset_status a mechanical, grounded answer instead of
# asking the (text-only) LLM to guess whether art exists for a day it can't actually see.
_VISUAL_ASSET_UNIT_TYPES = {"slide", "page"}


def text_allowed_for_digest(unit: Dict[str, Any], audience: str = "instructor") -> bool:
    """Whether this unit's TEXT may be put in the digest prompt (§8.4).

    Answer keys: never. Instructor-only text: allowed for instructor-facing
    deliverables (default), withheld for student-facing ones. Metadata on the same
    unit is still read for coverage regardless of this gate.
    """
    md = unit.get("metadata_json") or {}
    if unit.get("unit_type") in _ANSWER_KEY_UNIT_TYPES or md.get("is_answer_key") is True:
        return False
    if audience == "student":
        if md.get("restricted") is True:
            return False
        visibility = str(md.get("visibility") or "").strip().lower()
        access = str(md.get("access_level") or "").strip().lower()
        if visibility in _RESTRICTED_VISIBILITY or access == "admin_only":
            return False
    return True


def day_signature(day: Dict[str, Any]) -> str:
    """Cache-relevant projection of a calendar-day row: the fields that feed the
    MAP prompt (topic + lesson title). Folded into cache_key so an edit to the
    day's topic/title invalidates the digest even when no source unit changed."""
    return f"{day.get('topic') or ''}|{day.get('lesson_title') or ''}"


def has_extraction(digest: Dict[str, Any]) -> bool:
    """Whether a digest actually carries LLM-extracted content.

    A digest can be structurally complete and marked ``ok`` while every extracted
    field sits at its default — the signature of a MAP call that returned an
    unrelated shape back when that only logged a warning. Used by the cache check
    so such a digest is rebuilt rather than trusted, and safe to apply to any
    stored digest regardless of which code version wrote it.

    ``concept_type`` and ``derived_objective`` are the two fields every real day
    yields (unlike the optional interactive/job-aid ones, legitimately empty on many
    days), so requiring EITHER to be non-default avoids flagging a genuinely sparse
    day as poisoned.
    """
    ct = str(digest.get("concept_type") or "").strip()
    obj = str(digest.get("derived_objective") or "").strip()
    return bool((ct and ct.lower() != "unknown") or obj)


def cache_key(day_number: int, units: List[Dict[str, Any]], model: str,
              schema_version: str = DIGEST_SCHEMA_VERSION,
              prompt_version: str = "",
              day_meta: str = "", map_guidance: str = "",
              reference_units: Optional[List[Dict[str, Any]]] = None) -> str:
    """Content-addressed digest key (§4.4).

    Invalidates automatically when the digest schema, extractor model, prompt
    version, ANY source unit's content, the day's prompt-relevant metadata
    (``day_meta`` — topic/lesson title), or the resolved prompt-guidance text
    (``map_guidance`` — see resolve_prompt_guidance) changes. Folding the
    guidance text itself into the hash (not just a bool/version marker) means
    editing the underlying course prompt correctly busts every previously
    cached digest that guidance would have applied to, with no separate
    invalidation bookkeeping needed.

    ``prompt_version`` defaults to ``current_prompt_version()`` — resolved on each
    call rather than bound at import, so it carries the MAP template's live content
    hash and an edit to that template invalidates the digests it affects. Callers
    may still pass an explicit value (tests do) to pin the key.
    """
    prompt_version = prompt_version or current_prompt_version()
    unit_hashes = ",".join(sorted((u.get("content_hash") or _est_hash(u)) for u in units))
    # Hashed as a count plus digest rather than inline with `unit_hashes`, so a
    # stored digest built before assigned reading existed keys identically when
    # no reading resolves — an empty reference list must not invalidate every
    # cached digest in the store on deploy.
    refs = list(reference_units or [])
    ref_part = ""
    if refs:
        ref_hashes = ",".join(sorted((u.get("content_hash") or _est_hash(u)) for u in refs))
        ref_part = f"|ref{len(refs)}:{hashlib.sha256(ref_hashes.encode()).hexdigest()[:16]}"
    raw = (f"{schema_version}|{model}|{prompt_version}|day{day_number}|{day_meta}"
           f"|mg:{map_guidance}|{unit_hashes}{ref_part}")
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _est_hash(unit: Dict[str, Any]) -> str:
    return hashlib.sha256((unit.get("text_content") or "").encode()).hexdigest()[:16]


def _acs_codes(unit: Dict[str, Any]) -> List[str]:
    return [c for c in ((unit.get("metadata_json") or {}).get("acs_codes") or []) if c]


def _guidance_block(map_guidance: str) -> str:
    """Render the optional guidance as a clearly-delimited, contract-safe addendum —
    "" (no extra lines) when there is none, so a day with no guidance produces
    byte-identical prompt text to before this feature existed.

    CAREFUL — the heading below says "derived from the course's selected prompt
    template", and since 2026-08-13 that is only half true: CAS composes TWO layers
    into this one string (the distilled template, then the requester's own style and
    instructions — see promptops_app/services/user_directives.py), each carrying its
    own inner heading. The wrapper's CONSTRAINTS still apply correctly to both, which
    is what matters for the extraction contract; only its attribution clause is loose.

    It is left loose on purpose. Every character of this function's output is part of
    the MAP prompt, and the prompt version is ``PROMPT_VERSION_BASE`` + the template
    hash — which does NOT cover this module — so rewording the heading changes what
    every client's digests were built from WITHOUT invalidating them. Fixing the
    wording therefore means bumping PROMPT_VERSION_BASE, which rebuilds every day of
    every block for every tenant. Worth doing alongside a schema bump; not worth doing
    on its own for an attribution nicety."""
    text = (map_guidance or "").strip()
    if not text:
        return ""
    return (
        "\nADDITIONAL GENERATION GUIDANCE (derived from the course's selected "
        "prompt template — apply while extracting the fields above; this "
        "refines judgment/emphasis ONLY, it must never add a field not listed "
        "above or contradict the required JSON schema):\n" + text + "\n"
    )


#: Hard ceiling on the assembled SOURCES text for one day's MAP prompt. A day is not
#: bounded by anything upstream — attribution can legitimately place 130+ units on a
#: single day — and exceeding the extractor's context window is a HARD Bedrock error
#: ("Input is too long for requested model") that fails the whole day. Observed live
#: on Block 2 Day 1 before attribution was tightened. Chosen so that even worst-case
#: dense text (ACS code lists tokenize at roughly one token per character, far worse
#: than the usual ~4 chars/token) stays inside a 200k-token window.
#: Per-model input capacity, in CHARACTERS of assembled SOURCES block.
#:
#: Derived from each model's context window rather than guessed: a single global
#: constant is wrong by construction, because the same number is simultaneously too
#: small for a large-window model and too large for a small one.
#:
#: The chars-per-token divisor is deliberately pessimistic. Ordinary prose runs ~4
#: chars/token, but this content is not ordinary prose — ACS code lists
#: ("AM.I.B.K1") tokenize closer to 1 token per character, and underestimating here
#: means a hard "Input is too long" rejection that fails the whole day. 2.5
#: chars/token leaves room for the prompt scaffold, rubric and guidance that share
#: the window with the sources.
_CHARS_PER_TOKEN = 2.5
_CONTEXT_TOKENS = {
    # Current generation — verified invokable via `global.` in ap-south-1 AND
    # us-east-1 on this account (direct InvokeModel).
    "global.anthropic.claude-opus-5": 1_000_000,
    "global.anthropic.claude-sonnet-5": 1_000_000,
    "global.anthropic.claude-sonnet-4-6": 1_000_000,
    "global.anthropic.claude-fable-5": 1_000_000,
    "global.anthropic.claude-fable-5-1": 1_000_000,
    # Available on Bedrock but NO model access for this account's role.
    "global.anthropic.claude-haiku-4-5-20251001-v1:0": 200_000,
    "global.anthropic.claude-opus-4-8": 1_000_000,
    "global.anthropic.claude-sonnet-4-5-20250929-v1:0": 200_000,
    # Legacy Claude 3 — still referenced by academian.yaml / cengage.yaml, so they
    # need a window here or they would fall to the pessimistic default. Do not use
    # for new config: Sonnet 3 is end-of-life in us-east-1 and Haiku 3 is
    # provider-legacy and denied in every region.
    "anthropic.claude-3-sonnet-20240229-v1:0": 200_000,
    "anthropic.claude-3-haiku-20240307-v1:0": 200_000,
}
#: Each model's maximum OUTPUT tokens — the one kind of cap that is a real model
#: limit rather than a policy choice, so it is the only one MAP applies. Keys mirror
#: _CONTEXT_TOKENS exactly; an id absent from both falls back to MAP_MAX_TOKENS,
#: which is deliberately the previous flat value so an unrecognised model can never
#: end up MORE throttled than before this table existed.
#:
#: The first four values are NOT independent guesses — they are copied from CAS's
#: promptops_app.core.models registry, which carries these numbers from live probes
#: on the same account. The two deployables cannot share a module, so
#: tests/unit/test_no_silent_truncation.py asserts they still agree for every id
#: present in both; that test is what makes this a mirror rather than a second
#: opinion. Note in particular that Haiku 4.5 is 16,384, not 64,000 — grouping it
#: with the rest of the modern family is the obvious wrong guess.
_MAX_OUTPUT_TOKENS = {
    # Mirrored from CAS's registry (probe-verified there).
    "global.anthropic.claude-opus-5": 64_000,
    "global.anthropic.claude-sonnet-5": 64_000,
    "global.anthropic.claude-sonnet-4-5-20250929-v1:0": 64_000,
    "global.anthropic.claude-haiku-4-5-20251001-v1:0": 16_384,
    "global.anthropic.claude-fable-5": 128_000,
    "global.anthropic.claude-fable-5-1": 128_000,
    # Not in CAS's registry, so these are the published maxima rather than measured
    # ones. Safe to be wrong high: Bedrock does not reject an oversized max_tokens
    # (128,000 was accepted in the probe recorded in that registry), so the cost of
    # an over-guess is nothing while an under-guess silently shortens a digest.
    "global.anthropic.claude-sonnet-4-6": 64_000,
    "global.anthropic.claude-opus-4-8": 64_000,
    # Legacy Claude 3: 4,096 is the provider's hard maximum, so the flat 32,000 was
    # asking these ids for eight times what they can produce.
    "anthropic.claude-3-sonnet-20240229-v1:0": 4_096,
    "anthropic.claude-3-haiku-20240307-v1:0": 4_096,
}


def max_output_tokens(model: str) -> int:
    """The output ceiling to request for *model*.

    An explicit DIS_MAP_MAX_TOKENS override wins, because an operator setting it is
    making a deliberate choice; otherwise the model's own documented maximum, and
    the previous flat default for anything unrecognised.
    """
    if _env_int_or_none("DIS_MAP_MAX_TOKENS") is not None:
        return MAP_MAX_TOKENS
    return _MAX_OUTPUT_TOKENS.get(model) or MAP_MAX_TOKENS


#: Reserved for the prompt scaffold (schema, rubric, guidance, day metadata) that
#: shares the window with the sources.
_PROMPT_OVERHEAD_CHARS = 40_000

#: Ordered escalation ladder: when a day's sources do not fit the configured model,
#: move UP to a larger-window model instead of trimming. Comma-separated model IDs,
#: largest window last is irrelevant — they are sorted by known window.
#:
#: Empty by default: on this account every larger-window model (Opus 5, Sonnet 5,
#: Sonnet 4.6) is AccessDenied for DIS's principal, and escalating to a model that
#: cannot be invoked would fail the day outright — worse than the trim it avoids.
#: Populate it once a larger model is genuinely invokable from this environment,
#: measured on repeated probes rather than one:
#:     DIS_MAP_ESCALATION_MODELS=global.anthropic.claude-opus-5
MAP_ESCALATION_MODELS = [
    m.strip() for m in (os.getenv("DIS_MAP_ESCALATION_MODELS") or "").split(",") if m.strip()
]


#: Hard override of the derived per-model budget. 0 = NO LIMIT (never trim; accept a
#: hard context-window rejection instead). Unset ⇒ use the model-derived capacity,
#: which is the better behaviour and needs no configuration.
MAP_MAX_SOURCE_CHARS = _env_int_or_none("DIS_MAP_MAX_SOURCE_CHARS")
MAP_MAX_UNIT_CHARS = _env_int("DIS_MAP_MAX_UNIT_CHARS", 0)


def context_budget_chars(model: str) -> int:
    """Characters of SOURCES this model can take. 0 = unlimited (explicit override).

    An unknown model gets the smallest known window rather than an optimistic guess:
    being wrong low costs a flagged trim, being wrong high costs the whole day.
    """
    if MAP_MAX_SOURCE_CHARS is not None:      # explicit operator override wins
        return MAP_MAX_SOURCE_CHARS
    tokens = _CONTEXT_TOKENS.get(model) or min(_CONTEXT_TOKENS.values())
    # Floored, never negative: a budget below zero is truthy in the trim comparison
    # and would keep only the first unit of every day.
    return max(int(tokens * _CHARS_PER_TOKEN) - _PROMPT_OVERHEAD_CHARS, 10_000)


def select_model_for(configured: str, needed_chars: int) -> tuple[str, Optional[str]]:
    """Pick the model to run this day on, escalating if the sources don't fit.

    Returns ``(model, note)`` where *note* is None when the configured model was
    kept, else a human-readable reason recorded on the digest so the substitution is
    visible rather than inferred from a cost report.

    Escalating beats trimming: a larger window keeps ALL of the day's material, where
    trimming silently removes evidence the extraction is supposed to be based on.
    """
    if not needed_chars or needed_chars <= context_budget_chars(configured):
        return configured, None
    ladder = sorted(
        (m for m in MAP_ESCALATION_MODELS if m != configured),
        key=lambda m: _CONTEXT_TOKENS.get(m, 0),
    )
    for candidate in ladder:
        if needed_chars <= context_budget_chars(candidate):
            note = (f"escalated from {configured} to {candidate}: sources are "
                    f"{needed_chars} chars, over that model's capacity")
            log.warning("MAP %s", note)
            return candidate, note
    return configured, None

#: Truncation order when a day exceeds the budget: keep the units we are most
#: confident belong to this day. Mirrors attribution's own signal hierarchy
#: (metadata day_number > item cross-reference > filename token > term overlap), so
#: what gets dropped first is what was least certainly this day's material.
_SIGNAL_PRIORITY = {"raw": 0, "S1": 1, "S2": 2, "S3": 3}


def _source_body(llm_units: List[Dict[str, Any]],
                 limit: Optional[int] = None) -> tuple[str, Dict[str, int]]:
    """Assemble the SOURCES block, trimming to *limit* chars (0/None = no trim).

    Returns ``(body, dropped)`` where ``dropped`` is {"units": n, "chars": n} —
    empty when everything fit. Truncation is reported, never silent: the caller
    turns it into a digest review flag so a reviewer can see the day was too large
    rather than wondering why its extraction looks thin.
    """
    def rank(u: Dict[str, Any]) -> int:
        sig = str(u.get("attribution_signal") or "raw")
        return _SIGNAL_PRIORITY.get(sig.split(":")[0], 4)

    ordered = sorted(enumerate(llm_units), key=lambda p: (rank(p[1]), p[0]))
    kept: List[tuple[int, str]] = []
    used = 0
    dropped_units = dropped_chars = 0
    for idx, u in ordered:
        text = u.get("text_content") or ""
        if MAP_MAX_UNIT_CHARS:                      # 0 => send the unit whole
            text = text[:MAP_MAX_UNIT_CHARS]
        block = f"[{u.get('unit_type')}] {u.get('title') or ''}\n{text}"
        if limit and used + len(block) > limit and kept:
            dropped_units += 1
            dropped_chars += len(block)
            continue
        kept.append((idx, block))
        used += len(block)
    # Restore the original unit order so the prompt still reads in source order.
    body = "\n\n".join(b for _, b in sorted(kept, key=lambda p: p[0]))
    dropped = {"units": dropped_units, "chars": dropped_chars} if dropped_units else {}
    return body, dropped


#: Sentinel for "no cap" on _reading_body. NOT 0 — see that function's docstring.
READING_NO_LIMIT = None


def _reading_body(references: Any, limit: Optional[int] = READING_NO_LIMIT
                  ) -> tuple[str, Dict[str, int]]:
    """The ASSIGNED READING block: the handbook passages this day's calendar cites.

    Kept out of ``_source_body`` and labelled separately because the two are
    different kinds of evidence and the model must not conflate them. The day's
    own units are what the block teaches; this is a shared reference work the
    calendar points the day at, and a digest that reported handbook prose as the
    block's own material would be wrong in a way nothing downstream could detect.

    Trims from the END rather than by rank: this is book prose in reading order,
    so the first pages of the assigned range are the ones the day actually opens
    on, and dropping the tail keeps a contiguous passage where dropping the
    lowest-ranked chunks would leave holes mid-argument.

    ``limit`` is a HARD character cap, and ``0`` means "no room" — deliberately
    NOT ``_source_body``'s "0 = do not trim" convention. The caller computes it
    as ``budget - len(body)``, which reaches 0 whenever the day's own units
    already fill the model's window; under the other convention that arithmetic
    silently meant "unlimited", so exactly when there was no room left the whole
    chapter was appended to an already-full prompt with an empty ``dropped``
    dict — no flag, no log, and (with the escalation ladder empty on this
    account) no larger model to absorb it. Measured: limit=10,000 kept 9,161
    chars and reported 47 dropped units; limit=0 kept 150,255 and reported none.
    Pass ``READING_NO_LIMIT`` for the unbounded sizing pass.

    Nothing is force-kept either. ``_source_body`` always retains its first unit
    because a day must contribute something; assigned reading is supplementary
    to the day's own material, so when there is no room it yields entirely
    rather than evicting what it was meant to support.
    """
    units = list(getattr(references, "units", None) or [])
    if not units:
        return "", {}
    label = getattr(references, "label", lambda: "")() or "assigned reading"
    header = (f"\n\n=== ASSIGNED READING — {label} ===\n"
              f"(Shared reference work cited by this day's calendar row. It is what the "
              f"day reads FROM, not material this block authored.)\n")
    kept: List[str] = []
    used = len(header)
    dropped_units = dropped_chars = 0
    withheld = 0
    for u in units:
        # The same gate every other unit passes through. Reference works reach
        # this function straight from a SQL read that filters on document TYPE
        # only, so without this an answer key bound into a textbook appendix — or
        # any ebook_reference a future ingestion marks is_answer_key — would enter
        # the prompt, and "answer keys: never, regardless of audience" is stated
        # in this module as unconditional. Instructor visibility is fine here:
        # build_digest fixes the audience to instructor (see its docstring).
        if not text_allowed_for_digest(u, "instructor"):
            withheld += 1
            continue
        text = u.get("text_content") or ""
        if MAP_MAX_UNIT_CHARS:
            text = text[:MAP_MAX_UNIT_CHARS]
        if not text.strip():
            continue
        if limit is not None and used + len(text) > limit:
            dropped_units += 1
            dropped_chars += len(text)
            continue
        kept.append(text)
        used += len(text)
    dropped: Dict[str, int] = {}
    if dropped_units:
        dropped = {"reading_units": dropped_units, "reading_chars": dropped_chars}
    if withheld:
        dropped["reading_units_withheld"] = withheld
    if not kept:
        return "", dropped
    return header + "\n\n".join(kept), dropped


def _llm_extract(day: Dict[str, Any], llm_units: List[Dict[str, Any]], model: str,
                 call_llm, safe_json, map_guidance: str = "", references: Any = None
                 ) -> tuple[Dict[str, Any], int, int, Dict[str, int], str, Optional[str]]:
    """Call the extractor and return (fields, tokens_in, tokens_out, dropped,
    model_used, escalation_note).

    ``dropped`` reports any SOURCES truncation that survived model escalation.

    ``map_guidance`` (optional) is judgment/emphasis instructions distilled from
    the course's selected CDD/Blueprint prompt (see resolve_prompt_guidance) —
    appended AFTER the fixed schema/rubric below, never allowed to redefine it;
    the model is told explicitly it may only refine judgment within the fields
    already specified, not add a field or contradict the required JSON shape.
    """
    # Measure the day untrimmed FIRST, then choose a model that can hold it. Sizing
    # the content to the model is backwards when a larger-window model is available:
    # escalating keeps all of the day's evidence, trimming silently discards the very
    # material the extraction is supposed to rest on.
    full_body, _ = _source_body(llm_units, limit=0)
    full_reading, _ = _reading_body(references, limit=READING_NO_LIMIT)
    model, escalation = select_model_for(model, len(full_body) + len(full_reading))

    budget = context_budget_chars(model)
    body, dropped = _source_body(llm_units, limit=budget)
    # The block's own material is sized first and the assigned reading fills what
    # is left. Deliberately in that order: a handbook chapter is an order of
    # magnitude larger than a day's lesson units, so sizing them together would let
    # book prose evict the very material the digest is meant to be about.
    reading, reading_dropped = _reading_body(references, limit=max(0, budget - len(body)))
    if reading:
        body = f"{body}{reading}" if body.strip() else reading.lstrip("\n")
    dropped = {**dropped, **reading_dropped}
    if reading_dropped:
        log.warning("digest MAP day %s: ASSIGNED READING truncated for model %s — "
                    "dropped %d passage(s) (%d chars) of %s",
                    day.get("day_number"), model, reading_dropped["reading_units"],
                    reading_dropped["reading_chars"],
                    getattr(references, "label", lambda: "?")())
    if dropped.get("units"):
        log.warning("digest MAP day %s: SOURCES truncated to %d chars for model %s — "
                    "dropped %d lowest-confidence unit(s) (%d chars). No larger model "
                    "was available; see DIS_MAP_ESCALATION_MODELS.",
                    day.get("day_number"), budget, model,
                    dropped["units"], dropped["chars"])
    template, _template_hash = map_prompt()
    prompt = prompt_template_render(template, {
        "schema": MAP_SCHEMA,
        # _guidance_block already supplies its own surrounding blank lines; `or "\n"`
        # preserves the blank line the template needs between the rubric and the DAY
        # header when there is no guidance (the same subtlety the previous
        # string-concatenation version documented at length).
        "guidance_block": _guidance_block(map_guidance) or "\n",
        "day_number": day.get("day_number"),
        "topic": day.get("topic") or "",
        "lesson_title": day.get("lesson_title") or "",
        "sources": body if body.strip() else "(no text-allowed source units for this day)",
    })
    text, ti, to = call_llm(model, prompt, max_output_tokens(model))
    data = safe_json(text) or {}
    if not data or "derived_objective" not in data or "concept_type" not in data:
        # safe_json's json.loads is strict — either the raw response didn't
        # parse at all (likely truncated mid-JSON for content-rich days) or it
        # parsed into an unrelated shape. A provider error no longer arrives here
        # at all: call_llm raises LLMCallFailed, which build_digest catches. So this
        # branch now means what it says — the model answered, in the wrong shape.
        #
        # This USED to only log and let every field default, leaving
        # digest_status="ok". That is the failure mode that shipped a complete-
        # looking 20-day Blueprint in which every LLM-derived cell was its default
        # (concept_type="Unknown", objective "—", misconceptions "NONE DOCUMENTED",
        # every candidate "No") while coverage reported zero failed days and the job
        # reported success. A silent default is indistinguishable from a real
        # extraction downstream, so raise instead: build_digest's own per-day
        # isolation catches this, marks the day digest_status="failed", and the day
        # then renders "REVIEW NEEDED" and appears in coverage.failed_days — which is
        # exactly the reviewer-visible convention the AIM reference itself uses.
        log.warning(
            "digest MAP day %s: response missing expected keys (parsed=%r), "
            "raw_text=%r", day.get("day_number"), bool(data), text[:2000],
        )
        # These messages are the only artefact that leaves the DIS process, so they
        # carry the reply snippet and the model: a body cut off mid-JSON means the
        # token budget was too small, and the model matters because escalation may
        # have swapped in one this account cannot invoke. The raw_text logged above is
        # not reachable by whoever reads the failed generation.
        #
        # The stub branch below is now DEFENSIVE, not the main path: call_llm raises
        # LLMCallFailed on a provider error rather than returning
        # '{"doc_type":"other",...}', so a timeout or throttle never reaches here. It
        # is kept because a stored digest from before that change carries this shape,
        # and because a model could in principle reply with it — in which case
        # reporting "lacks the required keys" would repeat 2026-08-13's mistake of
        # sending diagnosis to the prompt instead of to a 60-second read timeout.
        from services.pipeline.common import is_llm_failure_stub
        if is_llm_failure_stub(text):
            raise MapExtractionError(
                f"MAP call for day {day.get('day_number')} FAILED at the provider "
                f"(model={model}) — the reply is call_llm's failure stub, so no "
                f"extraction happened. This is a credentials/timeout/throttling/quota "
                f"problem, not a prompt or schema one. The preceding '[LLM] failed' log "
                f"line names the underlying AWS exception.",
                tokens_in=ti, tokens_out=to, model=model,
            )
        raise MapExtractionError(
            f"MAP reply for day {day.get('day_number')} lacks the required keys "
            f"(derived_objective/concept_type); model={model} "
            f"keys={sorted(data)[:8]} reply[:160]={text[:160]!r}",
            tokens_in=ti, tokens_out=to, model=model,
        )
    fields = {
        "derived_objective": data.get("derived_objective", ""),
        "misconceptions": data.get("misconceptions", []) or [],
        "salient_excerpts": data.get("salient_excerpts", []) or [],
        "concept_type": data.get("concept_type", "Unknown"),
        "concept_type_explanation": data.get("concept_type_explanation", ""),
        "concept_scope": data.get("concept_scope", ""),
        "interactive_candidate": bool(data.get("interactive_candidate", False)),
        "interactive_type": data.get("interactive_type", ""),
        "interactive_content": data.get("interactive_content", ""),
        "interactive_rationale": data.get("interactive_rationale", ""),
        "interactive_scope": data.get("interactive_scope", ""),
        "job_aid_candidate": bool(data.get("job_aid_candidate", False)),
        "job_aid_type": data.get("job_aid_type", ""),
        "job_aid_description": data.get("job_aid_description", ""),
        "job_aid_source_reference": data.get("job_aid_source_reference", "N/A"),
    }
    return fields, ti, to, dropped, model, escalation


def build_digest(day: Dict[str, Any], units: List[Dict[str, Any]], tenant_cfg,
                 model: Optional[str] = None, client_id: str = "", block: str = "",
                 budget: Optional[Dict[str, int]] = None, map_guidance: str = "",
                 references: Any = None) -> Dict[str, Any]:
    """Build one per-day digest. Per-day failures are isolated (digest_status=failed),
    never propagated, so one bad day can't sink the block build.

    Digests are ALWAYS built for the instructor audience: block-wide CDD/Blueprint
    are instructor-facing deliverables (§8.4), so instructor-only guide text is
    legitimate digest material. Student-facing day-scoped retrieval applies its own
    audience gate at read time on the raw units (see day_scoped), and never reuses
    an instructor digest. Keeping this fixed means the digest cache/id can't collide
    or be reused across audiences."""
    from services.pipeline.common import call_llm, safe_json

    model = model or tenant_cfg.pipeline.models.digest_extraction
    dn = day.get("day_number")

    # Coverage bookkeeping from ALL units (visibility-independent). "calendar" is
    # checked against the day ROW itself, not a unit — calendar days are never
    # ingested as content_units with unit_type=="calendar_day", so keying this off
    # `present` (as the other three sources are) made it permanently "missing".
    present = {_UNIT_TO_SOURCE.get(u.get("unit_type")) for u in units}
    availability = {s: ("present" if s in present else "missing")
                    for s in TRACKED_SOURCES if s != "calendar"}
    availability["calendar"] = "present" if (day.get("source_text") or "").strip() else "missing"
    acs = sorted({c for u in units for c in _acs_codes(u)}, key=attribution.acs_sort_key)
    substantive = [u for u in units if u.get("unit_type") in SUBSTANTIVE_UNIT_TYPES]
    has_visual_asset = any(u.get("unit_type") in _VISUAL_ASSET_UNIT_TYPES for u in units)
    # The handbook passages this day's calendar row assigns, already resolved by
    # ENUMERATE. Only the units whose text may actually reach the prompt count —
    # a reading that resolves entirely to withheld material fed the extractor
    # nothing, and calling such a day "not thin" would be the same lie in reverse.
    reading_units = [u for u in (list(getattr(references, "units", None) or []))
                     if text_allowed_for_digest(u, "instructor")]
    reading_files = []
    for u in reading_units:
        name = str((u.get("metadata_json") or {}).get("source_file_name") or "")
        if name and name not in reading_files:
            reading_files.append(name)

    digest: Dict[str, Any] = {
        "digest_id": f"{client_id}:{block}:day{dn}",
        "client_id": client_id,
        "block": block,
        "day_number": dn,
        "topic": day.get("topic"),
        "lesson_title": day.get("lesson_title"),
        "acs_codes": acs,
        "source_availability": availability,
        "storyline_source_asset_status": "AVAILABLE" if has_visual_asset else "NEEDS NEW ART",
        "unit_ids": [u.get("content_unit_id") for u in units],
        "digest_schema_version": DIGEST_SCHEMA_VERSION,
        # The EFFECTIVE version (base + template content hash), not the bare base
        # marker. Recording the base alone made a stored digest's provenance
        # unreadable: a v6-era digest and one built from a since-edited template
        # both claimed "map-v6", so the only way to tell which prompt produced a
        # given digest was to recompute its cache_key. Diagnosing a stale-digest
        # incident is exactly when this field is needed.
        "prompt_version": current_prompt_version(),
        "extractor_model": model,
        # Reference units are folded into the SAME hash as the day's own: a day
        # whose assigned reading resolves differently (a handbook ingested, a
        # chapter now locatable, a corrected citation) is a different extraction
        # and must not be served from the cache built before it.
        "cache_key": cache_key(dn, units, model, day_meta=day_signature(day),
                               map_guidance=map_guidance,
                               reference_units=list(getattr(references, "units", None) or [])),
        "map_guidance_applied": bool((map_guidance or "").strip()),
        "digest_status": "ok",
        "review_flags": [],
        # Provenance for the assigned reading, so the deliverable can name the
        # handbook a reading-only day was actually taught from instead of
        # rendering an em dash under "Source Files" for a day whose digest was
        # built from 70k characters of chapter text.
        "assigned_reading": {
            "label": (getattr(references, "label", lambda: "")() or "") if references else "",
            "unit_count": len(reading_units),
            "files": reading_files,
        },
    }

    # The LLM sees only text-allowed units; metadata above already used all units.
    # Fixed instructor audience (see docstring) — never student.
    llm_units = [u for u in units if text_allowed_for_digest(u, "instructor")]
    digest["text_withheld_units"] = len(units) - len(llm_units)
    try:
        fields, ti, to, dropped, model_used, escalation = _llm_extract(
            day, llm_units, model, call_llm, safe_json, map_guidance=map_guidance,
            references=references)
        digest.update(fields)
        # Record the model that actually ran, not the one configured — an escalated
        # day is a different extraction and the cache key is keyed on the model, so
        # the digest must say which one produced it.
        digest["extractor_model"] = model_used
        if escalation:
            digest["review_flags"].append(f"MODEL_ESCALATED — {escalation}")
        if dropped:
            digest["review_flags"].append(
                f"SOURCES_TRUNCATED — day exceeded the extractor input budget; "
                f"{dropped['units']} lowest-confidence unit(s) omitted from extraction"
            )
        if budget is not None:
            budget["calls"] = budget.get("calls", 0) + 1
            budget["tok_in"] = budget.get("tok_in", 0) + ti
            budget["tok_out"] = budget.get("tok_out", 0) + to
    except Exception as exc:  # per-day failure isolation (§8.3)
        log.warning("digest MAP failed for day %s: %s", dn, exc)
        digest["digest_status"] = "failed"
        # A provider failure now arrives as LLMCallFailed carrying the real AWS
        # exception, so the stored error names the actual cause (e.g.
        # "ReadTimeoutError") instead of describing the fallback stub's shape.
        digest["error"] = str(exc)
        from services.pipeline.common import LLMCallFailed
        if isinstance(exc, LLMCallFailed):
            # An attempt was made and, for a read timeout, the model generated and AWS
            # billed it — so count the call even though no token counts came back.
            # Reporting zero calls here is what made prod's build look like it never
            # contacted a model.
            if budget is not None:
                budget["calls"] = budget.get("calls", 0) + 1
            if exc.model:
                digest["extractor_model"] = exc.model
        # The success path's counter above is only reached when _llm_extract
        # RETURNS, so before this every failed day contributed zero calls and zero
        # tokens to the build report — prod's 2026-08-13 Block 2 build reported
        # map_calls=0 while all 20 days had made a real, billed Bedrock call, and
        # the cost dashboard recorded $0 for it. Only MapExtractionError knows a
        # call actually happened; anything else raised before or around the call
        # with no usable counts, and inventing numbers for it would be worse than
        # reporting none.
        if isinstance(exc, MapExtractionError):
            if budget is not None:
                budget["calls"] = budget.get("calls", 0) + 1
                budget["tok_in"] = budget.get("tok_in", 0) + exc.tokens_in
                budget["tok_out"] = budget.get("tok_out", 0) + exc.tokens_out
            # The model that actually ran, which escalation may have changed from
            # the configured one. On the success path this is recorded from
            # model_used; a failed day needs it for the same reason and is exactly
            # the case where "which model was this?" is the question being asked.
            if exc.model:
                digest["extractor_model"] = exc.model

    # Structural review flags.
    #
    # Assigned reading counts as material. It is not one of the block's own units
    # — it is a handbook passage the calendar names for this day — but it is real
    # ingested text that reached the extractor, and every consumer of THIN_DAY
    # treats the flag as "this day has nothing to teach from". Omitting reading
    # here is what made a live Block 9 build report 18 of 20 days thin while
    # ENUMERATE, which DOES account for reading, flagged only one: the two halves
    # of the same judgment disagreed, and the workbook rendered the wrong one into
    # High Risk Days, Production Readiness and an Academian Question on every one
    # of those days asking whether source material was missing.
    if not substantive and not reading_units:
        digest["review_flags"].append("THIN_DAY — calendar row only")
    # A day with ANY real ingested unit (even one typed outside the "substantive"
    # set above, e.g. a generic 'chunk' unit — the same criterion the Source Files
    # Today column uses) shouldn't raise a reviewer question implying the day has
    # no material; only flag it when there's truly nothing at all. Caught live:
    # using the narrower `substantive` set here first under-suppressed — several
    # Block 2 review days had real files ingested as 'chunk' (outside that set),
    # so "does slide material exist?" still fired despite files already showing
    # in Source Files Today. Matching that column's own criterion exactly (any
    # non-calendar_day unit) is what actually reflects what the user can see.
    has_other_units = any(u.get("unit_type") != "calendar_day" for u in units)
    for s in ("slide", "guide_section"):
        if availability.get(s) == "missing" and not (has_other_units or reading_units):
            digest["review_flags"].append(f"MISSING_SOURCE — {s}")

    return digest

