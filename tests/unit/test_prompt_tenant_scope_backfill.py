"""The backfill in migrations/.../20260818_1000_000100000023_prompt_tenant_scope.py.

Loads the migration file directly (it isn't import-friendly as a normal
module — alembic revision filenames aren't valid identifiers) and runs its
BACKFILL_SQL constant against the same in-memory SQLite schema the rest of
the suite uses, so this exercises the exact statement the migration runs in
production rather than a hand-copied duplicate.

Regression coverage for two review findings:
  - the backfill must resolve ownership via tenant_memberships (a user with
    exactly one active membership), never via users.project_id ("last-active
    tenant", not ownership — wrong for anyone in more than one org)
  - `is_default IS NOT TRUE` must catch NULL is_default rows the same as
    FALSE ones (the column has no server_default)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations" / "versions" / "20260818_1000_000100000023_prompt_tenant_scope.py"
)


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("prompt_tenant_scope_migration", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_user(db, username, *, memberships=()):
    from app.core.security import hash_password
    from promptops_app.database import TenantMembership, User

    user = User(username=username, password_hash=hash_password("x"), role="author", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    for project_id in memberships:
        db.add(TenantMembership(user_id=user.id, project_id=project_id, role="author", active=True))
    db.commit()
    return user


def _make_project(db, name):
    from promptops_app.database import Project

    p = Project(name=name, is_active=True)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _make_prompt(db, *, owner, is_default=False, name=None):
    from promptops_app.database import Prompt

    p = Prompt(name=name or f"prompt-{owner}-{is_default}", owner=owner,
               prompt_kind="library", is_default=is_default)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_unambiguous_single_membership_resolves(db, migration):
    project = _make_project(db, "Solo Tenant")
    _make_user(db, "solo_user", memberships=[project.id])
    prompt = _make_prompt(db, owner="solo_user")

    db.execute(text(migration.BACKFILL_SQL))
    db.commit()
    db.refresh(prompt)
    assert prompt.project_id == project.id


def test_multiple_active_memberships_stays_null(db, migration):
    """The exact bug: using users.project_id would confidently (and possibly
    wrongly) pick whichever org this user logged into last."""
    project_a = _make_project(db, "Multi Tenant A")
    project_b = _make_project(db, "Multi Tenant B")
    _make_user(db, "multi_user", memberships=[project_a.id, project_b.id])
    prompt = _make_prompt(db, owner="multi_user")

    db.execute(text(migration.BACKFILL_SQL))
    db.commit()
    db.refresh(prompt)
    assert prompt.project_id is None


def test_no_membership_stays_null(db, migration):
    _make_user(db, "orphan_user", memberships=[])
    prompt = _make_prompt(db, owner="orphan_user")

    db.execute(text(migration.BACKFILL_SQL))
    db.commit()
    db.refresh(prompt)
    assert prompt.project_id is None


def test_inactive_membership_is_not_counted(db, migration):
    """An inactive membership must not make an otherwise-unambiguous user
    look multi-tenant, and must not itself be used to resolve ownership."""
    from promptops_app.database import TenantMembership

    project = _make_project(db, "Active Tenant")
    former_project = _make_project(db, "Former Tenant")
    user = _make_user(db, "formerly_multi_user", memberships=[project.id])
    db.add(TenantMembership(user_id=user.id, project_id=former_project.id, role="author", active=False))
    db.commit()
    prompt = _make_prompt(db, owner="formerly_multi_user")

    db.execute(text(migration.BACKFILL_SQL))
    db.commit()
    db.refresh(prompt)
    assert prompt.project_id == project.id


def test_null_is_default_is_treated_as_not_default(db, migration):
    """A row written before the ORM's is_default default applied (no
    server_default) must still be backfilled, same as is_default=False."""
    from promptops_app.database import Prompt

    project = _make_project(db, "Null Default Tenant")
    _make_user(db, "null_default_user", memberships=[project.id])
    prompt = _make_prompt(db, owner="null_default_user", name="null-default-prompt")
    prompt.is_default = None
    db.commit()

    db.execute(text(migration.BACKFILL_SQL))
    db.commit()
    db.refresh(prompt)
    assert prompt.project_id == project.id


def test_true_is_default_is_never_backfilled(db, migration):
    project = _make_project(db, "Default Seed Tenant")
    _make_user(db, "seed_owner", memberships=[project.id])
    prompt = _make_prompt(db, owner="seed_owner", is_default=True)

    db.execute(text(migration.BACKFILL_SQL))
    db.commit()
    db.refresh(prompt)
    assert prompt.project_id is None


def test_already_stamped_project_id_is_left_alone(db, migration):
    project = _make_project(db, "Original Tenant")
    other_project = _make_project(db, "Other Tenant")
    _make_user(db, "already_stamped_user", memberships=[other_project.id])
    prompt = _make_prompt(db, owner="already_stamped_user")
    prompt.project_id = project.id
    db.commit()

    db.execute(text(migration.BACKFILL_SQL))
    db.commit()
    db.refresh(prompt)
    assert prompt.project_id == project.id
