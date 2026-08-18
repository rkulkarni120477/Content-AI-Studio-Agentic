"""
Integration tests for the authentication endpoints.

Tests the full request→router→service→DB→response cycle.
The database is SQLite in-memory (from conftest.py).
No LLM calls are made by these tests.
"""

from __future__ import annotations

import pytest


class TestLogin:
    def test_valid_credentials_return_token(self, client, admin_user):
        """Happy path: correct username and password returns a JWT."""
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "test_admin", "password": "test_password", "platform_admin": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["username"] == "test_admin"
        assert data["user"]["role"] == "admin"
        # Admin should have a non-empty permissions list.
        assert len(data["user"]["permissions"]) > 0

    def test_wrong_password_returns_401(self, client, admin_user):
        """Incorrect password must return 401, not 403 or 500."""
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "test_admin", "password": "wrong_password", "platform_admin": True},
        )

        assert response.status_code == 401
        assert "error" in response.json()
        assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_nonexistent_user_returns_401(self, client):
        """Non-existent username must return 401 — same as wrong password.

        Both cases return the same vague message to prevent user enumeration.
        """
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": "whatever", "platform_admin": True},
        )

        assert response.status_code == 401

    def test_inactive_user_cannot_login(self, client, db):
        """A deactivated account must not be able to log in."""
        from app.core.security import hash_password
        from promptops_app.database import User

        inactive = User(
            username="inactive_user",
            password_hash=hash_password("test_password"),
            role="author",
            is_active=False,
            is_platform_admin=True,
        )
        db.add(inactive)
        db.commit()

        response = client.post(
            "/api/v1/auth/login",
            json={"username": "inactive_user", "password": "test_password", "platform_admin": True},
        )

        assert response.status_code == 401

    def test_missing_fields_return_422(self, client):
        """Pydantic validation: missing required fields return 422."""
        response = client.post("/api/v1/auth/login", json={"username": "test_admin"})
        assert response.status_code == 422


class TestGetMe:
    def test_authenticated_user_gets_profile(self, client, auth_headers, admin_user):
        """GET /me returns the authenticated user's full profile."""
        response = client.get("/api/v1/auth/me", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "test_admin"
        assert data["role"] == "admin"
        assert data["is_active"] is True
        assert isinstance(data["permissions"], list)
        assert len(data["permissions"]) > 0

    def test_unauthenticated_request_returns_401(self, client):
        """Requests without a token are rejected."""
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_invalid_token_returns_401(self, client):
        """A garbage token string is rejected."""
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer not.a.real.token"},
        )
        assert response.status_code == 401

    def test_permissions_match_role(self, client, auth_headers):
        """The permissions list returned for admin must include admin-only perms."""
        response = client.get("/api/v1/auth/me", headers=auth_headers)
        permissions = response.json()["permissions"]

        assert "workflow.bulk_approve" in permissions
        assert "system.clear_db" in permissions
        assert "users.create" in permissions

    def test_author_permissions_do_not_include_admin_perms(self, client, author_headers):
        """Author role must not have admin-only permissions."""
        response = client.get("/api/v1/auth/me", headers=author_headers)
        permissions = response.json()["permissions"]

        assert "workflow.bulk_approve" not in permissions
        assert "system.clear_db" not in permissions
        assert "users.create" not in permissions
        # But should have content creation perms.
        assert "generate.run" in permissions
        assert "cdd.generate" in permissions


class TestLogout:
    def test_logout_returns_200(self, client, auth_headers):
        """Logout acknowledges the request and returns a message."""
        response = client.post("/api/v1/auth/logout", headers=auth_headers)

        assert response.status_code == 200
        assert "message" in response.json()

    def test_unauthenticated_logout_returns_401(self, client):
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 401


class TestTokenRefresh:
    def test_refresh_returns_new_token(self, client, auth_headers):
        """A valid token can be exchanged for a fresh one."""
        response = client.post("/api/v1/auth/refresh", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["user"]["username"] == "test_admin"

    def test_refreshed_token_is_usable(self, client, auth_headers):
        """The new token from /refresh works on subsequent requests."""
        refresh_response = client.post("/api/v1/auth/refresh", headers=auth_headers)
        new_token = refresh_response.json()["access_token"]
        new_headers = {"Authorization": f"Bearer {new_token}"}

        me_response = client.get("/api/v1/auth/me", headers=new_headers)
        assert me_response.status_code == 200
