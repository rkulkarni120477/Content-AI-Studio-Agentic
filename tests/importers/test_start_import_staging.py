"""Regression test: a staged import package must survive to whatever process
runs the import job.

tempfile.mkstemp()'s default directory is local to the process's container;
when PROMPTOPS_USE_CELERY is on, the job runs in the celery_worker container,
not the api container that received the upload, and can't see it there. That
produced "Invalid IMSCC package: Uploaded package is no longer available on
the server." at Start Import. The fix stages the package under the repo root
instead (app.api.v1.routers.imports._IMPORT_STAGING_DIR), which both
containers bind-mount to the same host directory.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.v1.routers.imports import _IMPORT_STAGING_DIR, project_router
from app.core.dependencies import get_current_user, get_db
from app.core.exceptions import AppError
from tests.importers.fixtures import build_sample_imscc


@pytest.fixture()
def start_import_client(db, monkeypatch):
    from promptops_app.jobs import dispatch

    captured = {}
    monkeypatch.setattr(
        dispatch, "submit",
        lambda fn, job_id: captured.setdefault("job_id", job_id),
    )

    app = FastAPI()

    @app.exception_handler(AppError)
    async def _handle_app_error(_request, exc: AppError):  # noqa: ANN001
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": getattr(exc, "message", str(exc))},
        )

    app.include_router(project_router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        username="tester", role="admin", id=1,
    )
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client, captured


def test_staged_package_lives_under_the_shared_repo_dir_not_os_tempdir(start_import_client, db):
    from promptops_app.database import GenerationJob, Project

    project = Project(name="Import Test Co", is_active=True)
    db.add(project)
    db.commit()
    db.refresh(project)

    client, captured = start_import_client
    resp = client.post(
        f"/projects/{project.id}/imports",
        files={"file": ("sample.imscc", build_sample_imscc(), "application/zip")},
        data={"name": "Imported Course"},
    )
    assert resp.status_code == 201, resp.text

    job = db.query(GenerationJob).filter(GenerationJob.id == captured["job_id"]).first()
    package_path = json.loads(job.request_json)["package_path"]

    try:
        assert os.path.commonpath([package_path, _IMPORT_STAGING_DIR]) == _IMPORT_STAGING_DIR
        assert os.path.isfile(package_path)
    finally:
        if os.path.isfile(package_path):
            os.remove(package_path)
