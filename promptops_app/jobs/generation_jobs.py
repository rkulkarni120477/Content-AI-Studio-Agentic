"""Background generation job — executed in a worker thread.

This module contains:
* ``create_job``   — called from the UI thread to create the DB record.
* ``run_generation_job`` — called from the worker thread (or Celery task).

Thread-safety contract
----------------------
* run_generation_job() creates its own SQLAlchemy session via SessionLocal().
* It NEVER touches st.session_state or calls any Streamlit API.
* All inputs are read from GenerationJob.request_json.
* All outputs are written to the DB (Generation, Block, GenerationJob).

Celery migration path
---------------------
1.  ``pip install celery redis``
2.  Decorate ``run_generation_job`` with ``@celery_app.task(name="gen.run")``.
3.  In generate.py replace ``job_runner.submit(run_generation_job, job_id)``
    with ``run_generation_job.delay(job_id)``.
4.  Remove job_runner.py.
"""

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

from promptops_app.database import (
    Block,
    Generation,
    GenerationJob,
    SessionLocal,
    build_style_context,
    get_active_blueprint_version,
    get_active_cdd_version,
    get_active_style,
    log_event,
    resolve_document_references,
)
from promptops_app.prompt_templates import (
    LESSON_WITH_CONTEXT_SYSTEM,
    LESSON_WITH_CONTEXT_USER,
    PERSONA_PREFIX_TEMPLATE,
)
from promptops_app.services.llm_service import generate_with_metadata as _llm_call
from promptops_app.services.usage_service import UsageLogContext, log_llm_usage
from promptops_app.core.shared import (
    build_context_injection,
    fill_template,
    make_source_context,
    split_into_blocks,
    trim_generation_context,
)
from promptops_app.jobs.job_status import (
    JobStatus,
    STAGE_CONTEXT,
    STAGE_CE_VALIDATION,
    STAGE_LLM,
    STAGE_PROMPT,
    STAGE_SAVE,
    STAGE_SPLIT,
    set_completed,
    set_failed,
    set_running,
)
from promptops_app.parsers.blueprint_parser import build_component_generation_prompt
from promptops_app.repositories import document_repository, prompt_repository
from promptops_app.services.audit_service import log_audit_event
from promptops_app.services.evaluation_service import get_initial_quality_metadata

_log = logging.getLogger(__name__)


# ── Public: UI-thread helpers ─────────────────────────────────────────────────

def create_job(db, *, user_name: str, request_params: dict) -> str:
    """Create a GenerationJob row and return its ID.

    Call this from the Streamlit UI thread *before* submitting to the executor.
    The job starts in ``queued`` state; the worker will advance it from there.
    """
    job_id = uuid.uuid4().hex  # 32-char hex, fits in String(64)
    now = datetime.now(timezone.utc)
    job = GenerationJob(
        id=job_id,
        job_type="generation",
        status=JobStatus.QUEUED,
        progress=0,
        stage="Queued",
        request_json=json.dumps(request_params, ensure_ascii=False),
        created_by=user_name,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.commit()
    _log.info("Created generation job %s for user '%s'", job_id, user_name)
    return job_id


# ── Public: worker-thread entry point ────────────────────────────────────────

def run_generation_job(job_id: str) -> None:  # noqa: C901 (complexity)
    """Run the full generation pipeline in a background thread.

    Parameters
    ----------
    job_id:
        The GenerationJob PK to process.  This is the *only* argument so the
        function signature is directly Celery-compatible.
    """
    db = SessionLocal()
    job = None
    _job_start = time.monotonic()
    try:
        # ── Load job ──────────────────────────────────────────────────
        job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if not job:
            _log.error("Job %s not found", job_id)
            return
        if job.status == JobStatus.CANCELLED:
            _log.info("Job %s was cancelled before it started", job_id)
            return

        params = json.loads(job.request_json)

        topic              = params["topic"]
        b_type             = params["b_type"]
        p_framework        = params["p_framework"]
        eff_cdd_id         = params.get("eff_cdd_id")
        eff_bp_id          = params.get("eff_bp_id")
        model_choice       = params.get("model_choice", "GPT-5.4")
        target_audience    = params.get("target_audience", "")
        expert_domain      = params.get("expert_domain", "")
        expert_exp         = params.get("expert_exp", 20)
        aud_cat            = params.get("aud_cat", "Professional/Corporate")
        ctx_docs           = params.get("ctx_docs", [])
        user_name          = params.get("user_name", "")
        project_id         = params.get("project_id")
        course_id          = params.get("course_id")
        selected_component  = params.get("selected_component", {})
        supp_files          = params.get("supplementary_files", [])
        extra_instructions  = params.get("extra_instructions", "")

        # ── Stage 1 — Context ─────────────────────────────────────────
        set_running(db, job, *STAGE_CONTEXT)

        context = ""

        # Pre-parsed supplementary file content (read in UI thread and serialised
        # into request_json so we don't try to share UploadedFile objects across threads)
        for sf in supp_files:
            if sf.get("content"):
                context += make_source_context(sf["name"], sf["content"])

        # Library documents selected by the user
        if ctx_docs:
            lib_docs = document_repository.get_documents_by_filenames(db, ctx_docs)
            for d in lib_docs:
                context += make_source_context(d.filename, d.content or "")

        # Backtick document references embedded in the topic string
        _ref_ctx, _ref_names = resolve_document_references(db, topic)
        if _ref_names:
            context += _ref_ctx

        # Source documents linked to the active CDD — prepended so they take priority
        if eff_cdd_id:
            try:
                _cdd_ver_for_src = get_active_cdd_version(db, eff_cdd_id)
                if _cdd_ver_for_src and _cdd_ver_for_src.generation_params:
                    _cdd_gp = json.loads(_cdd_ver_for_src.generation_params)
                    # Support new list format and old single-ID format
                    _src_doc_ids = _cdd_gp.get("source_document_ids") or []
                    if not _src_doc_ids and _cdd_gp.get("source_document_id"):
                        _src_doc_ids = [_cdd_gp["source_document_id"]]
                    _src_prefix = ""
                    for _src_doc_id in _src_doc_ids:
                        _src_doc = document_repository.get_document_by_id(db, _src_doc_id)
                        if _src_doc and _src_doc.content:
                            _src_prefix += make_source_context(_src_doc.filename, _src_doc.content)
                    if _src_prefix:
                        context = _src_prefix + context
            except Exception as _src_exc:
                _log.warning(
                    "Job %s could not load CDD source documents (non-fatal): %s",
                    job_id, _src_exc,
                )

        if context:
            context = trim_generation_context(context)

        # ── Stage 2 — Prompt assembly ─────────────────────────────────
        set_running(db, job, *STAGE_PROMPT)

        prompt = prompt_repository.get_prompt_by_name(db, p_framework)
        if not prompt or not prompt.active_version:
            set_failed(db, job, f"Prompt '{p_framework}' not found or has no active version.")
            return

        ver = prompt_repository.get_prompt_version(db, prompt.id, prompt.active_version)
        if not ver:
            set_failed(db, job, f"Active version not found for prompt '{p_framework}'.")
            return

        ctx_injection, used_cdd_label, used_bp_label = build_context_injection(
            db, eff_cdd_id, eff_bp_id, target_audience=target_audience
        )

        _active_style = get_active_style(db, project_id=project_id, course_id=course_id)
        _style_inj    = build_style_context(db, _active_style) if _active_style else ""

        User_prefix = PERSONA_PREFIX_TEMPLATE.format(
            expert_exp=expert_exp,
            expert_domain=expert_domain,
            aud_cat=aud_cat,
            target_audience=target_audience,
        )
        if _style_inj:
            User_prefix += (
                "\n\n--- ACTIVE INSTRUCTIONAL STYLE ---\n"
                "The following style definition MUST be applied to all content you generate. "
                "Tone, structure, vocabulary, and formatting must comply with these rules:\n\n"
                f"{_style_inj}\n"
                "--- END STYLE DEFINITION ---\n"
            )

        citation_instruction = (
            "\n\nIMPORTANT: Whenever you use information from a provided source, cite it as "
            "[Source: filename]. At the end of EACH block, include a 'Sources Used' list."
        )

        _comp_type = selected_component.get("type", "lesson")

        if eff_cdd_id or eff_bp_id:
            if _comp_type == "lesson":
                system_p = User_prefix + LESSON_WITH_CONTEXT_SYSTEM + citation_instruction
                user_p   = LESSON_WITH_CONTEXT_USER.format(
                    lesson_topic=topic,
                    lesson_title=topic,
                    lesson_objective=f"As defined in the Blueprint for '{topic}'",
                    content_type=b_type,
                    context_injection=ctx_injection,
                ) + context
            else:
                _bp_ver  = get_active_blueprint_version(db, eff_bp_id) if eff_bp_id else None
                _cdd_ver = get_active_cdd_version(db, eff_cdd_id) if eff_cdd_id else None
                _comp_sys_p, _comp_usr_p = build_component_generation_prompt(
                    selected_component, _bp_ver, _cdd_ver, target_audience, expert_domain,
                )
                system_p = User_prefix + _comp_sys_p + citation_instruction
                user_p   = _comp_usr_p + context
        else:
            system_p = User_prefix + ver.system_prompt + citation_instruction
            user_p   = fill_template(
                ver.user_prompt_template,
                {"topic": topic, "block_type": b_type, "target_audience": target_audience},
            ) + context

        # Append user's additional instructions if provided
        if extra_instructions and extra_instructions.strip():
            user_p += f"\n\n**Additional Instructions:**\n{extra_instructions.strip()}"

        # ── Stage 3 — LLM call ────────────────────────────────────────
        set_running(db, job, *STAGE_LLM)
        _llm_result = _llm_call(model_choice, system_p, user_p)

        # Log usage using the existing session (avoids extra connection overhead)
        log_llm_usage(db, _llm_result, UsageLogContext(
            user_name=user_name,
            project_id=project_id,
            course_id=course_id,
            entity_type="generation",
            prompt_template=p_framework,
            prompt_version=ver.version if ver else "",
        ))

        if _llm_result.is_error:
            _log.error(
                "Job %s LLM failed [model=%s type=%s duration=%.1fs]: %s",
                job_id, _llm_result.model, _llm_result.error_type,
                _llm_result.total_duration_s, _llm_result.text,
            )
            set_failed(db, job, f"ERROR: {_llm_result.text}")
            return

        out = _llm_result.text
        _log.info(
            "Job %s LLM done [model=%s status=%s tokens=%s/%s duration=%.1fs]",
            job_id, _llm_result.model, _llm_result.status,
            _llm_result.prompt_tokens, _llm_result.completion_tokens,
            _llm_result.total_duration_s,
        )

        # Strip internal authoring term "storyboard" from user-facing output
        out = re.sub(r'(?i)\bstoryboard\b', '', out)
        out = re.sub(r'  +', ' ', out)
        out = re.sub(r'(?m)^ +$', '', out)

        # ── Stage 4 — CE Validation ───────────────────────────────────
        set_running(db, job, *STAGE_CE_VALIDATION)
        try:
            from promptops_app.services.ce_validation_service import run_ce_validation
            out = run_ce_validation(
                out,
                db,
                active_style=_active_style,
                model_choice=model_choice,
                llm_call_fn=_llm_call,
            )
        except Exception as _ce_exc:
            _log.warning(
                "Job %s CE validation failed (non-fatal) — using original output: %s",
                job_id, _ce_exc,
            )

        # ── Stage 5 — Split into blocks ───────────────────────────────
        set_running(db, job, *STAGE_SPLIT)
        blocks = (
            split_into_blocks(out)
            if b_type in ("full_course", "Full Course")
            else [(b_type or "Body", 1, out, re.findall(r"\[Source:\s*(.*?)\]", out))]
        )

        # ── Stage 6 — Persist ─────────────────────────────────────────
        set_running(db, job, *STAGE_SAVE)

        used_cdd_ver = None
        used_bp_ver  = None
        if eff_cdd_id:
            cdd_v = get_active_cdd_version(db, eff_cdd_id)
            used_cdd_ver = cdd_v.version if cdd_v else None
        if eff_bp_id:
            bp_v = get_active_blueprint_version(db, eff_bp_id)
            used_bp_ver = bp_v.version if bp_v else None

        g_entry = Generation(
            topic=topic,
            prompt_name=p_framework,
            prompt_version=ver.version,
            block_type=b_type,
            output_text=out,
            created_by=user_name,
            cdd_id=eff_cdd_id,
            cdd_version=used_cdd_ver,
            blueprint_id=eff_bp_id,
            blueprint_version=used_bp_ver,
            project_id=project_id,
            course_id=course_id,
        )
        db.add(g_entry)
        db.commit()
        db.refresh(g_entry)

        saved_blocks = []
        for bt, ordr, cnt, srcs in blocks:
            _, eval_data, ai_rev_text = get_initial_quality_metadata(cnt, bt)
            blk = Block(
                generation_id=g_entry.id,
                block_type=bt,
                block_label=f"{bt.title()} - {topic}",
                content=cnt,
                sources=json.dumps(list(set(srcs))) if srcs else None,
                plagiarism_score=None,          # set by async Copyleaks scan
                plagiarism_report=None,
                eval_score=eval_data.get("structural_score", 0),
                eval_report=json.dumps(eval_data),
                ai_review=ai_rev_text,
            )
            db.add(blk)
            saved_blocks.append((blk, cnt))
        db.commit()   # flush so blocks have their PKs

        # ── Enqueue async Copyleaks scan for each block (non-blocking) ──────
        try:
            from promptops_app.database import PlagiarismReport
            from promptops_app.services.plagiarism_service import generate_scan_id
            from promptops_app.jobs.plagiarism_jobs import run_plagiarism_scan

            for blk, cnt in saved_blocks:
                scan_id = generate_scan_id()
                report  = PlagiarismReport(
                    block_id=blk.id,
                    project_id=project_id,
                    course_id=course_id,
                    scan_id=scan_id,
                    status="pending",
                )
                db.add(report)
                db.commit()
                db.refresh(report)

                task = run_plagiarism_scan.delay(report.id, cnt)
                report.celery_task_id = task.id
                db.commit()

        except Exception as _plag_exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Could not enqueue plagiarism scan (Celery/Redis not available?): %s",
                _plag_exc,
            )

        log_event(
            db,
            "generation",
            user_name,
            f"Generated '{topic}' ({b_type}) — CDD:{used_cdd_label} BP:{used_bp_label}",
            {
                "topic": topic, "block_type": b_type, "prompt": p_framework,
                "version": ver.version, "cdd": used_cdd_label, "blueprint": used_bp_label,
                "blocks": len(blocks),
            },
        )

        # ── Audit ─────────────────────────────────────────────────────
        log_audit_event(db, user_name, "content.generated", entity_type="generation",
                        entity_id=g_entry.id, project_id=project_id, course_id=course_id,
                        metadata={"topic": topic, "block_type": b_type, "prompt": p_framework,
                                  "blocks": len(blocks), "cdd": used_cdd_label, "blueprint": used_bp_label})

        # ── Done ──────────────────────────────────────────────────────
        job.result_json = json.dumps({
            "generation_id": g_entry.id,
            "blocks": len(blocks),
            "llm_model": _llm_result.model,
            "llm_status": _llm_result.status,
            "llm_prompt_tokens": _llm_result.prompt_tokens,
            "llm_completion_tokens": _llm_result.completion_tokens,
            "llm_duration_s": round(_llm_result.total_duration_s, 2),
        })
        set_completed(db, job, g_entry.id)
        _log.info(
            "generation_job.completed  job_id=%r  generation_id=%d  blocks=%d"
            "  model=%r  status=%r  prompt_tokens=%s  completion_tokens=%s"
            "  llm_duration=%.1fs  total_duration=%.1fs",
            job_id, g_entry.id, len(blocks),
            _llm_result.model, _llm_result.status,
            _llm_result.prompt_tokens, _llm_result.completion_tokens,
            _llm_result.total_duration_s, time.monotonic() - _job_start,
        )

    except Exception as exc:  # pragma: no cover
        _log.error(
            "generation_job.failed  job_id=%r  total_duration=%.1fs  error=%s",
            job_id, time.monotonic() - _job_start, exc, exc_info=True,
        )
        if job:
            try:
                set_failed(db, job, str(exc))
            except Exception:
                pass
    finally:
        db.close()
