"""JWT-backed browser cookie session manager.

Flow
----
  Login  → create_token() → stored in browser cookie by CookieManager
  Load   → decode_token() → session_state restored from cookie payload
  Logout → cookie deleted  → session_state.user cleared

The JWT payload embeds:
  - sub  / role          : identity (always present)
  - ws   (workspace)     : selected_project/cluster/course IDs + nav_page
  - cfg  (sidebar config): model_choice, expert_domain, target_audience, aud_cat

This means after a browser refresh or a new tab, the user is automatically
re-authenticated and returned to the same workspace without re-selecting.
"""
from __future__ import annotations

import datetime
import logging

import jwt

from promptops_app.core.config import settings

_log = logging.getLogger(__name__)

COOKIE_NAME = "contentai_session"
_ALGO       = "HS256"
_TTL_DAYS   = 7


def _secret() -> str:
    return settings.jwt_secret_key.get_secret_value()


def create_token(
    username: str,
    role: str,
    *,
    workspace: dict | None = None,
    cfg: dict | None = None,
) -> str:
    """Return a signed JWT encoding identity, workspace, and sidebar config.

    None / falsy values are stripped so the token stays small.
    """
    exp = datetime.datetime.utcnow() + datetime.timedelta(days=_TTL_DAYS)
    payload: dict = {
        "sub":  username,
        "role": role,
        "exp":  exp,
        "iat":  datetime.datetime.utcnow(),
    }
    if workspace:
        payload["ws"] = {k: v for k, v in workspace.items() if v is not None}
    if cfg:
        payload["cfg"] = {k: v for k, v in cfg.items() if v}
    return jwt.encode(payload, _secret(), algorithm=_ALGO)


def decode_token(token: str) -> dict | None:
    """Validate and decode a session JWT.

    Returns a dict with keys ``username``, ``role``, and optionally
    ``workspace`` and ``cfg``.  Returns None if the token is expired
    or tampered with.
    """
    try:
        data = jwt.decode(token, _secret(), algorithms=[_ALGO])
        result: dict = {"username": data["sub"], "role": data["role"]}
        if "ws"  in data:
            result["workspace"] = data["ws"]
        if "cfg" in data:
            result["cfg"] = data["cfg"]
        return result
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError):
        return None
