"""
DIS – Auth Middleware + Role System
Roles: super_admin > client_admin > user
"""
from __future__ import annotations
import logging, time
from typing import Optional
import jwt
from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, JSONResponse
from config.settings import GlobalSettings, TenantConfig, get_settings, get_tenant_config

log = logging.getLogger(__name__)

ROLE_LEVEL = {"super_admin": 3, "client_admin": 2, "user": 1}
SKIP_PATHS = {
    "/", "/health", "/docs", "/redoc", "/openapi.json",
    "/docs-super-admin", "/docs-client-admin", "/docs-user",
    "/openapi-super-admin.json", "/openapi-client-admin.json", "/openapi-user.json",
    "/v1/auth/token",
}


def decode_token(token: str, settings: GlobalSettings) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm], options={"verify_aud": False})
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def create_access_token(tenant_id: str, client_id: str, user_id: str,
                        role: str, settings: GlobalSettings) -> str:
    payload = {
        "sub": user_id, "tenant_id": tenant_id, "client_id": client_id,
        "user_id": user_id, "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.jwt_expiry_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


class TenantAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            return await self._dispatch_authenticated(request, call_next)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=getattr(exc, "headers", None))

    async def _dispatch_authenticated(self, request: Request, call_next) -> Response:
        if request.url.path in SKIP_PATHS:
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token",
                                headers={"WWW-Authenticate": "Bearer"})

        settings = get_settings()
        bearer_token = auth.split(" ", 1)[1]

        # Service-to-service mode: CAS backend can call DIS without exposing DIS
        # directly to the browser. CAS remains the authority for user auth/roles.
        if settings.dis_service_token and bearer_token == settings.dis_service_token:
            tenant_id = request.headers.get("X-CAS-Tenant-Id", "")
            client_id = request.headers.get("X-CAS-Client-Id", tenant_id)
            user_id = request.headers.get("X-CAS-User-Id", "cas-service")
            role_header = request.headers.get("X-CAS-Roles", "client_admin")
            roles = {r.strip() for r in role_header.split(",") if r.strip()}
            if "super_admin" in roles:
                role = "super_admin"
            elif any(r in {"admin", "client_admin", "content_admin", "instructional_designer", "reviewer"} for r in roles):
                role = "client_admin"
            else:
                role = "user"
        else:
            payload = decode_token(bearer_token, settings)
            tenant_id = payload.get("tenant_id", "")
            client_id = payload.get("client_id", "")
            user_id = payload.get("user_id", payload.get("sub", ""))
            role = payload.get("role", "user")

        if not tenant_id:
            raise HTTPException(status_code=401, detail="Token missing tenant_id or X-CAS-Tenant-Id header")

        try:
            tenant_cfg = get_tenant_config(tenant_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))

        # DIS backend uses JWT auth only. mTLS is handled outside the app if ever required.

        client_id = tenant_cfg.effective_client_id(client_id)
        if not tenant_cfg.get_client(client_id):
            raise HTTPException(status_code=403, detail=f"Client/workspace '{client_id}' is not configured")

        # Inject into request state
        request.state.tenant_id = tenant_id
        request.state.client_id = client_id
        request.state.user_id = user_id
        request.state.role = role
        request.state.tenant_config = tenant_cfg
        request.state.client_config = tenant_cfg.get_client(client_id)
        request.state.namespace = tenant_cfg.get_namespace(client_id)

        return await call_next(request)


def get_current_tenant(request: Request) -> TenantConfig:
    cfg = getattr(request.state, "tenant_config", None)
    if not cfg:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return cfg


def require_role(min_role: str):
    def _dep(request: Request):
        role = getattr(request.state, "role", "user")
        if ROLE_LEVEL.get(role, 0) < ROLE_LEVEL.get(min_role, 99):
            raise HTTPException(status_code=403,
                detail=f"Role '{min_role}' required. Your role: '{role}'")
    return _dep
