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
import os
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, Dict, List, Optional

from app.core.dis_client import dis_client
from promptops_app.services.budget_service import BudgetExceededError
from promptops_app.services.user_directives import (
    compose_guidance,
    fingerprint,
    resolve_user_directives,
)

_log = logging.getLogger(__name__)

# The failure branches in _build_and_reduce log the real cause and then return None,
# which the job layer renders as one fixed "Digest pipeline unavailable (no enumerated
# days or DIS error)" string. That string is the ONLY artefact a prod user or on-call
# engineer sees, and it cannot distinguish causes that need completely different fixes:
# DIS unreachable, a Bedrock credential/model rejection inside DIS, or a block that
# genuinely enumerated zero days. Diagnosing 2026-08-13's prod failure meant reading
# container logs that are not accessible from where the report lands.
#
# A ContextVar rather than a return value because two callers (cdd.py's sync branch and
# run_block_wide_sync) depend on None meaning "fall back to the legacy path" — changing
# that contract to raise would silently disable their fallback. ContextVar (not a plain
# global) so concurrent jobs on the job_runner thread pool cannot read each other's
# reason; Celery's process workers are isolated either way.
_failure_reason: ContextVar[str] = ContextVar("block_wide_failure_reason", default="")


def last_failure_reason() -> str:
    """Reason for the most recent digest-pipeline failure *in this context*, or ""."""
    return _failure_reason.get("")


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
    # Reconcile the SELECTED prompt against what this pipeline can emit. Reporting
    # only — never gates the run (see prompt_capability's module docstring on why the
    # silent-ignore case needs a voice, and why it must not become a blocker).
    from promptops_app.services.prompt_capability import assess_selected_prompt
    capability = assess_selected_prompt(db, request_body, deliverable)

    if deliverable == "blueprint":
        gen = generate_blueprint_via_digests(db, request_body, current_user, dcid, map_guidance,
                                             capability=capability)
        if gen is None:
            return None
        _log.info("blueprint_generate_via_digests user=%s course=%s block=%s tier=%s",
                  getattr(current_user, "username", "?"), request_body.course_id, block,
                  gen["prompt_provenance"].get("quality_tier"))
        return persist_blueprint_and_respond(db, request_body, current_user, **gen)

    gen = generate_cdd_via_digests(db, request_body, current_user, dcid, map_guidance,
                                   capability=capability)
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


#: Worksheet 2's emitted columns. A module constant rather than a literal inside the
#: renderer so prompt_capability can reconcile a selected prompt against the REAL
#: emitted surface instead of a second, drift-prone copy of these names.
_SOURCE_INVENTORY_HEADER = [
    "Document Type", "File Count", "Days Applicable", "Status",
    "Production Action", "Status Notes",
]


def _header_lines(header: list[str]) -> list[str]:
    """Markdown header + separator row for *header* — the same two lines the day
    table builds inline, factored out so the three tables cannot drift apart."""
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]


def _source_inventory_table(rows: list[dict]) -> list[str]:
    if not rows:
        return ["_No source file inventory available._"]
    out = _header_lines(_SOURCE_INVENTORY_HEADER)
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


#: Worksheet 3's emitted columns — see _SOURCE_INVENTORY_HEADER on why this is a
#: constant.
_ACS_REGISTRY_HEADER = [
    "ACS Code", "Type", "Task Description", "Days Active", "High-Miss",
    "Quick Check Priority",
]


def _acs_registry_table(rows: list[dict]) -> list[str]:
    if not rows:
        return ["_No ACS registry available._"]
    out = _header_lines(_ACS_REGISTRY_HEADER)
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


def emitted_columns() -> dict:
    """The column labels this pipeline actually emits, per tabular worksheet.

    Public because ``prompt_capability`` reconciles the user-selected prompt against
    it: the reconciliation is only worth trusting if it reads the same list the
    renderer writes from, so this returns copies of the real constants rather than a
    restated set. Worksheets 1 and 5 are absent on purpose — their fields are
    key-value dicts assembled upstream (DIS ``worksheets.py`` / REDUCE), so no
    static label list for them exists here to reconcile against.
    """
    return {
        "day_table": list(_DAY_TABLE_HEADER),
        "source_file_inventory": list(_SOURCE_INVENTORY_HEADER),
        "acs_registry": list(_ACS_REGISTRY_HEADER),
    }


def _day_table_from_rows(rows: list[dict]) -> list[str]:
    out = _header_lines(_DAY_TABLE_HEADER)
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
def _env_int(name: str, default: int) -> int:
    """Positive int from the environment, else *default*. Junk and non-positive
    values fall back rather than becoming a number: a zero or negative estimate here
    would reserve nothing and make the whole pre-flight check silently vacuous.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        _log.warning("%s=%r is not an integer — using %d", name, raw, default)
        return default
    if value <= 0:
        _log.warning("%s=%d must be positive — using %d", name, value, default)
        return default
    return value


#: Overridable so an operator can retune the reservation against their own blocks
#: without a deploy — the same treatment the DIS_MAP_* input budgets get.
#:
#: The per-day figure predates user directives (style + additional instructions are
#: now appended to every day's MAP prompt — see user_directives), which adds up to
#: ~1.7k tokens per day when a requester supplies both. Measured Block 2 was already
#: ~9.3k/day against the 8.8k default, so a 20-day block with full directives runs
#: nearer 11k/day: still inside the reservation only because ESTIMATE_DAYS (25) pads
#: past the real day count. That padding is now doing real work rather than being
#: slack, so raise CAS_MAP_EST_INPUT_TOKENS_PER_DAY before raising ESTIMATE_DAYS if
#: blocks get longer. The consequence of being low is bounded and one-sided:
#: reconcile_budget corrects to actuals immediately after the build, so a build
#: starting very close to a cap could be admitted and then overshoot it slightly,
#: rather than spend going unrecorded.
_MAP_ESTIMATE_DAYS = _env_int("CAS_MAP_ESTIMATE_DAYS", 25)
_MAP_EST_INPUT_TOKENS_PER_DAY = _env_int("CAS_MAP_EST_INPUT_TOKENS_PER_DAY", 8_800)
_MAP_EST_OUTPUT_TOKENS_PER_DAY = _env_int("CAS_MAP_EST_OUTPUT_TOKENS_PER_DAY", 750)

#: Pricing FAMILY fallback for the reservation, and for a report that predates
#: ``map_model`` (an older DIS). Not a claim about which model DIS runs — DIS owns
#: that, and the settled figures always use the ``map_model`` it reports.
#: usage_service._find_pricing matches on substring, so any "claude…sonnet" string
#: resolves to Sonnet pricing, which is the family every current extractor is in.
_MAP_PRICING_MODEL = "anthropic.claude-sonnet"


def _map_usage_ctx(deliverable: str, request_body, current_user):
    """UsageLogContext for the MAP stage, or None when there is nothing to bill.

    Returns None — never raises — in two cases, because cost accounting must not be
    the reason a generation fails:
      * building the context itself failed;
      * none of project/course/user is known. Budgets are keyed on exactly those
        three (budget_service._levels_for), so a context without any of them can
        neither be enforced nor attributed, and constructing one would only make the
        logs claim an attribution that does not exist.
    """
    try:
        from promptops_app.services.usage_service import UsageLogContext
        project_id = getattr(request_body, "project_id", None)
        course_id = getattr(request_body, "course_id", None)
        user_name = getattr(current_user, "username", "") or ""
        if project_id is None and course_id is None and not user_name:
            _log.warning("map usage: no project/course/user on this request — MAP spend "
                         "cannot be attributed or enforced")
            return None
        return UsageLogContext(
            user_name=user_name,
            project_id=project_id,
            course_id=course_id,
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

    KNOWN LIMITATION: if the DIS call itself fails (network, 5xx) after DIS has
    already spent tokens on some days, no report comes back and that spend is
    unrecorded — CAS has no other way to learn it. The reservation is still released,
    so the effect is an under-count on a failed build rather than a leak. Closing it
    would need DIS to report partial usage on the error path.
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
            # status="success" describes the SPEND, not the deliverable: these tokens
            # were billed by a call that returned. A day can still land in
            # coverage.failed_days because its reply lacked the required keys — that is
            # a content outcome, surfaced by the digest's own status and the job's
            # warning, and marking the cost row "error" instead would both misreport
            # real spend and skew any success-rate view built on this table.
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


#: Cap on how many distinct MAP failure reasons travel with a generation. When a
#: block fails wholesale every day usually fails the SAME way, so the first few
#: distinct reasons carry all the diagnostic value; the rest are repetition that
#: would bloat every persisted version row and the user-facing warning.
_MAX_FAILURE_REASONS = 3


def _digest_failure_reasons(report: Optional[Dict[str, Any]]) -> List[str]:
    """Distinct per-day MAP failure reasons from a DIS build report.

    DIS records a real cause for every failed day and returns them under
    ``per_day[].error``. CAS received that all along and kept only six counters,
    so the cause was discarded at the boundary and the only surviving signal was
    *which* days failed — never *why*. Diagnosing prod's 2026-08-13 Block 2 run
    (20 of 20 days failed) came down to reading DIS container logs that whoever
    sees the failed generation cannot reach.

    Deduplicated rather than listed per day: 20 days failing identically is one
    problem reported once, and the day numbers are already in ``failed_days``.
    """
    if not isinstance(report, dict):
        return []
    reasons: List[str] = []
    for entry in report.get("per_day") or []:
        if not isinstance(entry, dict) or entry.get("status") != "failed":
            continue
        # A failed day with no recorded error is still worth counting — silently
        # skipping it would under-report the failure — but it has nothing to say.
        error = str(entry.get("error") or "").strip()
        if error and error not in reasons:
            reasons.append(error)
        if len(reasons) >= _MAX_FAILURE_REASONS:
            break
    return reasons


def _load_course(db, request_body):
    """The request's course row, or None. Never raises.

    Used for two independent things — the REDUCE prompts' cluster scope and the style
    context's cluster-prompt layer — so a failure narrows scope to project/global
    rather than failing the generation.
    """
    if db is None or not getattr(request_body, "course_id", None):
        return None
    try:
        from promptops_app.repositories.course_repository import get_course_by_id
        return get_course_by_id(db, request_body.course_id)
    except Exception:
        return None


def _build_and_reduce(deliverable: str, block: str, quality_tier: Optional[str],
                      current_user, dis_client_id: str, map_guidance: str = "",
                      *, db=None, request_body=None):
    """Returns (ReduceResult, build_report, UserDirectives); the first two are None
    on any failure.

    The directives are returned even on the failure paths, deliberately: they are
    what the requester asked for, and a failed generation is exactly when someone
    needs to know whether their style and instructions were resolved at all.

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
    # Resolved before the build, not just before REDUCE: the style the requester
    # selected has to reach MAP as well, and rendering it needs the course's cluster
    # for the cluster-prompt layer.
    course = _load_course(db, request_body)
    directives = resolve_user_directives(
        db, request_body, cluster_id=getattr(course, "cluster_id", None),
    )
    # One wire field, composed here: see user_directives.compose_guidance for why a
    # separate DIS request field would silently drop these during a mixed-version
    # deploy while continuing to serve digests built without them.
    map_guidance_wire = compose_guidance(map_guidance, directives.map_text)

    # MAP runs inside DIS on its own Bedrock client, so it never passes through CAS's
    # usage/budget choke point in core/llm_client.py. Left alone it is the largest
    # untracked spend in the product — a measured 20-day build is ~176k input tokens
    # against REDUCE's ~6k, so ~96% of a block-wide generation was invisible to both
    # the cost dashboards and the token-cap budgets. Reserve before the build,
    # then record and reconcile against what DIS actually spent.
    map_ctx = _map_usage_ctx(deliverable, request_body, current_user)
    reservation = _reserve_map_budget(db, map_ctx)
    report = None
    # Cleared per attempt so a retry in the same context cannot inherit and report
    # the previous attempt's cause.
    _failure_reason.set("")
    try:
        report = dis_client.build_digests_sync(block, current_user=current_user, client_id=dis_client_id,
                                                map_guidance=map_guidance_wire)
        bundle = dis_client.get_digests_bundle_sync(block, current_user=current_user, client_id=dis_client_id)
    except BudgetExceededError:
        # A quota breach must reach the HTTP layer as a real 402, not be folded into
        # the generic "DIS unavailable" fallback below.
        raise
    except Exception as exc:
        _log.warning("block_wide_dis_unavailable deliverable=%s block=%s error=%s — falling back",
                     deliverable, block, exc, exc_info=True)
        # type(exc).__name__ as well as the message: a bare str() on a connection
        # error is often empty, which would report a blank reason.
        _failure_reason.set(
            f"DIS digest build failed for block {block}: {type(exc).__name__}: {exc}".strip()
        )
        return None, None, directives
    finally:
        # In a finally so a failed or partial build still records what it burned and
        # releases the rest of the reservation — otherwise a failure permanently
        # leaks its worst-case hold until the budget period rolls over.
        _settle_map_usage(db, map_ctx, report, reservation)

    enumerate_summary = bundle.get("enumerate") or {}
    digests = bundle.get("digests") or []
    if not enumerate_summary.get("days"):
        _log.warning("block_wide_empty_enumerate deliverable=%s block=%s — falling back", deliverable, block)
        # DIS answered, so this is a content/scope problem (wrong block id, nothing
        # ingested for it, or a tenant mismatch) — not an outage. Naming the block and
        # the DIS client keeps that distinct from the exception branch above, which is.
        _failure_reason.set(
            f"DIS returned no days for block {block} (dis_client={dis_client_id!r}). "
            "Nothing is ingested for this block, or the block id does not match what "
            "was ingested."
        )
        return None, None, directives
    try:
        from promptops_app.services.block_wide_generator import BlockWideGenerator
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
            # The full style context, the requester's instructions and the declared
            # duration. Passed separately from map_guidance (rather than reusing the
            # composed wire string) because REDUCE gets the FULL style while MAP got
            # the compact form, and because the two authorities stay distinguishable
            # in the prompt and in the provenance row.
            user_directives=directives.reduce_text,
        )
    except Exception as exc:
        _log.warning("block_wide_reduce_failed deliverable=%s block=%s error=%s — falling back",
                     deliverable, block, exc, exc_info=True)
        # Distinct from the DIS branch: the digests were built and paid for, so the
        # fault is CAS-side (REDUCE model call, prompt, or parsing).
        _failure_reason.set(
            f"REDUCE failed for block {block} after digests were built: "
            f"{type(exc).__name__}: {exc}".strip()
        )
        return None, None, directives
    # Carried on coverage, not left on the report: coverage is what reaches the job
    # layer's warning and the persisted version row, whereas the report is summarised
    # into six counters by _provenance and then dropped. A run where MAP failed is
    # still a "successful" generation by every other measure, so this is the only
    # place the cause can surface to whoever has to act on it.
    reasons = _digest_failure_reasons(report)
    if reasons and isinstance(getattr(result, "coverage", None), dict):
        result.coverage["failure_reasons"] = reasons
    return result, report, directives


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
        # What the tier asked for is not what answered: the reliability layer falls
        # back to another model on a provider error, logs it at WARNING, and returns
        # text that looks the same. Without this an auditor comparing two runs of the
        # same tier sees one model name and cannot tell they were written by
        # different models — see ReduceResult.reduce_models_used. {model: call_count}.
        "reduce_models_used": prompt_provenance.get("reduce_models_used") or {},
        "reduce_prompts": prompt_provenance.get("reduce_prompts") or {},
        "digest_build": prompt_provenance.get("digest_build") or {},
        # Recorded in full, not as a boolean: this is the admin's own DB-maintained
        # prompt distilled down, and it is the ONLY channel by which that prompt
        # influences MAP/REDUCE — a reviewer asking "why did it say that?" needs the
        # actual text, not a flag saying some text existed.
        "map_guidance_applied": bool(prompt_provenance.get("map_guidance_applied")),
        "map_guidance": prompt_provenance.get("map_guidance") or "",
        # Identifies the composed string MAP actually received (prompt layer + the
        # requester's directives). Carried through rather than left on the version row
        # for the same no-join reason as everything else here; see _map_guidance_sent.
        "map_guidance_sent_chars": prompt_provenance.get("map_guidance_sent_chars") or 0,
        "map_guidance_sent_fingerprint": prompt_provenance.get("map_guidance_sent_fingerprint") or "",
        # The requester's own inputs, by the same standard as map_guidance above and
        # for the same reason: since 2026-08-13 the style, additional instructions and
        # declared duration reach MAP/REDUCE, so a row that omits them cannot answer
        # "why did it say that?" — and an auditor would have to join the version row
        # to find out, which is exactly what this helper exists to avoid. Includes
        # which stage each input reached, so a style requested but never applied (a
        # deleted style id) is distinguishable from one that shaped the output.
        # ``{}`` on the legacy path, where the concept does not apply.
        "user_directives": prompt_provenance.get("user_directives") or {},
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


def _map_guidance_sent(map_guidance: str, directives) -> Dict[str, Any]:
    """Size + fingerprint of the exact string MAP received.

    Recomposed here rather than threaded down from ``_build_and_reduce``:
    ``compose_guidance`` is pure, so recomputing cannot drift from what was sent,
    whereas an extra parameter through two call layers is one more thing a future
    caller can forget to pass — the failure mode this whole change is about.
    """
    sent = compose_guidance(map_guidance, getattr(directives, "map_text", "") or "")
    return {"map_guidance_sent_chars": len(sent),
            "map_guidance_sent_fingerprint": fingerprint(sent)}


def _model_call_counts(models_used) -> Dict[str, int]:
    """``["a", "a", "b"]`` -> ``{"a": 2, "b": 1}``; ``{}`` for nothing recorded.

    Insertion-ordered by first use, so the JSON reads in the order the run actually
    went. Tolerates None for the legacy path and the injected-``llm`` test seam,
    neither of which has a model to report.
    """
    counts: Dict[str, int] = {}
    for model in models_used or []:
        counts[model] = counts.get(model, 0) + 1
    return counts


def _provenance(deliverable: str, result, report, map_guidance: str = "",
                directives=None, capability=None) -> Dict[str, Any]:
    return {
        "prompt_source": "digest_pipeline",
        "deliverable": deliverable,
        "reduce_model": result.reduce_model,
        # {model_id: call_count} for the models that actually produced the text (see
        # ReduceResult). Distinct from reduce_model above, which records only what the
        # tier requested. Counts, not a set: "1 of 12 sections came from the fallback"
        # and "12 of 12 did" are the difference between a blip and a dead primary, and
        # a set collapses them into the same answer.
        "reduce_models_used": _model_call_counts(getattr(result, "reduce_models_used", None)),
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
        # The counters above say how many days failed; these say why. Kept on the
        # version row because that is the durable record — the job row is transient
        # and the DIS logs holding the original are unreachable from here.
        "digest_failures": _digest_failure_reasons(report),
        # Traceability (PL↔CAS sync review discipline): whether/what prompt-
        # derived guidance actually reached MAP/REDUCE for this generation, so
        # a reviewer can see it rather than trust it blindly (see prompt_
        # guidance.resolve_prompt_guidance's own docstring on why the digest
        # step itself is a fidelity risk that must stay inspectable).
        #
        # PRECISELY the prompt-derived layer, which since 2026-08-13 is no longer the
        # whole of what MAP received — the requester's directives are composed onto it
        # on the wire (compose_guidance). The two are kept apart here because they are
        # separately actionable (an admin edits one, a requester types the other) and
        # because embedding a 12k style context in every audit row is a cost the
        # user_directives block below deliberately avoids. What the composed string
        # was is still verifiable, via the two keys after it.
        "map_guidance_applied": bool((map_guidance or "").strip()),
        "map_guidance": map_guidance or "",
        # What MAP was ACTUALLY sent, identified rather than duplicated. Also the exact
        # discriminator for the per-day digest cache key, which is computed from this
        # composed string — so when someone asks why a block rebuilt from cold, equal
        # fingerprints across two runs rule this out and unequal ones confirm it.
        **_map_guidance_sent(map_guidance, directives),
        # The same traceability for the requester's OWN inputs, and for the same
        # reason: until 2026-08-13 the style, additional instructions and declared
        # duration were written into generation_params on this row while reaching no
        # model at all, so the record showed them as honoured. These keys say which
        # ones actually reached which stage — including the case that looks identical
        # from the outside, a style_id that pointed at a deleted style
        # (style_applied_to_* False beside a non-null style_id).
        #
        # Ids, counts and per-stage flags rather than the rendered text: the style
        # context alone can run to 12k chars, and everything needed to reconstruct it
        # is already on this row (style_id, extra_instructions,
        # estimated_duration_hours).
        "user_directives": getattr(directives, "applied", {}) or {},
        # Which REDUCE template/version actually drove this run, and whether each
        # layer came from the DB (admin edit), the shipped file, or the built-in
        # fallback — so a reviewer can distinguish "the admin's prompt produced
        # this" from "their edit was rejected and the built-in ran".
        "reduce_prompts": getattr(result, "prompt_provenance", {}) or {},
        # Whether the prompt the requester SELECTED is one this pipeline can satisfy
        # at all. Guidance is judgment-only by contract, so a prompt asking for a
        # different worksheet schema — or for a downloadable workbook built by a code
        # interpreter — runs to completion and is silently ignored. Recorded here so
        # "did my prompt do anything?" is answerable from the row rather than by
        # reading the pipeline (see promptops_app.services.prompt_capability).
        "prompt_capability": capability.to_provenance() if capability is not None else {},
    }


# --------------------------------------------------------------------------- #
# CDD
# --------------------------------------------------------------------------- #
def generate_cdd_via_digests(db, request_body, current_user, dis_client_id, map_guidance: str = "",
                             *, capability=None):
    """Run the block-wide digest pipeline for one CDD. Returns kwargs for
    persist_cdd_and_respond, or None to fall back to legacy."""
    result, report, directives = _build_and_reduce("cdd", request_body.block,
                                                   getattr(request_body, "quality_tier", None),
                                                   current_user, dis_client_id, map_guidance,
                                                   db=db, request_body=request_body)
    if result is None:
        return None
    from promptops_app.parsers.cdd_parser import parse_cdd_flat, parse_sections_from_text
    from promptops_app.services.prompt_capability import append_section
    # Appended BEFORE parsing so the reconciliation is captured as a section too, the
    # same way COVERAGE & REVIEW is. A report with no findings appends nothing, so an
    # aligned prompt still produces byte-identical output.
    raw_output = append_section(
        render_cdd_markdown(request_body.course_title, request_body.block, result), capability)
    sections = parse_sections_from_text(raw_output)
    for key, value in parse_cdd_flat(raw_output).items():
        if not key.startswith("_") and value.strip():
            sections[key] = value
    return {
        "raw_output": raw_output,
        "sections": sections,
        "dis_source_units": [],
        "prompt_provenance": _provenance("cdd", result, report, map_guidance, directives,
                                         capability),
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
def generate_blueprint_via_digests(db, request_body, current_user, dis_client_id, map_guidance: str = "",
                                   *, capability=None):
    """Run the block-wide digest pipeline for a Block Blueprint. Returns kwargs for
    persist_blueprint_and_respond, or None to fall back to legacy."""
    result, report, directives = _build_and_reduce("blueprint", request_body.block,
                                                   getattr(request_body, "quality_tier", None),
                                                   current_user, dis_client_id, map_guidance,
                                                   db=db, request_body=request_body)
    if result is None:
        return None
    from promptops_app.parsers.cdd_parser import parse_sections_from_text
    from promptops_app.services.prompt_capability import append_section
    raw_output = append_section(render_blueprint_markdown(request_body.block, result), capability)
    sections = parse_sections_from_text(raw_output)
    return {
        "raw_output": raw_output,
        "sections": sections,
        "dis_source_units": [],
        "prompt_provenance": _provenance("blueprint", result, report, map_guidance, directives,
                                         capability),
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
