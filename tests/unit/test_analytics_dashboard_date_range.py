"""CAS-18 / CAS-137: the Analytics Dashboard's date-range dropdown (Last 7
Days / Last 30 Days) computed date_from/date_to correctly on the frontend,
but GET /analytics/summary and GET /analytics/projects never declared those
query params, so FastAPI silently dropped them and every metric was always
an all-time total -- switching the dropdown changed nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from promptops_app.database import Block, Generation


def _make_generation(db, created_at: datetime, project_id: int = 1) -> None:
    db.add(Generation(
        prompt_name="p", prompt_version="v1", block_type="lesson",
        topic="t", output_text="x", project_id=project_id,
        created_by="test_admin", created_at=created_at,
    ))
    db.commit()


def test_summary_generations_count_changes_with_date_range(client, auth_headers, db):
    now = datetime.utcnow()
    _make_generation(db, now - timedelta(days=3))    # inside both 7-day and 30-day windows
    _make_generation(db, now - timedelta(days=20))   # inside 30-day only

    resp_7 = client.get(
        "/api/v1/analytics/summary",
        params={"date_from": (now - timedelta(days=7)).strftime("%Y-%m-%d")},
        headers=auth_headers,
    )
    resp_30 = client.get(
        "/api/v1/analytics/summary",
        params={"date_from": (now - timedelta(days=30)).strftime("%Y-%m-%d")},
        headers=auth_headers,
    )
    assert resp_7.status_code == 200 and resp_30.status_code == 200

    gens_7 = resp_7.json()["generations"]
    gens_30 = resp_30.json()["generations"]
    # The regression: these used to be identical no matter which range was picked.
    assert gens_30 > gens_7
    assert gens_7 == 1
    assert gens_30 == 2


def test_summary_with_no_date_range_is_unfiltered(client, auth_headers, db):
    """No date_from/date_to -> same all-time behavior as before this fix."""
    _make_generation(db, datetime.utcnow() - timedelta(days=400))

    resp = client.get("/api/v1/analytics/summary", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["generations"] == 1


def test_project_comparison_also_respects_the_date_range(client, auth_headers, db):
    """The frontend never sent a date range to /projects at all before this
    fix (getProjectAnalytics took zero params) -- prove the endpoint now
    accepts and applies one, not just /summary."""
    from promptops_app.database import Project

    project = Project(name="Test Project", client_name="c", is_active=True)
    db.add(project)
    db.commit()
    db.refresh(project)

    now = datetime.utcnow()
    _make_generation(db, now - timedelta(days=3), project_id=project.id)
    _make_generation(db, now - timedelta(days=20), project_id=project.id)

    resp_7 = client.get(
        "/api/v1/analytics/projects",
        params={"date_from": (now - timedelta(days=7)).strftime("%Y-%m-%d")},
        headers=auth_headers,
    )
    resp_30 = client.get(
        "/api/v1/analytics/projects",
        params={"date_from": (now - timedelta(days=30)).strftime("%Y-%m-%d")},
        headers=auth_headers,
    )
    assert resp_7.status_code == 200 and resp_30.status_code == 200

    row_7 = next(r for r in resp_7.json() if r["project_id"] == project.id)
    row_30 = next(r for r in resp_30.json() if r["project_id"] == project.id)
    assert row_7["generations"] == 1
    assert row_30["generations"] == 2


def test_project_metrics_batch_keeps_projects_separate(db):
    """/analytics/projects used to do up to 4 DB round-trips PER project (get
    generation ids, then count blocks/cdds/blueprints one project at a time)
    -- 15 projects meant up to 60 sequential queries, ~12s over the dev DB
    tunnel by the request-timing logs. get_project_metrics_batch replaces
    that with 4 GROUP BY queries total, independent of project count. The
    thing most likely to break in that rewrite is mixing up which count
    belongs to which project, so assert two projects' numbers directly
    against each other, not just that totals look plausible."""
    from promptops_app.database import Project
    from promptops_app.repositories.analytics_repository import get_project_metrics_batch

    p1 = Project(name="P1", client_name="c", is_active=True)
    p2 = Project(name="P2", client_name="c", is_active=True)
    db.add_all([p1, p2])
    db.commit()
    db.refresh(p1)
    db.refresh(p2)

    now = datetime.utcnow()
    g1 = Generation(prompt_name="p", prompt_version="v1", block_type="lesson",
                    topic="t", output_text="x", project_id=p1.id,
                    created_by="u", created_at=now)
    db.add(g1)
    db.commit()
    db.refresh(g1)
    db.add(Block(generation_id=g1.id, block_type="lesson", block_label="b"))
    _make_generation(db, now, project_id=p2.id)
    _make_generation(db, now, project_id=p2.id)
    db.commit()

    metrics = get_project_metrics_batch(db, [p1.id, p2.id])
    assert metrics[p1.id] == {"generations": 1, "blocks": 1, "cdds": 0, "blueprints": 0}
    assert metrics[p2.id] == {"generations": 2, "blocks": 0, "cdds": 0, "blueprints": 0}


def test_project_metrics_batch_defaults_to_zero_for_a_project_with_no_rows(db):
    """A plain GROUP BY never emits a row for a project with nothing in it --
    confirm the batch fills that gap in rather than KeyError-ing or omitting
    the project from the response the comparison table renders."""
    from promptops_app.database import Project
    from promptops_app.repositories.analytics_repository import get_project_metrics_batch

    empty = Project(name="Empty", client_name="c", is_active=True)
    db.add(empty)
    db.commit()
    db.refresh(empty)

    metrics = get_project_metrics_batch(db, [empty.id])
    assert metrics[empty.id] == {"generations": 0, "blocks": 0, "cdds": 0, "blueprints": 0}


def test_generation_history_also_respects_the_date_range(client, auth_headers, db):
    """PR review: GET /analytics/generations (the Interactive System History
    table) never declared date_from/date_to at all, so it was the one section
    of the dashboard that stayed the same no matter what range was picked."""
    now = datetime.utcnow()
    _make_generation(db, now - timedelta(days=3))
    _make_generation(db, now - timedelta(days=20))

    resp_7 = client.get(
        "/api/v1/analytics/generations",
        params={"date_from": (now - timedelta(days=7)).strftime("%Y-%m-%d")},
        headers=auth_headers,
    )
    resp_30 = client.get(
        "/api/v1/analytics/generations",
        params={"date_from": (now - timedelta(days=30)).strftime("%Y-%m-%d")},
        headers=auth_headers,
    )
    assert resp_7.status_code == 200 and resp_30.status_code == 200
    assert len(resp_7.json()) == 1
    assert len(resp_30.json()) == 2


def test_an_invalid_date_gets_a_422_instead_of_being_silently_dropped(client, auth_headers):
    """PR review: date_from/date_to used to be `str`, so a malformed value
    (e.g. a month of 13) fell through _parse_audit_date's except-None and the
    endpoint quietly returned an all-time total with a 200. Typing the query
    params as `date` makes FastAPI itself reject bad input."""
    resp = client.get(
        "/api/v1/analytics/summary",
        params={"date_from": "2026-13-01"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_a_row_saved_in_the_last_second_of_date_to_is_still_counted(client, auth_headers, db):
    """PR review: a literal 23:59:59 cutoff misses a row saved with
    microseconds later in that same last second. The exclusive
    `< date_to + 1 day` bound this PR switches to doesn't have that edge."""
    today = datetime.utcnow().date()
    late_row_today = datetime.combine(today, datetime.min.time()).replace(
        hour=23, minute=59, second=59, microsecond=999999,
    )
    _make_generation(db, late_row_today)

    resp = client.get(
        "/api/v1/analytics/summary",
        params={"date_from": today.strftime("%Y-%m-%d"), "date_to": today.strftime("%Y-%m-%d")},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["generations"] == 1


def test_archived_cdds_and_blueprints_are_excluded_from_the_comparison_table(db):
    """PR review: get_project_metrics_batch counted archived rows, but the
    summary cards (count_cdds_scoped / count_blueprints_scoped) already
    exclude them via _live_only -- the comparison table must agree."""
    from promptops_app.database import CourseDesignDocument, ModuleBlueprint, Project
    from promptops_app.repositories.analytics_repository import get_project_metrics_batch

    project = Project(name="Archive Test", client_name="c", is_active=True)
    db.add(project)
    db.commit()
    db.refresh(project)

    live_cdd = CourseDesignDocument(
        project_id=project.id, title="Live", course_title="Live Course", created_by="u",
    )
    archived_cdd = CourseDesignDocument(
        project_id=project.id, title="Archived", course_title="Archived Course",
        created_by="u", deleted_at=datetime.utcnow(),
    )
    live_bp = ModuleBlueprint(
        project_id=project.id, title="Live BP", module_title="Live Module", created_by="u",
    )
    archived_bp = ModuleBlueprint(
        project_id=project.id, title="Archived BP", module_title="Archived Module",
        created_by="u", deleted_at=datetime.utcnow(),
    )
    db.add_all([live_cdd, archived_cdd, live_bp, archived_bp])
    db.commit()

    metrics = get_project_metrics_batch(db, [project.id])
    assert metrics[project.id]["cdds"] == 1
    assert metrics[project.id]["blueprints"] == 1


def test_a_non_admins_counts_stay_scoped_to_their_own_rows_with_a_date_range(client, author_headers, db):
    """PR review: confirm the date filter doesn't widen a non-admin's scope --
    an author's counters must stay limited to their own generations even
    when a range is applied."""
    now = datetime.utcnow()
    _make_generation(db, now - timedelta(days=1))  # created_by="test_admin", not the author
    db.add(Generation(
        prompt_name="p", prompt_version="v1", block_type="lesson",
        topic="t", output_text="x", project_id=1,
        created_by="test_author", created_at=now - timedelta(days=1),
    ))
    db.commit()

    resp = client.get(
        "/api/v1/analytics/summary",
        params={"date_from": (now - timedelta(days=7)).strftime("%Y-%m-%d")},
        headers=author_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["generations"] == 1
