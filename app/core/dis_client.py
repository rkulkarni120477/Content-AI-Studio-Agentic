"""Client for DIS backend integration.

CAS remains the only browser-facing API. This client calls DIS with a service
bearer token and forwards the resolved CAS user/tenant/client context in headers.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict

import httpx
from fastapi import HTTPException, UploadFile

from app.core.config import settings
from app.core.dis_access import DISAccessContext, get_dis_access_for_user

_log = logging.getLogger(__name__)


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

    def retrieve_context_sync(self, purpose: str, payload: Dict[str, Any], current_user: Any = None,
                              client_id: str = "", timeout: float | None = None) -> Dict[str, Any]:
        """``timeout`` overrides the client default (dis_api_timeout_seconds, 300s).

        300s is right for a generation, which legitimately runs for minutes. It is
        wrong for a caller enriching an interactive request, where the lookup is
        supplementary and a slow DIS should cost a degraded answer rather than a
        five-minute hang — measured: a CDD regeneration sat in flight for over
        four minutes with the user seeing nothing at all.
        """
        extra: Dict[str, Any] = {"timeout": timeout} if timeout is not None else {}
        return self.request_sync("POST", f"/context/retrieve/{purpose}", json=payload,
                                 current_user=current_user, client_id=client_id, **extra)

    # ── Digest pipeline (block-wide CDD/Blueprint) ───────────────────────────
    def enumerate_block_sync(self, block: str, current_user: Any = None, client_id: str = "",
                             include_units: bool = False) -> Dict[str, Any]:
        """ENUMERATE a block: deterministic day/unit inventory + attribution +
        declared-ACS + coverage flags. Read-only."""
        return self.request_sync("POST", "/context/enumerate",
                                 json={"block": block, "include_units": include_units},
                                 current_user=current_user, client_id=client_id)

    def build_digests_sync(self, block: str, force: bool = False, current_user: Any = None,
                           client_id: str = "", map_guidance: str = "") -> Dict[str, Any]:
        """Build (or refresh, lazily + cached) the per-day digest tier for a block.
        Returns the build report (built/cached/failed, flags, attribution).

        Starts the build and polls for it, rather than holding one HTTP request open
        for its whole duration.

        The old shape put a 5-20 minute build inside a single request, which made that
        connection a single point of failure for work that costs real money. It failed
        in production twice on 2026-08-13: once at 21m52s and once at 8m07s — and the
        second time **all 20 days had already been built successfully**. The report was
        discarded and the user saw "generation failed" for a build that had completed.
        No timeout value fixes that; the request simply must not be load-bearing.

        So: POST returns as soon as the build is running, and progress/result are read
        from the registry (see dis_backend/services/digests/progress.py). A dropped
        connection now costs one poll interval instead of the entire build. The
        registry is in-process on a single-process DIS, so a restart mid-build is
        handled explicitly below rather than silently reported as success.

        ``map_guidance`` (optional) is judgment/emphasis guidance distilled from the
        course's selected CDD/Blueprint prompt (see
        promptops_app.services.prompt_guidance.resolve_prompt_guidance) — forwarded to
        every day's MAP call and folded into the cache key on the DIS side. "" (the
        default) reproduces this call's exact pre-existing behavior.
        """
        deadline_s = float(_get_setting("dis_digest_build_deadline_seconds", 2400))
        interval_s = float(_get_setting("dis_digest_build_poll_seconds", 5))
        # Retried because starting is now idempotent and cheap. DIS holds a
        # single-flight reservation per block, so a start whose response was lost
        # answers "already_running" the second time rather than launching a duplicate.
        # Without this, one dropped packet on a millisecond-long call fails a
        # generation before any work begins.
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                reply = self._start_digest_build(block, force, current_user, client_id,
                                                 map_guidance)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                _log.warning("starting the digest build for %s failed (attempt %s/3): %s",
                             block, attempt + 1, exc)
                if attempt < 2:
                    time.sleep(min(interval_s, 5.0))
        else:
            raise HTTPException(
                503, f"Could not start the digest build for {block}: {last_exc}")
        # A DIS that predates the async build ran it inline and returned the report.
        # Recognised so a mixed-version window during a rolling rebuild degrades to the
        # old behavior instead of failing outright.
        if "started" not in reply and "already_running" not in reply:
            return reply
        return self._await_digest_build(block, current_user, client_id,
                                        deadline_s=deadline_s, interval_s=interval_s,
                                        force=force, map_guidance=map_guidance)

    def _start_digest_build(self, block: str, force: bool, current_user: Any,
                            client_id: str, map_guidance: str) -> Dict[str, Any]:
        """Ask DIS to begin a build. Returns as soon as it is running, not when done.

        Idempotent per block: DIS reserves a single-flight slot, so calling this while a
        build is live answers ``already_running`` instead of starting a second one. That
        is what makes the retry above safe.

        Caveat worth knowing: because the reservation is keyed on the block alone, a
        caller that joins an in-flight build inherits *that* build's ``force`` and
        ``map_guidance``, not its own. Two users generating the same block from
        different prompts at the same moment is the only way to reach it, and waiting
        for the running build beats refusing to build at all — but the second user's
        guidance is not what produced the digests they get.
        """
        reply = self.request_sync(
            "POST", "/context/digests/build",
            json={"block": block, "force": force, "map_guidance": map_guidance,
                  "wait": False},
            current_user=current_user, client_id=client_id,
            # Generous only by the standards of a call that no longer waits for the
            # build: DIS reserves its slot and spawns a thread, so this returns in
            # milliseconds. The headroom is for a loaded event loop, not for work.
            timeout=min(self.timeout, 60.0),
        )
        return reply if isinstance(reply, dict) else {}

    def _await_digest_build(self, block: str, current_user: Any, client_id: str, *,
                            deadline_s: float, interval_s: float,
                            force: bool, map_guidance: str) -> Dict[str, Any]:
        """Poll until the build reaches a terminal state, then return its report.

        Three conditions have to be distinguished, and conflating any two of them is
        how a build silently turns into a wrong answer:

        * **terminal** — ``state`` is ``done`` (return the report) or ``failed``
          (raise, carrying DIS's own reason).
        * **transient** — the poll itself errored. DIS is momentarily busy or the
          network blipped; the build is unaffected. Keep polling.
        * **vanished** — DIS answers, but has no entry for a block we watched it start.
          Only a restart does that. Re-issue the build: per-day digests are cached, so
          it resumes rather than starting over. Bounded, because a build that cannot
          survive being started twice will not survive a third time either.
        """
        deadline = time.monotonic() + max(deadline_s, interval_s)
        seen_entry = False
        restarts = 0
        consecutive_errors = 0
        missing_streak = 0
        last_error = ""
        first = True
        while time.monotonic() < deadline:
            # Poll before sleeping. DIS reserves its registry slot synchronously, before
            # the POST returns, so an entry is already there — and the common case by
            # far is a retry whose days are all cached, which finishes in well under a
            # second. Sleeping first would add a fixed poll interval to every one of
            # those for no reason.
            if not first:
                time.sleep(interval_s)
            first = False
            try:
                snap = (self.get_digest_progress_sync(
                    block, current_user=current_user, client_id=client_id,
                    include_result=True, timeout=30.0,
                ) or {}).get("progress")
                consecutive_errors = 0
            except Exception as exc:  # noqa: BLE001 - any poll failure is transient here
                consecutive_errors += 1
                last_error = f"{type(exc).__name__}: {exc}"
                # ~2 minutes of continuous unreachability is an outage, not a blip, and
                # waiting out a 40-minute deadline against a dead DIS helps nobody.
                if consecutive_errors * interval_s >= 120:
                    raise HTTPException(
                        503, f"DIS unreachable while building digests for {block}: {last_error}")
                continue

            if snap is None:
                missing_streak += 1
                # A short grace period only: DIS reserves its slot before returning, so
                # a missing entry should be impossible. Bounded anyway, because the
                # alternative is waiting out the whole deadline on a build that no
                # process is running.
                if not seen_entry and missing_streak * interval_s < 30:
                    continue
                if restarts >= 2:
                    raise HTTPException(
                        503, f"DIS lost the digest build for {block} repeatedly "
                             f"(restarted mid-build). Completed days are cached; retry.")
                restarts += 1
                missing_streak = 0
                _log.warning("digest build for %s is not tracked by DIS (restart?) — "
                             "re-issuing, attempt %s", block, restarts + 1)
                try:
                    self._start_digest_build(block, force, current_user, client_id,
                                             map_guidance)
                except Exception as exc:  # noqa: BLE001
                    # A DIS that just restarted may not be accepting requests yet. That
                    # is the same transient condition as a failed poll, and letting it
                    # escape here would abandon a build over a blip during recovery —
                    # the precise class of bug this rewrite exists to remove. The next
                    # pass retries, and `restarts` still bounds the attempts.
                    consecutive_errors += 1
                    last_error = f"{type(exc).__name__}: {exc}"
                    _log.warning("re-issuing the digest build for %s failed: %s",
                                 block, last_error)
                continue

            seen_entry = True
            missing_streak = 0
            state = snap.get("state")
            if state == "failed":
                raise HTTPException(
                    502, f"DIS digest build for {block} failed: "
                         f"{snap.get('error') or 'no reason reported'}")
            if state == "done":
                report = snap.get("report")
                if report is None:
                    # Terminal with no report should be impossible (complete() sets both
                    # together). Treat it as a failure rather than returning None and
                    # letting the caller build a deliverable out of nothing.
                    raise HTTPException(
                        502, f"DIS reported the digest build for {block} complete but "
                             f"returned no report")
                return report

        raise HTTPException(
            504, f"Digest build for {block} did not finish within "
                 f"{int(deadline_s / 60)} minutes. Completed days are cached, so a "
                 f"retry resumes where this left off.")

    def get_digest_progress_sync(self, block: str, current_user: Any = None,
                                 client_id: str = "", include_result: bool = False,
                                 timeout: float = 10.0) -> Dict[str, Any]:
        """State and per-day progress of a digest build for *block*.

        Two callers, deliberately different: the UI polls this every 2 seconds for the
        counter and must fail fast (10s default) rather than hang a progress request
        behind a slow DIS; the build poller passes ``include_result=True`` and a longer
        timeout, because for it this is the delivery channel for the report.
        """
        params: Dict[str, Any] = {"block": block}
        if include_result:
            params["include_result"] = "true"
        return self.request_sync("GET", "/context/digests/progress",
                                 params=params,
                                 current_user=current_user, client_id=client_id,
                                 timeout=timeout)

    def get_digests_bundle_sync(self, block: str, current_user: Any = None,
                                client_id: str = "") -> Dict[str, Any]:
        """Fetch the REDUCE bundle for a block: {enumerate, digests}. Read-only —
        call build_digests_sync first to ensure freshness."""
        return self.request_sync("GET", "/context/digests",
                                 params={"block": block},
                                 current_user=current_user, client_id=client_id)

    def get_day_context_sync(self, block: str, day: int, audience: str = "instructor",
                             supplement_k: int | None = None, current_user: Any = None,
                             client_id: str = "", timeout: float | None = None) -> Dict[str, Any]:
        """Structured-first day-scoped context (§7) for DLU / Learn It / Today's
        Mission: every unit on ``block+day`` + the day's digest + a bounded kNN
        supplement. Pass ``audience='student'`` to withhold instructor-only text."""
        payload: Dict[str, Any] = {"block": block, "day": int(day), "audience": audience}
        if supplement_k is not None:
            payload["supplement_k"] = supplement_k
        # See retrieve_context_sync for why an interactive caller passes its own.
        extra: Dict[str, Any] = {"timeout": timeout} if timeout is not None else {}
        return self.request_sync("POST", "/context/retrieve/day", json=payload,
                                 current_user=current_user, client_id=client_id, **extra)

    def documents_library_sync(self, params: Dict[str, Any], current_user: Any = None, client_id: str = "") -> Dict[str, Any]:
        return self.request_sync("GET", "/context/documents/library", params=params, current_user=current_user, client_id=client_id)

    def resolved_tenant_id(self, current_user: Any = None, client_id: str = "") -> str:
        """The tenant a request with these arguments will actually reach.

        DIS derives the tenant from the caller's identity (the X-CAS-Tenant-Id
        header built in :meth:`_headers`), NOT from ``client_id`` — so a caller
        that caches anything tenant-scoped must key it on this, and a caller
        reading tenant config must check the answer came from the tenant it meant.
        Returns "" when it cannot be resolved, which callers should treat as
        "unknown", never as a default.
        """
        try:
            return str(self._resolve_access(current_user, client_id).tenant_id or "")
        except Exception:  # noqa: BLE001
            return ""

    def ui_config_sync(self, current_user: Any = None, client_id: str = "",
                       timeout: float | None = None) -> Dict[str, Any]:
        """Raw DIS source-library config, for sync server-side callers.

        Deliberately does NOT add the CAS-side `access` block that :meth:`ui_config`
        attaches for the frontend — this exists so a generation path can read the
        tenant's own retrieval config (which document types a purpose admits)
        instead of restating it in CAS, where the two would drift.
        """
        extra: Dict[str, Any] = {"timeout": timeout} if timeout is not None else {}
        return self.request_sync("GET", "/context/ui-config",
                                 current_user=current_user, client_id=client_id, **extra)

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
