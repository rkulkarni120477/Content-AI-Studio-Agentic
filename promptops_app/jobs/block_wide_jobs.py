"""Background job for block-wide CDD / Block Blueprint generation (§3.9).

Block-wide generation runs the digest pipeline (a cold build is ~20 MAP calls),
so it runs off the request thread on the same generic infra as every other job:
a plain function submitted to ``job_runner``'s ThreadPoolExecutor, opening its own
``SessionLocal`` and driving progress via the shared ``job_status`` helpers. It
never touches ``run_generation_job`` (isolation, like import_jobs.py).

The heavy digest generation + persistence live in
``promptops_app.services.block_wide_service`` and are shared with the synchronous
router branches, so there is no logic duplication and no router↔jobs import cycle.

On success ``job.result_entity_id`` is the created CDD / Blueprint id and
``job.result_json`` carries the CoverageReport; the frontend polls
``GET /api/v1/jobs/{job_id}`` and then fetches that entity.
"""
from __future__ import annotations

import json
import logging
import types

from promptops_app.database import GenerationJob, SessionLocal
from promptops_app.jobs.job_status import JobStatus, set_completed, set_failed, set_running
from promptops_app.services import block_wide_service

_log = logging.getLogger(__name__)

STAGE_BUILD = (20, "Building day digests...")
# No intermediate "reducing" stage: build+reduce happen inside one opaque call to
# generate_cdd_via_digests/generate_blueprint_via_digests (block_wide_service.py),
# which has no progress callback — the job sits at STAGE_BUILD for that whole
# (usually longest) phase, then jumps straight to STAGE_SAVE.
STAGE_SAVE = (90, "Saving output...")


def _reconstruct_request(params: dict) -> types.SimpleNamespace:
    """Rebuild a request-like object the service helpers read via getattr."""
    return types.SimpleNamespace(
        deliverable=params.get("deliverable", "cdd"),
        block=params.get("block"),
        quality_tier=params.get("quality_tier"),
        course_id=params.get("course_id"),
        project_id=params.get("project_id"),
        course_title=params.get("course_title", ""),
        document_title=params.get("document_title"),
        target_audience=params.get("target_audience", ""),
        expert_domain=params.get("expert_domain", ""),
        audience_category=params.get("audience_category", ""),
        estimated_duration_hours=params.get("estimated_duration_hours"),
        extra_instructions=params.get("extra_instructions", ""),
        model_choice=params.get("model_choice", ""),
        cdd_id=params.get("cdd_id"),
        prompt_id=params.get("prompt_id"),
    )


def _reconstruct_user(params: dict) -> types.SimpleNamespace:
    name = params.get("user_name", "") or "cas-user"
    return types.SimpleNamespace(username=name, id=name, email=name, role=params.get("role", "user"))


def _audit_failure(db, deliverable: str, req, user, job_id: str, reason: str,
                   *, map_guidance_applied: bool | None = None) -> None:
    """Record a ``*.block_failed`` audit event.

    Closes the async gap: the request is audited at enqueue and success is audited by
    the persist tail, so without this a failed run would show a request with no
    recorded outcome. Mirrors ``log_audit_event``'s own contract — audit logging must
    never be the reason a worker crashes — so every failure here is swallowed after
    logging, and the caller is already inside an error path.
    """
    from promptops_app.services.audit_service import log_audit_event

    action = "blueprint.block_failed" if deliverable == "blueprint" else "cdd.block_failed"
    entity = "blueprint" if deliverable == "blueprint" else "cdd"
    metadata = {
        "job_id": job_id,
        "block": getattr(req, "block", None),
        "quality_tier": getattr(req, "quality_tier", None) or "standard",
        "model_choice": getattr(req, "model_choice", ""),
        "prompt_id": getattr(req, "prompt_id", None),
        "reason": reason,
    }
    if map_guidance_applied is not None:
        metadata["map_guidance_applied"] = map_guidance_applied
    try:
        log_audit_event(
            db, getattr(user, "username", "") or "cas-user", action,
            entity_type=entity, entity_id=None,
            project_id=getattr(req, "project_id", None),
            course_id=getattr(req, "course_id", None),
            metadata=metadata,
        )
    except Exception:
        _log.exception("Block-wide job %s: audit failure event not written", job_id)


def run_block_wide_job(job_id: str) -> None:
    """Generate a block-wide CDD or Block Blueprint via the digest pipeline.

    Single ``job_id`` argument → Celery-compatible, like ``run_generation_job``.
    """
    db = SessionLocal()
    job = None
    try:
        job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if not job:
            _log.error("Block-wide job %s not found", job_id)
            return
        if job.status == JobStatus.CANCELLED:
            _log.info("Block-wide job %s cancelled before start", job_id)
            return

        params = json.loads(job.request_json)
        deliverable = params.get("deliverable", "cdd")
        req = _reconstruct_request(params)
        user = _reconstruct_user(params)
        dis_client_id = params.get("dis_client_id") or ""
        if not dis_client_id:
            from app.core.dis_access import resolve_course_dis_client
            dis_client_id = resolve_course_dis_client(
                db, course_id=req.course_id, project_id=req.project_id,
            )

        set_running(db, job, *STAGE_BUILD)

        from promptops_app.services.prompt_guidance import resolve_prompt_guidance
        map_guidance = resolve_prompt_guidance(db, req, deliverable, user)

        if deliverable == "blueprint":
            gen = block_wide_service.generate_blueprint_via_digests(db, req, user, dis_client_id, map_guidance)
        else:
            gen = block_wide_service.generate_cdd_via_digests(db, req, user, dis_client_id, map_guidance)

        if gen is None:
            set_failed(db, job, "Digest pipeline unavailable (no enumerated days or DIS error).")
            _audit_failure(db, deliverable, req, user, job_id,
                           "Digest pipeline unavailable (no enumerated days or DIS error).",
                           map_guidance_applied=bool((map_guidance or "").strip()))
            _log.warning("Block-wide job %s: digest pipeline returned no result", job_id)
            return

        set_running(db, job, *STAGE_SAVE)
        if deliverable == "blueprint":
            resp = block_wide_service.persist_blueprint_and_respond(db, req, user, **gen)
            entity_id = resp.blueprint_id
        else:
            resp = block_wide_service.persist_cdd_and_respond(db, req, user, **gen)
            entity_id = resp.cdd_id

        job.result_json = json.dumps({
            "deliverable": deliverable,
            "entity_id": entity_id,
            "coverage": gen.get("coverage"),
            "model_used": gen.get("model_used"),
        }, ensure_ascii=False)
        set_completed(db, job, entity_id)
        _log.info("Block-wide job %s completed: %s id=%s", job_id, deliverable, entity_id)

    except Exception as exc:  # noqa: BLE001 — worker boundary: never let a job crash silently
        _log.exception("Block-wide job %s failed", job_id)
        if job is not None:
            # A prior commit inside the persist tail may have poisoned the session
            # (PendingRollbackError); without rolling back first, set_failed's own
            # commit would raise and the job would be stuck in RUNNING forever.
            try:
                db.rollback()
            except Exception:
                pass
            try:
                set_failed(db, job, "Block-wide generation failed. Please try again.")
            except Exception:
                _log.exception("Block-wide job %s: could not mark failed", job_id)
            # Audit the failure too. `job` is set, but `deliverable`/`req`/`user` may
            # not be if the exception fired before they were parsed — re-derive them
            # from the job row defensively so an early crash is still audited rather
            # than silently leaving only a request event with no outcome.
            try:
                params = json.loads(job.request_json or "{}")
                _audit_failure(db, params.get("deliverable", "cdd"),
                               _reconstruct_request(params), _reconstruct_user(params),
                               job_id, f"{type(exc).__name__}: {exc}")
            except Exception:
                _log.exception("Block-wide job %s: could not audit failure", job_id)
    finally:
        db.close()
