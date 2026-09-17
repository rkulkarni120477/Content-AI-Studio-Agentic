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


def stamp_job_user(params: dict, current_user) -> dict:
    """Persist the caller identity the worker needs to reconstruct.

    ``_get_*_or_404`` helpers read ``_is_platform_admin`` and ``_project_id``
    off the request user. Storing only username/role made the worker rebuild a
    user that fails every tenant check, so Style Understand (and CDD/Blueprint
    regen) jobs died immediately with a generic failure message.
    """
    params["user_name"] = getattr(current_user, "username", "") or "cas-user"
    params["user_id"] = getattr(current_user, "id", None)
    params["role"] = getattr(current_user, "role", "user")
    params["is_platform_admin"] = bool(getattr(current_user, "_is_platform_admin", False))
    params["user_project_id"] = getattr(current_user, "_project_id", None)
    return params


def _reconstruct_user(params: dict) -> types.SimpleNamespace:
    name = params.get("user_name", "") or "cas-user"
    user_id = params.get("user_id")
    is_platform_admin = bool(params.get("is_platform_admin", False))
    user_project_id = params.get("user_project_id")
    if user_project_id is None and not is_platform_admin:
        # Jobs queued before user_project_id was persisted still carry the
        # workspace project_id from the original request body. Using it keeps
        # _get_*_or_404 from 404ing a row the HTTP handler already authorized.
        user_project_id = params.get("project_id")
    return types.SimpleNamespace(
        username=name,
        id=name if user_id is None else user_id,
        email=name,
        role=params.get("role", "user"),
        _is_platform_admin=is_platform_admin,
        _project_id=user_project_id,
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
        from app.core.exceptions import AppError

        if isinstance(exc, BudgetExceededError):
            message = str(exc)
        elif isinstance(exc, AppError) and str(getattr(exc, "message", "") or "").strip():
            # AppError.detail is a dict, so the string-detail branch below
            # never fired — LLM / validation / 404 reasons were replaced with
            # the generic "please try again" the activity bell shows.
            message = str(exc.message).strip()[:2000]
        else:
            detail = getattr(exc, "detail", None)
            message = (
                detail.strip()[:2000]
                if isinstance(detail, str) and detail.strip()
                else generic
            )
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
