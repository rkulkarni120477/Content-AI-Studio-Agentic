"""Reviewer dropdown must be scoped to the requesting tenant.

Covers three reported bugs, all one root cause — ``list_reviewers_and_admins``
queried ``User.role``/``is_active`` platform-wide with no tenant scope:

  * "Deleted tenant name is still displayed in the Reviewer dropdown"
  * "Reviewer filter displays users from other tenants in Cengage Workflow"
  * "Assign Reviewer dropdown displays users from other tenants in Approval
    Center" (same ``reviewers`` state as the filter, one root cause)

``hard_delete_tenant`` deliberately preserves User rows (only the Project row
and its TenantMembership rows go away), so a deleted tenant's admin/reviewer
kept showing up in every OTHER tenant's dropdown forever. Deletion just made
the pre-existing cross-tenant leak visible.

The endpoint tests below are the ones that matter for isolation: scoping that
depends on the frontend sending the right ``project_id`` is not isolation at
all, so the router must derive the tenant from the caller's token.
"""

from __future__ import annotations

import json

import pytest

from app.core.security import create_access_token, hash_password
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


def _login(db, *, username, project_id, role="admin", is_platform_admin=False):
    """Create a user with a membership and return auth headers for them."""
    from promptops_app.database import TenantMembership, User

    user = User(
        username=username, password_hash=hash_password("test_password"),
        role=role, is_active=True, is_platform_admin=is_platform_admin,
        project_id=project_id,
    )
    db.add(user)
    db.flush()
    if project_id is not None and not is_platform_admin:
        db.add(TenantMembership(user_id=user.id, project_id=project_id, role=role, active=True))
    db.commit()

    token = create_access_token(
        username, role, project_id=project_id, is_platform_admin=is_platform_admin,
    )
    return {"Authorization": f"Bearer {token}"}


def _usernames(response):
    assert response.status_code == 200, response.text
    return {u["username"] for u in response.json()}


# ── Repository-level ────────────────────────────────────────────────────────

def test_a_tenants_reviewer_list_never_included_another_tenants_admin(db, two_tenants):
    working, _cas_test, _wr, _cta = two_tenants

    usernames = {u.username for u in list_reviewers_and_admins(db, project_id=working.id)}
    assert usernames == {"working_reviewer"}
    assert "CAS-Test" not in usernames


def test_deleting_the_other_tenant_does_not_change_a_live_tenants_reviewer_list(db, two_tenants):
    working, cas_test, _wr, _cta = two_tenants

    tenant_service.hard_delete_tenant(db, cas_test, deleted_by="platform_admin")
    db.commit()

    # The leftover User row survives the tenant delete by design...
    from promptops_app.database import User
    assert db.query(User).filter_by(username="CAS-Test").first() is not None

    # ...but it must never appear in another tenant's scoped reviewer list,
    # before or after the deletion.
    usernames = {u.username for u in list_reviewers_and_admins(db, project_id=working.id)}
    assert usernames == {"working_reviewer"}


def test_no_tenant_returns_nothing_rather_than_every_tenants_reviewers(db, two_tenants):
    # A platform admin who has not picked a tenant must not silently get the
    # old cross-tenant list back.
    assert list_reviewers_and_admins(db, project_id=None) == []


def test_a_custom_tenant_role_granting_approve_counts_as_a_reviewer(db, two_tenants):
    """A custom TenantRole carries the "custom" sentinel in
    TenantMembership.role, so matching the role string alone would drop a
    tenant's own custom Leads out of their reviewer dropdown."""
    from promptops_app.database import TenantMembership, TenantRole, User

    working, _cas_test, _wr, _cta = two_tenants

    lead_role = TenantRole(
        project_id=working.id, key="custom_lead", name="Custom Lead",
        permissions=json.dumps(["workflow.approve", "workflow.submit"]),
    )
    viewer_role = TenantRole(
        project_id=working.id, key="custom_viewer", name="Custom Viewer",
        permissions=json.dumps(["prompts.view"]),
    )
    db.add_all([lead_role, viewer_role])
    db.flush()

    custom_lead = User(username="custom_lead_user", role="author", is_active=True)
    custom_viewer = User(username="custom_viewer_user", role="author", is_active=True)
    db.add_all([custom_lead, custom_viewer])
    db.flush()
    db.add_all([
        TenantMembership(user_id=custom_lead.id, project_id=working.id,
                         role="custom", custom_role_id=lead_role.id, active=True),
        TenantMembership(user_id=custom_viewer.id, project_id=working.id,
                         role="custom", custom_role_id=viewer_role.id, active=True),
    ])
    db.commit()

    usernames = {u.username for u in list_reviewers_and_admins(db, project_id=working.id)}
    assert "custom_lead_user" in usernames        # grants workflow.approve
    assert "custom_viewer_user" not in usernames  # does not


def test_an_inactive_membership_is_not_an_eligible_reviewer(db, two_tenants):
    from promptops_app.database import TenantMembership

    working, _cas_test, working_reviewer, _cta = two_tenants
    membership = db.query(TenantMembership).filter_by(
        user_id=working_reviewer.id, project_id=working.id,
    ).first()
    membership.active = False
    db.commit()

    assert list_reviewers_and_admins(db, project_id=working.id) == []


# ── Endpoint-level (tenant isolation is enforced server-side) ───────────────

def test_a_tenant_admin_cannot_enumerate_another_tenants_users(db, two_tenants, client):
    """The whole point: scoping that trusts a client-supplied project_id is
    not isolation. A Working admin asking for CAS-Test's id gets their OWN
    tenant's reviewers, not CAS-Test's."""
    working, cas_test, _wr, _cta = two_tenants
    headers = _login(db, username="working_admin", project_id=working.id)

    res = client.get(f"/api/v1/users/reviewers?project_id={cas_test.id}", headers=headers)
    usernames = _usernames(res)
    assert "CAS-Test" not in usernames
    assert usernames <= {"working_reviewer", "working_admin"}


def test_omitting_project_id_does_not_fall_back_to_the_platform_wide_list(db, two_tenants, client):
    working, _cas_test, _wr, _cta = two_tenants
    headers = _login(db, username="working_admin2", project_id=working.id)

    res = client.get("/api/v1/users/reviewers", headers=headers)
    usernames = _usernames(res)
    assert "CAS-Test" not in usernames
    assert "working_reviewer" in usernames


def test_a_platform_admin_may_still_scope_to_a_chosen_tenant(db, two_tenants, client):
    _working, cas_test, _wr, _cta = two_tenants
    headers = _login(db, username="platformadmin", project_id=None, is_platform_admin=True)

    res = client.get(f"/api/v1/users/reviewers?project_id={cas_test.id}", headers=headers)
    assert _usernames(res) == {"CAS-Test"}


def test_a_platform_admin_with_no_tenant_chosen_gets_nothing(db, two_tenants, client):
    headers = _login(db, username="platformadmin2", project_id=None, is_platform_admin=True)

    res = client.get("/api/v1/users/reviewers", headers=headers)
    assert _usernames(res) == set()


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
