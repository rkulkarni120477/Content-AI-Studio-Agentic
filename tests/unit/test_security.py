"""
Unit tests for JWT token creation, decoding, and password hashing.

These tests run with no database and no network.
"""

from __future__ import annotations

import time

import pytest

from app.core.exceptions import AuthenticationError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_is_deterministic(self):
        """The same password always produces the same hash."""
        assert hash_password("mypassword") == hash_password("mypassword")

    def test_different_passwords_produce_different_hashes(self):
        assert hash_password("password1") != hash_password("password2")

    def test_verify_correct_password(self):
        hashed = hash_password("correct_password")
        assert verify_password("correct_password", hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("correct_password")
        assert verify_password("wrong_password", hashed) is False

    def test_hash_is_not_plaintext(self):
        hashed = hash_password("secret")
        assert hashed != "secret"
        assert len(hashed) == 64  # SHA-256 hex digest length


class TestJWTTokens:
    def test_create_and_decode_basic_token(self):
        """A token can be created and decoded to recover subject and role."""
        token = create_access_token("shubham", "admin")
        payload = decode_access_token(token)

        assert payload["sub"] == "shubham"
        assert payload["role"] == "admin"

    def test_workspace_embedded_in_token(self):
        """Workspace state is stored and recovered from the token."""
        workspace = {"selected_project_id": 5, "selected_course_id": 12}
        token = create_access_token("shubham", "admin", workspace=workspace)
        payload = decode_access_token(token)

        assert payload["ws"]["selected_project_id"] == 5
        assert payload["ws"]["selected_course_id"] == 12

    def test_config_embedded_in_token(self):
        """Sidebar config is stored and recovered from the token."""
        cfg = {"model_choice": "GPT-5.4", "expert_domain": "Nursing"}
        token = create_access_token("shubham", "admin", config=cfg)
        payload = decode_access_token(token)

        assert payload["cfg"]["model_choice"] == "GPT-5.4"

    def test_null_workspace_values_stripped(self):
        """None values are not embedded in the token payload (keeps token small)."""
        workspace = {"selected_project_id": 5, "selected_course_id": None}
        token = create_access_token("shubham", "admin", workspace=workspace)
        payload = decode_access_token(token)

        assert "selected_project_id" in payload["ws"]
        assert "selected_course_id" not in payload["ws"]

    def test_tampered_token_raises_auth_error(self):
        """A token with a modified signature is rejected."""
        token = create_access_token("shubham", "admin")
        tampered = token[:-5] + "XXXXX"

        with pytest.raises(AuthenticationError):
            decode_access_token(tampered)

    def test_invalid_token_raises_auth_error(self):
        """A completely invalid string raises AuthenticationError, not a raw exception."""
        with pytest.raises(AuthenticationError):
            decode_access_token("not.a.valid.jwt.token")
