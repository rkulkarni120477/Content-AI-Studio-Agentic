"""
Unit tests for the RBAC permission system.

Verifies that each role holds exactly the permissions it should,
and that the reviewer blocklist prevents privilege escalation.
"""

from __future__ import annotations

import pytest

from app.core.permissions import get_permissions_for_role, rbac_check


class TestAdminPermissions:
    def test_admin_can_generate_cdd(self):
        assert rbac_check("admin", "cdd.generate") is True

    def test_admin_can_bulk_approve(self):
        assert rbac_check("admin", "workflow.bulk_approve") is True

    def test_admin_can_clear_db(self):
        assert rbac_check("admin", "system.clear_db") is True

    def test_admin_can_manage_users(self):
        for perm in ("users.create", "users.edit", "users.toggle"):
            assert rbac_check("admin", perm) is True, f"Admin should have {perm}"

    def test_admin_has_all_workflow_transitions(self):
        for perm in ("workflow.submit", "workflow.approve", "workflow.publish", "workflow.archive"):
            assert rbac_check("admin", perm) is True


class TestReviewerPermissions:
    def test_reviewer_can_generate_content(self):
        assert rbac_check("reviewer", "generate.run") is True

    def test_reviewer_can_approve_blocks(self):
        assert rbac_check("reviewer", "workflow.approve") is True

    def test_reviewer_cannot_bulk_approve(self):
        """Bulk approve is Admin-only regardless of the permission table."""
        assert rbac_check("reviewer", "workflow.bulk_approve") is False

    def test_reviewer_cannot_create_users(self):
        assert rbac_check("reviewer", "users.create") is False

    def test_reviewer_cannot_clear_db(self):
        assert rbac_check("reviewer", "system.clear_db") is False

    def test_reviewer_cannot_delete_project(self):
        assert rbac_check("reviewer", "project.delete") is False

    def test_reviewer_can_view_analytics(self):
        assert rbac_check("reviewer", "analytics.view_own") is True

    def test_reviewer_cannot_view_all_analytics(self):
        assert rbac_check("reviewer", "analytics.view_all") is False


class TestAuthorPermissions:
    def test_author_can_generate_content(self):
        assert rbac_check("author", "generate.run") is True

    def test_author_can_submit_for_review(self):
        assert rbac_check("author", "workflow.submit") is True

    def test_author_cannot_approve(self):
        """Authors can submit but not approve — that requires reviewer or admin."""
        assert rbac_check("author", "workflow.approve") is False

    def test_author_cannot_create_styles(self):
        assert rbac_check("author", "style.create") is False

    def test_author_can_export_course(self):
        assert rbac_check("author", "export.course") is True

    def test_author_cannot_manage_users(self):
        for perm in ("users.create", "users.edit", "users.toggle", "users.view"):
            assert rbac_check("author", perm) is False, f"Author should NOT have {perm}"


class TestGetPermissionsForRole:
    def test_admin_has_more_permissions_than_reviewer(self):
        admin_perms = set(get_permissions_for_role("admin"))
        reviewer_perms = set(get_permissions_for_role("reviewer"))
        assert admin_perms > reviewer_perms

    def test_reviewer_has_more_permissions_than_author(self):
        reviewer_perms = set(get_permissions_for_role("reviewer"))
        author_perms = set(get_permissions_for_role("author"))
        assert reviewer_perms > author_perms

    def test_returns_sorted_list(self):
        perms = get_permissions_for_role("admin")
        assert perms == sorted(perms)

    def test_unknown_role_has_no_permissions(self):
        assert get_permissions_for_role("unknown_role") == []
