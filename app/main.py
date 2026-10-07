"""
Content AI Studio — FastAPI application entry point.

This module is responsible for:
  - Creating and configuring the FastAPI application instance.
  - Registering all middleware (CORS, request logging, correlation IDs).
  - Registering the global exception handler so every AppError
    produces a consistent JSON error response.
  - Mounting the versioned API router (/api/v1/...).
  - Managing application lifespan (startup / shutdown hooks).

Usage
-----
    # Local development
    uvicorn app.main:app --reload --port 8000

    # Production (Docker)
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from promptops_app.core.logging import configure_logging

configure_logging()

from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.middleware import RequestLoggingMiddleware
from app.core.llm_client import initialise_llm_clients
from promptops_app.services.budget_service import BudgetExceededError

# Import agent builder models to register them with SQLAlchemy Base
# This must happen early to ensure all models are available for migrations
from promptops_app import agents_models as _agent_models  # noqa: F401

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — startup and shutdown logic
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once at startup and once at shutdown.

    Startup  : initialise shared resources (LLM clients, DB connection pool).
    Shutdown : release resources gracefully.
    """
    _log.info("startup  service=content-ai-api  version=%s", settings.app_version)

    # Run all DB migrations — creates missing tables AND adds any missing columns.
    # init_db() is idempotent: CREATE TABLE IF NOT EXISTS + ALTER TABLE ADD COLUMN
    # IF NOT EXISTS are both safe to run on every startup.
    from promptops_app.database import ensure_phoenix_database, init_db
    init_db()
    _log.info("startup_db_migrations_complete")
    #from promptops_app.database import ensure_phoenix_database, init_db
    #from app.core.database import wait_for_database

    # Wait for AWS RDS to become reachable before running migrations.
    # This handles EC2 + RDS being started at approximately the same time.
      #wait_for_database()

      #init_db()
      #_log.info("startup_db_migrations_complete")

    # Phoenix's own container can't create its own database — do it here,
    # once, idempotently, on every environment (local/dev/prod) without a
    # manual `CREATE DATABASE` step. Never blocks startup on failure.
    ensure_phoenix_database()

    # Initialise the OpenAI HTTP session and AWS Bedrock client once per
    # process.  This replaces the @st.cache_resource pattern from Streamlit.
    initialise_llm_clients()

    # Fail jobs that a previous process death (OOM kill, restart, redeploy) left
    # stranded at queued/running. Their run_* handlers never got to record an
    # error, so without this the row stays "running" forever and the UI shows a
    # phantom in-flight generation. No-ops when Celery owns the jobs.
    from promptops_app.jobs.reaper import reap_orphaned_jobs
    reap_orphaned_jobs()

    # CE review retention — prune old finding/result detail on startup. No-op
    # unless CE_REVIEW_ENABLED and CE_REVIEW_RETENTION_DAYS > 0. Never blocks startup.
    from promptops_app.services.ce_review.retention import run_startup_prune
    run_startup_prune()

    _log.info("startup_complete  llm_clients=ready")

    yield  # Application runs here

    # Phoenix's OTel BatchSpanProcessor batches trace exports asynchronously;
    # flush on the way out so a restart doesn't silently drop the last few seconds
    # of in-flight traces. Best-effort — Phoenix being unreachable at shutdown
    # must not block or fail the shutdown itself.
    try:
        from promptops_app.core.llm_client import flush_phoenix_traces
        flush_phoenix_traces()
    except Exception as exc:
        _log.debug("Phoenix flush on shutdown skipped: %s", exc)

    # Release reused DIS connection pools (P4.3/F7).
    try:
        from app.core.dis_client import dis_client
        await dis_client.aclose()
    except Exception:  # best-effort — never block shutdown on cleanup
        _log.warning("dis_client.aclose failed during shutdown", exc_info=True)

    _log.info("shutdown  service=content-ai-api")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_application() -> FastAPI:
    """
    Build and return the configured FastAPI application.

    Separating app creation into a factory function makes it easy to
    create isolated test instances without side effects.
    """
    application = FastAPI(
        title="Content AI Studio API",
        description=(
            "REST API for the Content AI Studio eLearning content generation platform. "
            "Provides endpoints for CDD generation, module blueprints, AI content "
            "generation, workflow approvals, and analytics."
        ),
        version=settings.app_version,
        docs_url="/docs",       # Swagger UI  — disable in prod if needed
        redoc_url="/redoc",     # ReDoc UI
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Allow the React frontend to call this API from a different origin.
    # In production, replace with the exact frontend domain.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],  # let the frontend read the correlation ID
    )

    # ── Request logging + correlation ID ─────────────────────────────────────
    # Adds a unique X-Request-ID to every request/response and logs
    # method, path, status code, and duration on completion.
    application.add_middleware(RequestLoggingMiddleware)

    # ── Global exception handler ──────────────────────────────────────────────
    # Converts every AppError subclass into a standard JSON error envelope.
    # Unhandled exceptions fall through to FastAPI's default 500 handler.
    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        """Convert domain exceptions into a consistent error response shape."""
        _log.warning(
            "app_error  code=%s  status=%d  path=%s  message=%s",
            exc.code, exc.status_code, request.url.path, exc.message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "detail": exc.detail,
                }
            },
        )

    # P2: a separate handler, not an AppError subclass — BudgetExceededError is
    # raised from promptops_app/services/budget_service.py, which has no
    # dependency on this app/ (FastAPI) layer anywhere else; the plain-exception
    # + dedicated-handler split keeps that boundary intact for this one case too.
    # 402, not 429 — 429 stays reserved for P5's separate rate limiting.
    @application.exception_handler(BudgetExceededError)
    async def budget_exceeded_handler(request: Request, exc: BudgetExceededError) -> JSONResponse:
        _log.warning(
            "quota_exceeded  scope=%s  scope_id=%s  path=%s",
            exc.scope, exc.scope_id, request.url.path,
        )
        return JSONResponse(
            status_code=402,
            content={
                "error": {
                    "code": "QUOTA_EXCEEDED",
                    "message": str(exc),
                    "detail": {
                        "scope": exc.scope,
                        "scope_id": exc.scope_id,
                        "limit_type": exc.limit_type,
                        "limit_usd": round(exc.limit_usd, 2),
                        "current_spend": round(exc.current_spend, 2),
                    },
                }
            },
        )

    # ── API routers ───────────────────────────────────────────────────────────
    # All routes live under /api/v1/... for clear versioning.
    application.include_router(api_v1_router, prefix="/api/v1")

    return application


# Module-level app instance used by uvicorn.
app = create_application()
