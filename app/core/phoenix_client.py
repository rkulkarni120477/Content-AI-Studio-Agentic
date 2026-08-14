"""Server-side client for reading trace detail back from self-hosted Phoenix.

CAS is the only caller — the browser never talks to Phoenix directly and never
receives these credentials. Used by generations.py's trace-detail endpoint:
given a trace_id already looked up via a CAS-owned resource the caller is
authorized for, fetch that trace's spans (prompt/response, model, usage) from
Phoenix's client API.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import HTTPException

from app.core.config import settings


def get_trace_observations(trace_id: str) -> List[Dict[str, Any]]:
    """Return every span for a trace, including full input/output."""
    from phoenix.client import Client

    try:
        client = Client(base_url=settings.phoenix_base_url, api_key=settings.phoenix_api_key or None)
        return client.spans.get_spans(
            project_identifier=settings.phoenix_project_name,
            trace_ids=[trace_id],
        )
    except Exception as exc:
        raise HTTPException(503, f"Phoenix unavailable: {exc}") from exc
