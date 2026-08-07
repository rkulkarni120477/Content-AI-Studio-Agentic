"""Block-wide REDUCE + VERIFY for CDD / Blueprint (digest-tier pipeline).

Replaces the single-call "stuff the whole block in one prompt" path. Consumes the
DIS ENUMERATE summary + per-day digests (plain JSON, so this stays decoupled from
the DIS backend) and produces:

  * a **deterministic** day-anchored table skeleton — one row per enumerated day,
    built in code so row completeness is structural, not left to the model; the
    LLM only fills the narrative cell of each row (batched, D6), and
  * a **CoverageReport** (VERIFY, pure code): missing days, declared-vs-covered
    ACS orphans, failed digests, thin days, unattributed units.

The quality tier (D2) selects only the REDUCE model; MAP/digests are shared.

The LLM transport is injectable (``llm(model_choice, system, user) -> str``) so the
whole reduce+verify path is unit-testable offline; the default wraps
``llm_service.generate_with_metadata``.
"""
from __future__ import annotations

import json
import logging
import re
import textwrap
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from promptops_app.core.models import resolve_tier

log = logging.getLogger(__name__)

LlmFn = Callable[[str, str, str], str]  # (model_choice, system, user) -> text

DEFAULT_BATCH_SIZE = 10

_ACS_SEGMENT_RE = re.compile(r"^([A-Za-z]*)(\d*)$")


def _acs_sort_key(code: str):
    """Natural sort per dot-separated ACS-code segment (e.g. 'AM.I.G.K10' vs
    'AM.I.G.K2') — a plain string sort orders K10-K19 before K2 since '1' < '2'
    character-wise. Mirrors services.digests.attribution.acs_sort_key on the DIS
    side (dis_backend and promptops_app are separate deployables with no shared
    import path, so this is intentionally duplicated rather than cross-imported)."""
    key = []
    for segment in (code or "").split("."):
        m = _ACS_SEGMENT_RE.match(segment)
        letters, digits = m.groups() if m else (segment, "")
        key.append((letters, int(digits) if digits else -1))
    return key

_DEFAULT_SYSTEM = {
    "cdd": (
        "You are an expert instructional designer writing a Course Design Document "
        "for an instructor-facing audience. For each course day you are given "
        "structured facts (topic, ACS codes, concept type, source availability, a "
        "derived objective). Write a concise, faithful narrative cell for each day. "
        "Do NOT invent facts, ACS codes, or sources beyond those given. If a day's "
        "digest_status is 'failed' or sources are missing, say so plainly and write "
        "'REVIEW NEEDED'."
    ),
    "blueprint": (
        "You are an expert curriculum architect filling a Block Blueprint worksheet "
        "for an instructor-facing audience. For each course day you are given "
        "structured facts. Write a concise, faithful 1-2 sentence blueprint cell per "
        "day. Never add facts beyond those given. If digest_status is 'failed', "
        "output 'REVIEW NEEDED'."
    ),
}

_TEMPLATE_NAME = {"cdd": "cdd_reduce", "blueprint": "blueprint_reduce_worksheet"}


@dataclass
class CoverageReport:
    deliverable: str
    tier: str
    total_days: int = 0
    enumerated_days: int = 0
    days_in_output: int = 0
    missing_days: List[int] = field(default_factory=list)
    declared_acs: List[str] = field(default_factory=list)
    covered_acs: List[str] = field(default_factory=list)
    orphan_acs: List[str] = field(default_factory=list)
    failed_days: List[int] = field(default_factory=list)
    thin_days: List[int] = field(default_factory=list)
    enumerate_flags: List[str] = field(default_factory=list)
    complete: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReduceResult:
    deliverable: str
    tier: str
    reduce_model: str
    max_output_tokens: int
    sections: List[Dict[str, Any]]
    coverage: Dict[str, Any]
    llm_calls: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _guidance_block(map_guidance: str) -> str:
    """Render optional prompt-derived guidance as a clearly-delimited,
    contract-safe addendum to the REDUCE narrative-fill prompt — "" when there
    is none, so a call with no guidance produces byte-identical prompt text to
    before this feature existed. Mirrors dis_backend/services/digests/mapper.
    py's own ``_guidance_block`` (duplicated, not imported — dis_backend and
    promptops_app are separate deployables with no shared import path, same
    reason _acs_sort_key above is a local copy)."""
    text = (map_guidance or "").strip()
    if not text:
        return ""
    return (
        "\n\nADDITIONAL GENERATION GUIDANCE (derived from the course's selected "
        "prompt template — apply while filling the fields requested above; this "
        "refines judgment/emphasis ONLY, it must never add a field not requested "
        "above or contradict any instruction above):\n" + text
    )


def _safe_json(text: str) -> Any:
    try:
        clean = (text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)
    except Exception:
        return None


class BlockWideGenerator:
    def __init__(self, llm: Optional[LlmFn] = None, batch_size: int = DEFAULT_BATCH_SIZE):
        self._llm = llm
        self.batch_size = max(1, batch_size)
        self._max_tokens: Optional[int] = None   # set per reduce() from the tier

    # -- LLM seam -------------------------------------------------------------
    def _call(self, model_choice: str, system: str, user: str) -> str:
        if self._llm is not None:
            return self._llm(model_choice, system, user)
        # Lazy default: the real reliability layer (retry/fallback + usage log).
        # Pass the tier's output-token headroom so long sectioned reduces don't
        # truncate at the historical flat cap (D6).
        from promptops_app.services.llm_service import generate_with_metadata
        result = generate_with_metadata(model_choice, system, user, max_tokens=self._max_tokens)
        # generate_with_metadata never raises — on total failure (after retry +
        # fallback) it returns status="error" with a human error string as .text.
        # If we didn't check, that string would become "narrative" (or fail JSON
        # parse → every row REVIEW NEEDED) and the caller would persist an empty
        # deliverable AS SUCCESS. Raise so _build_and_reduce degrades to legacy
        # generation / marks the job failed instead.
        if getattr(result, "status", None) == "error":
            raise RuntimeError(getattr(result, "text", None) or "LLM reduce call failed")
        return getattr(result, "text", "") or ""

    def _system(self, deliverable: str) -> str:
        # Prefer the editable template (AIM domain/style layer, §5.3); fall back to
        # the built-in so generation never breaks on a missing/DB-less template.
        try:
            from promptops_app.prompts.prompt_loader import load_template
            tpl = load_template(_TEMPLATE_NAME.get(deliverable, "cdd_reduce"))
            system = getattr(tpl, "system_template", None)
            if system and str(system).strip():
                return str(system)
        except Exception as exc:
            log.debug("reduce template load failed (%s); using built-in system", exc)
        return _DEFAULT_SYSTEM.get(deliverable, _DEFAULT_SYSTEM["cdd"])

    # -- public API -----------------------------------------------------------
    def reduce(self, enumerate_summary: Dict[str, Any], digests: List[Dict[str, Any]],
               deliverable: str = "cdd", tier: str | None = None,
               block_overview: Optional[Dict[str, Any]] = None,
               source_file_inventory: Optional[List[Dict[str, Any]]] = None,
               acs_registry: Optional[List[Dict[str, Any]]] = None,
               map_guidance: str = "") -> ReduceResult:
        """``map_guidance`` (optional) is judgment/emphasis instructions distilled
        from the course's selected CDD/Blueprint prompt (see
        promptops_app.services.prompt_guidance.resolve_prompt_guidance) — folded
        into the narrative-fill call below. "" (the default) reproduces this
        method's exact pre-existing behavior."""
        deliverable = deliverable if deliverable in _DEFAULT_SYSTEM else "cdd"
        tm = resolve_tier(tier)
        by_day = {d.get("day_number"): d for d in digests}
        days = enumerate_summary.get("days", [])

        rows = [self._skeleton_row(day, by_day.get(day.get("day_number"))) for day in days]

        self._max_tokens = tm.max_output_tokens
        system = self._system(deliverable)
        llm_calls = self._fill_narratives(rows, deliverable, system, tm.reduce_model,
                                          block_overview, map_guidance)

        coverage = self.verify(rows, enumerate_summary, deliverable, tm.tier)
        sections = [
            {"key": "block_overview", "title": "BLOCK OVERVIEW", "fields": block_overview or {}},
            {"key": "source_file_inventory", "title": "SOURCE FILE INVENTORY", "rows": source_file_inventory or []},
            {"key": "acs_registry", "title": "ACS CODE REGISTRY", "rows": acs_registry or []},
            {"key": "day_table",
             "title": f"{deliverable.upper()} — day-by-day ({coverage.enumerated_days} days)",
             "rows": rows},
        ]
        notes_calls = 0
        try:
            notes_fields, notes_calls = self._patterns_notes(rows, coverage, tm.reduce_model, map_guidance)
            sections.append({"key": "patterns_notes", "title": "PATTERNS & DESIGN NOTES", "fields": notes_fields})
        except Exception as exc:
            # Best-effort synthesis — a failure here must never sink the day
            # table/coverage that's already been built; the worksheet is just
            # omitted from this run.
            log.warning("patterns_notes synthesis failed (%s); worksheet omitted", exc)

        return ReduceResult(
            deliverable=deliverable, tier=tm.tier, reduce_model=tm.reduce_model,
            max_output_tokens=tm.max_output_tokens, sections=sections,
            coverage=coverage.to_dict(), llm_calls=llm_calls + notes_calls,
        )

    def _patterns_notes(self, rows: List[Dict[str, Any]], coverage: "CoverageReport",
                        model_choice: str, map_guidance: str = "") -> tuple[Dict[str, str], int]:
        """Worksheet 5 synthesis. Every field with a single correct answer (learn-
        while-doing days, handbook edition conflicts, high-risk days) is a CODE
        conclusion, not an LLM judgment call — a model asked to "phrase" a fact can
        still second-guess it (seen live: given two distinct handbook editions in
        the facts, one run's prose concluded "no conflicts detected" anyway). The
        LLM is only asked for the two fields that are genuinely open-ended prose
        synthesis with no single correct answer: content_arc_summary and
        production_readiness.

        ``map_guidance`` (optional, same source as _fill_narratives's) is appended
        AFTER the dedented prompt below, not interpolated inside the f-string —
        the FACTS section already interpolates raw json.dumps() output whose own
        indentation doesn't match the template's, so building the guidance segment
        as a separate, non-dedented append avoids compounding that with a second
        unrelated block of non-indented text landing mid-template."""
        learn_while_doing = sorted(r["day_number"] for r in rows if r.get("projects_today"))

        editions_by_day = {r["day_number"]: r["handbook_reference"] for r in rows if r.get("handbook_reference")}
        seen_editions: Dict[str, List[int]] = {}
        for dn in sorted(editions_by_day):
            # Generic FAA handbook code, not hardcoded to "8083" — the earlier
            # version of this regex only matched AM's own handbook series, so any
            # block citing a different handbook fell back to the raw citation
            # text as the "edition" key, meaning two different page ranges of the
            # SAME handbook (which real citations often are) would be
            # miscounted as two different editions.
            m = re.search(r"(FAA-H-\d{4}-\d+[A-Z]?)", editions_by_day[dn])
            edition = m.group(1) if m else editions_by_day[dn]
            seen_editions.setdefault(edition, []).append(dn)
        has_conflict = len(seen_editions) > 1
        edition_summary = "; ".join(f"{ed} on days {','.join(map(str, ds))}" for ed, ds in seen_editions.items())

        high_risk = coverage.thin_days + coverage.failed_days
        code_fields = {
            "high_risk_days": (f"Days {', '.join(map(str, sorted(set(high_risk))))} — thin/failed digest coverage."
                               if high_risk else "No high-risk days detected."),
            "learn_while_doing_days": (f"Days {', '.join(map(str, learn_while_doing))} open a project the same day "
                                       f"new content is introduced ({len(learn_while_doing)} of "
                                       f"{coverage.enumerated_days} days).") if learn_while_doing
                                       else "No learn-while-doing days detected.",
            "handbook_edition_conflicts": (f"CONFLICT: multiple handbook editions cited — {edition_summary}. "
                                           "Verify with SME which edition/volume is actually intended."
                                           if has_conflict else f"No edition conflicts detected. {edition_summary}."),
        }

        facts = {
            "total_days": coverage.total_days,
            "enumerated_days": coverage.enumerated_days,
            "missing_days": coverage.missing_days,
            "failed_days": coverage.failed_days,
            "thin_days": coverage.thin_days,
            "orphan_acs": coverage.orphan_acs,
            **code_fields,
        }
        prompt = textwrap.dedent(f"""\
            You are writing the "Content Arc Summary" and "Production Readiness"
            fields of a Block Blueprint's Patterns & Design Notes worksheet, from
            VERIFIED structural facts only — do not add any fact not given below,
            and do not contradict any of them. Respond with ONLY this JSON object,
            no preamble:

            {{"content_arc_summary": str, "production_readiness": str}}

            FACTS:
            {json.dumps(facts, indent=2)}
            """)
        prompt += _guidance_block(map_guidance)
        text = self._call(model_choice, "You write faithful, fact-grounded prose. Never invent or contradict a given fact.", prompt)
        data = _safe_json(text) or {}
        fields = {
            "content_arc_summary": data.get("content_arc_summary", "") or "NOT AVAILABLE",
            **code_fields,
            "production_readiness": data.get("production_readiness", "") or "NOT AVAILABLE",
        }
        return fields, 1

    @staticmethod
    def _academian_questions(review_flags: List[str]) -> str:
        """Render existing review flags as reviewer-facing questions (AIM's own
        sample uses this column for flagged anomalies, e.g. "The ACS code for the
        project doesn't match..." on Day 18) — templated from real flags already
        computed by ENUMERATE/mapper, never a new fabricated observation."""
        questions = []
        for flag in review_flags:
            if flag.startswith("MISSING_SOURCE"):
                source = flag.split("—", 1)[-1].strip() if "—" in flag else flag
                questions.append(f"Can you confirm whether {source} material exists for this day, or should it be sourced?")
            elif flag.startswith("THIN_DAY"):
                questions.append("This day has no substantive source content — is that expected (e.g. a review/consolidation day), or is source material missing?")
        return " ".join(questions)

    def _skeleton_row(self, day: Dict[str, Any], digest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        dn = day.get("day_number")
        digest = digest or {}
        # ACS: prefer the digest's, else the enumerate day's (both metadata-derived).
        acs = digest.get("acs_codes") or day.get("acs_codes") or []
        # Some calendar days genuinely carry no topic text by design (e.g. an
        # application/consolidation day with no new lecture content) — fall back
        # to the lesson title so the cell shows the day's real identity instead
        # of going blank; the narrative cell (LLM-derived from real day units)
        # already conveys that it's a consolidation/application day.
        topic = digest.get("topic") or day.get("topic") or day.get("lesson_title") or ""
        projects_today = list(day.get("projects_today", []) or [])
        return {
            "day_number": dn,                                  # structural anchor
            "topic": topic,
            "acs_codes": acs,
            "concept_type": digest.get("concept_type", "Unknown"),
            "concept_type_explanation": digest.get("concept_type_explanation", ""),
            "source_availability": digest.get("source_availability", {}),
            "review_flags": list(digest.get("review_flags", []) or []),
            "digest_status": digest.get("digest_status", "missing"),
            "derived_objective": digest.get("derived_objective", ""),
            "misconceptions": list(digest.get("misconceptions", []) or []),
            "projects_today": projects_today,
            "assessment_today": list(day.get("assessment_today", []) or []),
            "handbook_reference": day.get("handbook_reference", ""),
            "handbook_edition": day.get("handbook_edition", ""),
            "source_files_today": list(day.get("source_files_today", []) or []),
            "hangar_activity_today": list(day.get("hangar_activity_today", []) or []),
            # Mechanical, not an LLM judgment call — reuses the same criterion
            # verify()/_patterns_notes already uses at the block level ("a project
            # opens the same day new content is introduced"), surfaced per-day here.
            "learn_while_doing": bool(projects_today),
            "interactive_candidate": bool(digest.get("interactive_candidate", False)),
            "interactive_type": digest.get("interactive_type", ""),
            "interactive_content": digest.get("interactive_content", ""),
            "interactive_rationale": digest.get("interactive_rationale", ""),
            "interactive_scope": digest.get("interactive_scope", ""),
            "storyline_source_asset_status": digest.get("storyline_source_asset_status", "NEEDS NEW ART"),
            "job_aid_candidate": bool(digest.get("job_aid_candidate", False)),
            "job_aid_type": digest.get("job_aid_type", ""),
            "job_aid_description": digest.get("job_aid_description", ""),
            "job_aid_source_reference": digest.get("job_aid_source_reference", "N/A"),
            "academian_questions": self._academian_questions(list(digest.get("review_flags", []) or [])),
            "narrative": "",
            "how_it_is_applied": "",
            "learn_while_doing_reason": "",
            "hangar_activity_note": "",
        }

    def _fill_narratives(self, rows: List[Dict[str, Any]], deliverable: str,
                         system: str, model_choice: str,
                         block_overview: Optional[Dict[str, Any]] = None,
                         map_guidance: str = "") -> int:
        """Batch rows and ask the LLM for a narrative + how-it's-applied cell per
        day. Any row the LLM omits (or a failed/parse error) degrades to
        'REVIEW NEEDED'/a neutral default, never blank — the row still exists
        (structural completeness).

        how_it_is_applied needs cross-day context (e.g. "exercised in Projects
        2-1 through 2-3 on Days 2-4") that a single day's own digest doesn't
        carry — BLOCK_CONTEXT gives every batch a lightweight (day/topic/
        projects) view of the WHOLE block to cross-reference against, still as
        one call per batch (no new LLM call added).

        learn_while_doing_reason and hangar_activity_note are the same idea
        applied to two more Day-by-Day Map cells that used to be purely
        mechanical (bare "Yes"/"No", a raw joined filename list) — flat next
        to the AIM reference's reasoned prose for the same cells. The
        underlying FACT stays code-computed (learn_while_doing's boolean,
        hangar_activity_today's file list) and is handed to the LLM read-only
        in the payload below; the LLM is only asked to phrase/explain it, the
        same "code concludes, LLM phrases" split _patterns_notes already uses
        — it cannot flip Yes/No or invent a hangar activity that isn't listed.

        objective_block_framing and cross_day_misconception_note extend this
        further to derived_objective/misconceptions themselves — the AIM
        reference constantly connects a day's objective/misconceptions to
        block-level stakes (grading policy, ACS subjects) or an earlier/later
        day (e.g. a Day 5 note pointing back to Day 1's concept), which a
        single day's own per-day MAP call is structurally blind to. Unlike
        learn_while_doing/hangar_activity there is no fixed code-computed fact
        to protect here — derived_objective/misconceptions are themselves
        already LLM prose from the per-day digest — so this is APPEND-only:
        the LLM returns a short addition, and code appends it to the existing
        value; it can never replace, reword, or drop what the per-day digest
        already produced. block_facts (from block_overview, when available)
        gives it real block-level grounding instead of inventing one."""
        calls = 0
        block_context = [{"day_number": r["day_number"], "topic": r["topic"],
                          "projects_today": r["projects_today"]} for r in rows]
        block_overview = block_overview or {}
        block_facts = {
            "acs_subjects_covered": block_overview.get("acs_subjects_covered"),
            "grading_policy": block_overview.get("grading_policy"),
            "total_days": block_overview.get("total_days"),
        }
        for i in range(0, len(rows), self.batch_size):
            batch = rows[i:i + self.batch_size]
            payload = [{
                "day_number": r["day_number"],
                "topic": r["topic"],
                "acs_codes": r["acs_codes"],
                "concept_type": r["concept_type"],
                "derived_objective": r["derived_objective"],
                "misconceptions": r["misconceptions"],
                "availability": r["source_availability"],
                "digest_status": r["digest_status"],
                "learn_while_doing": r["learn_while_doing"],
                "hangar_activity_today": r["hangar_activity_today"],
            } for r in batch]
            user = (
                "Return ONLY a JSON object mapping each day_number (as a string) to an "
                'object {"narrative": str, "how_it_is_applied": str, '
                '"learn_while_doing_reason": str, "hangar_activity_note": str, '
                '"objective_block_framing": str, "cross_day_misconception_note": str}. '
                "narrative is a 1-2 sentence summary of the day. how_it_is_applied is "
                "one sentence on how THIS day's content connects to work on OTHER days "
                "(e.g. a specific project/activity on a later day that exercises it) — "
                "use BLOCK_CONTEXT below to find that connection; if none is evident, "
                'say "Not directly exercised elsewhere in this block." rather than '
                "inventing one.\n\n"
                "learn_while_doing_reason justifies the day's ALREADY-DECIDED "
                "learn_while_doing value (true = a project opens this same day; false "
                "= none does) — do not contradict it. If false, name the nearest day "
                "(from BLOCK_CONTEXT) whose project first exercises this day's topic, "
                'e.g. "no project opens this day (Project 2-1 opens Day 2)"; if true, '
                'name the project, e.g. "opens Project 2-1 the same day this content '
                'is introduced." One short clause, no leading Yes/No (that prefix is '
                "added separately).\n\n"
                "hangar_activity_note: if hangar_activity_today is non-empty, one or "
                "two sentences on what that activity likely covers and how it connects "
                "to this day's acs_codes/topic — grounded only in the activity's own "
                "title and this day's known facts, never inventing procedural detail "
                'you cannot see. If hangar_activity_today is empty, use "N/A — no '
                'hangar activity listed for this day."\n\n'
                "objective_block_framing: an OPTIONAL clause to APPEND to this day's "
                "own derived_objective (never replace it) that adds real block-level "
                "stakes from BLOCK_FACTS below — e.g. for a summative-assessment day, "
                'something like "to a 70% or higher standard, per the block\'s grading '
                'policy." Leave "" for an ordinary instructional day where no '
                "block-level framing genuinely adds value — do not force one.\n\n"
                "cross_day_misconception_note: an OPTIONAL single sentence to APPEND "
                "as one more entry in this day's own misconceptions list (never replace "
                "or reword the existing entries) — only when BLOCK_CONTEXT shows a "
                "genuine, specific connection to a concept first introduced on an "
                'earlier day being revisited/tested today, e.g. "Connects to Day 1\'s '
                'drawing revision-control concept, reinforced here." Leave "" when no '
                "such connection is evident, or when this day's own misconceptions list "
                "is empty (e.g. an assessment/review day with nothing to document) — "
                "do not invent a connection to pad an empty list.\n\n"
                f"BLOCK_FACTS (block-level, for framing only — never contradict):\n{json.dumps(block_facts, indent=2)}\n\n"
                f"BLOCK_CONTEXT (all days, for cross-referencing only):\n{json.dumps(block_context, indent=2)}"
                + _guidance_block(map_guidance) +
                "\n\nDAYS TO FILL IN:\n" + json.dumps(payload, indent=2)
            )
            text = self._call(model_choice, system, user)
            calls += 1
            parsed = _safe_json(text)
            mapping = parsed if isinstance(parsed, dict) else {}
            for r in batch:
                fallback_lwd_reason = (
                    f"opens {', '.join(r['projects_today'])} the same day this content is introduced."
                    if r["learn_while_doing"] else "no project opens this day."
                )
                fallback_hangar_note = (
                    ", ".join(r["hangar_activity_today"]) if r["hangar_activity_today"]
                    else "N/A — no hangar activity listed for this day."
                )
                if r["digest_status"] == "failed":
                    r["narrative"] = "REVIEW NEEDED — per-day digest failed."
                    r["how_it_is_applied"] = "N/A — digest failed."
                    r["learn_while_doing_reason"] = fallback_lwd_reason
                    r["hangar_activity_note"] = fallback_hangar_note
                    continue
                cell = mapping.get(str(r["day_number"])) or mapping.get(r["day_number"])
                if isinstance(cell, dict):
                    r["narrative"] = str(cell.get("narrative") or "").strip() or "REVIEW NEEDED — no narrative returned."
                    r["how_it_is_applied"] = (str(cell.get("how_it_is_applied") or "").strip()
                                              or "Not directly exercised elsewhere in this block.")
                    lwd_reason = str(cell.get("learn_while_doing_reason") or "").strip() or fallback_lwd_reason
                    # Consistency guard, caught by adversarial review: the LLM
                    # is instructed not to contradict learn_while_doing, but
                    # nothing enforced that — a model returning the false-case
                    # example phrasing ("no project opens...") for a day where
                    # learn_while_doing is True would ship as a self-
                    # contradicting cell ("Yes — no project opens this day").
                    # Detected textually, not trusted; falls back to the
                    # deterministic reason rather than shipping the
                    # contradiction.
                    if r["learn_while_doing"] and re.match(r"\s*no\s+project", lwd_reason, re.I):
                        lwd_reason = fallback_lwd_reason
                    r["learn_while_doing_reason"] = lwd_reason
                    r["hangar_activity_note"] = str(cell.get("hangar_activity_note") or "").strip() or fallback_hangar_note
                    objective_framing = str(cell.get("objective_block_framing") or "").strip()
                    if objective_framing and r["derived_objective"]:
                        r["derived_objective"] = f"{r['derived_objective']} {objective_framing}".strip()
                    cross_day_note = str(cell.get("cross_day_misconception_note") or "").strip()
                    if cross_day_note and r["misconceptions"]:
                        r["misconceptions"] = list(r["misconceptions"]) + [cross_day_note]
                else:
                    # Backward-compatible with a bare-string response.
                    r["narrative"] = str(cell).strip() if cell else "REVIEW NEEDED — no narrative returned."
                    r["how_it_is_applied"] = "Not directly exercised elsewhere in this block."
                    r["learn_while_doing_reason"] = fallback_lwd_reason
                    r["hangar_activity_note"] = fallback_hangar_note
        return calls

    def verify(self, rows: List[Dict[str, Any]], enumerate_summary: Dict[str, Any],
               deliverable: str, tier: str) -> CoverageReport:
        days = enumerate_summary.get("days", [])
        enumerated_days = enumerate_summary.get("enumerated_days", len(days))
        total_days = enumerate_summary.get("total_days", enumerated_days)
        declared = list(enumerate_summary.get("declared_acs", []) or [])

        row_days = {r["day_number"] for r in rows}
        expected_days = {d.get("day_number") for d in days}
        missing_days = sorted(d for d in expected_days if d not in row_days)

        covered: set[str] = set()
        failed_days, thin_days = [], []
        for r in rows:
            # A day counts as covered unless its digest actually FAILED. Keying on
            # `== "ok"` under-counted coverage for any other non-failed status DIS
            # might emit (e.g. a future "cached"/"refreshed"), producing phantom
            # orphan_acs and a false `complete=False` even though the day is fine.
            if r["digest_status"] == "failed":
                failed_days.append(r["day_number"])
            else:
                covered.update(r["acs_codes"])
            if any(str(f).startswith("THIN_DAY") for f in r.get("review_flags", [])):
                thin_days.append(r["day_number"])

        orphans = sorted(set(declared) - covered, key=_acs_sort_key)
        rpt = CoverageReport(
            deliverable=deliverable, tier=tier,
            total_days=total_days, enumerated_days=enumerated_days,
            days_in_output=len(rows), missing_days=missing_days,
            declared_acs=sorted(declared, key=_acs_sort_key),
            covered_acs=sorted(covered, key=_acs_sort_key),
            orphan_acs=orphans, failed_days=sorted(failed_days),
            thin_days=sorted(thin_days),
            enumerate_flags=list(enumerate_summary.get("flags", []) or []),
        )
        rpt.complete = (not missing_days and not orphans and not failed_days
                        and enumerated_days == total_days)
        return rpt
