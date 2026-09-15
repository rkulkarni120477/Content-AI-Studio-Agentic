"""Background jobs for design-document generation and section/item regen.

Converts the formerly synchronous CDD / Blueprint / Style understand routes
into durable ``GenerationJob`` work so leaving the page cannot orphan the UI.
Workers lazily import the router ``execute_*`` helpers (same LLM + persist
paths) to avoid duplicating business logic and to keep import cycles out of
module load time.
"""
from __future__ import annotations

import json
import logging
import types

from promptops_app.database import GenerationJob, SessionLocal
from promptops_app.jobs.job_status import JobStatus, set_completed, set_failed, set_running
from promptops_app.services.budget_service import BudgetExceededError

_log = logging.getLogger(__name__)


def _reconstruct_user(params: dict) -> types.SimpleNamespace:
    name = params.get("user_name", "") or "cas-user"
    user_id = params.get("user_id")
    return types.SimpleNamespace(
        username=name,
        id=name if user_id is None else user_id,
        email=name,
        role=params.get("role", "user"),
    )


def _request_fields(model_cls, params: dict) -> dict:
    """Keep only fields the Pydantic request model accepts."""
    fields = getattr(model_cls, "model_fields", None) or getattr(model_cls, "__fields__", {})
    return {k: v for k, v in params.items() if k in fields}


def _load_job(db, job_id: str):
    job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
    if not job:
        _log.error("design job %s not found", job_id)
        return None
    if job.status == JobStatus.CANCELLED:
        _log.info("design job %s cancelled before start", job_id)
        return None
    if job.status == JobStatus.COMPLETED:
        _log.info("design job %s already completed — skipping", job_id)
        return None
    return job


def _fail(db, job, exc: Exception, generic: str) -> None:
    _log.exception("design job %s failed: %s", getattr(job, "id", "?"), exc)
    if job is None:
        return
    try:
        message = str(exc) if isinstance(exc, BudgetExceededError) else generic
        # HTTPException / ValidationError often carry a useful detail string.
        detail = getattr(exc, "detail", None)
        if isinstance(detail, str) and detail.strip() and not isinstance(exc, BudgetExceededError):
            message = detail.strip()[:2000]
        set_failed(db, job, message)
    except Exception:  # pragma: no cover
        pass


def _store_model_result(job, response) -> None:
    """Persist a Pydantic (or dict-like) response into job.result_json."""
    if hasattr(response, "model_dump"):
        payload = response.model_dump()
    elif hasattr(response, "dict"):
        payload = response.dict()
    elif isinstance(response, dict):
        payload = response
    else:
        payload = {"value": str(response)}
    job.result_json = json.dumps(payload, ensure_ascii=False, default=str)


def run_cdd_generate_job(job_id: str) -> None:
    db = SessionLocal()
    job = None
    try:
        job = _load_job(db, job_id)
        if job is None:
            return
        from app.api.v1.routers.cdd import execute_cdd_generate
        from app.schemas.cdd import CDDGenerateRequest

        params = json.loads(job.request_json or "{}")
        request = CDDGenerateRequest(**_request_fields(CDDGenerateRequest, params))
        user = _reconstruct_user(params)
        set_running(db, job, 15, "Generating CDD...")
        result = execute_cdd_generate(db, request, user)
        _store_model_result(job, result)
        db.commit()
        set_completed(db, job, result.cdd_id)
        _log.info("cdd_generate_job_done job=%s cdd_id=%s", job_id, result.cdd_id)
    except Exception as exc:  # noqa: BLE001
        _fail(db, job, exc, "CDD generation failed. Please try again.")
    finally:
        db.close()


def run_blueprint_generate_job(job_id: str) -> None:
    db = SessionLocal()
    job = None
    try:
        job = _load_job(db, job_id)
        if job is None:
            return
        from app.api.v1.routers.blueprints import execute_blueprint_generate
        from app.schemas.blueprint import BlueprintGenerateRequest

        params = json.loads(job.request_json or "{}")
        request = BlueprintGenerateRequest(**_request_fields(BlueprintGenerateRequest, params))
        user = _reconstruct_user(params)
        set_running(db, job, 15, "Generating blueprint...")
        result = execute_blueprint_generate(db, request, user)
        _store_model_result(job, result)
        db.commit()
        set_completed(db, job, result.blueprint_id)
        _log.info("blueprint_generate_job_done job=%s bp_id=%s", job_id, result.blueprint_id)
    except Exception as exc:  # noqa: BLE001
        _fail(db, job, exc, "Blueprint generation failed. Please try again.")
    finally:
        db.close()


def run_style_understand_job(job_id: str) -> None:
    db = SessionLocal()
    job = None
    try:
        job = _load_job(db, job_id)
        if job is None:
            return
        from app.api.v1.routers.styles import execute_style_understand
        from app.schemas.style import StyleUnderstandRequest

        params = json.loads(job.request_json or "{}")
        style_id = int(params["style_id"])
        request = StyleUnderstandRequest(**_request_fields(StyleUnderstandRequest, params))
        user = _reconstruct_user(params)
        set_running(db, job, 20, "Generating style understanding...")
        result = execute_style_understand(db, style_id, request, user)
        _store_model_result(job, result)
        db.commit()
        set_completed(db, job, style_id)
        _log.info("style_understand_job_done job=%s style_id=%s", job_id, style_id)
    except Exception as exc:  # noqa: BLE001
        _fail(db, job, exc, "Style understanding failed. Please try again.")
    finally:
        db.close()


def run_cdd_regen_item_job(job_id: str) -> None:
    db = SessionLocal()
    job = None
    try:
        job = _load_job(db, job_id)
        if job is None:
            return
        from app.api.v1.routers.cdd import execute_cdd_regen_item
        from app.schemas.cdd import CDDRegenerateItemRequest

        params = json.loads(job.request_json or "{}")
        cdd_id = int(params["cdd_id"])
        request = CDDRegenerateItemRequest(**_request_fields(CDDRegenerateItemRequest, params))
        user = _reconstruct_user(params)
        set_running(db, job, 30, "Regenerating CDD item...")
        result = execute_cdd_regen_item(db, cdd_id, request, user)
        _store_model_result(job, result)
        db.commit()
        set_completed(db, job, cdd_id)
    except Exception as exc:  # noqa: BLE001
        _fail(db, job, exc, "CDD item regeneration failed. Please try again.")
    finally:
        db.close()


def run_cdd_regen_section_job(job_id: str) -> None:
    db = SessionLocal()
    job = None
    try:
        job = _load_job(db, job_id)
        if job is None:
            return
        from app.api.v1.routers.cdd import execute_cdd_regen_section
        from app.schemas.cdd import CDDRegenerateSectionRequest

        params = json.loads(job.request_json or "{}")
        cdd_id = int(params["cdd_id"])
        request = CDDRegenerateSectionRequest(**_request_fields(CDDRegenerateSectionRequest, params))
        user = _reconstruct_user(params)
        set_running(db, job, 30, "Regenerating CDD section...")
        result = execute_cdd_regen_section(db, cdd_id, request, user)
        _store_model_result(job, result)
        db.commit()
        set_completed(db, job, cdd_id)
    except Exception as exc:  # noqa: BLE001
        _fail(db, job, exc, "CDD section regeneration failed. Please try again.")
    finally:
        db.close()


def run_blueprint_regen_item_job(job_id: str) -> None:
    db = SessionLocal()
    job = None
    try:
        job = _load_job(db, job_id)
        if job is None:
            return
        from app.api.v1.routers.blueprints import execute_blueprint_regen_item
        from app.schemas.blueprint import BlueprintRegenerateItemRequest

        params = json.loads(job.request_json or "{}")
        blueprint_id = int(params["blueprint_id"])
        request = BlueprintRegenerateItemRequest(
            **_request_fields(BlueprintRegenerateItemRequest, params)
        )
        user = _reconstruct_user(params)
        set_running(db, job, 30, "Regenerating blueprint item...")
        result = execute_blueprint_regen_item(db, blueprint_id, request, user)
        _store_model_result(job, result)
        db.commit()
        set_completed(db, job, blueprint_id)
    except Exception as exc:  # noqa: BLE001
        _fail(db, job, exc, "Blueprint item regeneration failed. Please try again.")
    finally:
        db.close()


def run_blueprint_regen_section_job(job_id: str) -> None:
    db = SessionLocal()
    job = None
    try:
        job = _load_job(db, job_id)
        if job is None:
            return
        from app.api.v1.routers.blueprints import execute_blueprint_regen_section
        from app.schemas.blueprint import BlueprintRegenerateSectionRequest

        params = json.loads(job.request_json or "{}")
        blueprint_id = int(params["blueprint_id"])
        request = BlueprintRegenerateSectionRequest(
            **_request_fields(BlueprintRegenerateSectionRequest, params)
        )
        user = _reconstruct_user(params)
        set_running(db, job, 30, "Regenerating blueprint section...")
        result = execute_blueprint_regen_section(db, blueprint_id, request, user)
        _store_model_result(job, result)
        db.commit()
        set_completed(db, job, blueprint_id)
    except Exception as exc:  # noqa: BLE001
        _fail(db, job, exc, "Blueprint section regeneration failed. Please try again.")
    finally:
        db.close()
