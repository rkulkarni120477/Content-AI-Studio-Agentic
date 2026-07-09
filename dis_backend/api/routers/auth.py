from fastapi import APIRouter, HTTPException
from api.middleware.auth import create_access_token
from config.settings import get_settings, get_tenant_config, get_tenant_registry, get_platform_config
from models.schemas import TokenRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.post("/token", response_model=TokenResponse)
async def get_token(body: TokenRequest):
    """Issue a demo JWT using v9 simplified auth.

    Roles are not accepted from request body.

    * super_admin: configured in config/platform.yaml and can omit client_id.
    * client_admin/user: configured in config/clients/<client_id>.yaml and must pass client_id.
    """
    settings = get_settings()
    registry = get_tenant_registry()
    platform = get_platform_config()

    if body.secret not in (platform.demo_secret, "demo_secret", settings.jwt_secret):
        raise HTTPException(401, "Invalid credentials")

    # Platform-level super admin, not stored inside any client config.
    if platform.is_super_admin(body.user_id):
        selected_client_id = body.client_id or registry.first_active_client_id()
        try:
            client_cfg = get_tenant_config(selected_client_id)
        except (KeyError, PermissionError) as e:
            raise HTTPException(401, str(e))
        role = "super_admin"
        token = create_access_token(
            tenant_id=client_cfg.tenant_id,
            client_id=client_cfg.effective_client_id(""),
            user_id=body.user_id,
            role=role,
            settings=settings,
        )
        return TokenResponse(
            access_token=token,
            expires_in=settings.jwt_expiry_minutes * 60,
            tenant_id=client_cfg.tenant_id,
            client_id=client_cfg.effective_client_id(""),
            user_id=body.user_id,
            role=role,
        )

    if not body.client_id:
        raise HTTPException(400, "client_id is required for client_admin/user login")

    try:
        client_cfg = get_tenant_config(body.client_id)
    except (KeyError, PermissionError) as e:
        raise HTTPException(401, str(e))

    user_cfg = client_cfg.get_user(body.user_id)
    if not user_cfg:
        raise HTTPException(403, f"User '{body.user_id}' is not configured for client '{body.client_id}'")

    role = user_cfg.role
    if role not in ("client_admin", "user"):
        raise HTTPException(403, f"Unsupported client role '{role}'. Use client_admin or user.")

    token = create_access_token(
        tenant_id=client_cfg.tenant_id,
        client_id=client_cfg.effective_client_id(""),
        user_id=body.user_id,
        role=role,
        settings=settings,
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expiry_minutes * 60,
        tenant_id=client_cfg.tenant_id,
        client_id=client_cfg.effective_client_id(""),
        user_id=body.user_id,
        role=role,
    )
