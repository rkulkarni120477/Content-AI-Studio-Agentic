"""Auto-provision a DIS client/workspace for a newly created tenant.

A "client" in DIS has no database row — it's a YAML file under
dis_backend/config/clients/ (loaded once into an in-process TenantRegistry)
plus an entry in config/dis_access.json's available_clients allowlist (the
gate app/core/dis_access.py checks before trusting a resolved client id).
Tenant creation used to require picking one of a handful of pre-existing
clients from a dropdown; this makes a brand-new one for real instead.

Best-effort throughout: DIS being unreachable, or the config directory being
unwritable, must never fail tenant creation itself — the tenant still exists
in CAS's own DB either way, just without a working Source Library client
until this is retried or fixed by hand.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.dis_access import (  # noqa: SLF001 — intentional shared-path reuse
    _access_config_path,
    load_dis_access_config,
    normalize_client_name,
)

_log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLIENTS_DIR = _REPO_ROOT / "dis_backend" / "config" / "clients"


def _write_client_yaml(slug: str, display_name: str) -> None:
    """Write a minimal TenantConfig YAML — every other field defaults, and
    TenantConfig.get_client() already synthesizes a matching ClientConfig
    when none is listed ("backward-safe one-client mode")."""
    _CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _CLIENTS_DIR / f"{slug}.yaml"
    content = (
        f"# Auto-created on tenant creation — see app/services/dis_provisioning.py.\n"
        f"tenant_id: {json.dumps(slug)}\n"
        f"display_name: {json.dumps(display_name)}\n"
        f"namespace: {json.dumps(slug)}\n"
    )
    path.write_text(content, encoding="utf-8")


def _add_to_available_clients(slug: str) -> None:
    """Idempotently append *slug* to config/dis_access.json's available_clients."""
    path = _access_config_path()
    cfg: dict = {}
    if path.exists():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

    available = list(cfg.get("available_clients") or [])
    if slug not in available:
        available.append(slug)
    cfg["available_clients"] = available

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    if str(settings.dis_available_clients or "").strip():
        _log.warning(
            "dis_client_provisioned_but_env_override_active  slug=%s  "
            "DIS_AVAILABLE_CLIENTS is set and takes priority over config/dis_access.json — "
            "add %r to it by hand too, or this client stays invisible.",
            slug, slug,
        )


def _reload_dis_registry(anchor_tenant_id: str) -> None:
    """Best-effort: ask DIS to reload config/clients/*.yaml right now, so the
    new file is live without restarting the dis_backend container."""
    if not settings.dis_enabled or not anchor_tenant_id:
        return
    service_token = str(settings.dis_service_token or "").strip()
    if not service_token:
        return
    url = f"{str(settings.dis_api_base_url).rstrip('/')}/admin/config/reload"
    try:
        httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {service_token}",
                "X-CAS-Tenant-Id": anchor_tenant_id,
                "X-CAS-Roles": "super_admin",
            },
            timeout=10,
        ).raise_for_status()
    except Exception as exc:
        _log.warning("dis_registry_reload_failed  anchor_tenant_id=%s  error=%s", anchor_tenant_id, exc)


def provision_dis_client(client_name: str, display_name: str) -> None:
    """Create a DIS client/workspace for *client_name* and make it immediately usable.

    The id actually written (YAML tenant_id/namespace + available_clients
    entry) is ``normalize_client_name(client_name)`` — the exact same
    normalization app.core.dis_access applies to a tenant's Project.client_name
    at *read* time (resolve_course_dis_client, get_dis_access_for_user, ...).
    Provisioning under the tenant's slug instead would silently create a
    client no lookup path ever resolves to, since resolution is keyed on
    client_name everywhere, never on slug.

    Called after the tenant's own DB transaction has committed — this is an
    external side effect, not something to roll back the tenant over.
    """
    slug = normalize_client_name(client_name)
    if not slug:
        _log.warning("dis_client_provisioning_skipped_empty_client_name  display_name=%r", display_name)
        return
    try:
        _write_client_yaml(slug, display_name)
        _add_to_available_clients(slug)
        cfg = load_dis_access_config(force_reload=True)
        anchor = cfg.get("default_tenant_id", "")
        _reload_dis_registry(anchor)
        _log.info("dis_client_provisioned  slug=%s  display_name=%r", slug, display_name)
    except Exception:
        _log.exception("dis_client_provisioning_failed  slug=%s", slug)
