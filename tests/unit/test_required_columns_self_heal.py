"""The schema self-heal for ORM-required columns.

Why this exists (measured, 2026-08-14): adding ``deleted_at``/``deleted_by`` to
the CDD and Blueprint models took the whole CDD and Blueprint UI down against a
database still at revision 000100000020 —

    (psycopg2.errors.UndefinedColumn)
    column course_design_documents.deleted_at does not exist

— because SQLAlchemy names every mapped column in its SELECT, so a missing one
breaks queries that never reference it. The deploy pipeline runs no
``alembic upgrade``, so code always reaches production ahead of its migration;
that window has to be survivable rather than fatal.

These tests guard the two ways the repair can rot: the statement list drifting
away from the models it is meant to cover, and the statements themselves
stopping being safe to run repeatedly.
"""

import pytest
from sqlalchemy import inspect as sa_inspect

from promptops_app.database import (
    _REQUIRED_COLUMNS,
    Base,
    CourseDesignDocument,
    ModuleBlueprint,
    _ensure_required_columns,
)


def _declared_column(statement: str) -> str:
    """The column an ``ADD COLUMN IF NOT EXISTS`` statement adds."""
    return statement.split("IF NOT EXISTS", 1)[1].strip().split()[0]


def test_every_listed_table_is_a_real_table():
    known = set(Base.metadata.tables)
    assert set(_REQUIRED_COLUMNS) <= known, (
        f"unknown tables in _REQUIRED_COLUMNS: {set(_REQUIRED_COLUMNS) - known}"
    )


@pytest.mark.parametrize("table", sorted(_REQUIRED_COLUMNS))
def test_each_statement_adds_a_column_the_orm_actually_declares(table):
    """Stops the repair list from drifting into columns no model has.

    A statement for a column the ORM does not declare is dead DDL that still
    takes a lock on every boot.
    """
    orm_columns = set(Base.metadata.tables[table].columns.keys())
    for statement in _REQUIRED_COLUMNS[table]:
        column = _declared_column(statement)
        assert column in orm_columns, (
            f"{table}.{column} is repaired on boot but not declared on the model"
        )


@pytest.mark.parametrize("table", sorted(_REQUIRED_COLUMNS))
def test_each_statement_targets_its_own_table(table):
    for statement in _REQUIRED_COLUMNS[table]:
        assert f"ALTER TABLE {table} " in statement


@pytest.mark.parametrize("table", sorted(_REQUIRED_COLUMNS))
def test_statements_are_additive_and_idempotent(table):
    """No DROP, no NOT NULL, no rewrite — this runs unattended on every boot."""
    for statement in _REQUIRED_COLUMNS[table]:
        upper = statement.upper()
        assert "ADD COLUMN IF NOT EXISTS" in upper, f"not idempotent: {statement}"
        assert "DROP" not in upper, f"destructive: {statement}"
        assert "NOT NULL" not in upper.replace("IF NOT EXISTS", ""), (
            f"a NOT NULL add would fail on a table with rows: {statement}"
        )


def test_the_archive_columns_are_covered():
    """The specific outage this was written for.

    Named explicitly rather than left to the generic checks: if someone removes
    these entries, the CDD and Blueprint pages go down again on the next deploy
    that lands before its migration.
    """
    for model, table in (
        (CourseDesignDocument, "course_design_documents"),
        (ModuleBlueprint, "module_blueprints"),
    ):
        covered = {_declared_column(s) for s in _REQUIRED_COLUMNS[table]}
        assert {"deleted_at", "deleted_by"} <= covered
        mapped = {c.key for c in sa_inspect(model).columns}
        assert {"deleted_at", "deleted_by"} <= mapped


def test_repair_is_a_no_op_on_a_complete_schema(monkeypatch):
    """Nothing is executed when every column is already present.

    The common case is a database that is already correct; it must not pay for
    a DDL statement per boot.
    """
    import promptops_app.database as db_mod

    executed = []

    class FakeInspector:
        def get_table_names(self):
            return list(_REQUIRED_COLUMNS)

        def get_columns(self, table):
            return [{"name": _declared_column(s)} for s in _REQUIRED_COLUMNS[table]]

    class FakeConn:
        def execute(self, statement):
            executed.append(str(statement))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("sqlalchemy.inspect", lambda _engine: FakeInspector())
    monkeypatch.setattr(db_mod.engine, "begin", lambda: FakeConn())

    _ensure_required_columns()

    assert executed == []


def test_repair_runs_only_the_missing_statements(monkeypatch):
    import promptops_app.database as db_mod

    executed = []

    class FakeInspector:
        def get_table_names(self):
            return list(_REQUIRED_COLUMNS)

        def get_columns(self, table):
            # Everything present except course_design_documents.deleted_by.
            cols = [_declared_column(s) for s in _REQUIRED_COLUMNS[table]]
            if table == "course_design_documents":
                cols = [c for c in cols if c != "deleted_by"]
            return [{"name": c} for c in cols]

    class FakeConn:
        def execute(self, statement):
            executed.append(str(statement))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("sqlalchemy.inspect", lambda _engine: FakeInspector())
    monkeypatch.setattr(db_mod.engine, "begin", lambda: FakeConn())

    _ensure_required_columns()

    assert len(executed) == 1
    assert "deleted_by" in executed[0]


def test_a_refused_alter_does_not_kill_startup(monkeypatch):
    """A read-only replica or least-privilege role must still boot.

    Requests that do not need the new column should keep working; the failure
    belongs in the log, not in a crash loop.
    """
    import promptops_app.database as db_mod

    class FakeInspector:
        def get_table_names(self):
            return list(_REQUIRED_COLUMNS)

        def get_columns(self, table):
            return []          # everything looks missing

    class ExplodingConn:
        def __enter__(self):
            raise RuntimeError("permission denied for table")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("sqlalchemy.inspect", lambda _engine: FakeInspector())
    monkeypatch.setattr(db_mod.engine, "begin", lambda: ExplodingConn())

    _ensure_required_columns()      # must not raise


def test_a_missing_table_is_skipped(monkeypatch):
    """A fresh database has no tables yet — create_all/migrations build them whole."""
    import promptops_app.database as db_mod

    executed = []

    class FakeInspector:
        def get_table_names(self):
            return []

        def get_columns(self, table):  # pragma: no cover — must never be reached
            raise AssertionError("inspected a table that does not exist")

    class FakeConn:
        def execute(self, statement):
            executed.append(str(statement))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("sqlalchemy.inspect", lambda _engine: FakeInspector())
    monkeypatch.setattr(db_mod.engine, "begin", lambda: FakeConn())

    _ensure_required_columns()

    assert executed == []
