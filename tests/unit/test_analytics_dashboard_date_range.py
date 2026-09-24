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
