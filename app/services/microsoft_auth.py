"""
Microsoft Entra ID (Azure AD) OAuth sign-in + user self-provisioning.

Flow (stateless — no server session):
  1. /login/microsoft?organization_code=<slug> builds a Microsoft authorize URL.
     CSRF state + the org slug + next path are packed into a short-lived signed
     JWT (state token) instead of a server session.
  2. Microsoft redirects back to /auth/microsoft/callback?code=...&state=...
  3. We validate the state token, exchange the code for id-token claims, then
     find-or-create the user + their membership in that tenant, and issue our
     own app JWT.

`msal` is imported lazily so this module loads even before the dependency is
installed / Azure is configured.
"""

from __future__ import annotations

import datetime
import logging
import re
import secrets
import uuid
from typing import Any, Optional

import jwt

from app.core.config import settings
from app.core.exceptions import AuthenticationError, ValidationError

_log = logging.getLogger(__name__)

SCOPES = ["User.Read"]
_STATE_PURPOSE = "ms_oauth_state"
_STATE_TTL_SECONDS = 600


# ---------------------------------------------------------------------------
# Tenant lookup + Azure credential resolution
# ---------------------------------------------------------------------------

def get_tenant_by_slug(db, slug: str):
    from promptops_app.database import Project

    s = (slug or "").strip().lower()
    if not s:
        return None
    return db.query(Project).filter(Project.slug == s).first()


def _resolve_client_id(project) -> Optional[str]:
    return (getattr(project, "azure_client_id", None) or settings.azure_client_id or "").strip() or None


def _resolve_client_secret(project) -> Optional[str]:
    return (getattr(project, "azure_client_secret", None) or settings.azure_client_secret_value or "").strip() or None


def _resolve_authority(project) -> str:
    tid = (getattr(project, "azure_tenant_id", None) or settings.azure_tenant_id or "common").strip()
    return f"https://login.microsoftonline.com/{tid}"


def _redirect_uri() -> str:
    if settings.azure_redirect_uri:
        return settings.azure_redirect_uri.strip()
    # Fallback — should be set explicitly in .env to match the Azure registration.
    return "http://localhost:8000/api/v1/auth/microsoft/callback"


def microsoft_enabled_for_tenant(project) -> bool:
    if not project or not getattr(project, "microsoft_auth_enabled", True):
        return False
    if not settings.microsoft_auth_enabled:
        return False
    return bool(_resolve_client_id(project) and _resolve_client_secret(project))


def _msal_app(project):
    try:
        import msal
    except ImportError as exc:  # pragma: no cover
        raise ValidationError("Microsoft sign-in is not available on this server (msal not installed).") from exc

    client_id = _resolve_client_id(project)
    client_secret = _resolve_client_secret(project)
    if not client_id or not client_secret:
        raise ValidationError("Microsoft sign-in is not configured for this organization.")
    return msal.ConfidentialClientApplication(
        client_id,
        authority=_resolve_authority(project),
        client_credential=client_secret,
    )


# ---------------------------------------------------------------------------
# Public: login options for a tenant slug
# ---------------------------------------------------------------------------

def tenant_login_options(db, slug: str) -> dict:
    project = get_tenant_by_slug(db, slug)
    if not project:
        return {"valid": False}
    return {
        "valid": True,
        "slug": project.slug,
        "name": project.name,
        "status": project.status or "active",
        "microsoft_enabled": microsoft_enabled_for_tenant(project),
    }


# ---------------------------------------------------------------------------
# State token (signed, short-lived) — replaces Flask session
# ---------------------------------------------------------------------------

def _issue_state(slug: str, next_path: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "purpose": _STATE_PURPOSE,
        "slug": slug,
        "next": next_path or "/",
        "nonce": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + datetime.timedelta(seconds=_STATE_TTL_SECONDS),
    }
    return jwt.encode(payload, settings.jwt_secret_value, algorithm="HS256")


def _decode_state(state: str) -> dict:
    try:
        payload = jwt.decode(state, settings.jwt_secret_value, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid or expired sign-in session.") from exc
    if payload.get("purpose") != _STATE_PURPOSE or not payload.get("slug"):
        raise AuthenticationError("Invalid sign-in session.")
    return payload


# ---------------------------------------------------------------------------
# Build authorize URL
# ---------------------------------------------------------------------------

def build_login_url(db, slug: str, next_path: str = "/") -> str:
    project = get_tenant_by_slug(db, slug)
    if not project:
        raise ValidationError("Unknown organization code.")
    if (project.status or "active") != "active":
        raise ValidationError("This organization has been suspended.")
    if not microsoft_enabled_for_tenant(project):
        raise ValidationError("Microsoft sign-in is not enabled for this organization.")

    state = _issue_state(project.slug, next_path)
    return _msal_app(project).get_authorization_request_url(
        scopes=SCOPES,
        state=state,
        redirect_uri=_redirect_uri(),
    )


# ---------------------------------------------------------------------------
# Exchange code -> claims
# ---------------------------------------------------------------------------

def _claims_from_result(result: dict[str, Any]) -> dict[str, Any]:
    claims = result.get("id_token_claims") or {}
    if claims:
        return claims
    return {
        "oid": result.get("oid"),
        "preferred_username": result.get("preferred_username"),
        "name": result.get("name"),
        "email": result.get("email"),
    }


def exchange_code_for_claims(db, code: str, slug: str) -> dict[str, Any]:
    project = get_tenant_by_slug(db, slug)
    if not project:
        raise ValidationError("Unknown organization code.")
    result = _msal_app(project).acquire_token_by_authorization_code(
        code, scopes=SCOPES, redirect_uri=_redirect_uri(),
    )
    if "error" in result:
        raise AuthenticationError(result.get("error_description") or result.get("error"))
    return _claims_from_result(result)


# ---------------------------------------------------------------------------
# Find-or-create user + membership
# ---------------------------------------------------------------------------

def _normalize_email(claims: dict[str, Any]) -> Optional[str]:
    for key in ("email", "preferred_username", "upn"):
        value = (claims.get(key) or "").strip().lower()
        if value and "@" in value:
            return value[:255]
    return None


def _username_base(email: str) -> str:
    local = re.sub(r"[^a-z0-9._-]", "", email.split("@", 1)[0].lower())
    return (local or "user")[:90]


def _unique_username(db, base: str) -> str:
    from promptops_app.database import User

    if not db.query(User).filter(User.username == base).first():
        return base
    for n in range(2, 10000):
        cand = f"{base[:90]}_{n}"
        if not db.query(User).filter(User.username == cand).first():
            return cand
    return f"user_{uuid.uuid4().hex[:8]}"


def _email_allowed(email: str, project) -> bool:
    raw = (getattr(project, "allowed_email_domains", None) or "").strip()
    if not raw:
        return True
    allowed = [p.strip().lower().lstrip("@") for p in raw.split(",") if p.strip()]
    if not allowed:
        return True
    return email.rsplit("@", 1)[-1].lower() in allowed


def find_or_create_user(db, claims: dict[str, Any], slug: str):
    """Return (user, membership). Creates the user and/or membership as needed.

    Matches a person by Microsoft OID (global), then email (global). Creates a
    global User row if none exists, then ensures an active membership in this
    tenant with the tenant's default new-member role.
    """
    from promptops_app.database import TenantMembership, User
    from app.services import tenant_service

    project = get_tenant_by_slug(db, slug)
    if not project:
        raise ValidationError("Unknown organization code.")
    if (project.status or "active") != "active":
        raise ValidationError("This organization has been suspended.")
    if not microsoft_enabled_for_tenant(project):
        raise ValidationError("Microsoft sign-in is not enabled for this organization.")

    oid = (claims.get("oid") or "").strip() or None
    email = _normalize_email(claims)
    display_name = (claims.get("name") or "").strip() or (email.split("@")[0] if email else "User")

    if not email and not oid:
        raise AuthenticationError("Microsoft account did not return an email or id.")
    if email and not _email_allowed(email, project):
        raise AuthenticationError(
            f"This Microsoft account is not authorized for organization '{project.slug}'."
        )

    # Find the global user by OID, then email.
    user = None
    if oid:
        user = db.query(User).filter(User.microsoft_oid == oid).first()
    if not user and email:
        user = db.query(User).filter(User.email == email).first()

    if not user:
        username = _unique_username(db, _username_base(email or f"user{oid or ''}"))
        user = User(
            username=username,
            password_hash=None,
            display_name=display_name[:200],
            email=email,
            microsoft_oid=oid,
            role=tenant_service.DEFAULT_MEMBER_ROLE,
            project_id=project.id,
            is_active=True,
            is_platform_admin=False,
        )
        db.add(user)
        db.flush()
    else:
        # Backfill identity fields if missing.
        if oid and not user.microsoft_oid:
            user.microsoft_oid = oid
        if email and not user.email:
            user.email = email
        if display_name and not user.display_name:
            user.display_name = display_name[:200]

    if not user.is_active:
        raise AuthenticationError("Your account has been deactivated. Contact an admin.")

    # Ensure a membership for this tenant.
    membership = (
        db.query(TenantMembership)
        .filter(TenantMembership.user_id == user.id, TenantMembership.project_id == project.id)
        .first()
    )
    if membership is None:
        tenant_service.assert_can_add_member(db, project.id)
        default_role = tenant_service.normalize_role(
            getattr(project, "azure_new_user_role", None) or settings.azure_new_user_role
        )
        membership = TenantMembership(
            user_id=user.id, project_id=project.id, role=default_role,
            active=True, created_by="microsoft",
        )
        db.add(membership)
        db.flush()
    elif not membership.active:
        raise AuthenticationError("Your membership in this organization has been deactivated.")

    # Remember last-active tenant for convenience.
    user.project_id = project.id
    return user, membership


# ---------------------------------------------------------------------------
# Callback helper
# ---------------------------------------------------------------------------

def slug_from_state(state: str) -> str:
    return _decode_state(state)["slug"]


def next_from_state(state: str) -> str:
    try:
        return _decode_state(state).get("next") or "/"
    except Exception:
        return "/"
