"""Integration test for POST /imports/validate (Session 1).

Self-contained: the imports router is mounted on a fresh app and the auth
dependency is overridden, so the test does not depend on the global
IMPORT_COURSES_ENABLED flag (which gates the router in the real app) or on a DB.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.v1.routers.imports import router as imports_router
from app.core.dependencies import get_current_user
from app.core.exceptions import AppError
from tests.importers.fixtures import build_sample_imscc


@pytest.fixture()
def validate_client():
    app = FastAPI()

    @app.exception_handler(AppError)
    async def _handle_app_error(_request, exc: AppError):  # noqa: ANN001
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": getattr(exc, "message", str(exc))},
        )

    app.include_router(imports_router, prefix="/imports")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        username="tester", role="admin"
    )
    with TestClient(app) as client:
        yield client


def test_validate_returns_structure_counts(validate_client):
    resp = validate_client.post(
        "/imports/validate",
        files={"file": ("sample.imscc", build_sample_imscc(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["course_title"] == "Sample Course"
    assert body["structure_counts"] == {
        "modules": 2, "pages": 2, "quizzes": 1,
        "assignments": 0, "discussions": 0, "resources": 1,
    }
    assert body.get("package_format", "imscc") == "imscc"
    assert any("External Tool" in w for w in body["warnings"])


def test_validate_cendoc_package(validate_client):
    from tests.importers.fixtures import build_sample_cendoc

    resp = validate_client.post(
        "/imports/validate",
        files={"file": ("cendoc.zip", build_sample_cendoc(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["course_title"] == "Sample Cendoc Book"
    assert body["package_format"] == "cendoc"
    assert body["structure_counts"]["modules"] >= 1
    assert body["structure_counts"]["pages"] >= 2


def test_validate_rejects_corrupt_package(validate_client):
    resp = validate_client.post(
        "/imports/validate",
        files={"file": ("broken.imscc", b"not a zip", "application/zip")},
    )
    assert resp.status_code == 422, resp.text
