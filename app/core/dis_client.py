"""Client for DIS backend integration.

CAS remains the only browser-facing API. This client calls DIS with a service
bearer token and forwards the resolved CAS user/tenant/client context in headers.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

import httpx
from fastapi import HTTPException, UploadFile

from app.core.config import settings
from app.core.dis_access import DISAccessContext, get_dis_access_for_user


def _get_setting(name: str, default: Any = None) -> Any:
    return getattr(settings, name, default)


class DISClient:
    def __init__(self) -> None:
        self.enabled = bool(_get_setting("dis_enabled", True))
        self.base_url = str(_get_setting("dis_api_base_url", "http://127.0.0.1:8010/v1")).rstrip("/")
        self.timeout = float(_get_setting("dis_api_timeout_seconds", 300))
        # Service token is read at request time so .env / process env changes are picked up after restart.
        # Reused httpx clients (P4.3/F7) — one connection pool instead of a new
        # client (and TCP/TLS handshake) per call. The async client is bound to
        # the event loop it was created on, so it is cached per-loop and rebuilt
        # if the loop changes/closes (the test suite runs many short-lived loops;
        # production has a single long-lived loop → exactly one reused client).
        self._async_client: httpx.AsyncClient | None = None
        self._async_loop: Any = None
        self._sync_client: httpx.Client | None = None

    def _get_async_client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        client = self._async_client
        if client is None or client.is_closed or self._async_loop is not loop:
            client = httpx.AsyncClient(timeout=self.timeout)
            self._async_client = client
            self._async_loop = loop
        return client

    def _get_sync_client(self) -> httpx.Client:
        client = self._sync_client
        if client is None or client.is_closed:
            client = httpx.Client(timeout=self.timeout)
            self._sync_client = client
        return client

    async def aclose(self) -> None:
        """Release the reused connection pools — called from the app lifespan."""
        if self._async_client is not None and not self._async_client.is_closed:
            await self._async_client.aclose()
        self._async_client = None
        self._async_loop = None
        if self._sync_client is not None and not self._sync_client.is_closed:
            self._sync_client.close()
        self._sync_client = None

    def _resolve_access(self, current_user: Any = None, client_id: str = "") -> DISAccessContext:
        return get_dis_access_for_user(current_user, requested_client_id=client_id or None)

    def _headers(self, current_user: Any = None, client_id: str = "") -> Dict[str, str]:
        service_token = str(_get_setting("dis_service_token", "dev-dis-token") or "dev-dis-token")
        if not service_token:
            raise HTTPException(503, "DIS_SERVICE_TOKEN is not configured in CAS backend")
        access = self._resolve_access(current_user, client_id)
        user_id = str(getattr(current_user, "id", "") or getattr(current_user, "username", "") or "cas-user")
        username = str(getattr(current_user, "username", "") or "")
        email = str(getattr(current_user, "email", "") or username)
        cas_role = str(getattr(current_user, "role", "user") or "user")
        # Include both CAS role and DIS role. DIS middleware treats super_admin
        # specially and client_admin/user normally.
        roles = ",".join([cas_role, access.dis_role])
        return {
            "Authorization": f"Bearer {service_token}",
            "X-CAS-Tenant-Id": access.tenant_id,
            "X-CAS-Client-Id": access.client_id,
            "X-CAS-User-Id": username or user_id,
            "X-CAS-User-Email": email,
            "X-CAS-Roles": roles,
        }

    async def request(self, method: str, path: str, *, current_user: Any = None, client_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "documents": [], "sources": [], "combined_context": "", "source_units": []}
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            client = self._get_async_client()
            response = await client.request(method, url, headers=self._headers(current_user, client_id), **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise HTTPException(exc.response.status_code, f"DIS error: {detail}") from exc
        except httpx.RequestError as exc:
            detail = str(exc) or f"Cannot reach DIS backend at {self.base_url}. Start DIS with: uvicorn main:app --port 8010 --reload"
            raise HTTPException(503, f"DIS unavailable: {detail}") from exc


    def request_sync(self, method: str, path: str, *, current_user: Any = None, client_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        """Synchronous DIS request helper for existing sync FastAPI routers.

        CAS has several mature synchronous generation endpoints. This helper
        lets them inject DIS context without converting the whole generation
        stack to async. Browser/frontend still never calls DIS directly.
        """
        if not self.enabled:
            return {"enabled": False, "documents": [], "sources": [], "combined_context": "", "source_units": []}
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            client = self._get_sync_client()
            response = client.request(method, url, headers=self._headers(current_user, client_id), **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise HTTPException(exc.response.status_code, f"DIS error: {detail}") from exc
        except httpx.RequestError as exc:
            detail = str(exc) or f"Cannot reach DIS backend at {self.base_url}. Start DIS with: uvicorn main:app --port 8010 --reload"
            raise HTTPException(503, f"DIS unavailable: {detail}") from exc

    def retrieve_context_sync(self, purpose: str, payload: Dict[str, Any], current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return self.request_sync("POST", f"/context/retrieve/{purpose}", json=payload, current_user=current_user, client_id=client_id)

    def documents_library_sync(self, params: Dict[str, Any], current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return self.request_sync("GET", "/context/documents/library", params=params, current_user=current_user, client_id=client_id)

    async def ui_config(self, current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        data = await self.request("GET", "/context/ui-config", current_user=current_user, client_id=client_id)
        # Add CAS-side access info for the frontend. DIS should not need to know
        # which options a super admin may switch to; CAS owns that UX decision.
        access = self._resolve_access(current_user, client_id)
        data.setdefault("access", {})
        data["access"].update({
            "client_id": access.client_id,
            "tenant_id": access.tenant_id,
            "dis_role": access.dis_role,
            "is_dis_super_admin": access.is_super_admin,
            "available_clients": access.available_clients,
        })
        return data

    async def documents_library(self, params: Dict[str, Any], current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return await self.request("GET", "/context/documents/library", params=params, current_user=current_user, client_id=client_id)

    async def source_structure(self, job_id: str, current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return await self.request("GET", f"/context/sources/{job_id}/structure", current_user=current_user, client_id=client_id)

    async def delete_source(self, job_id: str, current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return await self.request("DELETE", f"/context/sources/{job_id}", current_user=current_user, client_id=client_id)

    async def retrieve_context(self, purpose: str, payload: Dict[str, Any], current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return await self.request("POST", f"/context/retrieve/{purpose}", json=payload, current_user=current_user, client_id=client_id)

    async def upload_document(self, *, file: UploadFile, form_fields: Dict[str, Any], current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        raw = await file.read()
        files = {"file": (file.filename, raw, file.content_type or "application/octet-stream")}
        data = {k: str(v) for k, v in form_fields.items() if v is not None}
        return await self.request("POST", "/ingest/upload", files=files, data=data, current_user=current_user, client_id=client_id)

    async def upload_documents(self, *, files: list[UploadFile], form_fields: Dict[str, Any], current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        results = []
        errors = []
        for file in files:
            try:
                results.append(await self.upload_document(file=file, form_fields=form_fields, current_user=current_user, client_id=client_id))
            except HTTPException as exc:
                errors.append({"filename": file.filename, "status_code": exc.status_code, "detail": exc.detail})
        return {"ok": not errors, "uploaded": len(results), "failed": len(errors), "results": results, "errors": errors}


    def generated_upsert_sync(self, payload: Dict[str, Any], current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return self.request_sync("POST", "/generated-documents/upsert", json=payload, current_user=current_user, client_id=client_id)

    def generated_list_sync(self, params: Dict[str, Any], current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return self.request_sync("GET", "/generated-documents/list", params=params, current_user=current_user, client_id=client_id)

    async def generated_list(self, params: Dict[str, Any], current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return await self.request("GET", "/generated-documents/list", params=params, current_user=current_user, client_id=client_id)

    async def generated_get(self, generated_doc_id: str, current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return await self.request("GET", f"/generated-documents/{generated_doc_id}", current_user=current_user, client_id=client_id)

    async def generated_activate(self, generated_doc_id: str, current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return await self.request("POST", f"/generated-documents/{generated_doc_id}/activate", current_user=current_user, client_id=client_id)

    async def generated_delete(self, generated_doc_id: str, current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return await self.request("DELETE", f"/generated-documents/{generated_doc_id}", current_user=current_user, client_id=client_id)

    async def folder_scan(self, *, payload: Dict[str, Any], current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return await self.request("POST", "/ingest/folder-scan", json=payload, current_user=current_user, client_id=client_id)


dis_client = DISClient()

# Large source view helpers attached after class definition for backwards-safe patching.
async def _source_overview(self, job_id: str, current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
    return await self.request("GET", f"/context/sources/{job_id}/overview", current_user=current_user, client_id=client_id)

async def _source_pages(self, job_id: str, params: Dict[str, Any] | None = None, current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
    return await self.request("GET", f"/context/sources/{job_id}/content/pages", params=params or {}, current_user=current_user, client_id=client_id)

async def _source_units(self, job_id: str, current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
    return await self.request("GET", f"/context/sources/{job_id}/content/units", current_user=current_user, client_id=client_id)

async def _source_unit_detail(self, job_id: str, unit_id: str, current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
    return await self.request("GET", f"/context/sources/{job_id}/content/units/{unit_id}", current_user=current_user, client_id=client_id)

async def _source_search(self, job_id: str, params: Dict[str, Any] | None = None, current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
    return await self.request("GET", f"/context/sources/{job_id}/search", params=params or {}, current_user=current_user, client_id=client_id)

DISClient.source_overview = _source_overview
DISClient.source_pages = _source_pages
DISClient.source_units = _source_units
DISClient.source_unit_detail = _source_unit_detail
DISClient.source_search = _source_search
