"""Resolve DIS tenant/client access from the authenticated CAS user.

Production intent:
- Browser/frontend never sends DIS secrets.
- CAS backend resolves client access from the logged-in CAS user.
- Super admins can switch client from Source Library UI.
- Client admins/users are locked to their mapped client automatically.

Access configuration can be stored once in `config/dis_access.json` so you do
not need to export PowerShell variables every time. Environment variables remain
supported as overrides for deployment.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings


@dataclass(frozen=True)
class DISAccessContext:
    tenant_id: str
    client_id: str
    dis_role: str
    is_super_admin: bool = False
    available_clients: list[str] = field(default_factory=list)


_ACCESS_CONFIG_CACHE: dict[str, Any] | None = None


def _setting(name: str, default: Any = "") -> Any:
    return getattr(settings, name, default)


def _split_csv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _parse_map(value: Any) -> dict[str, str]:
    """Parse mapping from dict, JSON object, or `user=client,user2=client2`."""
    if not value:
        return {}
    if isinstance(value, dict):
        return {str(k).strip().lower(): str(v).strip() for k, v in value.items() if str(k).strip() and str(v).strip()}
    raw = str(value).strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return _parse_map(parsed)
    except Exception:
        pass
    result: dict[str, str] = {}
    for part in raw.split(","):
        if not part.strip():
            continue
        if "=" in part:
            user, client = part.split("=", 1)
        elif ":" in part:
            user, client = part.split(":", 1)
        else:
            continue
        user = user.strip().lower()
        client = client.strip()
        if user and client:
            result[user] = client
    return result


def _access_config_path() -> Path:
    configured = str(_setting("dis_access_config_path", "") or "").strip()
    if configured:
        return Path(configured)
    # app/core/dis_access.py -> project root/config/dis_access.json
    return Path(__file__).resolve().parents[2] / "config" / "dis_access.json"


def load_dis_access_config(force_reload: bool = False) -> dict[str, Any]:
    """Load persistent Source Library access config.

    Env values override file values where configured. This lets production use
    secure env/secrets while local dev can simply edit config/dis_access.json.
    """
    global _ACCESS_CONFIG_CACHE
    if _ACCESS_CONFIG_CACHE is not None and not force_reload:
        return _ACCESS_CONFIG_CACHE

    path = _access_config_path()
    file_cfg: dict[str, Any] = {}
    if path.exists():
        try:
            file_cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            file_cfg = {}

    default_client = str(_setting("dis_default_client_id", "") or file_cfg.get("default_client_id") or _setting("dis_default_tenant_id", "") or file_cfg.get("default_tenant_id") or "aim").strip()
    default_tenant = str(_setting("dis_default_tenant_id", "") or file_cfg.get("default_tenant_id") or default_client).strip()

    env_clients = _split_csv(_setting("dis_available_clients", ""))
    available_clients = env_clients or _split_csv(file_cfg.get("available_clients")) or [default_client]

    env_super_users = _split_csv(_setting("dis_super_admin_usernames", ""))
    env_super_roles = _split_csv(_setting("dis_super_admin_roles", ""))
    super_users = env_super_users or _split_csv(file_cfg.get("super_admin_usernames"))
    super_roles = env_super_roles or _split_csv(file_cfg.get("super_admin_roles"))

    # Keep old env DIS_USER_CLIENT_MAP as client-admin map for backward compatibility.
    env_user_map = _parse_map(_setting("dis_user_client_map", ""))
    file_client_admin_map = _parse_map(file_cfg.get("client_admin_map", {}))
    file_user_client_map = _parse_map(file_cfg.get("user_client_map", {}))

    client_admin_map = {**file_client_admin_map, **env_user_map}
    user_client_map = file_user_client_map

    cfg = {
        "path": str(path),
        "default_client_id": default_client,
        "default_tenant_id": default_tenant,
        "available_clients": available_clients,
        "super_admin_usernames": [u.lower() for u in super_users],
        "super_admin_roles": [r.lower() for r in super_roles],
        "client_admin_map": {k.lower(): v for k, v in client_admin_map.items()},
        "user_client_map": {k.lower(): v for k, v in user_client_map.items()},
    }
    _ACCESS_CONFIG_CACHE = cfg
    return cfg


def save_dis_access_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist local access config to config/dis_access.json.

    Intended for local/admin UI. In production, you may disable this endpoint or
    manage access from the real users/tenants table instead.
    """
    global _ACCESS_CONFIG_CACHE
    path = _access_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    allowed_keys = {
        "default_client_id",
        "default_tenant_id",
        "available_clients",
        "super_admin_usernames",
        "super_admin_roles",
        "client_admin_map",
        "user_client_map",
    }
    clean: dict[str, Any] = {k: payload[k] for k in allowed_keys if k in payload}
    if not clean.get("default_client_id"):
        clean["default_client_id"] = "cengage"
    if not clean.get("default_tenant_id"):
        clean["default_tenant_id"] = clean["default_client_id"]
    if not clean.get("available_clients"):
        clean["available_clients"] = [clean["default_client_id"]]

    path.write_text(json.dumps(clean, indent=2, sort_keys=True), encoding="utf-8")
    _ACCESS_CONFIG_CACHE = None
    return load_dis_access_config(force_reload=True)


def _current_username(user: Any) -> str:
    return str(getattr(user, "username", "") or getattr(user, "email", "") or getattr(user, "id", "")).strip()



# Same alias normalization the Source Library router applies to project client
# names, so "Cengage" / "cengage_learning" / "AIM" all resolve consistently.
_CLIENT_NAME_ALIASES = {"cengage_learning": "cengage", "cengage": "cengage", "aim": "aim", "academian": "academian", "demo": "demo"}


def _normalize_client_name(value: Any) -> str:
    v = str(value or "").strip().lower().replace(" ", "_")
    return _CLIENT_NAME_ALIASES.get(v, v)


def _membership_clients(current_user: Any) -> list[tuple[str, str]]:
    """Return [(client_id, membership_role)] from the user's active project
    memberships, newest membership first.

    This links Source Library access to tenant/org membership: adding a user to
    an org whose projects carry a client_name grants DIS access to that client
    without editing config/dis_access.json. Strictly read-only; any DB problem
    returns [] so resolution falls back to the previous (config-only) behavior.
    """
    user_id = getattr(current_user, "id", None)
    if not user_id:
        return []
    try:
        from promptops_app.database import Project, SessionLocal, TenantMembership
        with SessionLocal() as db:
            rows = (
                db.query(Project.client_name, TenantMembership.role)
                .join(TenantMembership, TenantMembership.project_id == Project.id)
                .filter(TenantMembership.user_id == int(user_id), Project.is_active == True)  # noqa: E712
                .order_by(TenantMembership.id.desc())
                .all()
            )
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    for client_name, role in rows:
        cid = _normalize_client_name(client_name)
        if cid:
            out.append((cid, str(role or "").strip().lower()))
    return out


def _infer_client_from_username(username: str, available: list[str]) -> str:
    lowered = username.lower()
    for client in available:
        if client and client.lower() in lowered:
            return client
    return ""


def get_dis_access_for_user(current_user: Any, requested_client_id: str | None = None) -> DISAccessContext:
    cfg = load_dis_access_config()
    username = _current_username(current_user)
    username_l = username.lower()
    role = str(getattr(current_user, "role", "user") or "user").lower()

    default_client = cfg["default_client_id"]
    default_tenant = cfg["default_tenant_id"]
    available = cfg["available_clients"] or [default_client]

    super_users = set(cfg["super_admin_usernames"])
    super_roles = set(cfg["super_admin_roles"])

    # Safe local default: if no super admin is configured anywhere, CAS role admin
    # can act as DIS super admin. Once config/dis_access.json exists with admin
    # explicitly listed, this remains clear and controlled.
    if not super_users and not super_roles:
        is_super_admin = role == "admin"
    else:
        is_super_admin = username_l in super_users or role in super_roles

    client_admin_map = cfg["client_admin_map"]
    user_client_map = cfg["user_client_map"]
    profile_client = str(getattr(current_user, "client_id", "") or getattr(current_user, "tenant_id", "") or "").strip()
    inferred_client = _infer_client_from_username(username, available)

    if is_super_admin:
        client_id = (requested_client_id or client_admin_map.get(username_l) or user_client_map.get(username_l) or profile_client or inferred_client or default_client).strip()
        if available and client_id not in available:
            client_id = default_client
        return DISAccessContext(
            tenant_id=client_id or default_tenant,
            client_id=client_id or default_client,
            dis_role="super_admin",
            is_super_admin=True,
            available_clients=available,
        )

    if username_l in client_admin_map:
        client_id = client_admin_map[username_l]
        dis_role = "client_admin"
    elif username_l in user_client_map:
        client_id = user_client_map[username_l]
        dis_role = "user"
    else:
        # Resolution step 3 (new): derive access from tenant/org membership.
        # A user added to an org whose projects carry client_name=AIM gets AIM
        # Source Library access automatically — no config edit, no restart.
        # Explicit config mappings above still take priority; when the lookup
        # yields nothing (no memberships, projects without client_name, or any
        # DB error) we fall through to the previous default behavior unchanged.
        membership = _membership_clients(current_user)
        member_clients: list[str] = []
        for cid, _mrole in membership:
            if cid not in member_clients and (not available or cid in available):
                member_clients.append(cid)
        requested_norm = _normalize_client_name(requested_client_id or "")
        # Membership only ADDS access — it never blocks a request the previous
        # config-only logic would have allowed. So it applies only when the
        # requested client is one of the user's membership clients, or when no
        # specific client was requested (follow the newest membership). Any other
        # case falls through to the legacy default path unchanged.
        if member_clients and (not requested_norm or requested_norm in member_clients):
            client_id = requested_norm if requested_norm in member_clients else member_clients[0]
            member_role = next((mrole for cid, mrole in membership if cid == client_id), "")
            dis_role = "client_admin" if (member_role == "admin" or role in {"admin", "reviewer"}) else "user"
            return DISAccessContext(
                tenant_id=client_id,
                client_id=client_id,
                dis_role=dis_role,
                is_super_admin=False,
                available_clients=[client_id],
            )
        client_id = profile_client or inferred_client or default_client
        dis_role = "client_admin" if role in {"admin", "reviewer"} else "user"

    if available and client_id not in available:
        client_id = default_client
    return DISAccessContext(
        tenant_id=client_id or default_tenant,
        client_id=client_id or default_client,
        dis_role=dis_role,
        is_super_admin=False,
        available_clients=[client_id or default_client],
    )


def build_dis_profile(current_user: Any) -> dict[str, Any]:
    access = get_dis_access_for_user(current_user)
    return {
        "client_id": access.client_id,
        "tenant_id": access.tenant_id,
        "dis_role": access.dis_role,
        "is_dis_super_admin": access.is_super_admin,
        "available_clients": access.available_clients,
    }
