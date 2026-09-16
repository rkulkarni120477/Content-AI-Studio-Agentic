"""Auto-provision a DIS client/workspace for a newly created tenant.

A "client" in DIS has no database row — it's a YAML file under
dis_backend/config/clients/ (loaded once into an in-process TenantRegistry)
plus an entry in config/dis_access.json's available_clients allowlist (the
gate app/core/dis_access.py checks before trusting a resolved client id).

Phase 8 generates a `client:` wrapper YAML from an explicit template family
(minimal | academic | publishing) plus optional overrides, after strict
validation. CREATE ONLY: an existing target file is never overwritten.

The CAS tenant-create path keeps calling ``provision_dis_client(name, display)``
and therefore uses template ``minimal`` as that path's declared family — not
inferred from the client name. Callers that need academic/publishing pass
``template=`` explicitly.

Best-effort throughout: DIS being unreachable, or the config directory being
unwritable, must never fail tenant creation itself — the tenant still exists
in CAS's own DB either way, just without a working Source Library client
until this is retried or fixed by hand.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import httpx
import yaml

from app.core.config import settings
from app.core.dis_access import (  # noqa: SLF001 — intentional shared-path reuse
    _access_config_path,
    load_dis_access_config,
    normalize_client_name,
)
from app.services.dis_metadata_templates import (
    PROVISIONING_STAMP_VERSION,
    TEMPLATE_FAMILIES,
    deepcopy_sections,
)

_log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CLIENTS_DIR = _REPO_ROOT / "dis_backend" / "config" / "clients"

# CAS tenant-create still calls provision_dis_client(name, display). That path
# selects this family explicitly in the function default — it is not inferred
# from client_name / slug / domain.
_CAS_DEFAULT_TEMPLATE = "minimal"

_ALLOWED_OVERRIDE_KEYS = frozenset({
    "metadata_schemas",
    "metadata_framework",
    "source_ui",
    "retrieval",
    "profile",
})
_FORBIDDEN_OVERRIDE_SECTIONS = frozenset({
    "document_processing",
    "structure_store",
    "vector_store",
    "storage",
    "namespace",
    "client",
    "tenant_id",
    "auth",
    "pipeline",
    "embedding",
    "deduplication",
})
_SECRET_KEY_RE = re.compile(
    r"(password|secret|api[_-]?key|access[_-]?key|token|credential|private[_-]?key)",
    re.I,
)
_SCHEMA_FIELD_TYPES = frozenset({"string", "enum", "list", "number", "date", "boolean"})
_TAXONOMY_TYPES = frozenset({"select", "text"})
_UPLOAD_CONTROLS = frozenset({"text", "select", "textarea"})
_KNOWN_CLIENT_PROFILES = frozenset({"aim"})
_PROMOTE_FALLBACK = frozenset({"index", "retrieval", "filter_options", "cas_list"})


class DisProvisioningValidationError(ValueError):
    """Generated configuration is invalid; nothing must be written."""


class DisProvisioningExistsError(FileExistsError):
    """CREATE ONLY: target client YAML already exists."""


def _import_dis_file(module_name: str, relative: str):
    import importlib.util
    import sys

    path = _REPO_ROOT / "dis_backend" / relative
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _field_registry_mod():
    return _import_dis_file(
        "_dis_provisioning_field_registry",
        "services/metadata_framework/registry.py",
    )


def _dis_settings_mod():
    return _import_dis_file(
        "_dis_provisioning_tenant_settings",
        "config/settings.py",
    )


def valid_promote_targets() -> frozenset:
    try:
        return frozenset(_field_registry_mod().VALID_PROMOTE)
    except Exception:
        return _PROMOTE_FALLBACK


def registry_reference_names() -> set[str]:
    """Canonical keys + aliases from DEFAULT_REGISTRY (not copied into YAML)."""
    names: set[str] = set()
    try:
        reg = _field_registry_mod()
        for spec in reg.DEFAULT_REGISTRY.all():
            names.add(spec.key)
            names.update(spec.aliases or ())
            if spec.filter_options_key:
                names.add(spec.filter_options_key)
    except Exception:
        pass
    return names


def load_via_tenant_config(raw: Mapping[str, Any]):
    """Load *raw* through the real TenantRegistry client-wrapper converter."""
    settings_mod = _dis_settings_mod()
    registry_cls = settings_mod.TenantRegistry
    holder = registry_cls.__new__(registry_cls)
    return registry_cls._client_raw_to_tenant(holder, copy.deepcopy(dict(raw)))


def _deep_merge(base: Any, override: Any) -> Any:
    if override is None:
        return copy.deepcopy(base)
    if isinstance(base, dict) and isinstance(override, dict):
        out = copy.deepcopy(base)
        for key, value in override.items():
            if key in out:
                out[key] = _deep_merge(out[key], value)
            else:
                out[key] = copy.deepcopy(value)
        return out
    return copy.deepcopy(override)


def render_client_yaml(document: Mapping[str, Any]) -> str:
    """Deterministic YAML: insertion order, no timestamps, explicit unicode."""
    dumped = yaml.safe_dump(
        dict(document),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=120,
    )
    header = (
        "# Generated by Phase 8 metadata provisioning "
        "(app/services/dis_provisioning.py).\n"
        "# provisioning.template / version identify the generator; they are not a "
        "metadata authority and are ignored at runtime.\n"
    )
    return header + dumped


def _identity_namespace(slug: str) -> str:
    """Preserve the existing provisioner convention: namespace == client id."""
    return slug


def _schema_body_from_override(slug: str, raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise DisProvisioningValidationError("metadata_schemas override must be a mapping")
    if "required_fields" in raw or "optional_fields" in raw:
        return copy.deepcopy(raw)
    if slug in raw and isinstance(raw[slug], dict):
        return copy.deepcopy(raw[slug])
    if "default" in raw and isinstance(raw["default"], dict):
        return copy.deepcopy(raw["default"])
    raise DisProvisioningValidationError(
        "metadata_schemas override must be a schema body or keyed by client id"
    )


def build_client_document(
    slug: str,
    display_name: str,
    template: str,
    overrides: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve template + overrides in memory. Template is required (no inference)."""
    if template is None or str(template).strip() == "":
        raise DisProvisioningValidationError("template family is required")
    family = str(template).strip().lower()
    if family not in TEMPLATE_FAMILIES:
        raise DisProvisioningValidationError(
            f"invalid template family {template!r}; expected one of {sorted(TEMPLATE_FAMILIES)}"
        )
    if not slug or not str(slug).strip():
        raise DisProvisioningValidationError("client id is required")
    if not display_name or not str(display_name).strip():
        raise DisProvisioningValidationError("display name is required")

    ov = dict(overrides or {})
    unknown = set(ov) - _ALLOWED_OVERRIDE_KEYS
    forbidden = set(ov) & _FORBIDDEN_OVERRIDE_SECTIONS
    if unknown:
        raise DisProvisioningValidationError(f"unsupported override keys: {sorted(unknown)}")
    if forbidden:
        raise DisProvisioningValidationError(f"forbidden override keys: {sorted(forbidden)}")

    sections = deepcopy_sections(family)
    schema_body = sections.get("metadata_schemas_body")
    source_ui = sections.get("source_ui")
    framework = None

    if "metadata_schemas" in ov:
        schema_body = _deep_merge(
            schema_body or {},
            _schema_body_from_override(slug, ov["metadata_schemas"]),
        )
    if "metadata_framework" in ov:
        framework = _deep_merge({}, copy.deepcopy(ov["metadata_framework"]))
    ui_override = ov.get("source_ui")
    if "retrieval" in ov:
        retrieval_ov = ov["retrieval"]
        if not isinstance(retrieval_ov, dict):
            raise DisProvisioningValidationError("retrieval override must be a mapping")
        extra_ret = set(retrieval_ov) - {"source_ui"}
        if extra_ret:
            raise DisProvisioningValidationError(
                f"retrieval override may only contain source_ui; extra keys: {sorted(extra_ret)}"
            )
        if ui_override is None:
            ui_override = retrieval_ov.get("source_ui")
    if ui_override is not None:
        source_ui = _deep_merge(source_ui or {}, copy.deepcopy(ui_override))

    document: Dict[str, Any] = {
        "provisioning": {
            "template": family,
            "version": PROVISIONING_STAMP_VERSION,
        },
        "client": {
            "client_id": slug,
            "client_name": str(display_name).strip(),
            "namespace": _identity_namespace(slug),
            "enabled": True,
            "namespace_prefix": slug,
        },
    }
    if schema_body:
        document["metadata_schemas"] = {slug: schema_body}
    if framework:
        document["metadata_framework"] = framework
    if source_ui:
        document["retrieval"] = {"source_ui": source_ui}
    if "profile" in ov and ov["profile"] is not None:
        document["profile"] = ov["profile"]
    return document


def _walk_secret_keys(node: Any, path: str = "") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            key_s = str(key)
            here = f"{path}.{key_s}" if path else key_s
            if _SECRET_KEY_RE.search(key_s):
                raise DisProvisioningValidationError(
                    f"secrets are not permitted in generated YAML ({here})"
                )
            if isinstance(value, str) and "://" in value and "@" in value.split("://", 1)[-1]:
                raise DisProvisioningValidationError(
                    f"credential URL is not permitted in generated YAML ({here})"
                )
            _walk_secret_keys(value, here)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk_secret_keys(item, f"{path}[{i}]")


def _field_list(schema: Mapping[str, Any], which: str) -> list:
    raw = schema.get(which) or []
    if not isinstance(raw, list):
        raise DisProvisioningValidationError(f"{which} must be a list")
    return raw


def _validate_schema(slug: str, document: Mapping[str, Any]) -> set[str]:
    schemas = document.get("metadata_schemas")
    if schemas is None:
        return set()
    if not isinstance(schemas, dict) or not schemas:
        raise DisProvisioningValidationError("metadata_schemas must be a non-empty mapping")
    if slug not in schemas:
        raise DisProvisioningValidationError(
            f"metadata_schemas must be keyed by client id {slug!r}"
        )
    extra_keys = set(schemas) - {slug}
    if extra_keys:
        raise DisProvisioningValidationError(
            f"metadata_schemas has unexpected keys {sorted(extra_keys)}"
        )
    body = schemas[slug]
    if not isinstance(body, dict):
        raise DisProvisioningValidationError("metadata schema body must be a mapping")
    seen: set[str] = set()
    for which in ("required_fields", "optional_fields"):
        for field in _field_list(body, which):
            if not isinstance(field, dict) or not str(field.get("name") or "").strip():
                raise DisProvisioningValidationError(f"{which} entries must have a name")
            name = str(field["name"]).strip()
            if name in seen:
                raise DisProvisioningValidationError(f"duplicate metadata field {name!r}")
            seen.add(name)
            ftype = str(field.get("type") or "string")
            if ftype not in _SCHEMA_FIELD_TYPES:
                raise DisProvisioningValidationError(
                    f"invalid metadata field type {ftype!r} for {name!r}"
                )
            values = field.get("values") or []
            if ftype == "enum":
                if not isinstance(values, list) or not values:
                    raise DisProvisioningValidationError(
                        f"enum field {name!r} requires a non-empty values list"
                    )
                if len(values) != len(set(values)):
                    raise DisProvisioningValidationError(f"enum field {name!r} has duplicate values")
            elif values and not isinstance(values, list):
                raise DisProvisioningValidationError(f"field {name!r} values must be a list")
    return seen


def _validate_framework(document: Mapping[str, Any]) -> set[str]:
    framework = document.get("metadata_framework")
    if framework is None:
        return set()
    if not isinstance(framework, dict):
        raise DisProvisioningValidationError("metadata_framework must be a mapping")
    extra = set(framework) - {"fields"}
    if extra:
        raise DisProvisioningValidationError(
            f"metadata_framework may only contain fields; extra keys: {sorted(extra)}"
        )
    fields = framework.get("fields")
    if not fields:
        raise DisProvisioningValidationError("metadata_framework.fields must be a non-empty mapping")
    if not isinstance(fields, dict):
        raise DisProvisioningValidationError("metadata_framework.fields must be a mapping")
    promote_ok = valid_promote_targets()
    seen: set[str] = set()
    for key, body in fields.items():
        name = str(key).strip()
        if not name:
            raise DisProvisioningValidationError("metadata_framework field key is empty")
        if name == "acs_codes":
            raise DisProvisioningValidationError("acs_codes must not be added to the Field Registry")
        if name in seen:
            raise DisProvisioningValidationError(f"duplicate metadata_framework field {name!r}")
        seen.add(name)
        if not isinstance(body, dict):
            raise DisProvisioningValidationError(f"metadata_framework field {name!r} must be a mapping")
        promote = body.get("promote")
        if promote is None:
            raise DisProvisioningValidationError(
                f"metadata_framework field {name!r} requires promote targets"
            )
        if isinstance(promote, str):
            items = [promote]
        elif isinstance(promote, list):
            items = list(promote)
        else:
            raise DisProvisioningValidationError(
                f"metadata_framework field {name!r} promote must be a string or list"
            )
        if not items:
            raise DisProvisioningValidationError(
                f"metadata_framework field {name!r} requires at least one promote target"
            )
        for item in items:
            p = str(item).strip()
            if p not in promote_ok:
                raise DisProvisioningValidationError(
                    f"invalid Field Registry promote target {item!r} for {name!r}"
                )
    return seen


def _source_ui_keys(source_ui: Mapping[str, Any]) -> list:
    refs = []
    filters = source_ui.get("taxonomy_filters") or []
    if filters and not isinstance(filters, list):
        raise DisProvisioningValidationError("taxonomy_filters must be a list")
    seen_tax: set[str] = set()
    for entry in filters:
        if not isinstance(entry, dict):
            raise DisProvisioningValidationError("taxonomy_filters entries must be mappings")
        key = str(entry.get("key") or "").strip()
        if not key:
            raise DisProvisioningValidationError("taxonomy_filters entry missing key")
        if key in seen_tax:
            raise DisProvisioningValidationError(f"duplicate taxonomy_filters key {key!r}")
        seen_tax.add(key)
        t = str(entry.get("type") or "select").strip().lower()
        if t not in _TAXONOMY_TYPES:
            raise DisProvisioningValidationError(f"invalid taxonomy_filters type {t!r} for {key!r}")
        refs.append(("taxonomy", key))
    upload = source_ui.get("upload") or {}
    if upload and not isinstance(upload, dict):
        raise DisProvisioningValidationError("upload must be a mapping")
    fields = (upload or {}).get("fields") or []
    if fields and not isinstance(fields, list):
        raise DisProvisioningValidationError("upload.fields must be a list")
    seen_up: set[str] = set()
    seen_order: set[int] = set()
    for entry in fields:
        if not isinstance(entry, dict):
            raise DisProvisioningValidationError("upload.fields entries must be mappings")
        key = str(entry.get("key") or "").strip()
        if not key:
            raise DisProvisioningValidationError("upload.fields entry missing key")
        if key in seen_up:
            raise DisProvisioningValidationError(f"duplicate upload field {key!r}")
        seen_up.add(key)
        control = str(entry.get("control") or "text").strip().lower()
        if control not in _UPLOAD_CONTROLS:
            raise DisProvisioningValidationError(f"invalid source_ui control {control!r} for {key!r}")
        if "order" in entry:
            try:
                order = int(entry["order"])
            except (TypeError, ValueError) as exc:
                raise DisProvisioningValidationError(f"invalid upload order for {key!r}") from exc
            if order in seen_order:
                raise DisProvisioningValidationError(f"duplicate upload order {order}")
            seen_order.add(order)
        refs.append(("upload", key))
    common = source_ui.get("common_filters") or []
    if common and not isinstance(common, list):
        raise DisProvisioningValidationError("common_filters must be a list")
    return refs


def _validate_source_ui(
    document: Mapping[str, Any], schema_names: set[str], overlay_names: set[str]
) -> None:
    retrieval = document.get("retrieval")
    if retrieval is None:
        return
    if not isinstance(retrieval, dict):
        raise DisProvisioningValidationError("retrieval must be a mapping")
    extra = set(retrieval) - {"source_ui"}
    if extra:
        raise DisProvisioningValidationError(
            f"generated retrieval may only contain source_ui; extra keys: {sorted(extra)}"
        )
    source_ui = retrieval.get("source_ui")
    if not source_ui:
        return
    if not isinstance(source_ui, dict):
        raise DisProvisioningValidationError("source_ui must be a mapping")
    allowed = schema_names | overlay_names | registry_reference_names()
    for kind, key in _source_ui_keys(source_ui):
        if key not in allowed:
            raise DisProvisioningValidationError(
                f"source_ui {kind} references unknown field {key!r}"
            )


def _validate_profile(document: Mapping[str, Any], slug: str) -> None:
    if "profile" not in document:
        return
    profile = document.get("profile")
    if profile is None or str(profile).strip() == "":
        raise DisProvisioningValidationError("profile reference must be a non-empty string")
    pid = str(profile).strip().lower()
    if pid not in _KNOWN_CLIENT_PROFILES:
        raise DisProvisioningValidationError(f"unknown client profile {profile!r}")
    if pid != slug:
        raise DisProvisioningValidationError(
            f"client profile {pid!r} is bound to client id {pid!r}; "
            "Phase 8 does not create or attach Python profiles to other tenants"
        )


def _existing_namespace_owners(clients_dir: Path, skip: Optional[Path] = None) -> Dict[str, str]:
    owners: Dict[str, str] = {}
    if not clients_dir.is_dir():
        return owners
    for path in sorted(clients_dir.glob("*.yaml")):
        if skip is not None and path.resolve() == skip.resolve():
            continue
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        client_meta = raw.get("client") or {}
        tenant_id = (
            (client_meta.get("client_id") if isinstance(client_meta, dict) else None)
            or raw.get("tenant_id")
            or path.stem
        )
        namespace = (
            (client_meta.get("namespace") if isinstance(client_meta, dict) else None)
            or raw.get("namespace")
            or f"{tenant_id}_ns"
        )
        owners[str(namespace)] = str(tenant_id)
    return owners


def _validate_namespace(slug: str, document: Mapping[str, Any], clients_dir: Path) -> None:
    client = document.get("client") or {}
    namespace = client.get("namespace") if isinstance(client, dict) else None
    expected = _identity_namespace(slug)
    if namespace != expected:
        raise DisProvisioningValidationError(
            f"namespace must follow the existing provisioner convention ({expected!r})"
        )
    if not namespace or "/" in str(namespace) or "\\" in str(namespace) or ".." in str(namespace):
        raise DisProvisioningValidationError(f"invalid namespace {namespace!r}")
    owners = _existing_namespace_owners(clients_dir)
    owner = owners.get(str(namespace))
    if owner and owner != slug:
        raise DisProvisioningValidationError(
            f"namespace {namespace!r} already owned by {owner!r}"
        )


def _validate_identity(slug: str, display_name: str, document: Mapping[str, Any]) -> None:
    client = document.get("client")
    if not isinstance(client, dict):
        raise DisProvisioningValidationError("generated YAML must use the client: wrapper")
    if client.get("client_id") != slug:
        raise DisProvisioningValidationError("client.client_id must match the provisioned client id")
    if not str(client.get("client_name") or "").strip():
        raise DisProvisioningValidationError("client.client_name is required")
    if str(client.get("client_name")).strip() != str(display_name).strip():
        raise DisProvisioningValidationError("client.client_name must match display name")
    if "tenant_id" in document or "display_name" in document:
        raise DisProvisioningValidationError("legacy top-level TenantConfig shape is not permitted")


def validate_client_document(
    document: Mapping[str, Any],
    *,
    slug: str,
    display_name: str,
    clients_dir: Optional[Path] = None,
) -> None:
    if not isinstance(document, dict):
        raise DisProvisioningValidationError("generated document must be a mapping")
    _validate_identity(slug, display_name, document)
    _walk_secret_keys(document)
    if document.get("document_processing") is not None:
        raise DisProvisioningValidationError("document_processing is out of scope for Phase 8")
    for store_key in ("structure_store", "vector_store"):
        block = document.get(store_key)
        if isinstance(block, dict) and block.get("enabled"):
            raise DisProvisioningValidationError(f"{store_key} must remain disabled")
        if isinstance(block, dict) and (
            block.get("password") or block.get("url") or block.get("username")
        ):
            raise DisProvisioningValidationError(f"{store_key} credentials are not permitted")
    dir_ = clients_dir if clients_dir is not None else _CLIENTS_DIR
    _validate_namespace(slug, document, dir_)
    schema_names = _validate_schema(slug, document)
    overlay_names = _validate_framework(document)
    _validate_source_ui(document, schema_names, overlay_names)
    _validate_profile(document, slug)
    stamp = document.get("provisioning")
    if stamp is not None:
        if not isinstance(stamp, dict):
            raise DisProvisioningValidationError("provisioning stamp must be a mapping")
        if stamp.get("template") not in TEMPLATE_FAMILIES:
            raise DisProvisioningValidationError("provisioning stamp template is invalid")
        if str(stamp.get("version")) != PROVISIONING_STAMP_VERSION:
            raise DisProvisioningValidationError("provisioning stamp version is invalid")
    try:
        tenant = load_via_tenant_config(document)
    except DisProvisioningValidationError:
        raise
    except Exception as exc:
        raise DisProvisioningValidationError(
            f"generated YAML is not loadable by TenantConfig: {exc}"
        ) from exc
    if getattr(tenant, "tenant_id", None) != slug:
        raise DisProvisioningValidationError("TenantConfig tenant_id mismatch")
    if getattr(tenant, "namespace", None) != _identity_namespace(slug):
        raise DisProvisioningValidationError("TenantConfig namespace mismatch")


def _client_yaml_path(slug: str, clients_dir: Optional[Path] = None) -> Path:
    return (clients_dir if clients_dir is not None else _CLIENTS_DIR) / f"{slug}.yaml"


def write_new_client_yaml(
    slug: str,
    display_name: str,
    *,
    template: str,
    overrides: Optional[Mapping[str, Any]] = None,
    clients_dir: Optional[Path] = None,
) -> Path:
    """Validate, then CREATE ONLY write. Raises before any write on failure."""
    dir_ = clients_dir if clients_dir is not None else _CLIENTS_DIR
    dir_.mkdir(parents=True, exist_ok=True)
    path = _client_yaml_path(slug, dir_)
    document = build_client_document(slug, display_name, template, overrides)
    validate_client_document(document, slug=slug, display_name=display_name, clients_dir=dir_)
    if path.exists():
        raise DisProvisioningExistsError(str(path))
    content = render_client_yaml(document)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags, 0o644)
    except FileExistsError as exc:
        raise DisProvisioningExistsError(str(path)) from exc
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    return path


def _write_client_yaml(slug: str, display_name: str, template: str = _CAS_DEFAULT_TEMPLATE) -> None:
    """CREATE ONLY write used by provision_dis_client and existing unit tests."""
    write_new_client_yaml(slug, display_name, template=template, clients_dir=_CLIENTS_DIR)


def _add_to_available_clients(slug: str) -> None:
    """Idempotently append *slug* to config/dis_access.json's available_clients.

    Raises on a malformed existing file rather than silently treating it as
    empty: the caller (provision_dis_client) already treats this whole
    function as best-effort and logs + moves on, but swallowing a parse error
    here would have gone on to overwrite default_client_id, default_tenant_id,
    super_admin_usernames and client_admin_map with a bare
    {"available_clients": [...]} skeleton — turning a momentary corruption
    (a half-written concurrent edit, a hand-edit typo) into a permanent one.

    The write itself is tmp-file-then-rename, atomic on the same filesystem,
    so a crash mid-write can't leave a half-written file either.

    # ponytail: no cross-process lock — two provisions racing on the exact
    # same instant could still lose one's append (last replace wins). Add a
    # lock (e.g. flock while both processes share a filesystem) if tenant
    # creation ever becomes frequent enough for that to matter.
    """
    path = _access_config_path()
    cfg: dict = {}
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        if raw.strip():
            cfg = json.loads(raw)

    available = list(cfg.get("available_clients") or [])
    if slug not in available:
        available.append(slug)
    cfg["available_clients"] = available

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / (path.name + ".tmp")
    tmp_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)

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


def provision_dis_client(
    client_name: str,
    display_name: str,
    template: str = _CAS_DEFAULT_TEMPLATE,
    overrides: Optional[Mapping[str, Any]] = None,
) -> None:
    """Create a DIS client/workspace for *client_name* and make it immediately usable.

    The id actually written (YAML client_id/namespace + available_clients
    entry) is ``normalize_client_name(client_name)`` — the exact same
    normalization app.core.dis_access applies to a tenant's Project.client_name
    at *read* time. Provisioning under the tenant's slug instead would silently
    create a client no lookup path ever resolves to.

    *template* must be an explicit family (minimal | academic | publishing).
    The default ``minimal`` is the CAS tenant-create path's declared family,
    not a guess from the client name.

    Called after the tenant's own DB transaction has committed — this is an
    external side effect, not something to roll back the tenant over.
    """
    if not settings.dis_enabled:
        _log.info("dis_client_provisioning_skipped_dis_disabled  client_name=%r", client_name)
        return
    slug = normalize_client_name(client_name)
    if not slug:
        _log.warning("dis_client_provisioning_skipped_empty_client_name  display_name=%r", display_name)
        return
    try:
        write_new_client_yaml(
            slug,
            display_name,
            template=template,
            overrides=overrides,
            clients_dir=_CLIENTS_DIR,
        )
        _add_to_available_clients(slug)
        cfg = load_dis_access_config(force_reload=True)
        anchor = cfg.get("default_tenant_id", "")
        _reload_dis_registry(anchor)
        _log.info(
            "dis_client_provisioned  slug=%s  display_name=%r  template=%s",
            slug, display_name, template,
        )
    except DisProvisioningExistsError:
        _log.warning(
            "dis_client_provisioning_stopped_exists  slug=%s  "
            "CREATE ONLY — existing YAML was not modified",
            slug,
        )
    except DisProvisioningValidationError:
        _log.exception("dis_client_provisioning_validation_failed  slug=%s", slug)
    except Exception:
        _log.exception("dis_client_provisioning_failed  slug=%s", slug)
