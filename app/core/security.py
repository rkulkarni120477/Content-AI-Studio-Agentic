"""
JWT authentication utilities — token creation, decoding, and password hashing.

This module is ported from ``promptops_app/auth/session_manager.py`` with
two key changes:
  1. Tokens are returned in the HTTP response body (Bearer scheme) instead of
     browser cookies.  The React frontend stores the token in memory or
     localStorage and sends it via the ``Authorization: Bearer <token>`` header.
  2. The workspace and config payloads (``ws``, ``cfg``) from the Streamlit
     cookie are preserved in the JWT so the frontend can restore its last
     session state after a page refresh.

Token payload structure
-----------------------
{
    "sub":  "username",           # subject — user's login name
    "role": "admin",              # DB role value
    "exp":  <unix timestamp>,     # expiry
    "iat":  <unix timestamp>,     # issued at
    "ws":   { ... },              # optional: last workspace selection
    "cfg":  { ... },              # optional: sidebar model/audience config
}

Usage
-----
    from app.core.security import create_access_token, decode_access_token

    token = create_access_token("shubham", "admin")
    payload = decode_access_token(token)
"""

from __future__ import annotations

import binascii
import datetime
import hashlib
import logging
import os
from typing import Optional

import jwt

from app.core.config import settings
from app.core.exceptions import AuthenticationError

_log = logging.getLogger(__name__)

_ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------

def create_access_token(
    username: str,
    role: str,
    *,
    tenant_id: str | None = None,
    tenant_slug: str | None = None,
    is_platform_admin: bool = False,
    workspace: dict | None = None,
    config: dict | None = None,
) -> str:
    """
    Create and sign a JWT access token.

    Parameters
    ----------
    username          : The user's login name (stored as ``sub`` claim).
    role              : The user's DB role (``admin``, ``reviewer``, ``author``).
    tenant_id         : Tenant UUID.  None for platform admins.
    tenant_slug       : Tenant org code.  None for platform admins.
    is_platform_admin : True only for platform super-admin accounts.
    workspace         : Optional workspace state dict.
    config            : Optional sidebar config dict.

    Returns
    -------
    A signed JWT string.  Expiry is controlled by ``settings.jwt_token_expire_days``.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    expire = now + datetime.timedelta(days=settings.jwt_token_expire_days)

    payload: dict = {
        "sub":               username,
        "role":              role,
        "tenant_id":         tenant_id,
        "tenant_slug":       tenant_slug,
        "is_platform_admin": is_platform_admin,
        "iat":               now,
        "exp":               expire,
    }

    # Only embed non-null workspace values to keep the token small.
    if workspace:
        payload["ws"] = {k: v for k, v in workspace.items() if v is not None}

    if config:
        payload["cfg"] = {k: v for k, v in config.items() if v}

    token = jwt.encode(payload, settings.jwt_secret_value, algorithm=_ALGORITHM)
    _log.debug(
        "token_created  user=%s  role=%s  tenant=%s  platform_admin=%s  expires=%s",
        username, role, tenant_slug or "platform", is_platform_admin, expire.isoformat(),
    )
    return token


# ---------------------------------------------------------------------------
# Token decoding
# ---------------------------------------------------------------------------

def decode_access_token(token: str) -> dict:
    """
    Decode and validate a JWT access token.

    Returns the decoded payload dict on success.
    Raises ``AuthenticationError`` if the token is expired, tampered, or malformed.

    The returned dict always contains ``sub`` (username) and ``role``.
    It may optionally contain ``ws`` (workspace) and ``cfg`` (config).
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_value,
            algorithms=[_ALGORITHM],
        )
        return payload

    except jwt.ExpiredSignatureError:
        _log.info("token_expired")
        raise AuthenticationError("Session expired. Please log in again.")

    except jwt.InvalidTokenError as exc:
        _log.warning("token_invalid  reason=%s", exc)
        raise AuthenticationError("Invalid authentication token.")


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(plain_password: str) -> str:
    """
    Hash a plain-text password using PBKDF2-HMAC-SHA256 with a random salt.

    Matches the scheme used in ``promptops_app/database.py`` so passwords
    created via the Streamlit app work unchanged after migration.

    Format: hex(salt[16 bytes] + dk[32 bytes])
    """
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain_password.encode(), salt, 100_000)
    return binascii.hexlify(salt + dk).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Compare a plain-text password against its PBKDF2 hash.

    Returns True if the password matches, False otherwise.
    """
    try:
        raw = binascii.unhexlify(hashed_password.encode())
        salt, key = raw[:16], raw[16:]
        dk = hashlib.pbkdf2_hmac("sha256", plain_password.encode(), salt, 100_000)
        return dk == key
    except Exception:
        return False
