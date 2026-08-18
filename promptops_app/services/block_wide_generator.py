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

#: Cell text when a declared column came back without a value for a day. Named rather
#: than blank for the same reason every other default here is: a reviewer cannot tell
#: an empty cell that was answered "nothing" from one the model never returned, and
#: only the second is a defect worth chasing. Module level so ``block_wide_service``'s
#: renderer defaults to the SAME string this stage writes — two copies would drift and
#: a reviewer would meet two different markers for one condition.
EXTENSION_MISSING = "REVIEW NEEDED — not returned"

#: Built-in fallback for the declared-column fill, used when the template file and any
#: DB override are absent or fail their contract. Mirrors
#: templates/extension_columns_reduce.md — the file is the maintained copy.
_DEFAULT_EXTENSION_SYSTEM = (
    "You are filling ADDITIONAL Worksheet 4 columns that this course's selected "
    "Blueprint prompt declared, from the verified per-day facts supplied. Fill each "
    "declared column from those facts and nothing else; you are not given the raw "
    "source documents, so where a column cannot be answered from the facts present "
    "return \"REVIEW NEEDED — not derivable from the day facts\" rather than "
    "inferring. Never contradict or re-decide a fact you are given. One short phrase "
    "or sentence per cell. Return ONLY the JSON object requested."
)

_DEFAULT_EXTENSION_USER = (
    "Return ONLY a JSON object mapping each day_number (as a string) to an object "
    "whose keys are exactly the column labels listed under COLUMNS below, with a "
    "string value for each.\n\n"
    "COLUMNS — each label, then what its cell must contain:\n{{columns}}\n\n"
    "BLOCK_CONTEXT (all days, for cross-referencing only — never a source of new "
    "facts):\n{{block_context}}{{guidance_block}}\n\n"
    "DAYS TO FILL IN:\n{{day_records}}"
)

_TEMPLATE_NAME = {"cdd": "cdd_reduce", "blueprint": "blueprint_reduce_worksheet"}
_PATTERNS_TEMPLATE_NAME = "patterns_notes_reduce"

# ── Built-in USER prompts (last-resort fallback tier) ────────────────────────
# These are the same {{placeholder}} templates shipped in prompts/templates/, kept
# in code as the tier that cannot fail: a deleted/malformed/contract-violating
# template file or DB row degrades to these rather than to a broken generation.
# Both the resolved and the fallback path render through the SAME code below, so
# there is exactly one prompt-assembly path to reason about.
#
# Keep the JSON key names here in sync with reduce_prompts.NARRATIVE_CONTRACT /
# PATTERNS_CONTRACT and with the parsers in _fill_narratives/_patterns_notes.
_DEFAULT_NARRATIVE_USER = (
    'Return ONLY a JSON object mapping each day_number (as a string) to an object '
    '{"narrative": str, "how_it_is_applied": str, "learn_while_doing_reason": str, '
    '"hangar_activity_note": str, "objective_block_framing": str, '
    '"cross_day_misconception_note": str}. narrative is a 1-2 sentence summary of '
    'the day. how_it_is_applied is one sentence on how THIS day\'s content connects '
    'to work on OTHER days (e.g. a specific project/activity on a later day that '
    'exercises it) — use BLOCK_CONTEXT below to find that connection; if none is '
    'evident, say "Not directly exercised elsewhere in this block." rather than '
    'inventing one.\n\n'
    'learn_while_doing_reason justifies the day\'s ALREADY-DECIDED learn_while_doing '
    'value (true = a project opens this same day; false = none does) — do not '
    'contradict it. If false, name the nearest day (from BLOCK_CONTEXT) whose '
    'project first exercises this day\'s topic, e.g. "no project opens this day '
    '(Project 2-1 opens Day 2)"; if true, name the project, e.g. "opens Project 2-1 '
    'the same day this content is introduced." One short clause, no leading Yes/No '
    '(that prefix is added separately).\n\n'
    'hangar_activity_note: if hangar_activity_today is non-empty, one or two '
    'sentences on what that activity likely covers and how it connects to this '
    'day\'s acs_codes/topic — grounded only in the activity\'s own title and this '
    'day\'s known facts, never inventing procedural detail you cannot see. If '
    'hangar_activity_today is empty, use "N/A — no hangar activity listed for this '
    'day."\n\n'
    'objective_block_framing: an OPTIONAL clause to APPEND to this day\'s own '
    'derived_objective (never replace it) that adds real block-level stakes from '
    'BLOCK_FACTS below — e.g. for a summative-assessment day, something like "to a '
    '70% or higher standard, per the block\'s grading policy." Leave "" for an '
    'ordinary instructional day where no block-level framing genuinely adds value — '
    'do not force one.\n\n'
    'cross_day_misconception_note: an OPTIONAL single sentence to APPEND as one more '
    'entry in this day\'s own misconceptions list (never replace or reword the '
    'existing entries) — only when BLOCK_CONTEXT shows a genuine, specific '
    'connection to a concept first introduced on an earlier day being '
    'revisited/tested today, e.g. "Connects to the <concept> introduced on Day <N>, '
    'reinforced here." — naming the actual concept and day from BLOCK_CONTEXT, never a '
    'worked example carried over from another block. Leave "" when no such connection '
    'is evident, or when '
    'this day\'s own misconceptions list is empty (e.g. an assessment/review day '
    'with nothing to document) — do not invent a connection to pad an empty list.\n\n'
    'BLOCK_FACTS (block-level, for framing only — never contradict):\n'
    '{{block_facts}}\n\n'
    'BLOCK_CONTEXT (all days, for cross-referencing only):\n'
    '{{block_context}}{{guidance_block}}\n\n'
    'DAYS TO FILL IN:\n{{day_records}}'
)

_DEFAULT_PATTERNS_SYSTEM = (
    "You write faithful, fact-grounded prose. Never invent or contradict a given fact."
)

_DEFAULT_PATTERNS_USER = (
    'You are writing the "Content Arc Summary" and "Production Readiness" fields of '
    'a Block Blueprint\'s Patterns & Design Notes worksheet, from VERIFIED '
    'structural facts only — do not add any fact not given below, and do not '
    'contradict any of them. Respond with ONLY this JSON object, no preamble:\n\n'
    '{"content_arc_summary": str, "production_readiness": str}\n\n'
    'FACTS:\n{{facts}}\n{{guidance_block}}'
)


def _loose_key(text: str) -> str:
    """Lower-cased, punctuation- and space-free form of a column label, for matching a
    model's reply keys against the declared ones."""
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _day_list(days: List[int]) -> str:
    """``day 6`` / ``days 5, 6`` — a reviewer reads these lines in prose, and
    "days 6" reads as a transcription error rather than as one day."""
    uniq = sorted(set(days))
    return f"day {uniq[0]}" if len(uniq) == 1 else f"days {', '.join(str(d) for d in uniq)}"


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
    #: The models that actually returned the text, in call order — NOT necessarily
    #: ``reduce_model``, which is only what the tier ASKED for. The reliability layer
    #: silently falls back to another model on a provider error, so recording the
    #: request as though it were the answer misattributes the output. Measured
    #: 2026-08-14: a whole CDD recorded reduce_model="Claude Sonnet 5 (Bedrock)"
    #: while Sonnet 4.5 wrote every word of it. Empty on the injected-``llm`` test
    #: seam, which returns bare text with no model to report. Summarised to
    #: ``{model: call_count}`` on the provenance/audit rows (_model_call_counts).
    reduce_models_used: List[str] = field(default_factory=list)
    #: Which template/version/tier supplied each REDUCE prompt for this run
    #: (see reduce_prompts.ReducePrompt.to_provenance). Persisted with the
    #: artifact so a reviewer can tell an admin-edited prompt from the built-in
    #: rather than inferring it.
    prompt_provenance: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _guidance_block(map_guidance: str, user_directives: str = "") -> str:
    """Render the optional guidance layers as clearly-delimited, contract-safe
    addenda to the REDUCE narrative-fill prompt — "" when there are none, so a
    call with no guidance produces byte-identical prompt text to before this
    feature existed. Mirrors dis_backend/services/digests/mapper.py's own
    ``_guidance_block`` (duplicated, not imported — dis_backend and
    promptops_app are separate deployables with no shared import path, same
    reason _acs_sort_key above is a local copy).

    Two layers, kept separately labelled and ordered deliberately:

    * ``map_guidance`` — distilled from the course's selected prompt template.
      Standing policy for this course, maintained by an admin.
    * ``user_directives`` — the style, instructions and duration the requester
      supplied for THIS run (see promptops_app.services.user_directives).

    The requester's directives come last because when a per-run instruction and a
    standing template conflict, the person clicking Generate should win. They are
    a separate section rather than one merged blob so that the model can tell the
    two apart, and so a reviewer reading a provenance row can too.

    Both are appended into the template's single ``{guidance_block}`` slot instead
    of adding a second template variable: reduce templates are DB-editable and a
    newly-declared variable an admin's stored template does not mention would be
    rejected as a variable violation, silently demoting them to the built-in
    prompt — which is the failure mode this whole change is about."""
    parts = []
    text = (map_guidance or "").strip()
    if text:
        parts.append(
            "ADDITIONAL GENERATION GUIDANCE (derived from the course's selected "
            "prompt template — apply while filling the fields requested above; this "
            "refines judgment/emphasis ONLY, it must never add a field not requested "
            "above or contradict any instruction above):\n" + text
        )
    directives = (user_directives or "").strip()
    if directives:
        parts.append(directives)
    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)


def _safe_json(text: str) -> Any:
    try:
        clean = (text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)
    except Exception:
        return None


class BlockWideGenerator:
    """Block-wide REDUCE.

    ``db`` + the scope ids are what make the REDUCE prompts genuinely editable at
    runtime: ``prompt_loader.load_template`` only consults its DB tier when a
    session is supplied, so without them an admin's edit in the Prompts UI could
    never reach this call (it silently resolved from the file tier at best). Pass
    them and resolution follows the normal scope precedence — prompt_id → course →
    cluster → project → global → file → built-in.

    All of them are optional so existing callers and tests keep working unchanged;
    omitting ``db`` simply drops back to the file/built-in tiers.
    """

    def __init__(self, llm: Optional[LlmFn] = None, batch_size: int = DEFAULT_BATCH_SIZE,
                 *, db: Any = None, project_id: Any = None, cluster_id: Any = None,
                 course_id: Any = None, prompt_id: Any = None, user_name: str = ""):
        self._llm = llm
        self.batch_size = max(1, batch_size)
        self._max_tokens: Optional[int] = None   # set per reduce() from the tier
        self._db = db
        self._scope = {
            "project_id": project_id,
            "cluster_id": cluster_id,
            "course_id": course_id,
            "prompt_id": prompt_id,
        }
        self._user_name = user_name
        self._block_label = ""   # set per reduce() from the enumerate summary
        #: Per-call cost/traceability context for llm_usage_logs, set per reduce()
        #: once the deliverable and tier are known. Every OTHER LLM surface in this
        #: app (feedback, workflow, canvas, regen) passes one; the block-wide reduce
        #: was the only one that did not, so its calls — the most expensive per
        #: request in the product — produced no token, cost, or latency rows at all.
        self._usage_ctx: Any = None
        #: Provenance for the prompts actually used, surfaced on ReduceResult so a
        #: reviewer can see which template/version/tier produced a deliverable.
        self.prompt_provenance: Dict[str, Any] = {}
        #: Models that actually answered, appended per call by _call — see
        #: ReduceResult.reduce_models_used for why the requested model is not enough.
        self.reduce_models_used: List[str] = []

    # -- LLM seam -------------------------------------------------------------
    def _call(self, model_choice: str, system: str, user: str) -> str:
        if self._llm is not None:
            return self._llm(model_choice, system, user)
        # Lazy default: the real reliability layer (retry/fallback + usage log).
        # Pass the tier's output-token headroom so long sectioned reduces don't
        # truncate at the historical flat cap (D6).
        from promptops_app.services.llm_service import generate_with_metadata
        result = generate_with_metadata(model_choice, system, user, max_tokens=self._max_tokens,
                                        usage_ctx=self._usage_ctx)
        # generate_with_metadata never raises — on total failure (after retry +
        # fallback) it returns status="error" with a human error string as .text.
        # If we didn't check, that string would become "narrative" (or fail JSON
        # parse → every row REVIEW NEEDED) and the caller would persist an empty
        # deliverable AS SUCCESS. Raise so _build_and_reduce degrades to legacy
        # generation / marks the job failed instead.
        if getattr(result, "status", None) == "error":
            raise RuntimeError(getattr(result, "text", None) or "LLM reduce call failed")
        # Record who actually answered, before returning only the text. A fallback is
        # logged at WARNING and then forgotten; this is the part that survives into
        # the artifact and the audit row.
        used = getattr(result, "model", None)
        if used:
            self.reduce_models_used.append(used)
        return getattr(result, "text", "") or ""

    def _build_usage_ctx(self, deliverable: str, prompt) -> Any:
        """Cost/traceability context for every REDUCE call of this generation.

        ``entity_id`` is deliberately the block label, not a deliverable row id: the
        row does not exist yet when these calls run (it is created by
        persist_*_and_respond afterwards), and the block is what a cost review of a
        block-wide generation actually groups by. Returns None on any failure —
        usage logging must never be able to break a generation.
        """
        try:
            from promptops_app.services.usage_service import UsageLogContext
            return UsageLogContext(
                user_name=self._user_name or "",
                project_id=self._scope.get("project_id"),
                course_id=self._scope.get("course_id"),
                entity_type=deliverable,
                entity_id=str(self._block_label or ""),
                prompt_template=prompt.template_name or "",
                prompt_version=str(prompt.template_version or ""),
            )
        except Exception as exc:   # noqa: BLE001 — accounting, never load-bearing
            log.debug("block-wide usage context unavailable: %s", exc)
            return None

    def _resolve_prompt(self, name: str, builtin_system: str, builtin_user: str, contract):
        """Resolve one REDUCE prompt through DB → file → built-in, contract-checked.

        Threading ``self._db`` and the scope ids here is what reaches the DB tier;
        ``reduce_prompts.resolve_reduce_prompt`` validates that an admin's edit
        still asks for every JSON key the parser reads, and degrades per-layer to
        the built-in when it doesn't (loudly, never silently)."""
        from promptops_app.services.reduce_prompts import resolve_reduce_prompt
        return resolve_reduce_prompt(
            name,
            builtin_system=builtin_system,
            builtin_user=builtin_user,
            contract=contract,
            db=self._db,
            **self._scope,
        )

    # -- public API -----------------------------------------------------------
    def reduce(self, enumerate_summary: Dict[str, Any], digests: List[Dict[str, Any]],
               deliverable: str = "cdd", tier: str | None = None,
               block_overview: Optional[Dict[str, Any]] = None,
               source_file_inventory: Optional[List[Dict[str, Any]]] = None,
               acs_registry: Optional[List[Dict[str, Any]]] = None,
               map_guidance: str = "", user_directives: str = "",
               extension_columns: Optional[List[Dict[str, str]]] = None) -> ReduceResult:
        """``map_guidance`` (optional) is judgment/emphasis instructions distilled
        from the course's selected CDD/Blueprint prompt (see
        promptops_app.services.prompt_guidance.resolve_prompt_guidance) — folded
        into the narrative-fill call below. "" (the default) reproduces this
        method's exact pre-existing behavior.

        ``user_directives`` (optional) is the same idea one layer closer to the
        user: the style, additional instructions and declared duration supplied on
        the generation form for this specific run (see
        promptops_app.services.user_directives.resolve_user_directives). Kept a
        separate argument rather than pre-concatenated into ``map_guidance`` so the
        two authorities stay distinguishable in the prompt and in provenance."""
        deliverable = deliverable if deliverable in _DEFAULT_SYSTEM else "cdd"
        # Reset per run: the instance is reusable, and carrying a previous run's
        # models forward would attribute this deliverable to a model it never called.
        self.reduce_models_used = []
        tm = resolve_tier(tier)
        by_day = {d.get("day_number"): d for d in digests}
        days = enumerate_summary.get("days", [])
        self._block_label = enumerate_summary.get("block") or ""

        rows = [self._skeleton_row(day, by_day.get(day.get("day_number"))) for day in days]
        # Per-day × block-registry join; must run before the day table is rendered.
        self._apply_assessment_columns(rows, acs_registry)

        self._max_tokens = tm.max_output_tokens
        from promptops_app.services.reduce_prompts import NARRATIVE_CONTRACT
        narrative_prompt = self._resolve_prompt(
            _TEMPLATE_NAME.get(deliverable, "cdd_reduce"),
            _DEFAULT_SYSTEM.get(deliverable, _DEFAULT_SYSTEM["cdd"]),
            _DEFAULT_NARRATIVE_USER,
            NARRATIVE_CONTRACT,
        )
        self.prompt_provenance = {"narrative": narrative_prompt.to_provenance()}
        self._usage_ctx = self._build_usage_ctx(deliverable, narrative_prompt)
        llm_calls = self._fill_narratives(rows, deliverable, narrative_prompt, tm.reduce_model,
                                          block_overview, map_guidance, user_directives)
        # After the narratives: these columns are told not to contradict the row they
        # sit in, so they are filled from a row that is already complete.
        llm_calls += self._fill_extensions(rows, list(extension_columns or []),
                                           tm.reduce_model, map_guidance, user_directives)

        coverage = self.verify(rows, enumerate_summary, deliverable, tm.tier)
        sections = [
            {"key": "block_overview", "title": "BLOCK OVERVIEW", "fields": block_overview or {}},
            {"key": "source_file_inventory", "title": "SOURCE FILE INVENTORY", "rows": source_file_inventory or []},
            {"key": "acs_registry", "title": "ACS CODE REGISTRY", "rows": acs_registry or []},
            {"key": "day_table",
             "title": f"{deliverable.upper()} — day-by-day ({coverage.enumerated_days} days)",
             "rows": rows,
             # Carried on the section, not read off the generator, so the renderer
             # emits exactly the columns THIS result was built with.
             "extension_columns": [c["label"] for c in (extension_columns or [])]},
        ]
        notes_calls = 0
        try:
            notes_fields, notes_calls = self._patterns_notes(rows, coverage, tm.reduce_model,
                                                             map_guidance, user_directives)
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
            reduce_models_used=list(self.reduce_models_used),
            prompt_provenance=dict(self.prompt_provenance),
        )

    _EXTENSION_TEMPLATE_NAME = "extension_columns_reduce"

    def _fill_extensions(self, rows: List[Dict[str, Any]], columns: List[Dict[str, str]],
                         model_choice: str, map_guidance: str = "",
                         user_directives: str = "") -> int:
        """Fill the additional day columns the selected prompt declared. Returns the
        number of LLM calls made — 0 when nothing is declared.

        A SEPARATE call rather than extra keys on the narrative reply, deliberately.
        The narrative template's contract pins six reply keys the parser reads by name;
        making a seventh set conditional on the request would mean a contract that
        cannot be checked without knowing the request, and an admin editing that
        template could no longer be told what it must contain. Keeping this apart means
        a run that declares nothing produces byte-identical prompts, byte-identical
        output, and no extra call — the same discipline ``_guidance_block`` follows.

        REDUCE tier, not MAP, and that is the whole reason this is affordable: the MAP
        template's hash is part of every per-day digest's cache key, so sourcing these
        cells there would invalidate every digest of every block for every tenant. The
        cost is grounding — this stage sees the established day facts, never the raw
        source text — which is why the prompt is told to return a review marker instead
        of inferring, and why a declared column can never overwrite a pipeline cell.
        """
        if not columns or not rows:
            return 0
        from promptops_app.services.reduce_prompts import (
            EXTENSION_CONTRACT, render_user_prompt,
        )
        prompt = self._resolve_prompt(
            self._EXTENSION_TEMPLATE_NAME, _DEFAULT_EXTENSION_SYSTEM,
            _DEFAULT_EXTENSION_USER, EXTENSION_CONTRACT,
        )
        self.prompt_provenance["extension_columns"] = prompt.to_provenance()

        labels = [c["label"] for c in columns]
        columns_text = "\n".join(f"- {c['label']}: {c['description']}" for c in columns)
        block_context = [{"day_number": r["day_number"], "topic": r["topic"],
                          "projects_today": r["projects_today"]} for r in rows]
        calls = 0
        for i in range(0, len(rows), self.batch_size):
            batch = rows[i:i + self.batch_size]
            payload = [{
                "day_number": r["day_number"],
                "topic": r["topic"],
                "acs_codes": r["acs_codes"],
                "concept_type": r["concept_type"],
                "concept_scope": r.get("concept_scope", ""),
                "derived_objective": r["derived_objective"],
                "misconceptions": r["misconceptions"],
                "projects_today": r["projects_today"],
                "hangar_activity_today": r["hangar_activity_today"],
                "digest_status": r["digest_status"],
            } for r in batch]
            user = render_user_prompt(
                prompt,
                {
                    "columns": columns_text,
                    "block_context": json.dumps(block_context, indent=2),
                    "guidance_block": _guidance_block(map_guidance, user_directives),
                    "day_records": json.dumps(payload, indent=2),
                },
                EXTENSION_CONTRACT,
                _DEFAULT_EXTENSION_USER,
            )
            try:
                text = self._call(model_choice, prompt.system, user)
            except Exception as exc:  # noqa: BLE001
                # These columns are additive: losing them must never cost the run the
                # 31 columns that were already built and paid for.
                log.warning("extension_columns fill failed for batch at %d (%s) — "
                            "those days render %r", i, exc, EXTENSION_MISSING)
                text = ""
            calls += 1
            parsed = _safe_json(text)
            mapping = parsed if isinstance(parsed, dict) else {}
            for r in batch:
                # Same discipline as _fill_narratives: a failed digest has no verified
                # facts for this stage to work from, so its cells state that rather
                # than carrying whatever a model produced from an empty payload.
                if r.get("digest_status") == "failed":
                    r["extensions"] = {label: "REVIEW NEEDED — per-day digest failed."
                                       for label in labels}
                    continue
                cell = mapping.get(str(r["day_number"])) or mapping.get(r["day_number"])
                values = cell if isinstance(cell, dict) else {}
                # Match the reply's keys case- and spacing-insensitively. They are
                # model-generated, so "instructional model stage" for a column declared
                # "Instructional Model Stage" is an ordinary near-miss — and an exact
                # lookup would report a value the model DID return as never returned,
                # which is the one thing this marker must not say falsely.
                loose = {_loose_key(k): v for k, v in values.items() if isinstance(k, str)}
                r["extensions"] = {}
                for label in labels:
                    raw = values.get(label)
                    if raw is None:
                        raw = loose.get(_loose_key(label))
                    r["extensions"][label] = str(raw or "").strip() or EXTENSION_MISSING
        fillable = [r for r in rows if r.get("digest_status") != "failed"]
        filled = sum(1 for r in fillable
                     if any(v != EXTENSION_MISSING for v in (r.get("extensions") or {}).values()))
        if filled < len(fillable):
            log.warning("extension_columns: %d/%d fillable days have at least one "
                        "populated declared column", filled, len(fillable))
        return calls

    def _patterns_notes(self, rows: List[Dict[str, Any]], coverage: "CoverageReport",
                        model_choice: str, map_guidance: str = "",
                        user_directives: str = "") -> tuple[Dict[str, str], int]:
        """Worksheet 5 synthesis. Every field with a single correct answer (learn-
        while-doing days, handbook edition conflicts, high-risk days, the consolidated
        missing-source summary) is a CODE conclusion, not an LLM judgment call — a
        model asked to "phrase" a fact can still second-guess it (seen live: given two
        distinct handbook editions in the facts, one run's prose concluded "no
        conflicts detected" anyway). The
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

        # AIM's Blueprint worksheet 5 requires a consolidated list of every
        # MISSING_SOURCE raised anywhere in worksheets 1-4. Grouped by reason with
        # its day list rather than one line per day: the same missing artefact
        # typically spans a run of days, and twenty near-identical lines is the
        # form in which a reviewer stops reading them. A code conclusion for the
        # same reason the three fields below are - the flags were already computed
        # by ENUMERATE/mapper, and a model asked to "summarise" them can drop one.
        missing_by_reason: Dict[str, List[int]] = {}
        for r in rows:
            for flag in (r.get("review_flags") or []):
                text = str(flag)
                if text.startswith("MISSING_SOURCE"):
                    reason = text.split("—", 1)[-1].strip() if "—" in text else text
                    missing_by_reason.setdefault(reason, []).append(r["day_number"])
        # Worksheet 1's own gaps (e.g. an unread syllabus) reach CoverageReport as
        # enumerate flags, not as a day's review_flags, so they would be missed by
        # the loop above - which is exactly the "anywhere in worksheets 1-4" part.
        # getattr, not attribute access: _patterns_notes is best-effort and its caller
        # OMITS worksheet 5 entirely when it raises, so a caller holding a partial
        # coverage object would trade the whole worksheet for one optional list. The
        # per-day flags above still land; only the block-level entries drop.
        block_level = [str(f) for f in (getattr(coverage, "enumerate_flags", None) or [])
                       if "MISSING_SOURCE" in str(f) or str(f).startswith("SYLLABUS_")]
        if missing_by_reason or block_level:
            parts = [f"{reason} ({_day_list(days)})"
                     for reason, days in sorted(missing_by_reason.items())]
            parts += [f"{f} (block level)" for f in block_level]
            missing_summary = "; ".join(parts)
        else:
            missing_summary = "No MISSING_SOURCE flags raised."

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
            "missing_source_summary": missing_summary,
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
        from promptops_app.services.reduce_prompts import PATTERNS_CONTRACT, render_user_prompt
        resolved = self._resolve_prompt(
            _PATTERNS_TEMPLATE_NAME, _DEFAULT_PATTERNS_SYSTEM,
            _DEFAULT_PATTERNS_USER, PATTERNS_CONTRACT,
        )
        self.prompt_provenance["patterns_notes"] = resolved.to_provenance()
        prompt = render_user_prompt(
            resolved,
            {"facts": json.dumps(facts, indent=2),
             "guidance_block": _guidance_block(map_guidance, user_directives)},
            PATTERNS_CONTRACT,
            _DEFAULT_PATTERNS_USER,
        )
        text = self._call(model_choice, resolved.system, prompt)
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

    @staticmethod
    def _apply_assessment_columns(rows: List[Dict[str, Any]],
                                  acs_registry: Optional[List[Dict[str, Any]]]) -> None:
        """Fill each row's ``quick_check_targets`` and ``summative_exam_cluster``.

        Both are a per-day × block-registry join, so neither can come from a single
        day's digest — hence a separate pass here, in code, after the rows exist.

        **Deliberately not LLM-generated.** The AIM reference builds "Targets for
        Quick Check" from an AKTR miss-rate table with real figures ("79.6%, rank
        #1"), and that table is not ingested anywhere in this system (see
        dis_backend/services/digests/worksheets.py's module docstring). Asking a model
        for it would manufacture percentages, so this states the day's codes and the
        registry's own priority, and says NO AKTR DATA where the analytics are absent
        — the same honesty rule the rest of the pipeline follows.

        "Summative Exam Item Cluster" is likewise an estimate in the reference ("Items
        ~6-10 (estimated)"); without an ingested exam blueprint there is no item count
        to distribute, so it reports the gap rather than inventing ranges.
        """
        by_code: Dict[str, Dict[str, Any]] = {}
        for entry in acs_registry or []:
            code = str(entry.get("acs_code") or "").strip()
            if code:
                by_code[code] = entry

        for r in rows:
            codes = [c for c in (r.get("acs_codes") or []) if c]
            if not codes:
                r["quick_check_targets"] = "No ACS codes mapped to this day."
                r["summative_exam_cluster"] = "REVIEW NEEDED — no ACS codes mapped to this day."
                continue

            high_miss, priorities = [], []
            for code in codes:
                entry = by_code.get(code) or {}
                miss = str(entry.get("high_miss") or "").strip()
                if miss and miss.upper() not in {"NO", "N/A", "NONE", "NO AKTR DATA"}:
                    high_miss.append(f"{code} ({miss})")
                priority = str(entry.get("priority") or "").strip()
                if priority:
                    priorities.append(f"{code}: {priority}")

            parts = []
            if high_miss:
                parts.append("HIGH-MISS: " + "; ".join(high_miss))
            if priorities:
                parts.append("; ".join(priorities))
            else:
                # Registry itself had nothing for these codes — say so rather than
                # implying the codes were assessed and found low-priority.
                parts.append(f"NO AKTR DATA for {', '.join(codes)} — priority defaults apply.")
            r["quick_check_targets"] = " · ".join(parts)
            r["summative_exam_cluster"] = (
                "REVIEW NEEDED — no summative exam blueprint in source; "
                "item cluster cannot be estimated."
            )

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
            "concept_scope": digest.get("concept_scope", ""),
            # Filled by _apply_assessment_columns() once the block-level ACS
            # registry is available (they are a per-day × block-registry join, so
            # they cannot be derived from one day's digest alone).
            "quick_check_targets": "",
            "summative_exam_cluster": "",
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
                         prompt: Any, model_choice: str,
                         block_overview: Optional[Dict[str, Any]] = None,
                         map_guidance: str = "", user_directives: str = "") -> int:
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
        # ``prompt`` is normally a resolved ReducePrompt. A bare string is accepted
        # as "this system prompt + the built-in USER contract", which is what a
        # caller that only has a system prompt (and every pre-templating test)
        # supplies — normalising here keeps one prompt-assembly path below instead
        # of branching on the argument type at each use.
        if isinstance(prompt, str):
            from promptops_app.services.reduce_prompts import ReducePrompt
            prompt = ReducePrompt(
                system=prompt, user_template=_DEFAULT_NARRATIVE_USER,
                template_name=_TEMPLATE_NAME.get(deliverable, "cdd_reduce"),
                template_version="builtin",
            )

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
            from promptops_app.services.reduce_prompts import NARRATIVE_CONTRACT, render_user_prompt
            user = render_user_prompt(
                prompt,
                {
                    "block_facts": json.dumps(block_facts, indent=2),
                    "block_context": json.dumps(block_context, indent=2),
                    "guidance_block": _guidance_block(map_guidance, user_directives),
                    "day_records": json.dumps(payload, indent=2),
                },
                NARRATIVE_CONTRACT,
                _DEFAULT_NARRATIVE_USER,
            )
            text = self._call(model_choice, prompt.system, user)
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
