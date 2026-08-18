"""A post-call report must never sink the call it reports on.

``build_usage_summary`` runs after the model has answered and the money has
been spent. It exists to show a cost chip in the UI. When it raised, the whole
request raised with it: a CDD regeneration that had genuinely succeeded —
grounded context assembled, gpt-4o returning corrected content in 5.4s —
came back as a 500, the frontend committed nothing, and the user saw "no
change" with no indication that anything had gone wrong or that they had been
charged for it.

The trigger was schema drift on ``llm_usage_logs``: the table carried
``trace_id`` while the ORM declared ``langfuse_trace_id``, and SQLAlchemy names
every mapped column in its SELECT, so a query that wanted one row raised
UndefinedColumn instead.

The Phoenix migration since reconciled the ORM to ``trace_id`` — which inverts
that exposure rather than ending it, because revision 000100000017 creates the
column AS ``langfuse_trace_id``. A database that ran 17 but not the
000100000022 rename now has only the name the ORM stopped asking for. Hence the
boot-time repair below ensures BOTH names.

These tests pin the property that made the drift catastrophic rather than
cosmetic — because the next drift, or the next transient database error, must
cost a cost chip and nothing more.
"""

import pytest

from promptops_app.services.budget_service import build_usage_summary


class _Ctx:
    user_name = "sami"
    project_id = 23
    course_id = 48


class _ExplodingDb:
    """A session whose reads fail, as a drifted column or a dropped connection
    would make them."""

    def __init__(self, exc=None):
        self.exc = exc or RuntimeError("column does not exist")
        self.rolled_back = False

    def query(self, *a, **k):
        raise self.exc

    def rollback(self):
        self.rolled_back = True


def test_a_broken_summary_returns_none_instead_of_raising():
    """The caller loses its cost chip, not its generation."""
    assert build_usage_summary(_ExplodingDb(), _Ctx(), "cdd_section_regen", "169") is None


def test_the_session_is_rolled_back_so_the_caller_can_keep_using_it():
    """A failed transaction left open would break the caller's next statement
    and turn the cosmetic failure back into a fatal one."""
    db = _ExplodingDb()
    build_usage_summary(db, _Ctx(), "cdd_section_regen", "169")
    assert db.rolled_back


def test_it_survives_a_session_that_cannot_even_roll_back():
    """Whatever is wrong with the database, this function still returns."""

    class _Worse(_ExplodingDb):
        def rollback(self):
            raise RuntimeError("connection already closed")

    assert build_usage_summary(_Worse(), _Ctx(), "cdd_section_regen", "169") is None


@pytest.mark.parametrize("exc", [
    RuntimeError("boom"),
    KeyError("missing"),
    ValueError("bad"),
])
def test_any_failure_mode_is_contained(exc):
    """Deliberately broad. The value of a cost chip does not justify any
    exception class being allowed through to the caller."""
    assert build_usage_summary(_ExplodingDb(exc), _Ctx(), "cdd_x", "1") is None


def test_the_real_implementation_is_still_reachable():
    """The wrapper must delegate, not replace — a fail-safe that always returns
    None would hide the feature rather than protect it."""
    from promptops_app.services import budget_service

    assert callable(budget_service._build_usage_summary)


def test_the_drifted_column_is_registered_for_self_heal():
    """The underlying fix: the boot-time repair adds the column the ORM needs,
    so the next container start ends the drift rather than surviving it.

    The name tracked here is the ORM's current one, ``trace_id``. It was
    ``langfuse_trace_id`` until the Phoenix migration reconciled the model, and
    the old name is deliberately NOT kept alongside it — a column no model
    declares cannot break a read, so repairing it would be dead DDL taking a lock
    on every boot.
    """
    from promptops_app.database import _REQUIRED_COLUMNS

    statements = " ".join(_REQUIRED_COLUMNS.get("llm_usage_logs", ()))
    assert "ADD COLUMN IF NOT EXISTS trace_id" in statements
    # Additive only: the rename that carries the old values across is revision
    # 000100000022's job, not a boot-time repair's.
    assert "DROP" not in statements.upper()
    assert "RENAME" not in statements.upper()


def test_the_failsafe_covers_whatever_the_orm_currently_declares():
    """The guard that survives the next rename.

    Read the mapped column off the ORM rather than naming it, so this fails if a
    future rename lands without extending the boot-time repair — which is exactly
    what the Phoenix reconciliation did to the previous version of this test.
    """
    from promptops_app.database import _REQUIRED_COLUMNS, LLMUsageLog

    mapped = {c.name for c in LLMUsageLog.__table__.columns if "trace" in c.name}
    statements = " ".join(_REQUIRED_COLUMNS.get("llm_usage_logs", ()))
    assert mapped, "LLMUsageLog no longer maps a trace column — update this test"
    for name in mapped:
        assert f"ADD COLUMN IF NOT EXISTS {name} " in statements, (
            f"the ORM declares {name!r} but the boot-time repair does not ensure "
            f"it; a database without that column fails EVERY read of this table"
        )
