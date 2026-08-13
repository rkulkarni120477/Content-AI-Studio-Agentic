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


def test_a_zombie_row_is_not_adopted(monkeypatch):
    """The regression this endpoint could otherwise introduce.

    A process death leaves a row at `running` forever — the reaper runs only at
    startup. Adopting one sets isGenerating on every mount, and BlockWidePanel renders
    `disabled={isGenerating || !block.trim()}` with no cancel control, so the user
    would be locked out of that course permanently. Before the reattach feature, a page
    reload was the escape hatch; this bound is what replaces it.
    """
    import inspect

    from promptops_app.jobs.reaper import DEFAULT_STALE_AFTER_MINUTES

    src = inspect.getsource(job_repository.get_active_job_for_user)
    assert "DEFAULT_STALE_AFTER_MINUTES" in src, (
        "the adoption window must come from the reaper's own threshold so a row the "
        "reaper calls stranded can never be adopted as live"
    )
    assert "updated_at" in src
    assert DEFAULT_STALE_AFTER_MINUTES >= 30, (
        "updated_at does not advance during a build, so the window must still cover a "
        "real cold build"
    )


def test_the_staleness_window_is_overridable_but_defaults_to_the_reaper():
    import inspect

    sig = inspect.signature(job_repository.get_active_job_for_user)
    assert sig.parameters["max_age_minutes"].default is None


def test_the_block_is_reported_so_a_page_can_name_what_it_adopted(monkeypatch):
    """/active matches on course + job_type, and a course holds several blocks — so a
    page showing "Block 3" can adopt a running Block 2 build. Naming it makes that
    visible instead of reporting Block 2's completion as Block 3's."""
    import json as _json

    job = _Job()
    job.request_json = _json.dumps({"block": "Block 2", "deliverable": "cdd"})
    resp, _ = _call(monkeypatch, job)
    assert resp.job.block == "Block 2"


def test_timestamps_carry_an_explicit_utc_marker(monkeypatch):
    """created_at is naive UTC; without a marker JavaScript parses it as LOCAL time, so
    an elapsed-time calculation in IST comes out ~5.5h negative and never displays."""
    import datetime as _dt
    from app.api.v1.routers import jobs as jobs_mod

    naive = _dt.datetime(2026, 8, 13, 6, 25, 22)
    assert jobs_mod._utc_iso(naive) == "2026-08-13T06:25:22Z"
    assert jobs_mod._utc_iso(None) is None
    aware = _dt.datetime(2026, 8, 13, 6, 25, 22, tzinfo=_dt.timezone.utc)
    assert jobs_mod._utc_iso(aware).endswith("+00:00"), "must not double-stamp"
