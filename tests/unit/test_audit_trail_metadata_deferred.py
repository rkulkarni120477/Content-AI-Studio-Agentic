"""CAS-140, round 2: the first fix (a lean COUNT query) was confirmed correct
but not the actual cause -- EXPLAIN ANALYZE on prod showed the count query
already ran in under a millisecond. Profiling GET /audit-trail against a real
copy of prod data instead pointed at metadata_json: it averages ~35KB and
reaches 2.3MB on actions like content.generated/cdd.created, and the list
query fetched it for every row on every page even though the UI only shows
it once a row is expanded. Fetching a 25-row page filtered to a heavy action
took ~4.7s with that column, ~0.2s without it -- json parsing/serialization
of the column itself was negligible (~20ms), so the cost was purely
transferring bytes nothing used.

Fix: the list endpoint defers metadata_json out of the query entirely
(list_audit_logs(include_metadata=False)); a new GET /audit-trail/{id}
fetches one row's full detail on demand, for when a row is expanded.
"""
from __future__ import annotations

from promptops_app.repositories.audit_repository import create_audit_log


def test_list_audit_logs_can_defer_metadata_json_out_of_the_select(db):
    create_audit_log(db, user_id="alice", action="content.generated", metadata={"output": "x" * 1000})

    # Inspect the compiled query directly rather than relying on ORM instance
    # state, so this fails if a future edit re-adds metadata_json to the
    # SELECT even though nothing in the test ever touches .metadata_json.
    from promptops_app.repositories.audit_repository import _base_query
    from promptops_app.database import AuditLog
    from sqlalchemy.orm import defer

    deferred_q = _base_query(db, select_col=AuditLog, user_id=None, action=None,
                              entity_type=None, project_id=None, course_id=None,
                              date_from=None, date_to=None).options(defer(AuditLog.metadata_json))
    sql = str(deferred_q.statement.compile(db.get_bind()))
    assert "metadata_json" not in sql

    full_q = _base_query(db, select_col=AuditLog, user_id=None, action=None,
                          entity_type=None, project_id=None, course_id=None,
                          date_from=None, date_to=None)
    sql_full = str(full_q.statement.compile(db.get_bind()))
    assert "metadata_json" in sql_full


def test_the_list_endpoint_omits_metadata_but_the_detail_endpoint_returns_it(client, auth_headers, db):
    entry = create_audit_log(
        db, user_id="test_admin", action="content.generated",
        metadata={"output": "a real generated lesson", "system_prompt": "sp"},
    )

    list_resp = client.get("/api/v1/analytics/audit-trail", headers=auth_headers)
    assert list_resp.status_code == 200
    row = next(r for r in list_resp.json()["items"] if r["id"] == entry.id)
    assert row["metadata"] is None

    detail_resp = client.get(f"/api/v1/analytics/audit-trail/{entry.id}", headers=auth_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["metadata"] == {"output": "a real generated lesson", "system_prompt": "sp"}


def test_a_non_admin_can_fetch_their_own_event_detail(client, author_headers, db):
    entry = create_audit_log(db, user_id="test_author", action="content.generated", metadata={"output": "mine"})

    resp = client.get(f"/api/v1/analytics/audit-trail/{entry.id}", headers=author_headers)
    assert resp.status_code == 200
    assert resp.json()["metadata"] == {"output": "mine"}


def test_a_non_admin_cannot_fetch_someone_elses_event_detail(client, author_headers, db):
    entry = create_audit_log(db, user_id="someone_else", action="content.generated", metadata={"output": "not yours"})

    resp = client.get(f"/api/v1/analytics/audit-trail/{entry.id}", headers=author_headers)
    assert resp.status_code == 404


def test_a_missing_event_id_is_404(client, auth_headers):
    resp = client.get("/api/v1/analytics/audit-trail/999999", headers=auth_headers)
    assert resp.status_code == 404


def test_the_export_flow_still_gets_metadata_since_it_needs_it_for_the_csv(db):
    """Regression: only the list view should skip metadata_json -- CSV export
    (export_to_csv_rows -> list_audit_logs with the default include_metadata=True)
    must keep getting it, since the exported CSV has a metadata column."""
    from promptops_app.repositories.audit_repository import export_to_csv_rows

    create_audit_log(db, user_id="alice", action="content.generated", metadata={"output": "keep me"})

    rows = export_to_csv_rows(db, user_id="alice")
    assert len(rows) == 1
    assert "keep me" in rows[0]["metadata"]


def test_audit_trail_export_route_is_not_shadowed_by_the_new_detail_route(client, auth_headers, db):
    """Routing regression: /audit-trail/{event_id} is registered after
    /audit-trail/export specifically so 'export' is never parsed as an
    event id -- prove the export route still resolves."""
    create_audit_log(db, user_id="test_admin", action="user.login")

    resp = client.get("/api/v1/analytics/audit-trail/export", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
