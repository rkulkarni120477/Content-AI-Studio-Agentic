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
import textwrap
from typing import Any, Dict, List, Optional

from services.digests import attribution

log = logging.getLogger(__name__)

DIGEST_SCHEMA_VERSION = "v6"  # bumped: added concept_type_explanation, interactive_scope,
# job_aid_source_reference (LLM fields) and storyline_source_asset_status (mechanical, from
# unit types) to close the AIM-reference Day-by-Day Map column gap. Same cache-invalidation
# requirement as every prior bump here: this is a structural change to what a digest CONTAINS,
# and cache_key doesn't know that on its own.
PROMPT_VERSION = "map-v5"  # bumped: _llm_extract now accepts an optional map_guidance block
# (see resolve_prompt_guidance in promptops_app/services/prompt_guidance.py) — judgment/
# emphasis instructions distilled from the course's selected CDD/Blueprint prompt, appended
# AFTER the fixed schema/rubric below. cache_key now folds map_guidance's own text into the
# hash (see cache_key), so this bump is a belt-and-suspenders marker, not the only thing
# invalidating old cache entries.
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


def cache_key(day_number: int, units: List[Dict[str, Any]], model: str,
              schema_version: str = DIGEST_SCHEMA_VERSION,
              prompt_version: str = PROMPT_VERSION,
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
    """
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


def _llm_extract(day: Dict[str, Any], llm_units: List[Dict[str, Any]], model: str,
                 call_llm, safe_json, map_guidance: str = "") -> tuple[Dict[str, Any], int, int]:
    """Call the extractor and return (fields, tokens_in, tokens_out). Never raises
    for empty output — parse failures degrade to empty fields.

    ``map_guidance`` (optional) is judgment/emphasis instructions distilled from
    the course's selected CDD/Blueprint prompt (see resolve_prompt_guidance) —
    appended AFTER the fixed schema/rubric below, never allowed to redefine it;
    the model is told explicitly it may only refine judgment within the fields
    already specified, not add a field or contradict the required JSON shape.
    """
    body = "\n\n".join(
        f"[{u.get('unit_type')}] {u.get('title') or ''}\n{(u.get('text_content') or '')[:4000]}"
        for u in llm_units
    )
    prompt = textwrap.dedent(f"""\
        You are extracting a compact JSON digest of ONE course day for a Block
        Blueprint / Course Design Document. Extract ONLY these fields. Do not invent
        a source type that is absent. Be concise. If a field genuinely doesn't
        apply (e.g. no good interactive/job-aid opportunity, or no source page can
        be identified), use the "no"/false/"N/A" values shown — do not force one
        that isn't warranted by the sources.

        List fields (misconceptions, salient_excerpts) must be a genuine JSON array
        — use [] (empty) when the day has nothing to document (e.g. an assessment or
        pure-review day has no new misconceptions to list). NEVER put a placeholder
        string like "no", "N/A", or "none" INSIDE the array as if it were a real
        item — that renders as a literal, wrong-looking item downstream instead of
        the clean "none documented" an empty array produces.

        Respond with ONLY the JSON object below — no preamble, no explanation,
        no markdown fence, nothing before or after it.

        {{"derived_objective": str, "misconceptions": [str],
        "salient_excerpts": [str], "concept_type": str, "concept_type_explanation": str,
        "interactive_candidate": bool, "interactive_type": str,
        "interactive_content": str, "interactive_rationale": str, "interactive_scope": str,
        "job_aid_candidate": bool, "job_aid_type": str, "job_aid_description": str,
        "job_aid_source_reference": str}}

        concept_type is one of, or a combination of, the following — pick whichever
        single label fits best, or combine two with " + " and append " (Mixed)" when
        a day genuinely blends two of them (e.g. "Conceptual + Skill (Mixed)" for a
        day that introduces new understanding AND opens hands-on practice of it the
        same day):
          - Conceptual: first-encounter understanding — explaining what something IS,
            why it matters, how pieces relate, or HOW a tool/technique/process is used
            or its operating principles. This still applies even when the SOURCES
            describe usage/procedure in prose — being TAUGHT ABOUT how something is
            done is Conceptual, not Procedural/Skill, unless the day ALSO has the
            learner actually doing it (a bench task, project activity, lab/hangar
            exercise). No hands-on practice yet.
          - Procedural: a sequence of steps/actions the LEARNER THEMSELVES follows to
            do a task THIS day (not merely reading a description of the steps).
          - Skill: hands-on practice/application BY THE LEARNER of something already
            introduced — requires an actual bench task, project, or exercise in the
            SOURCES this day, not just explanatory description of what skilled use
            looks like.
          - Factual: discrete facts, definitions, or terminology to recall, with no
            process or unifying framework tying them together.
          - Metacognitive: reflection on one's own learning/strategy — review,
            self-assessment, or planning how to approach material, not new subject
            content itself (e.g. a review day with no graded assessment).
          - Summative Assessment: the day's PURPOSE is administering a graded
            final/cumulative/block-ending exam — not new instruction and not
            reflection/review. Use this, not Metacognitive, when the SOURCES show an
            actual graded test happening this day.
        concept_type_explanation is one sentence grounding that choice in what the
        SOURCES below actually contain — never a generic restatement of the label. If
        you pick Procedural or Skill, name the specific hands-on task/project/exercise
        from the SOURCES that justifies it, not just the topic being discussed.

        interactive_scope is a short phrase naming what the interactive would cover
        (e.g. "labeling the parts of a title block"); "" when interactive_candidate
        is false.
        job_aid_source_reference names the specific source file or handbook
        chapter/page (from the SOURCES below) that grounds the job aid content;
        "N/A" when job_aid_candidate is false or no such source is identifiable —
        never invent a citation that isn't in the SOURCES.
        interactive_type/job_aid_type/interactive_content/job_aid_description are
        "" when their *_candidate is false.
        """)
    # Built as a separate dedented block, not interpolated inline above: the
    # guidance text's own lines carry no leading whitespace, and mixing that
    # into the middle of the dedented f-string above would confuse textwrap.
    # dedent's common-prefix calculation for the WHOLE prompt (it would stop
    # stripping the schema block's indentation too, for every call, not just
    # ones with guidance). `or "\n"`: _guidance_block already ends the schema
    # block's own paragraph, so an empty guidance must still supply the blank
    # line the two dedented blocks used to be separated by in one f-string —
    # caught by actually executing this and diffing the assembled prompt,
    # not just reading the source; a bare `prompt += _guidance_block(...)`
    # silently dropped that blank line whenever there was no guidance.
    prompt += _guidance_block(map_guidance) or "\n"
    prompt += textwrap.dedent(f"""\
        DAY {day.get('day_number')}: {day.get('topic') or ''}
        LESSON: {day.get('lesson_title') or ''}
        SOURCES:
        {body if body.strip() else '(no text-allowed source units for this day)'}
        """)
    text, ti, to = call_llm(model, prompt, MAP_MAX_TOKENS)
    data = safe_json(text) or {}
    if not data or "derived_objective" not in data or "concept_type" not in data:
        # safe_json's json.loads is strict — either the raw response didn't
        # parse at all (likely truncated mid-JSON for content-rich days) or it
        # parsed into an unrelated shape (e.g. call_llm's exception-path
        # fallback '{"doc_type":"other",...}', which IS valid JSON but has none
        # of our keys). Either way every field below silently defaults with
        # digest_status staying "ok" — log the raw text so the actual failure
        # mode is visible.
        log.warning(
            "digest MAP day %s: response missing expected keys (parsed=%r), "
            "raw_text=%r", day.get("day_number"), bool(data), text[:2000],
        )
    fields = {
        "derived_objective": data.get("derived_objective", ""),
        "misconceptions": data.get("misconceptions", []) or [],
        "salient_excerpts": data.get("salient_excerpts", []) or [],
        "concept_type": data.get("concept_type", "Unknown"),
        "concept_type_explanation": data.get("concept_type_explanation", ""),
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
    return fields, ti, to


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
        "prompt_version": PROMPT_VERSION,
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
        fields, ti, to = _llm_extract(day, llm_units, model, call_llm, safe_json, map_guidance=map_guidance)
        digest.update(fields)
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

