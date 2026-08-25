"""Reviewer dropdown must be scoped to the requesting tenant.

Regression for: a hard-deleted tenant's leftover admin/reviewer user (User
rows are deliberately preserved by tenant_service.hard_delete_tenant — only
the Project row and its TenantMembership rows go away) kept showing up in
every OTHER tenant's "Assign Reviewer" dropdown forever, because
list_reviewers_and_admins queried User.role/is_active platform-wide with no
tenant scope at all. That's the same bug whether or not the tenant was ever
deleted — deletion just made the cross-tenant leak obvious.
"""

from __future__ import annotations

import pytest

from app.services import tenant_service
from promptops_app.repositories.user_repository import list_reviewers_and_admins


@pytest.fixture()
def two_tenants(db):
    from promptops_app.database import Project, TenantMembership, User

    working = Project(name="Working", slug="working-tenant", is_active=True, status="active")
    cas_test = Project(name="CAS-Test", slug="cas-test-tenant", is_active=True, status="active")
    db.add_all([working, cas_test])
    db.flush()

    working_reviewer = User(username="working_reviewer", role="reviewer", is_active=True)
    cas_test_admin = User(username="CAS-Test", role="admin", is_active=True)
    db.add_all([working_reviewer, cas_test_admin])
    db.flush()

    db.add_all([
        TenantMembership(user_id=working_reviewer.id, project_id=working.id, role="reviewer", active=True),
        TenantMembership(user_id=cas_test_admin.id, project_id=cas_test.id, role="admin", active=True),
    ])
    db.commit()

    return working, cas_test, working_reviewer, cas_test_admin


def test_a_tenants_reviewer_list_never_included_another_tenants_admin(db, two_tenants):
    working, cas_test, working_reviewer, cas_test_admin = two_tenants

    usernames = {u.username for u in list_reviewers_and_admins(db, project_id=working.id)}
    assert usernames == {"working_reviewer"}
    assert "CAS-Test" not in usernames


def test_deleting_the_other_tenant_does_not_change_a_live_tenants_reviewer_list(db, two_tenants):
    working, cas_test, working_reviewer, cas_test_admin = two_tenants

    tenant_service.hard_delete_tenant(db, cas_test, deleted_by="platform_admin")
    db.commit()

    # The leftover User row survives the tenant delete by design...
    from promptops_app.database import User
    assert db.query(User).filter_by(username="CAS-Test").first() is not None

    # ...but it must never appear in another tenant's scoped reviewer list,
    # before or after the deletion.
    usernames = {u.username for u in list_reviewers_and_admins(db, project_id=working.id)}
    assert usernames == {"working_reviewer"}


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
