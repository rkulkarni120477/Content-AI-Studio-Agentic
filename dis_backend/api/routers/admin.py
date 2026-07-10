from fastapi import APIRouter, Depends, HTTPException, Query, Request
from api.middleware.auth import get_current_tenant, require_role
from config.settings import get_tenant_config, get_tenant_registry
from models.schemas import TenantSummary

router = APIRouter(prefix="/admin", tags=["Admin"])


def _client_config_summary(tenant):
    """Return dashboard-safe config summary. No secrets/passwords are exposed."""
    client_id = tenant.effective_client_id("")
    client = tenant.get_client(client_id)
    if not client:
        raise HTTPException(404, f"Client/workspace config not found: {client_id}")
    return {
        "client_id": client.client_id,
        "client_name": client.display_name,
        "namespace": tenant.get_namespace(client_id),
        "is_active": tenant.is_active,
        "allowed_file_types": client.allowed_file_types,
        "max_file_size_mb": client.max_file_size_mb,
        "storage_provider": tenant.storage.provider,
        "storage_base_prefix": tenant.storage.base_prefix,
        "processing": tenant.processing.model_dump(),
        "llm": {
            "provider_configured": tenant.pipeline.llm_provider,
            "bedrock_enabled": tenant.pipeline.bedrock_enabled,
            "anthropic_enabled": tenant.pipeline.anthropic_enabled,
            "note": "llm_provider=mock uses rule-based extraction. llm_provider=bedrock can use configured Bedrock models for classification/metadata/structure/quality even when embedding.enabled=false.",
        },
        "structure_store_enabled": tenant.structure_store.enabled,
        "structure_store_provider": tenant.structure_store.provider,
        "embedding_enabled": tenant.embedding.enabled,
        "vector_store_enabled": tenant.vector_store.enabled,
        "vector_store_provider": tenant.vector_store.provider,
        "metadata_schema": tenant.get_metadata_schema(client_id).model_dump(),
    }


@router.get("/clients")
async def list_clients(request: Request, _=Depends(require_role("super_admin"))):
    """Super admin only: list all configured clients/workspaces."""
    return [
        TenantSummary(
            tenant_id=t.tenant_id,
            display_name=t.display_name,
            namespace=t.namespace,
            is_active=t.is_active,
            client_count=1,
            storage_provider=t.storage.provider,
        )
        for t in get_tenant_registry().all_tenants()
    ]


@router.get("/client-configs")
async def all_client_configs(request: Request, _=Depends(require_role("super_admin"))):
    """Super admin only: see dashboard-safe config summary for every configured client."""
    registry = get_tenant_registry()
    return {
        "total_clients": len(registry.all_tenants()),
        "clients": [_client_config_summary(t) for t in registry.all_tenants()],
    }


@router.get("/client-config")
async def client_config(
    request: Request,
    client_id: str = Query("", description="Super admin only. Optional client_id to view a specific client config summary."),
    _=Depends(require_role("client_admin")),
):
    """Client config summary.

    * client_admin: sees only own client config; no query parameter needed.
    * super_admin: can either omit client_id to see the client attached to current token, or pass client_id.

    This returns a safe dashboard summary only. It never returns secrets, passwords, AWS keys, or JWT secret.
    """
    role = getattr(request.state, "role", "user")

    if role == "super_admin" and client_id:
        try:
            tenant = get_tenant_config(client_id)
        except KeyError:
            raise HTTPException(404, f"Client '{client_id}' not found")
        except PermissionError as e:
            raise HTTPException(403, str(e))
        return _client_config_summary(tenant)

    if client_id and role != "super_admin":
        # Client admins must not use this endpoint to inspect another client.
        current_client_id = getattr(request.state, "client_id", "")
        if client_id != current_client_id:
            raise HTTPException(403, "Client admin can view only own client config")

    tenant = get_current_tenant(request)
    return _client_config_summary(tenant)



@router.post("/config/reload")
async def reload(_=Depends(require_role("super_admin"))):
    """Super admin only: reload platform.yaml and config/clients/*.yaml. Restart is still safer after major changes."""
    import config.settings as cs
    cs._registry = None
    registry = cs.get_tenant_registry()
    return {"status": "reloaded", "clients": len(registry.all_tenants())}


@router.get("/tenants")
async def list_tenants_legacy(request: Request, _=Depends(require_role("super_admin"))):
    """Backward-compatible alias for /admin/clients."""
    return await list_clients(request, _)
