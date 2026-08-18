"""Server-side client for reading trace detail back from self-hosted Phoenix.

CAS is the only caller — the browser never talks to Phoenix directly and never
receives these credentials. Used by generations.py's trace-detail endpoint:
given a trace_id already looked up via a CAS-owned resource the caller is
authorized for, fetch that trace's spans (prompt/response, model, usage) from
Phoenix's client API.
"""
from __future__ import annotations

from typing import Any, Dict, List

import httpx
from fastapi import HTTPException

from app.core.config import settings

# get_spans's own defaults (limit=100, no explicit timeout beyond its 5s
# DEFAULT_TIMEOUT_IN_SECONDS) silently truncate/time out with no signal to
# the caller — a real trace is normally one span, but this stays generous
# rather than assume that never changes.
_GET_SPANS_LIMIT = 500
_GET_SPANS_TIMEOUT_SECONDS = 15


def get_trace_observations(trace_id: str) -> List[Dict[str, Any]]:
    """Return every span for a trace, including full input/output."""
    from phoenix.client import Client

    try:
        client = Client(base_url=settings.phoenix_base_url, api_key=settings.phoenix_api_key or None)
        return client.spans.get_spans(
            project_identifier=settings.phoenix_project_name,
            trace_ids=[trace_id],
            limit=_GET_SPANS_LIMIT,
            timeout=_GET_SPANS_TIMEOUT_SECONDS,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, f"Phoenix error: {exc.response.text}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(503, f"Phoenix unavailable: {exc}") from exc
    except Exception as exc:
        raise HTTPException(503, f"Phoenix unavailable: {exc}") from exc
