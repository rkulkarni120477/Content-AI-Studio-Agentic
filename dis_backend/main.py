"""
DIS – Dynamic Ingestion System
Run locally:  uvicorn main:app --reload --port 8000
"""
import logging, time
from contextlib import asynccontextmanager
from copy import deepcopy
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from api.middleware.auth import TenantAuthMiddleware
from api.routers import admin, auth, ingestion, context, generated_documents
from config.settings import get_settings, get_tenant_registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s – %(message)s")
log = logging.getLogger("dis")

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("=== DIS v%s starting (env=%s) ===", settings.app_version, settings.environment)
    registry = get_tenant_registry()
    log.info("Loaded %d client(s)", len(registry.all_tenants()))
    for t in registry.all_tenants():
        log.info("  client=%s namespace=%s storage=%s", t.tenant_id, t.namespace, t.storage.provider)
        # D4 follow-up: fail loudly at startup if a tenant enables the digest MAP
        # fan-out but langgraph is missing, so the sequential fallback can't
        # silently mask a broken install (plan §5.1 / D4).
        if getattr(t.pipeline, "digest_fanout_enabled", False):
            from services.digests.graph import langgraph_available
            if not langgraph_available():
                log.warning(
                    "client=%s has pipeline.digest_fanout_enabled=true but langgraph "
                    "is not importable — digest builds will fall back to the sequential "
                    "loop. Install langgraph (pinned in requirements.txt) or disable the flag.",
                    t.tenant_id,
                )
    yield
    log.info("=== DIS shutting down ===")

settings = get_settings()

# Default docs/openapi are disabled so we can expose role-specific Swagger views.
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="DIS Ingestion System with role-based auth, config-driven document processing, parallel ingestion, and Studio context retrieval",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(TenantAuthMiddleware)

@app.middleware("http")
async def timing(request: Request, call_next):
    """Add timing header without masking real route errors.

    Starlette can raise RuntimeError("No response returned.") when the
    client disconnects or an upload is interrupted while BaseHTTPMiddleware is
    waiting for the downstream response. Previously this middleware converted
    that into a noisy unhandled traceback. Return a clear JSON error instead so
    CAS can show the real issue and DIS keeps serving later requests.
    """
    t = time.monotonic()
    try:
        resp = await call_next(request)
    except RuntimeError as exc:
        if "No response returned" in str(exc):
            log.warning("No response returned for %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=499,
                content={"detail": "Client disconnected before DIS returned a response"},
            )
        raise
    resp.headers["X-Response-Time-Ms"] = f"{(time.monotonic()-t)*1000:.1f}"
    return resp

@app.exception_handler(Exception)
async def global_error(request: Request, exc: Exception):
    log.exception("Unhandled: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Internal error", "type": type(exc).__name__})

PREFIX = settings.api_prefix
app.include_router(auth.router, prefix=PREFIX)
app.include_router(ingestion.router, prefix=PREFIX)
app.include_router(context.router, prefix=PREFIX)
app.include_router(generated_documents.router, prefix=PREFIX)
app.include_router(admin.router, prefix=PREFIX)


def _base_openapi_schema():
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Paste only the JWT access token, or use Bearer <token>."
    }
    for path, methods in schema.get("paths", {}).items():
        if path in ("/", "/health", f"{PREFIX}/auth/token"):
            continue
        for method, operation in methods.items():
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                operation.setdefault("security", [{"BearerAuth": []}])
    return schema


def _allow_path_for_role(path: str, role: str) -> bool:
    # Auth and health are visible in all role-specific docs.
    if path in ("/", "/health", f"{PREFIX}/auth/token"):
        return True

    if role == "super_admin":
        return True

    if role == "client_admin":
        allowed_prefixes = (
            f"{PREFIX}/ingest/",
            f"{PREFIX}/context/",
        )
        allowed_exact = {
            f"{PREFIX}/admin/client-config",
        }
        # Hide platform-only admin endpoints from client admin docs.
        return path.startswith(allowed_prefixes) or path in allowed_exact

    # Normal user: no admin and no ingestion. Only Studio context APIs.
    if role == "user":
        return path.startswith(f"{PREFIX}/context/")

    return False


def _openapi_for_role(role: str):
    schema = deepcopy(_base_openapi_schema())
    schema["info"]["title"] = app.title
    paths = schema.get("paths", {})
    schema["paths"] = {p: m for p, m in paths.items() if _allow_path_for_role(p, role)}
    # Clean tags list according to remaining operations.
    used_tags = set()
    for methods in schema["paths"].values():
        for op in methods.values():
            for tag in op.get("tags", []):
                used_tags.add(tag)
    if "tags" in schema:
        schema["tags"] = [t for t in schema["tags"] if t.get("name") in used_tags]
    return schema


@app.get("/openapi-super-admin.json", include_in_schema=False)
async def openapi_super_admin():
    return _openapi_for_role("super_admin")

@app.get("/openapi-client-admin.json", include_in_schema=False)
async def openapi_client_admin():
    return _openapi_for_role("client_admin")

@app.get("/openapi-user.json", include_in_schema=False)
async def openapi_user():
    return _openapi_for_role("user")

@app.get("/docs", include_in_schema=False)
async def docs_default():
    # Default to client-admin docs because that is the common local testing role.
    return RedirectResponse(url="/docs-client-admin")

@app.get("/docs-super-admin", include_in_schema=False)
async def docs_super_admin():
    return get_swagger_ui_html(openapi_url="/openapi-super-admin.json", title="DIS Ingestion System")

@app.get("/docs-client-admin", include_in_schema=False)
async def docs_client_admin():
    return get_swagger_ui_html(openapi_url="/openapi-client-admin.json", title="DIS Ingestion System")

@app.get("/docs-user", include_in_schema=False)
async def docs_user():
    return get_swagger_ui_html(openapi_url="/openapi-user.json", title="DIS Ingestion System")

#: External binaries an extractor shells out to. A missing one does not crash
#: anything — the extractor logs and returns empty text, the pipeline indexes the
#: document as a success, and the content is silently gone. 110 of AIM's 121 .doc
#: files were lost exactly that way, undetected for a month, because nothing ever
#: asked whether the binary was there. Reported here so the answer is one request
#: away instead of buried per-file in ingestion logs.
_EXTRACTOR_BINARIES = {
    "antiword": "pre-2007 binary .doc (OLE2) text extraction",
    "tesseract": "OCR for image-only DOCX exam figures and scanned pages",
}


def _extractor_dependencies() -> dict:
    import shutil
    return {name: {"present": shutil.which(name) is not None, "used_for": why}
            for name, why in _EXTRACTOR_BINARIES.items()}


@app.get("/health")
async def health():
    registry = get_tenant_registry()
    deps = _extractor_dependencies()
    missing = [n for n, d in deps.items() if not d["present"]]
    return {
        # Degraded, not ok: the service answers requests, but any upload of an
        # affected type will be accepted and silently yield nothing.
        "status": "ok" if not missing else "degraded",
        "version": settings.app_version,
        "environment": settings.environment,
        "clients": len(registry.all_tenants()),
        "extractor_dependencies": deps,
        "degraded_reason": (
            f"missing extractor binaries: {', '.join(missing)} — uploads of the "
            f"affected types will extract to empty text"
        ) if missing else None,
    }

@app.get("/")
async def root():
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "docs": {
            "super_admin": "/docs-super-admin",
            "client_admin": "/docs-client-admin",
            "user": "/docs-user",
        },
    }
