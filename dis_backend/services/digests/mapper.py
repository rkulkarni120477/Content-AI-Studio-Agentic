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
import json
import logging
from typing import Any, Dict, List, Optional

from services.digests import attribution
from services.digests.prompt_template import render as prompt_template_render

log = logging.getLogger(__name__)

DIGEST_SCHEMA_VERSION = "v7"
# Every bump here is a structural change to what a digest CONTAINS, which cache_key
# cannot infer on its own — so the version must move for existing digests to rebuild.
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
    """

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
MAP_MAX_TOKENS = 4096  # raised from 900 (itself raised from 600) — 900 was still an artificial
# ceiling below this model's real limit. aim.yaml's digest_extraction model is Bedrock Claude 3
# Sonnet, whose actual max output is 4096 — matching that gives every field (derived_objective,
# misconceptions, salient_excerpts, concept_type, 7 interactive/job-aid fields) full headroom to
# return complete, untruncated content instead of risking exactly the failure this file's own
# comment on `_llm_extract` already warned about ("likely truncated mid-JSON for content-rich
# days"). Matches the fallback fix in llm_service.py: cap to the model's REAL ceiling, not below.

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
              day_meta: str = "", map_guidance: str = "") -> str:
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
    raw = (f"{schema_version}|{model}|{prompt_version}|day{day_number}|{day_meta}"
           f"|mg:{map_guidance}|{unit_hashes}")
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _est_hash(unit: Dict[str, Any]) -> str:
    return hashlib.sha256((unit.get("text_content") or "").encode()).hexdigest()[:16]


def _acs_codes(unit: Dict[str, Any]) -> List[str]:
    return [c for c in ((unit.get("metadata_json") or {}).get("acs_codes") or []) if c]


def _guidance_block(map_guidance: str) -> str:
    """Render the optional prompt-derived guidance as a clearly-delimited,
    contract-safe addendum — "" (no extra lines) when there is none, so a day
    with no guidance produces byte-identical prompt text to before this
    feature existed."""
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
MAP_MAX_SOURCE_CHARS = 120_000
MAP_MAX_UNIT_CHARS = 4_000

#: Truncation order when a day exceeds the budget: keep the units we are most
#: confident belong to this day. Mirrors attribution's own signal hierarchy
#: (metadata day_number > item cross-reference > filename token > term overlap), so
#: what gets dropped first is what was least certainly this day's material.
_SIGNAL_PRIORITY = {"raw": 0, "S1": 1, "S2": 2, "S3": 3}


def _source_body(llm_units: List[Dict[str, Any]]) -> tuple[str, Dict[str, int]]:
    """Assemble the SOURCES block under MAP_MAX_SOURCE_CHARS.

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
        block = (f"[{u.get('unit_type')}] {u.get('title') or ''}\n"
                 f"{(u.get('text_content') or '')[:MAP_MAX_UNIT_CHARS]}")
        if used + len(block) > MAP_MAX_SOURCE_CHARS and kept:
            dropped_units += 1
            dropped_chars += len(block)
            continue
        kept.append((idx, block))
        used += len(block)
    # Restore the original unit order so the prompt still reads in source order.
    body = "\n\n".join(b for _, b in sorted(kept, key=lambda p: p[0]))
    dropped = {"units": dropped_units, "chars": dropped_chars} if dropped_units else {}
    return body, dropped


def _llm_extract(day: Dict[str, Any], llm_units: List[Dict[str, Any]], model: str,
                 call_llm, safe_json, map_guidance: str = ""
                 ) -> tuple[Dict[str, Any], int, int, Dict[str, int]]:
    """Call the extractor and return (fields, tokens_in, tokens_out, dropped).

    ``dropped`` reports any SOURCES truncation applied to fit MAP_MAX_SOURCE_CHARS.

    ``map_guidance`` (optional) is judgment/emphasis instructions distilled from
    the course's selected CDD/Blueprint prompt (see resolve_prompt_guidance) —
    appended AFTER the fixed schema/rubric below, never allowed to redefine it;
    the model is told explicitly it may only refine judgment within the fields
    already specified, not add a field or contradict the required JSON shape.
    """
    body, dropped = _source_body(llm_units)
    if dropped:
        log.warning("digest MAP day %s: SOURCES truncated to %d chars — dropped %d "
                    "lowest-confidence unit(s) (%d chars) to fit the extractor's "
                    "context window", day.get("day_number"), MAP_MAX_SOURCE_CHARS,
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
    text, ti, to = call_llm(model, prompt, MAP_MAX_TOKENS)
    data = safe_json(text) or {}
    if not data or "derived_objective" not in data or "concept_type" not in data:
        # safe_json's json.loads is strict — either the raw response didn't
        # parse at all (likely truncated mid-JSON for content-rich days) or it
        # parsed into an unrelated shape (e.g. call_llm's exception-path
        # fallback '{"doc_type":"other",...}', which IS valid JSON but has none
        # of our keys).
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
        raise MapExtractionError(
            f"MAP reply for day {day.get('day_number')} lacks the required keys "
            f"(derived_objective/concept_type); got keys={sorted(data)[:8]}"
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
    return fields, ti, to, dropped


def build_digest(day: Dict[str, Any], units: List[Dict[str, Any]], tenant_cfg,
                 model: Optional[str] = None, client_id: str = "", block: str = "",
                 budget: Optional[Dict[str, int]] = None, map_guidance: str = "") -> Dict[str, Any]:
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
        "cache_key": cache_key(dn, units, model, day_meta=day_signature(day), map_guidance=map_guidance),
        "map_guidance_applied": bool((map_guidance or "").strip()),
        "digest_status": "ok",
        "review_flags": [],
    }

    # The LLM sees only text-allowed units; metadata above already used all units.
    # Fixed instructor audience (see docstring) — never student.
    llm_units = [u for u in units if text_allowed_for_digest(u, "instructor")]
    digest["text_withheld_units"] = len(units) - len(llm_units)
    try:
        fields, ti, to, dropped = _llm_extract(day, llm_units, model, call_llm, safe_json,
                                               map_guidance=map_guidance)
        digest.update(fields)
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
        digest["error"] = str(exc)

    # Structural review flags.
    if not substantive:
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
        if availability.get(s) == "missing" and not has_other_units:
            digest["review_flags"].append(f"MISSING_SOURCE — {s}")

    return digest

