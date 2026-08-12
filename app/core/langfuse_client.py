"""Server-side client for reading trace detail back from self-hosted Langfuse.

CAS is the only caller — the browser never talks to Langfuse directly and never
receives these credentials. Used by generations.py's trace-detail endpoint
(P1.4 of claude_plan_platform_hardening): given a Langfuse trace_id already
looked up via a CAS-owned resource the caller is authorized for, fetch that
trace's observations (prompt/response, model, usage) from Langfuse's public API.
"""
from __future__ import annotations

from typing import Any, Dict, List

import httpx
from fastapi import HTTPException

from app.core.config import settings


def _auth() -> tuple[str, str]:
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        raise HTTPException(503, "Langfuse is not configured on this server (LANGFUSE_PUBLIC_KEY/SECRET_KEY unset).")
    return (settings.langfuse_public_key, settings.langfuse_secret_key)


def get_trace_observations(trace_id: str) -> List[Dict[str, Any]]:
    """Return every observation for a trace, including full input/output.

    `fields=core,basic,io,usage,model` — without an explicit `fields` list the
    v2 endpoint omits input/output entirely (Langfuse v4's field-selection API).
    """
    url = f"{settings.langfuse_host.rstrip('/')}/api/public/v2/observations"
    params = {"traceId": trace_id, "fields": "core,basic,io,usage,model,metrics,trace_context"}
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, params=params, auth=_auth())
            resp.raise_for_status()
            return resp.json().get("data", [])
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"Langfuse error: {exc.response.text}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(503, f"Langfuse unavailable: {exc}") from exc
