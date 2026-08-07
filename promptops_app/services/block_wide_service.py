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
from typing import Any, Dict, Optional

from app.core.dis_client import dis_client

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


_DAY_TABLE_HEADER = [
    "Day", "Topic", "Handbook Reference", "Handbook Edition", "ACS", "Concept Type",
    "Concept Type Explanation", "Learn-While-Doing", "How It Is Applied", "Hangar Activity",
    "Projects Today", "Assessment Today", "Source Files", "Learning Objective", "Misconceptions",
    "Interactive Candidate", "Interactive Type", "Interactive Content", "Interactive Scope",
    "Interactive Rationale", "Storyline Source Asset Status", "Job Aid Candidate", "Job Aid Type",
    "Job Aid Description", "Job Aid Source Reference", "Academian Questions", "Notes",
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
        cells = [
            str(r.get("day_number")), topic, handbook, handbook_edition, acs, concept_type,
            concept_type_explanation, learn_while_doing, how_it_is_applied, hangar_activity,
            projects, assessment, files, objective, misconceptions,
            interactive_yn, interactive_type, interactive_content, interactive_scope,
            interactive_rationale, storyline_asset_status, job_aid_yn, job_aid_type,
            job_aid_desc, job_aid_source_ref, questions, note,
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
def _build_and_reduce(deliverable: str, block: str, quality_tier: Optional[str],
                      current_user, dis_client_id: str, map_guidance: str = ""):
    """Returns (ReduceResult, build_report) or (None, None) on any failure.

    ``map_guidance`` (optional) is judgment/emphasis guidance distilled from
    the course's selected CDD/Blueprint prompt (see
    promptops_app.services.prompt_guidance.resolve_prompt_guidance) — forwarded
    to both the MAP build (DIS side) and the REDUCE narrative fill below. ""
    (the default) reproduces this function's exact pre-existing behavior."""
    try:
        report = dis_client.build_digests_sync(block, current_user=current_user, client_id=dis_client_id,
                                                map_guidance=map_guidance)
        bundle = dis_client.get_digests_bundle_sync(block, current_user=current_user, client_id=dis_client_id)
    except Exception as exc:
        _log.warning("block_wide_dis_unavailable deliverable=%s block=%s error=%s — falling back",
                     deliverable, block, exc)
        return None, None

    enumerate_summary = bundle.get("enumerate") or {}
    digests = bundle.get("digests") or []
    if not enumerate_summary.get("days"):
        _log.warning("block_wide_empty_enumerate deliverable=%s block=%s — falling back", deliverable, block)
        return None, None
    try:
        from promptops_app.services.block_wide_generator import BlockWideGenerator
        result = BlockWideGenerator().reduce(
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


def _provenance(deliverable: str, result, report, map_guidance: str = "") -> Dict[str, Any]:
    return {
        "prompt_source": "digest_pipeline",
        "deliverable": deliverable,
        "reduce_model": result.reduce_model,
        "quality_tier": result.tier,
        "digest_build": {k: report.get(k) for k in ("built", "cached", "failed", "map_calls")}
        if isinstance(report, dict) else {},
        # Traceability (PL↔CAS sync review discipline): whether/what prompt-
        # derived guidance actually reached MAP/REDUCE for this generation, so
        # a reviewer can see it rather than trust it blindly (see prompt_
        # guidance.resolve_prompt_guidance's own docstring on why the digest
        # step itself is a fidelity risk that must stay inspectable).
        "map_guidance_applied": bool((map_guidance or "").strip()),
        "map_guidance": map_guidance or "",
    }


# --------------------------------------------------------------------------- #
# CDD
# --------------------------------------------------------------------------- #
def generate_cdd_via_digests(db, request_body, current_user, dis_client_id, map_guidance: str = ""):
    """Run the block-wide digest pipeline for one CDD. Returns kwargs for
    persist_cdd_and_respond, or None to fall back to legacy."""
    result, report = _build_and_reduce("cdd", request_body.block,
                                       getattr(request_body, "quality_tier", None),
                                       current_user, dis_client_id, map_guidance)
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
        "system_prompt": f"[digest-pipeline reduce · model={result.reduce_model}]",
        "user_prompt": f"[block-wide digest reduce · block={request_body.block} · tier={result.tier}]",
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
                                        current_user, dis_client_id, map_guidance)
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
        "system_prompt": f"[digest-pipeline reduce · model={result.reduce_model}]",
        "user_prompt": f"[block-wide blueprint reduce · block={request_body.block} · tier={result.tier}]",
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
