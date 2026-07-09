"""
API v1 master router — registers all feature sub-routers.

Adding a new module:
  1. Create  app/api/v1/routers/<module>.py  following the cdd.py pattern.
  2. Create  app/schemas/<module>.py         following the cdd.py schema pattern.
  3. Import and include the router below.
  4. Add the corresponding schema file.

Route prefix structure
----------------------
  /api/v1/health            Health check (no auth)
  /api/v1/auth/...          Authentication
  /api/v1/workspace/...     Session workspace state
  /api/v1/projects/...      Project management
  /api/v1/courses/...       Course management  (also /api/v1/*)
  /api/v1/users/...         User management
  /api/v1/styles/...        Style management
  /api/v1/documents/...     Reference document library
  /api/v1/cdd/...           Course Design Document pipeline
  /api/v1/blueprints/...    Module Blueprint pipeline
  /api/v1/generations/...   Content generation jobs
  /api/v1/jobs/...          Background job status
  /api/v1/blocks/...        Block editor  (also /api/v1/generations/*/blocks)
  /api/v1/workflow/...      Approval workflow
  /api/v1/prompts/...       Prompt template registry
  /api/v1/analytics/...     Metrics and observability
  /api/v1/admin/...         System administration
"""

from fastapi import APIRouter

from app.api.v1.routers.admin import router as admin_router
from app.api.v1.routers.analytics import router as analytics_router
from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.blocks import router as blocks_router
from app.api.v1.routers.blueprints import router as blueprints_router
from app.api.v1.routers.cdd import router as cdd_router
from app.api.v1.routers.cluster_prompts import router as cluster_prompts_router
from app.api.v1.routers.clusters import router as clusters_router
from app.api.v1.routers.courses import router as courses_router
from app.api.v1.routers.documents import router as documents_router
from app.api.v1.routers.generations import router as generations_router
from app.api.v1.routers.health import router as health_router
from app.api.v1.routers.jobs import router as jobs_router
from app.api.v1.routers.platform_tenants import router as platform_tenants_router
from app.api.v1.routers.projects import router as projects_router
from app.api.v1.routers.prompt_library import router as prompt_library_router
from app.api.v1.routers.prompts import router as prompts_router
from app.api.v1.routers.styles import router as styles_router
from app.api.v1.routers.users import router as users_router
from app.api.v1.routers.workflow import router as workflow_router
from app.api.v1.routers.workspace import router as workspace_router

api_v1_router = APIRouter()

# ── No authentication required ────────────────────────────────────────────────
api_v1_router.include_router(health_router, tags=["Health"])

# ── Authentication ────────────────────────────────────────────────────────────
api_v1_router.include_router(auth_router,      prefix="/auth",       tags=["Authentication"])

# ── Workspace ─────────────────────────────────────────────────────────────────
api_v1_router.include_router(workspace_router, prefix="/workspace",   tags=["Workspace"])

# ── Project hierarchy ─────────────────────────────────────────────────────────
api_v1_router.include_router(projects_router,  prefix="/projects",   tags=["Projects"])
api_v1_router.include_router(clusters_router,                        tags=["Clusters"])
api_v1_router.include_router(cluster_prompts_router,                 tags=["Cluster Prompts"])
api_v1_router.include_router(courses_router,                         tags=["Courses"])

# ── User management ───────────────────────────────────────────────────────────
api_v1_router.include_router(users_router,     prefix="/users",      tags=["Users"])

# ── Content infrastructure ────────────────────────────────────────────────────
api_v1_router.include_router(styles_router,    prefix="/styles",     tags=["Styles"])
api_v1_router.include_router(documents_router, prefix="/documents",  tags=["Documents"])

# ── Content pipeline ──────────────────────────────────────────────────────────
api_v1_router.include_router(cdd_router,        prefix="/cdd",        tags=["CDD — Course Design Document"])
api_v1_router.include_router(blueprints_router, prefix="/blueprints", tags=["Blueprints"])
api_v1_router.include_router(generations_router,prefix="/generations",tags=["Generations"])
api_v1_router.include_router(jobs_router,       prefix="/jobs",       tags=["Jobs"])

# ── Prompt registry ───────────────────────────────────────────────────────────
api_v1_router.include_router(prompts_router,   prefix="/prompts",    tags=["Prompts"])

# ── Prompt Library (ported standalone app; replaces the Central Repository UI) ──
api_v1_router.include_router(prompt_library_router, prefix="/prompt-library", tags=["Prompt Library"])

# ── Editor and workflow ───────────────────────────────────────────────────────
api_v1_router.include_router(blocks_router,    tags=["Blocks"])
api_v1_router.include_router(workflow_router,  prefix="/workflow",   tags=["Workflow"])

# ── Observability ─────────────────────────────────────────────────────────────
api_v1_router.include_router(analytics_router, prefix="/analytics",  tags=["Analytics"])

# ── System administration ─────────────────────────────────────────────────────
api_v1_router.include_router(admin_router,     prefix="/admin",      tags=["Admin"])

# ── Platform — tenant (organization) management (platform super-admin only) ───
api_v1_router.include_router(
    platform_tenants_router,
    prefix="/platform/tenants",
    tags=["Platform — Tenant Management"],
)
