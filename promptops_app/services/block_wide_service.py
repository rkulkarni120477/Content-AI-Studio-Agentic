"""Shared block-wide digest generation for CDD and Block Blueprint.

Both the synchronous router branches (cdd.py / blueprints.py, flag-gated `block`)
and the async worker (jobs/block_wide_jobs.py) call into here, so the digest
pipeline + persistence live in exactly one place and there is no router↔jobs
import cycle.

Pipeline: dis_client.build_digests → get bundle → BlockWideGenerator.reduce →
render markdown → parse → persist a versioned document with the CoverageReport in
generation_params (§3.10, no migration). Any DIS/reduce failure returns None so a
caller can fall back to the legacy single-call path.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any, Dict, Optional

from app.core.dis_client import dis_client
from promptops_app.services.budget_service import BudgetExceededError

_log = logging.getLogger(__name__)


def run_block_wide_sync(db, deliverable: str, request_body, current_user):
    """Shared, flag-gated block-wide SYNC branch for the CDD and Blueprint
    routers (single source of truth — the two routers previously duplicated this).

    Engages ONLY when ``request_body.block`` is set AND the digest pipeline is
    enabled for the course's DIS client. Returns the persisted response object
    (carrying cdd_id / blueprint_id) on success, or ``None`` when the block path
    doesn't apply or the pipeline fails — in which case the caller falls through
    to its unchanged legacy single-call path. The course DIS client is resolved
    once here and reused for the flag check and generation.

    Never engages for a day-scoped single-item request (``request_body.day_number``
    set) — that's a separate, lighter-weight enrichment of the legacy per-item path
    (see blueprints.py), not a whole-block generation; this pipeline ignores
    selected_module/day entirely and would produce the wrong document if it fired
    here. Only Blueprint requests ever carry day_number, but the check is generic
    so both routers share one choke point."""
    from app.core.config import settings
    from app.core.dis_access import resolve_course_dis_client

    if getattr(request_body, "day_number", None):
        return None
    block = getattr(request_body, "block", None)
    if not block:
        return None
    dcid = resolve_course_dis_client(
        db, course_id=request_body.course_id,
        project_id=getattr(request_body, "project_id", None),
    )
    if not settings.digest_pipeline_on_for(dcid):
        return None

    from promptops_app.services.prompt_guidance import resolve_prompt_guidance
    map_guidance = resolve_prompt_guidance(db, request_body, deliverable, current_user)

    if deliverable == "blueprint":
        gen = generate_blueprint_via_digests(db, request_body, current_user, dcid, map_guidance)
        if gen is None:
            return None
        _log.info("blueprint_generate_via_digests user=%s course=%s block=%s tier=%s",
                  getattr(current_user, "username", "?"), request_body.course_id, block,
                  gen["prompt_provenance"].get("quality_tier"))
        return persist_blueprint_and_respond(db, request_body, current_user, **gen)

    gen = generate_cdd_via_digests(db, request_body, current_user, dcid, map_guidance)
    if gen is None:
        return None
    _log.info("cdd_generate_via_digests user=%s course=%s block=%s tier=%s",
              getattr(current_user, "username", "?"), request_body.course_id, block,
              gen["prompt_provenance"].get("quality_tier"))
    return persist_cdd_and_respond(db, request_body, current_user, **gen)


# --------------------------------------------------------------------------- #
# Rendering — one "## WORKSHEET N: TITLE" section per ReduceResult.sections
# entry. This exact heading shape is what cdd_parser.is_dlu_cdd/split_cdd_
# worksheets already decoration-blind-detects for the existing DLU CDD export
# path — emitting it here means BOTH CDD and Blueprint inherit that XLSX
# export machinery for free, no new parsing/export logic needed.
# --------------------------------------------------------------------------- #
def _coverage_lines(cov: Dict[str, Any]) -> list[str]:
    out = [
        f"- **Days represented:** {cov.get('days_in_output')}/{cov.get('total_days')}",
        f"- **ACS covered:** {len(cov.get('covered_acs', []))}/{len(cov.get('declared_acs', []))}",
    ]
    if cov.get("orphan_acs"):
        out.append(f"- **⚠ Uncovered ACS:** {', '.join(cov['orphan_acs'])}")
    if cov.get("missing_days"):
        out.append(f"- **⚠ Missing days:** {cov['missing_days']}")
    if cov.get("failed_days"):
        out.append(f"- **⚠ Days needing review (digest failed):** {cov['failed_days']}")
    if cov.get("thin_days"):
        out.append(f"- **⚠ Thin days (no substantive source):** {cov['thin_days']}")
    for flag in cov.get("enumerate_flags", []):
        out.append(f"- {flag}")
    return out


def _fields_section(fields: Dict[str, Any]) -> list[str]:
    """Key-value worksheet (Block Overview / Patterns & Design Notes). Each entry
    is one markdown bullet, so — same discipline as the day-table cells — a raw
    newline in a value (plausible for LLM-generated prose, e.g. content_arc_summary)
    is collapsed rather than left to become a stray non-bullet line."""
    if not fields:
        return ["_No data available for this worksheet._"]
    out = []
    for key, value in fields.items():
        label = key.replace("_", " ").title()
        if isinstance(value, list):
            if not value:
                out.append(f"- **{label}:** —")
            else:
                # e.g. primary_handbooks: ["FAA-H-8083-30B — cited on Days 1-13, 17-19", ...]
                # — each entry is already pre-formatted prose (see worksheets.py), so this
                # only needs to join entries, not format their internal structure. "; " (not
                # ", ") avoids ambiguity with the commas already inside a day-range list.
                out.append(f"- **{label}:** {_cell('; '.join(str(v) for v in value), default='')}")
        else:
            out.append(f"- **{label}:** {_cell(str(value) if value is not None else '', default='—')}")
    return out


def _source_inventory_table(rows: list[dict]) -> list[str]:
    if not rows:
        return ["_No source file inventory available._"]
    out = ["| Document Type | File Count | Days Applicable | Status | Production Action | Status Notes |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        doc_type = _cell(r.get("document_type") or "", default="")
        days = _cell(", ".join(str(d) for d in (r.get("days_applicable") or [])), default="")
        action = _cell(r.get("production_action") or "", default="")
        notes = _cell(r.get("status_notes") or "", default="")
        # file_count/status are code-computed constants today (an int, and the
        # literal "EXISTS") with no row-breaking risk — routed through _cell()
        # anyway for the same reason task_description below is: every cell
        # must go through it, per _cell's own docstring, so a future change
        # that makes either field free text doesn't silently reopen the
        # row-breaking bug _cell exists to prevent.
        file_count = _cell(str(r.get("file_count", 0)), default="0")
        status = _cell(r.get("status") or "", default="")
        out.append(f"| {doc_type} | {file_count} | {days} | {status} | {action} | {notes} |")
    return out


def _acs_registry_table(rows: list[dict]) -> list[str]:
    if not rows:
        return ["_No ACS registry available._"]
    out = ["| ACS Code | Type | Task Description | Days Active | High-Miss | Quick Check Priority |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        days = _cell(", ".join(str(d) for d in (r.get("days_active") or [])), default="")
        # task_description is real extracted text from an ingested ACS-1-shaped
        # document (see worksheets.py::_acs1_task_descriptions) whenever one is
        # available, not a static placeholder — sanitized here for exactly the
        # reason _cell exists (a stray "|" in an extracted description would
        # otherwise break this row's syntax).
        desc = _cell(r.get("task_description") or "", default="")
        priority = _cell(r.get("quick_check_priority") or "", default="")
        # acs_code/acs_type/high_miss are closed-vocabulary/hardcoded values
        # today (no row-breaking risk) — routed through _cell() anyway so a
        # future change (e.g. wiring in real AKTR high-miss data) can't
        # silently reopen the row-breaking bug _cell exists to prevent.
        acs_code = _cell(r.get("acs_code") or "", default="")
        acs_type = _cell(r.get("acs_type") or "", default="")
        high_miss = _cell(r.get("high_miss") or "", default="")
        out.append(f"| {acs_code} | {acs_type} | {desc} | {days} | {high_miss} | {priority} |")
    return out


_WORKSHEET_TITLES = {
    "block_overview": "BLOCK OVERVIEW",
    "source_file_inventory": "SOURCE FILE INVENTORY",
    "acs_registry": "ACS CODE REGISTRY",
    "day_table": "DAY-BY-DAY MAP",
    "patterns_notes": "PATTERNS & DESIGN NOTES",
}


def _render_worksheets(result, intro: list[str]) -> str:
    """Shared multi-worksheet body for both CDD and Blueprint — same 5-worksheet
    shape (AIM's sample is a 6-sheet workbook; the 6th, "Intro to the Blueprint",
    is static reviewer-guidance text with no data to render and is skipped here)."""
    out = list(intro)
    for i, section in enumerate(result.sections or [], start=1):
        title = _WORKSHEET_TITLES.get(section.get("key"), section.get("title", "")).upper()
        out += ["", f"## WORKSHEET {i}: {title}", ""]
        key = section.get("key")
        if "fields" in section:
            out += _fields_section(section["fields"])
        elif key == "source_file_inventory":
            out += _source_inventory_table(section.get("rows") or [])
        elif key == "acs_registry":
            out += _acs_registry_table(section.get("rows") or [])
        elif key == "day_table":
            out += _day_table_from_rows(section.get("rows") or [])
        else:
            # An unrecognized section shape would otherwise silently render
            # through the day-table columns (wrong headers, misaligned data,
            # no error) — surface it instead of guessing.
            out.append(f"_Unrecognized worksheet section (key={key!r}) — rendering skipped._")
            _log.warning("block_wide render: unrecognized section key=%r, skipped", key)
    cov = result.coverage or {}
    out += ["", "## COVERAGE & REVIEW"] + _coverage_lines(cov)
    return "\n".join(out)


def _cell(text: str, default: str = "—") -> str:
    """Sanitize one markdown table cell. A raw '|' or newline in ANY cell breaks
    that row's syntax and can silently truncate every row after it in a strict
    markdown-table renderer — caught live: an unescaped newline in one day's
    calendar-derived field (assessment_today) made the table appear to end
    partway through a 20-day block. Every cell must go through this, not just
    the ones a past bug happened to expose."""
    clean = " ".join((text or "").split()).replace("|", "/")
    return clean or default


# Column order mirrors the AIM reference workbook's "4_Day-by-Day Map" sheet, so the
# generated Blueprint drops into the same review workflow. Deviations, all deliberate:
#   * "Notes" is ours (the REDUCE narrative cell) and has no reference counterpart, so
#     it sits last rather than displacing a reference column.
#   * "AIM SME Comments" is blank by design — a reviewer-fill column, populated on
#     0/20 days in the reference. Emitted so the column exists to be filled.
_DAY_TABLE_HEADER = [
    "Day", "Topic", "Handbook Reference", "Handbook Edition", "ACS", "Concept Type",
    "Concept Type Explanation", "Concept Scope", "Learn-While-Doing", "How It Is Applied",
    "Hangar Activity", "Projects Today", "Assessment Today", "Targets for Quick Check",
    "Summative Exam Item Cluster", "Source Files", "Learning Objective", "Misconceptions",
    "Interactive Candidate", "Interactive Type", "Interactive Content",
    "Storyline Source Asset Status", "Interactive Scope", "Interactive Rationale",
    "Job Aid Candidate", "Job Aid Type", "Job Aid Description", "Job Aid Source Reference",
    "Academian Questions", "AIM SME Comments", "Notes",
]


def _day_table_from_rows(rows: list[dict]) -> list[str]:
    out = ["| " + " | ".join(_DAY_TABLE_HEADER) + " |", "|" + "---|" * len(_DAY_TABLE_HEADER)]
    for r in rows:
        acs = _cell(", ".join(r.get("acs_codes") or []))
        note = _cell(r.get("narrative") or "", default="")
        topic = _cell(r.get("topic") or "", default="")
        objective = _cell(r.get("derived_objective") or "")
        misconceptions = _cell("; ".join(r.get("misconceptions") or []), default="NONE DOCUMENTED")
        handbook = _cell(r.get("handbook_reference") or "")
        handbook_edition = _cell(r.get("handbook_edition") or "", default="N/A")
        projects = _cell(", ".join(r.get("projects_today") or []))
        assessment = _cell(", ".join(r.get("assessment_today") or []))
        files = _cell(", ".join(r.get("source_files_today") or []))
        # The code-computed fact (which hangar-activity FILES actually exist
        # today) must survive in the cell regardless of what the LLM's note
        # says — flagged by adversarial review: rendering the note ALONE
        # (the pre-fix behavior) let a non-grounded or contradicting note
        # fully hide the real, traceable filenames. Same "fact prefix, LLM
        # phrasing after" pattern as learn_while_doing below — the note is
        # always an ADDITION to the file list, never a replacement of it.
        hangar_files_joined = ", ".join(r.get("hangar_activity_today") or [])
        hangar_note = r.get("hangar_activity_note") or ""
        if hangar_files_joined:
            hangar_activity = _cell(f"{hangar_files_joined} — {hangar_note}" if hangar_note else hangar_files_joined)
        else:
            hangar_activity = _cell(hangar_note, default="N/A — no hangar activity listed for this day.")
        learn_while_doing_yn = "Yes" if r.get("learn_while_doing") else "No"
        learn_while_doing = _cell(
            f"{learn_while_doing_yn} — {r['learn_while_doing_reason']}" if r.get("learn_while_doing_reason")
            else learn_while_doing_yn,
        )
        how_it_is_applied = _cell(r.get("how_it_is_applied") or "", default="")
        interactive_yn = "Yes" if r.get("interactive_candidate") else "No"
        interactive_type = _cell(r.get("interactive_type") or "", default="N/A")
        interactive_content = _cell(r.get("interactive_content") or "", default="N/A")
        interactive_scope = _cell(r.get("interactive_scope") or "", default="N/A")
        interactive_rationale = _cell(r.get("interactive_rationale") or "", default="N/A")
        storyline_asset_status = _cell(r.get("storyline_source_asset_status") or "", default="NEEDS NEW ART")
        job_aid_yn = "Yes" if r.get("job_aid_candidate") else "No"
        job_aid_type = _cell(r.get("job_aid_type") or "", default="N/A")
        job_aid_desc = _cell(r.get("job_aid_description") or "", default="N/A")
        job_aid_source_ref = _cell(r.get("job_aid_source_reference") or "", default="N/A")
        questions = _cell(r.get("academian_questions") or "", default="")
        concept_type = _cell(r.get("concept_type") or "", default="Unknown")
        concept_type_explanation = _cell(r.get("concept_type_explanation") or "", default="")
        # Per-day LLM field (schema v7) — the specific sub-topics/tools covered.
        concept_scope = _cell(r.get("concept_scope") or "", default="REVIEW NEEDED — no source available")
        # Both derived deterministically in verify()/_quick_check_targets from the
        # day's own ACS codes joined against the block's ACS registry. Never
        # invented: where the underlying analytics are absent they say so (the AIM
        # reference builds these from an AKTR miss-rate table that is not ingested
        # anywhere in this system — see worksheets.py's module docstring).
        quick_check = _cell(r.get("quick_check_targets") or "", default="NO AKTR DATA")
        exam_cluster = _cell(r.get("summative_exam_cluster") or "", default="")
        # "Day 5", not "5" — the AIM reference's own Day # column is written that way,
        # and a bare number makes every row read as a mismatch when the two are
        # compared cell-by-cell. Numeric consumers read day_number off the row dicts,
        # not this rendered cell.
        day_label = f"Day {r['day_number']}" if r.get("day_number") is not None else "—"
        cells = [
            day_label, topic, handbook, handbook_edition, acs, concept_type,
            concept_type_explanation, concept_scope, learn_while_doing, how_it_is_applied,
            hangar_activity, projects, assessment, quick_check, exam_cluster,
            files, objective, misconceptions,
            interactive_yn, interactive_type, interactive_content,
            storyline_asset_status, interactive_scope, interactive_rationale,
            job_aid_yn, job_aid_type, job_aid_desc, job_aid_source_ref, questions,
            # AIM SME Comments — reviewer-fill, intentionally blank (0/20 populated
            # in the reference). Emitted so the column exists in the workbook.
            "",
            note,
        ]
        out.append("| " + " | ".join(cells) + " |")
    return out


def render_cdd_markdown(course_title: str, block: Optional[str], result) -> str:
    """CDD markdown — same 6-worksheet shape as the Block Blueprint (per the
    2026-08-06 decision to give both deliverables the AIM-sample shape), just a
    different title line. is_dlu_cdd/split_cdd_worksheets detect this shape by
    heading pattern alone, so the existing DLU-CDD parsing/XLSX-export machinery
    picks this up unchanged."""
    intro = [f"# Course Design Document — {course_title}"]
    if block:
        intro.append(f"**Block:** {block}")
    return _render_worksheets(result, intro)


def render_blueprint_markdown(block: Optional[str], result) -> str:
    """Block Blueprint — see render_cdd_markdown; identical worksheet shape."""
    title = f"Block Blueprint — {block}" if block else "Block Blueprint"
    return _render_worksheets(result, [f"# {title}"])


# --------------------------------------------------------------------------- #
# Reduce (shared build → bundle → reduce)
# --------------------------------------------------------------------------- #
# ---------------------------------------------------------------------------
# MAP cost accounting
#
# MAP (per-day digest extraction) runs inside DIS, on DIS's own Bedrock client, so
# it never reaches CAS's usage/budget choke point in core/llm_client.py. Measured on
# a real 20-day block: MAP ~176k input / 15k output tokens vs REDUCE's ~6k / 2.5k —
# so without this, ~96% of a block-wide generation's spend was absent from
# llm_usage_logs, the cost dashboards, and the token-cap budgets, and a budget could
# never stop a run no matter how large.
#
# Two halves, deliberately separate:
#   * a pre-flight RESERVATION so an over-budget build is refused before it spends;
#   * a post-build RECORD + RECONCILE using the token counts DIS actually reports,
#     so attribution is exact rather than estimated.
# ---------------------------------------------------------------------------

#: Worst-case MAP size used for the pre-flight reservation, from the measured
#: per-day averages of real AIM blocks (~8.8k in / ~750 out per day) against a
#: generous 25-day block. Over-reserving is safe and intended: reconcile_budget
#: corrects it to the real figure immediately after the build, so the only effect of
#: being high is that a build starting very close to a cap is refused rather than
#: allowed to breach it.
_MAP_ESTIMATE_DAYS = 25
_MAP_EST_INPUT_TOKENS_PER_DAY = 8_800
_MAP_EST_OUTPUT_TOKENS_PER_DAY = 750

#: Pricing FAMILY fallback for the reservation, and for a report that predates
#: ``map_model`` (an older DIS). Not a claim about which model DIS runs — DIS owns
#: that, and the settled figures always use the ``map_model`` it reports.
#: usage_service._find_pricing matches on substring, so any "claude…sonnet" string
#: resolves to Sonnet pricing, which is the family every current extractor is in.
_MAP_PRICING_MODEL = "anthropic.claude-sonnet"


def _map_usage_ctx(deliverable: str, request_body, current_user):
    """UsageLogContext for the MAP stage, or None if it can't be built.

    Returns None rather than raising: cost accounting must never be the reason a
    generation fails, and a None context makes check_budget a documented no-op.
    """
    try:
        from promptops_app.services.usage_service import UsageLogContext
        return UsageLogContext(
            user_name=getattr(current_user, "username", "") or "",
            project_id=getattr(request_body, "project_id", None),
            course_id=getattr(request_body, "course_id", None),
            entity_type=deliverable,
            entity_id=str(getattr(request_body, "block", "") or "") or None,
            prompt_template="digest_map",
            prompt_version="",          # filled from the report's prompt_version below
        )
    except Exception:
        _log.warning("map usage context unavailable — MAP spend will not be attributed",
                     exc_info=True)
        return None


def _reserve_map_budget(db, map_ctx):
    """Reserve the worst-case MAP spend. Raises BudgetExceededError on a real breach.

    Deliberately propagates that one exception: refusing an over-budget build before
    it runs is the entire point, and the HTTP layer turns it into a 402. Every other
    failure is swallowed — a bug here must not block generation.
    """
    if map_ctx is None or db is None:
        return []
    try:
        from promptops_app.services.budget_service import check_budget
        result = check_budget(
            db, map_ctx, system_prompt="", user_prompt="",
            model=_MAP_PRICING_MODEL,
            estimated_input_tokens=_MAP_ESTIMATE_DAYS * _MAP_EST_INPUT_TOKENS_PER_DAY,
            estimated_output_tokens=_MAP_ESTIMATE_DAYS * _MAP_EST_OUTPUT_TOKENS_PER_DAY,
        )
        for warning in result.warnings or []:
            _log.warning("map budget warning: %s", warning)
        return result.reservations or []
    except BudgetExceededError:
        raise
    except Exception:
        _log.warning("map budget reservation failed — proceeding unreserved", exc_info=True)
        return []


def _settle_map_usage(db, map_ctx, report: Optional[Dict[str, Any]], reservation) -> None:
    """Write the MAP spend to llm_usage_logs and true up the reservation.

    Never raises. A build that failed before reporting still releases its
    reservation (actuals of zero), because a leaked hold would suppress every later
    generation in the period.
    """
    from promptops_app.services.budget_service import reconcile_budget
    from promptops_app.services.usage_service import estimate_cost, log_llm_usage

    # The report is an HTTP response body from DIS, so its shape is not guaranteed:
    # a non-dict (error string, unexpected payload) must settle the reservation at
    # zero rather than raise out of a finally block and replace the real result.
    rep = report if isinstance(report, dict) else {}
    if report is not None and not isinstance(report, dict):
        _log.warning("map usage: DIS build report was %s, not a dict — settling at zero",
                     type(report).__name__)
    try:
        tok_in = int(rep.get("map_tokens_in") or 0)
        tok_out = int(rep.get("map_tokens_out") or 0)
    except (TypeError, ValueError):
        _log.warning("map usage: non-numeric token counts in the DIS report — "
                     "settling at zero", exc_info=True)
        tok_in = tok_out = 0
    model = str(rep.get("map_model") or "") or _MAP_PRICING_MODEL
    cost = 0.0
    try:
        cost = estimate_cost(model, tok_in, tok_out)
    except Exception:
        _log.warning("map cost estimate failed for model=%s", model, exc_info=True)

    # Record first: a usage row is useful even if reconciliation then fails.
    if map_ctx is not None and db is not None and (tok_in or tok_out):
        try:
            from promptops_app.core.llm_client import LLMResult
            ctx = replace(map_ctx, prompt_version=str(rep.get("prompt_version") or ""))
            log_llm_usage(db, LLMResult(
                text="", model=model, prompt_tokens=tok_in, completion_tokens=tok_out,
                status="success",
            ), ctx)
            _log.info("map usage recorded: calls=%s in=%s out=%s cost=$%.4f model=%s",
                      rep.get("map_calls"), tok_in, tok_out, cost, model)
        except Exception:
            _log.warning("map usage logging failed — spend not attributed", exc_info=True)

    for res in reservation or []:
        try:
            reconcile_budget(db, res, cost, tok_in + tok_out)
        except Exception:
            _log.warning("map budget reconciliation failed — reservation may leak "
                         "until the period rolls over", exc_info=True)


def _build_and_reduce(deliverable: str, block: str, quality_tier: Optional[str],
                      current_user, dis_client_id: str, map_guidance: str = "",
                      *, db=None, request_body=None):
    """Returns (ReduceResult, build_report) or (None, None) on any failure.

    ``map_guidance`` (optional) is judgment/emphasis guidance distilled from
    the course's selected CDD/Blueprint prompt (see
    promptops_app.services.prompt_guidance.resolve_prompt_guidance) — forwarded
    to both the MAP build (DIS side) and the REDUCE narrative fill below. ""
    (the default) reproduces this function's exact pre-existing behavior.

    ``db`` + ``request_body`` are threaded purely so the REDUCE prompts resolve
    through their DB tier with this request's scope (prompt_id → course → cluster →
    project). Without them ``load_template`` cannot consult the DB at all, so an
    admin's edit in the Prompts UI would never reach generation. Both optional:
    omitted ⇒ file/built-in tiers, exactly as before."""
    # MAP runs inside DIS on its own Bedrock client, so it never passes through CAS's
    # usage/budget choke point in core/llm_client.py. Left alone it is the largest
    # untracked spend in the product — a measured 20-day build is ~176k input tokens
    # against REDUCE's ~6k, so ~96% of a block-wide generation was invisible to both
    # the cost dashboards and the token-cap budgets. Reserve before the build,
    # then record and reconcile against what DIS actually spent.
    map_ctx = _map_usage_ctx(deliverable, request_body, current_user)
    reservation = _reserve_map_budget(db, map_ctx)
    report = None
    try:
        report = dis_client.build_digests_sync(block, current_user=current_user, client_id=dis_client_id,
                                                map_guidance=map_guidance)
        bundle = dis_client.get_digests_bundle_sync(block, current_user=current_user, client_id=dis_client_id)
    except BudgetExceededError:
        # A quota breach must reach the HTTP layer as a real 402, not be folded into
        # the generic "DIS unavailable" fallback below.
        raise
    except Exception as exc:
        _log.warning("block_wide_dis_unavailable deliverable=%s block=%s error=%s — falling back",
                     deliverable, block, exc)
        return None, None
    finally:
        # In a finally so a failed or partial build still records what it burned and
        # releases the rest of the reservation — otherwise a failure permanently
        # leaks its worst-case hold until the budget period rolls over.
        _settle_map_usage(db, map_ctx, report, reservation)

    enumerate_summary = bundle.get("enumerate") or {}
    digests = bundle.get("digests") or []
    if not enumerate_summary.get("days"):
        _log.warning("block_wide_empty_enumerate deliverable=%s block=%s — falling back", deliverable, block)
        return None, None
    try:
        from promptops_app.services.block_wide_generator import BlockWideGenerator
        course = None
        if db is not None and getattr(request_body, "course_id", None):
            try:
                from promptops_app.repositories.course_repository import get_course_by_id
                course = get_course_by_id(db, request_body.course_id)
            except Exception:
                course = None   # scope narrows to project/global; never fatal
        generator = BlockWideGenerator(
            db=db,
            project_id=getattr(request_body, "project_id", None) or (course.project_id if course else None),
            cluster_id=course.cluster_id if course else None,
            course_id=getattr(request_body, "course_id", None),
            prompt_id=getattr(request_body, "prompt_id", None),
            user_name=getattr(current_user, "username", "") or "",
        )
        result = generator.reduce(
            enumerate_summary, digests, deliverable=deliverable, tier=quality_tier,
            block_overview=bundle.get("block_overview"),
            source_file_inventory=bundle.get("source_file_inventory"),
            acs_registry=bundle.get("acs_registry"),
            map_guidance=map_guidance,
        )
    except Exception as exc:
        _log.warning("block_wide_reduce_failed deliverable=%s block=%s error=%s — falling back",
                     deliverable, block, exc)
        return None, None
    return result, report


def _audit_provenance(prompt_provenance: Optional[Dict[str, Any]],
                      coverage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extra audit-metadata keys for a digest-pipeline generation ({} for legacy).

    Why this exists: the audit row's ``system_prompt``/``user_prompt`` fields hold the
    VERBATIM prompts on the legacy path, but the digest path has no single pair to
    record — REDUCE is N batched calls plus a separate per-day MAP stage on the DIS
    side, so those fields carry a descriptive label instead. Without this helper the
    same audit field would silently mean "the actual prompt" for one path and "a
    label" for the other, and a digest-generated deliverable could not be
    reconstructed from its audit row at all.

    What makes it reconstructible: the REDUCE template's name+version+tier (DB prompt
    versions are append-only and immutable, so name+version pins exact text — a
    ``file``-sourced layer is only as stable as the deployed file), the full distilled
    ``map_guidance`` text that actually reached MAP and REDUCE, the per-day build
    counts, and the coverage report. All of it also lands in the version row's
    ``generation_params``; duplicating it here means an auditor reading ``audit_logs``
    alone does not have to join another table to see what drove the generation.
    """
    if not prompt_provenance:
        return {}
    out: Dict[str, Any] = {
        "generation_path": prompt_provenance.get("prompt_source") or "digest_pipeline",
        "quality_tier": prompt_provenance.get("quality_tier"),
        "reduce_model": prompt_provenance.get("reduce_model"),
        "reduce_prompts": prompt_provenance.get("reduce_prompts") or {},
        "digest_build": prompt_provenance.get("digest_build") or {},
        # Recorded in full, not as a boolean: this is the admin's own DB-maintained
        # prompt distilled down, and it is the ONLY channel by which that prompt
        # influences MAP/REDUCE — a reviewer asking "why did it say that?" needs the
        # actual text, not a flag saying some text existed.
        "map_guidance_applied": bool(prompt_provenance.get("map_guidance_applied")),
        "map_guidance": prompt_provenance.get("map_guidance") or "",
    }
    if coverage:
        # The honest source-accounting for this path. `dis_source_units_count` is 0
        # here because the digest pipeline never populates that list — it reads
        # sources per-day on the DIS side — so on its own it reads as "no sources
        # used", which is the opposite of true.
        out["coverage"] = coverage
        out["source_accounting"] = {
            "enumerated_days": coverage.get("enumerated_days"),
            "days_in_output": coverage.get("days_in_output"),
            "failed_days": coverage.get("failed_days") or [],
            "thin_days": coverage.get("thin_days") or [],
            "declared_acs_count": len(coverage.get("declared_acs") or []),
            "covered_acs_count": len(coverage.get("covered_acs") or []),
            "orphan_acs_count": len(coverage.get("orphan_acs") or []),
            "complete": coverage.get("complete"),
        }
    return out


def _provenance(deliverable: str, result, report, map_guidance: str = "") -> Dict[str, Any]:
    return {
        "prompt_source": "digest_pipeline",
        "deliverable": deliverable,
        "reduce_model": result.reduce_model,
        "quality_tier": result.tier,
        # Includes the MAP token counts: they are the ONLY record of what the per-day
        # extraction cost. MAP runs on the DIS side through its own boto3 client, so it
        # never reaches llm_usage_logs — the DIS build report is the single place those
        # numbers exist, and dropping them here discarded the majority of the request's
        # token spend (Block 2: ~168k in / ~9k out across 20 calls).
        "digest_build": {k: report.get(k) for k in
                         ("built", "cached", "failed", "map_calls",
                          "map_tokens_in", "map_tokens_out")}
        if isinstance(report, dict) else {},
        # Traceability (PL↔CAS sync review discipline): whether/what prompt-
        # derived guidance actually reached MAP/REDUCE for this generation, so
        # a reviewer can see it rather than trust it blindly (see prompt_
        # guidance.resolve_prompt_guidance's own docstring on why the digest
        # step itself is a fidelity risk that must stay inspectable).
        "map_guidance_applied": bool((map_guidance or "").strip()),
        "map_guidance": map_guidance or "",
        # Which REDUCE template/version actually drove this run, and whether each
        # layer came from the DB (admin edit), the shipped file, or the built-in
        # fallback — so a reviewer can distinguish "the admin's prompt produced
        # this" from "their edit was rejected and the built-in ran".
        "reduce_prompts": getattr(result, "prompt_provenance", {}) or {},
    }


# --------------------------------------------------------------------------- #
# CDD
# --------------------------------------------------------------------------- #
def generate_cdd_via_digests(db, request_body, current_user, dis_client_id, map_guidance: str = ""):
    """Run the block-wide digest pipeline for one CDD. Returns kwargs for
    persist_cdd_and_respond, or None to fall back to legacy."""
    result, report = _build_and_reduce("cdd", request_body.block,
                                       getattr(request_body, "quality_tier", None),
                                       current_user, dis_client_id, map_guidance,
                                       db=db, request_body=request_body)
    if result is None:
        return None
    from promptops_app.parsers.cdd_parser import parse_cdd_flat, parse_sections_from_text
    raw_output = render_cdd_markdown(request_body.course_title, request_body.block, result)
    sections = parse_sections_from_text(raw_output)
    for key, value in parse_cdd_flat(raw_output).items():
        if not key.startswith("_") and value.strip():
            sections[key] = value
    return {
        "raw_output": raw_output,
        "sections": sections,
        "dis_source_units": [],
        "prompt_provenance": _provenance("cdd", result, report, map_guidance),
        # NOT a verbatim prompt — REDUCE is N batched calls plus a separate
        # per-day MAP stage, so there is no single pair to record. Says so
        # explicitly and points at the keys that ARE reconstructible, rather
        # than looking like a truncated prompt (see _audit_provenance).
        "system_prompt": (
            f"[digest-pipeline reduce · model={result.reduce_model} · "
            f"tier={result.tier} · not a verbatim prompt: see reduce_prompts (template name/version/source) and map_guidance]"
        ),
        "user_prompt": (
            f"[block-wide digest reduce · block={request_body.block} · "
            f"reduce_calls={result.llm_calls} · not a verbatim prompt: the per-day MAP prompts live on the DIS side and vary per batch]"
        ),
        "model_used": result.reduce_model,
        "tokens_used": None,
        "coverage": result.coverage,
    }


def persist_cdd_and_respond(db, request_body, current_user, *, raw_output, sections,
                            dis_source_units, prompt_provenance, system_prompt, user_prompt,
                            model_used, tokens_used, coverage=None):
    """Shared persistence + response tail for CDD (legacy and digest paths)."""
    from app.schemas.cdd import CDDGenerateResponse
    from promptops_app.database import CDDVersion, CourseDesignDocument
    from promptops_app.repositories.course_repository import set_active_cdd
    from promptops_app.services.audit_service import log_audit_event

    document_title = request_body.document_title or f"{request_body.course_title} — CDD"

    new_cdd = CourseDesignDocument(
        title=document_title,
        course_title=request_body.course_title,
        description="",
        active_version="v1",
        workflow_state="draft",
        created_by=current_user.username,
        project_id=request_body.project_id,
        course_id=request_body.course_id,
    )
    db.add(new_cdd)
    db.commit()
    db.refresh(new_cdd)

    generation_params = {
        "course_title":             request_body.course_title,
        "target_audience":          getattr(request_body, "target_audience", ""),
        "expert_domain":            getattr(request_body, "expert_domain", ""),
        "estimated_duration_hours": getattr(request_body, "estimated_duration_hours", None),
        "extra_instructions":       getattr(request_body, "extra_instructions", ""),
        "dis_source_units":         dis_source_units,
        **prompt_provenance,
    }
    if coverage is not None:
        generation_params["coverage"] = coverage

    version_record = CDDVersion(
        cdd_id=new_cdd.id,
        version="v1",
        full_content=raw_output,
        sections=json.dumps(sections),
        generation_params=json.dumps(generation_params),
        change_reason="Initial AI generation",
        is_active=True,
        created_by=current_user.username,
    )
    db.add(version_record)
    db.commit()

    try:
        dis_client.generated_upsert_sync({
            "generated_doc_id": f"cdd_{new_cdd.id}",
            "generated_type": "cdd",
            "title": document_title,
            "content": raw_output,
            "summary": raw_output[:500],
            "active": True,
            "metadata": {
                "course_title": request_body.course_title,
                "target_audience": getattr(request_body, "target_audience", ""),
                "expert_domain": getattr(request_body, "expert_domain", ""),
                "course_id": request_body.course_id,
                "project_id": request_body.project_id,
            },
            "source_documents_used": dis_source_units,
            "cas_ref": {"entity": "cdd", "id": new_cdd.id},
            "created_by": current_user.username,
        }, current_user=current_user)
    except Exception as exc:
        _log.warning("dis_generated_cdd_upsert_failed cdd_id=%s error=%s", new_cdd.id, exc)

    set_active_cdd(db, request_body.course_id, new_cdd.id)

    log_audit_event(
        db, current_user.username, "cdd.created",
        entity_type="cdd", entity_id=new_cdd.id,
        project_id=request_body.project_id, course_id=request_body.course_id,
        metadata={
            "title": document_title, "sections": len(sections),
            "model_choice": getattr(request_body, "model_choice", ""),
            "course_title": request_body.course_title,
            "target_audience": getattr(request_body, "target_audience", ""),
            "expert_domain": getattr(request_body, "expert_domain", ""),
            "extra_instructions": getattr(request_body, "extra_instructions", ""),
            "dis_source_units_count": len(dis_source_units) if dis_source_units else 0,
            "input_mode": "full", "system_prompt": system_prompt,
            "user_prompt": user_prompt, "output": raw_output,
            **_audit_provenance(prompt_provenance, coverage),
        },
    )

    _log.info("cdd_generate_complete  user=%s  cdd_id=%d  sections=%d  model=%s",
              current_user.username, new_cdd.id, len(sections), model_used)

    return CDDGenerateResponse(
        cdd_id=new_cdd.id, title=document_title, version="v1",
        sections_count=len(sections), full_content=raw_output, sections=sections,
        model_used=model_used or getattr(request_body, "model_choice", ""),
        tokens_used=tokens_used, auto_pinned=True,
    )


# --------------------------------------------------------------------------- #
# Blueprint (block-wide)
# --------------------------------------------------------------------------- #
def generate_blueprint_via_digests(db, request_body, current_user, dis_client_id, map_guidance: str = ""):
    """Run the block-wide digest pipeline for a Block Blueprint. Returns kwargs for
    persist_blueprint_and_respond, or None to fall back to legacy."""
    result, report = _build_and_reduce("blueprint", request_body.block,
                                        getattr(request_body, "quality_tier", None),
                                        current_user, dis_client_id, map_guidance,
                                        db=db, request_body=request_body)
    if result is None:
        return None
    from promptops_app.parsers.cdd_parser import parse_sections_from_text
    raw_output = render_blueprint_markdown(request_body.block, result)
    sections = parse_sections_from_text(raw_output)
    return {
        "raw_output": raw_output,
        "sections": sections,
        "dis_source_units": [],
        "prompt_provenance": _provenance("blueprint", result, report, map_guidance),
        # NOT a verbatim prompt — REDUCE is N batched calls plus a separate
        # per-day MAP stage, so there is no single pair to record. Says so
        # explicitly and points at the keys that ARE reconstructible, rather
        # than looking like a truncated prompt (see _audit_provenance).
        "system_prompt": (
            f"[digest-pipeline reduce · model={result.reduce_model} · "
            f"tier={result.tier} · not a verbatim prompt: see reduce_prompts (template name/version/source) and map_guidance]"
        ),
        "user_prompt": (
            f"[block-wide blueprint reduce · block={request_body.block} · "
            f"reduce_calls={result.llm_calls} · not a verbatim prompt: the per-day MAP prompts live on the DIS side and vary per batch]"
        ),
        "model_used": result.reduce_model,
        "tokens_used": None,
        "coverage": result.coverage,
    }


def persist_blueprint_and_respond(db, request_body, current_user, *, raw_output, sections,
                                  dis_source_units, prompt_provenance, system_prompt, user_prompt,
                                  model_used, tokens_used, coverage=None):
    """Persist a block-wide Block Blueprint (ModuleBlueprint + BlueprintVersion,
    module_number=0 block sentinel) + CoverageReport, mirror to DIS, auto-pin,
    audit, and return the response."""
    from app.schemas.blueprint import BlueprintComponent, BlueprintGenerateResponse
    from promptops_app.database import BlueprintVersion, ModuleBlueprint
    from promptops_app.parsers.blueprint_parser import parse_blueprint_components
    from promptops_app.repositories.course_repository import set_active_blueprint
    from promptops_app.services.audit_service import log_audit_event

    block = getattr(request_body, "block", None)
    cdd_id = getattr(request_body, "cdd_id", None)
    bp_title = f"{block} — Block Blueprint" if block else "Block Blueprint"

    new_bp = ModuleBlueprint(
        cdd_id=cdd_id,
        title=bp_title,
        module_title=f"Block: {block}" if block else "Block Blueprint",
        module_number=0,          # 0 = block-level sentinel (not a numbered module)
        active_version="v1",
        project_id=request_body.project_id,
        course_id=request_body.course_id,
        created_by=current_user.username,
    )
    db.add(new_bp)
    db.commit()
    db.refresh(new_bp)

    generation_params = {
        "block": block,
        "extra_instructions": getattr(request_body, "extra_instructions", "") or "",
        "dis_source_units": dis_source_units,
        **prompt_provenance,
    }
    if coverage is not None:
        generation_params["coverage"] = coverage

    version_record = BlueprintVersion(
        blueprint_id=new_bp.id,
        version="v1",
        full_content=raw_output,
        sections=json.dumps(sections),
        generation_params=json.dumps(generation_params),
        change_reason="Initial AI generation",
        is_active=True,
        created_by=current_user.username,
    )
    db.add(version_record)
    db.commit()

    try:
        dis_client.generated_upsert_sync({
            "generated_doc_id": f"blueprint_{new_bp.id}",
            "generated_type": "blueprint",
            "title": bp_title,
            "content": raw_output,
            "summary": raw_output[:500],
            "active": True,
            "metadata": {
                "block": block, "module_number": 0,
                "course_id": request_body.course_id,
                "project_id": request_body.project_id, "cdd_id": cdd_id,
            },
            "source_documents_used": dis_source_units,
            "cas_ref": {"entity": "blueprint", "id": new_bp.id},
            "created_by": current_user.username,
        }, current_user=current_user)
    except Exception as exc:
        _log.warning("dis_generated_blueprint_upsert_failed blueprint_id=%s error=%s", new_bp.id, exc)

    set_active_blueprint(db, request_body.course_id, new_bp.id)

    components = parse_blueprint_components(version_record)
    component_list = [BlueprintComponent(**c) for c in components]

    log_audit_event(
        db, current_user.username, "blueprint.created",
        entity_type="blueprint", entity_id=new_bp.id,
        project_id=request_body.project_id, course_id=request_body.course_id,
        metadata={
            "title": bp_title, "model_choice": getattr(request_body, "model_choice", ""),
            "block": block, "extra_instructions": getattr(request_body, "extra_instructions", ""),
            "dis_source_units_count": len(dis_source_units) if dis_source_units else 0,
            "input_mode": "full", "system_prompt": system_prompt,
            "user_prompt": user_prompt, "output": raw_output,
            **_audit_provenance(prompt_provenance, coverage),
        },
    )

    _log.info("blueprint_generate_complete  user=%s  bp_id=%d  components=%d  model=%s",
              current_user.username, new_bp.id, len(component_list), model_used)

    return BlueprintGenerateResponse(
        blueprint_id=new_bp.id, title=bp_title, version="v1",
        sections_count=len(sections), components=component_list,
        full_content=raw_output, model_used=model_used or getattr(request_body, "model_choice", ""),
        tokens_used=tokens_used, auto_pinned=True,
    )
