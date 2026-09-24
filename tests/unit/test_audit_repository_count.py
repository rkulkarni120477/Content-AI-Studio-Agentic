"""CAS-140: count_audit_logs must not wrap a full-column subquery.

Query.count() on db.query(AuditLog) compiles to
``SELECT count(*) FROM (SELECT <all columns incl. wide JSON/Text> ...) AS anon_1``,
forcing the DB to materialize every matching row (metadata_json, changes,
summary, ...) just to throw it away. At audit-log scale this is the root
cause of the slow /audit-trail response times reported in the ticket. The
fix counts a bare column instead.
"""
from promptops_app.repositories.audit_repository import (
    count_audit_logs,
    create_audit_log,
)


def test_count_audit_logs_uses_lean_count_query(db):
    from sqlalchemy import func
    from promptops_app.database import AuditLog

    lean_q = db.query(func.count(AuditLog.id)).filter(AuditLog.user_id == "x")
    sql = str(lean_q.statement.compile(db.get_bind()))

    assert "anon_1" not in sql
    assert "metadata_json" not in sql
    assert sql.strip().upper().startswith("SELECT count(audit_logs.id)".upper())


def test_count_audit_logs_respects_filters(db):
    create_audit_log(db, user_id="alice", action="user.login", project_id=1)
    create_audit_log(db, user_id="alice", action="user.login", project_id=2)
    create_audit_log(db, user_id="bob", action="user.login", project_id=1)

    assert count_audit_logs(db) == 3
    assert count_audit_logs(db, user_id="alice") == 2
    assert count_audit_logs(db, user_id="alice", project_id=1) == 1
    assert count_audit_logs(db, project_id=1) == 2
    assert count_audit_logs(db, user_id="carol") == 0
