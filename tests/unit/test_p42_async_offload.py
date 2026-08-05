"""P4.2 — blocking sync DB work no longer runs on the event loop.

Two fixes:
  * ``documents.upload_document`` is now a *sync* route, so FastAPI offloads the
    whole handler (file parse + DB commit) to its worker threadpool.
  * ``source_library`` routes stay async (they await DIS httpx calls) but the
    blocking DB-backed client resolution is pushed to the threadpool via
    ``_resolved_client_async``; behaviour must match the sync version exactly.
"""

from __future__ import annotations

import asyncio
import inspect


def test_upload_document_is_sync_route():
    """A sync route → FastAPI threadpool-offloads it (no event-loop blocking)."""
    from app.api.v1.routers.documents import upload_document

    assert not inspect.iscoroutinefunction(upload_document)


def test_resolved_client_async_is_coroutine():
    from app.api.v1.routers.source_library import _resolved_client_async

    assert inspect.iscoroutinefunction(_resolved_client_async)


def test_resolved_client_async_matches_sync(db, monkeypatch):
    """The threadpool-offloaded resolver returns exactly what the sync path does
    for the same scope — the offload is transparent, not a behaviour change."""
    from app.api.v1.routers import source_library
    from promptops_app.database import Course, Project

    # Isolate the DB-backed scope resolution from the DIS access-config layer.
    monkeypatch.setattr(
        source_library, "_allowed_client_for_user", lambda user, client_id="": client_id
    )

    project = Project(name="P42 Co", created_by="tester")
    db.add(project)
    db.commit()
    db.refresh(project)
    course = Course(name="P42 Course", project_id=project.id, created_by="tester")
    db.add(course)
    db.commit()
    db.refresh(course)

    sync_val = source_library._resolved_client(
        None, db, client_id="fallback", project_id=project.id, course_id=course.id
    )
    async_val = asyncio.run(
        source_library._resolved_client_async(
            None, db, client_id="fallback", project_id=project.id, course_id=course.id
        )
    )
    assert async_val == sync_val
