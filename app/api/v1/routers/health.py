"""
Health check endpoint.

Used by:
  - Docker / ECS health checks to determine if the container is ready.
  - AWS load balancer target group health checks.
  - CI/CD pipeline smoke tests after deployment.

No authentication is required — the load balancer calls this endpoint
before routing real user traffic to the instance.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.database import check_database_connection

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response showing the status of each dependency."""

    status: str
    version: str
    database: str


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Application health check",
    description="Returns the health status of the API and its dependencies. No authentication required.",
)
def health_check() -> HealthResponse:
    """
    Check that the API is running and can reach the database.

    Returns HTTP 200 if healthy.  The load balancer removes the instance
    from the rotation if this endpoint returns a non-2xx status.
    """
    from app.core.config import settings

    db_status = "connected" if check_database_connection() else "unreachable"

    return HealthResponse(
        status="ok",
        version=settings.app_version,
        database=db_status,
    )
