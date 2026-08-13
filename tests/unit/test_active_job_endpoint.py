"""A reloaded page must be able to find the job it was already watching.

Block-wide generation runs for minutes, and the poll chain lived only in browser
memory. A refresh, a closed laptop, or the network blip that surfaced to the user as
"Network error. Check your connection." orphaned the UI while the job kept running
server-side — so people either watched a dead spinner or re-submitted and paid for a
second concurrent build. Observed 2026-08-13 on a 22-minute Block 2 build.

GET /api/v1/jobs/active is the server-authoritative answer to "am I still building
something?", chosen over a job id in localStorage because it survives a cleared cache
and a different tab, and cannot disagree with the database.
"""
from __future__ import annotations

import types

import pytest

from promptops_app.repositories import job_repository


class _Job:
    id = "abc123"
    status = "running"
    progress = 20
    current_step = "Building day digests..."
    result_entity_id = None
    error_message = None
    created_at = None
    updated_at = None


def _call(monkeypatch, returned):
    """Invoke the endpoint function directly with the repository stubbed."""
    from app.api.v1.routers import jobs as jobs_router

    captured = {}

    def _fake(db, user_name, project_id=None, course_id=None, job_types=None):
        captured.update(user_name=user_name, course_id=course_id, job_types=job_types)
        return returned

    monkeypatch.setattr(job_repository, "get_active_job_for_user", _fake)
    user = types.SimpleNamespace(username="sami", id="sami", email="s@x.y", role="user")
    resp = jobs_router.get_active_job(course_id=48, job_type="cdd_block",
                                      db=object(), current_user=user)
    return resp, captured


def test_an_in_flight_job_is_returned_in_the_pollers_own_shape(monkeypatch):
    """Reattaching must need no second response format — the frontend feeds this
    straight back into the existing poll loop."""
    resp, _ = _call(monkeypatch, _Job())
    assert resp.job is not None
    assert resp.job.job_id == "abc123"
    assert resp.job.status == "running"
    assert resp.job.progress == 20
    assert resp.job.current_step == "Building day digests..."


def test_no_job_is_an_ordinary_200_with_an_explicit_null(monkeypatch):
    """Not a 404: "nothing is running" is the common case and must not read as a
    failure in logs, monitoring, or the client."""
    resp, _ = _call(monkeypatch, None)
    assert resp.job is None


def test_the_lookup_is_scoped_to_the_caller(monkeypatch):
    """A job row carries its owner's cost and error detail, so resume means MY job."""
    _, captured = _call(monkeypatch, _Job())
    assert captured["user_name"] == "sami"


def test_the_lookup_is_scoped_to_the_course_and_deliverable(monkeypatch):
    """A CDD page must not reattach to an unrelated job the same user started
    elsewhere in the app."""
    _, captured = _call(monkeypatch, _Job())
    assert captured["course_id"] == 48
    assert captured["job_types"] == ["cdd_block"]


def test_several_job_types_can_be_watched_at_once(monkeypatch):
    from app.api.v1.routers import jobs as jobs_router
    captured = {}
    monkeypatch.setattr(job_repository, "get_active_job_for_user",
                        lambda db, user_name, project_id=None, course_id=None, job_types=None:
                        captured.update(job_types=job_types) or None)
    user = types.SimpleNamespace(username="s", id="s", email="e", role="user")
    jobs_router.get_active_job(course_id=1, job_type="cdd_block, blueprint_block",
                               db=object(), current_user=user)
    assert captured["job_types"] == ["cdd_block", "blueprint_block"]


def test_a_blank_job_type_filter_is_not_a_filter(monkeypatch):
    """An empty string must mean "any job type", never an impossible IN () match."""
    from app.api.v1.routers import jobs as jobs_router
    captured = {}
    monkeypatch.setattr(job_repository, "get_active_job_for_user",
                        lambda db, user_name, project_id=None, course_id=None, job_types=None:
                        captured.update(job_types=job_types) or None)
    user = types.SimpleNamespace(username="s", id="s", email="e", role="user")
    for blank in ("", "   ", ",", " , "):
        jobs_router.get_active_job(course_id=1, job_type=blank,
                                   db=object(), current_user=user)
        assert captured["job_types"] is None, repr(blank)


def test_active_is_routed_before_the_job_id_wildcard():
    """/active sits under the same prefix as /{job_id}. Declared in the wrong order it
    would be parsed as a job id named "active" and always 404."""
    from app.api.v1.routers.jobs import router

    paths = [r.path for r in router.routes]
    assert paths.index("/active") < paths.index("/{job_id}")


def test_the_repository_filters_are_all_optional():
    """The helper is shared; narrowing arguments must stay opt-in so an existing
    caller that passes only a user keeps working."""
    import inspect

    sig = inspect.signature(job_repository.get_active_job_for_user)
    for name in ("project_id", "course_id", "job_types"):
        assert sig.parameters[name].default is None, name
