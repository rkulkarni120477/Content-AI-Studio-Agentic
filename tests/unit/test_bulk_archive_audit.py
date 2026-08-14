"""The bulk-archive audit row survives a large batch.

``log_audit_event`` swallows write failures by design — audit logging must never
crash a request. That makes an oversized column a silent hazard: the row simply
never appears, and only for the biggest operations, which are exactly the ones
worth recording. ``audit_logs.entity_id`` is VARCHAR(64), so a joined id list
overflows after about a dozen documents.

These tests hold the ids in metadata and keep entity_id within its column.
"""

import pytest
from sqlalchemy import create_engine, inspect as sa_inspect
from sqlalchemy.orm import sessionmaker

from promptops_app.database import AuditLog, Base, Course, CourseDesignDocument, Project
from promptops_app.services.audit_service import log_audit_event


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _entity_id_limit() -> int:
    return AuditLog.__table__.columns["entity_id"].type.length


def test_entity_id_column_is_still_narrow():
    """If this widens, the guard below can be relaxed — but not before."""
    assert _entity_id_limit() == 64


def test_a_joined_id_list_would_not_fit_in_entity_id():
    """The reason the ids live in metadata. Pins the arithmetic, not a guess."""
    fifty_ids = ",".join(str(i) for i in range(160, 210))
    assert len(fifty_ids) > _entity_id_limit()


def test_bulk_archive_audit_records_every_id_without_overflowing(db):
    """What the endpoints actually write: ids in metadata, entity_id unset."""
    project = Project(name="P")
    db.add(project)
    db.flush()
    course = Course(name="C", project_id=project.id)
    db.add(course)
    db.commit()

    archived_ids = list(range(160, 260))     # 100 documents in one batch

    log_audit_event(
        db, "sami", "cdd.archived",
        entity_type="cdd",
        course_id=course.id, project_id=project.id,
        metadata={"bulk": True, "archived_ids": archived_ids, "archived": len(archived_ids)},
    )
    db.commit()

    row = db.query(AuditLog).one()
    assert row.entity_id is None or len(row.entity_id) <= _entity_id_limit()

    # The whole batch is recoverable from the row — "which hundred?" has an answer.
    # metadata_json is Text, so it holds the list a VARCHAR(64) could not.
    import json
    meta = json.loads(row.metadata_json or "{}")
    assert meta["archived_ids"] == archived_ids


def test_the_bulk_endpoints_do_not_pass_an_id_list_as_entity_id():
    """Source-level guard: the shape is easy to reintroduce by habit."""
    import inspect as py_inspect

    from app.api.v1.routers import blueprints as bp_router
    from app.api.v1.routers import cdd as cdd_router

    for fn in (cdd_router.bulk_archive_cdds, bp_router.bulk_archive_blueprints):
        source = py_inspect.getsource(fn)
        assert 'entity_id=",".join' not in source, (
            f"{fn.__name__} joins ids into entity_id (VARCHAR(64)) — "
            "the audit row will be dropped silently on a large batch"
        )
        assert "archived_ids" in source, f"{fn.__name__} no longer records which ids moved"
