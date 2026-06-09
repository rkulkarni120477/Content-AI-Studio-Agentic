"""Copyleaks API client for plagiarism and AI-content detection.

Scan flow (webhook-based)
--------------------------
1. ``_get_access_token()``  — login once, cache JWT until near-expiry
2. ``submit_scan()``        — PUT base64-encoded content; Copyleaks calls
                              COPYLEAKS_WEBHOOK_URL when done
3. Results are delivered to ``POST /api/plagiarism/webhook/{status}/{scan_id}``
   and stored there — no polling needed.

Environment variables (never hardcoded)
----------------------------------------
COPYLEAKS_EMAIL         – account email
COPYLEAKS_API_KEY       – account API key
COPYLEAKS_PRODUCT       – "businesses" (default) or "education"
COPYLEAKS_WEBHOOK_URL   – template URL sent to Copyleaks, e.g.
                          https://yourserver.com/api/plagiarism/webhook/{status}/{scanId}
"""
from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from promptops_app.core.config import settings

_log = logging.getLogger(__name__)

# ── Copyleaks endpoint constants ──────────────────────────────────────────────
_AUTH_URL  = "https://id.copyleaks.com/v3/account/login/api"
_API_BASE  = "https://api.copyleaks.com"

# ── Token cache (module-level, process-wide) ──────────────────────────────────
_token_cache: dict = {"token": None, "expires_at": None}


class CopyleaksError(Exception):
    """Raised for any Copyleaks API failure."""


class CopyleaksNotConfigured(CopyleaksError):
    """Raised when credentials are absent from environment."""


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def _get_access_token() -> str:
    """Return a valid Bearer token, refreshing the cache when near-expiry."""
    now = datetime.now(timezone.utc)
    if (
        _token_cache["token"]
        and _token_cache["expires_at"]
        and _token_cache["expires_at"] > now + timedelta(minutes=5)
    ):
        return _token_cache["token"]

    email   = settings.copyleaks_email
    api_key = settings.copyleaks_api_key_value
    if not email or not api_key:
        raise CopyleaksNotConfigured(
            "Copyleaks credentials not configured. "
            "Set COPYLEAKS_EMAIL and COPYLEAKS_API_KEY in .env"
        )

    _log.info("Refreshing Copyleaks access token for %s", email)
    try:
        resp = requests.post(
            _AUTH_URL,
            json={"email": email, "key": api_key},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise CopyleaksError(f"Copyleaks auth request failed: {exc}") from exc

    if resp.status_code != 200:
        raise CopyleaksError(
            f"Copyleaks auth failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )

    data  = resp.json()
    token = data.get("access_token") or data.get("token")
    if not token:
        raise CopyleaksError(f"No access_token in Copyleaks auth response: {data}")

    expires_in = int(data.get("expires_in", 3600))
    _token_cache["token"]      = token
    _token_cache["expires_at"] = now + timedelta(seconds=expires_in)
    _log.debug("Copyleaks token cached for %ds", expires_in)
    return token


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_access_token()}",
        "Content-Type":  "application/json",
    }


def _product() -> str:
    return settings.copyleaks_product or "businesses"


# ---------------------------------------------------------------------------
# Scan submission
# ---------------------------------------------------------------------------

def generate_scan_id() -> str:
    """Standard UUID — unique identifier sent to Copyleaks and stored in the DB."""
    return str(uuid.uuid4())


def submit_scan(content: str, scan_id: str) -> None:
    """Encode and submit *content* for scanning under *scan_id*.

    Uses PUT (Copyleaks changed from POST to PUT in their v3 API).
    Results are delivered asynchronously to COPYLEAKS_WEBHOOK_URL.
    """
    url      = f"{_API_BASE}/v3/{_product()}/submit/file/{scan_id}"
    # Limit to 200 words — free trial allows 250 words per credit.
    # Scanning a sample is enough to detect plagiarism patterns.
    words    = content.split()
    sample   = " ".join(words[:200]) if len(words) > 200 else content
    b64      = base64.b64encode(sample.encode("utf-8")).decode("ascii")
    wh_url   = settings.copyleaks_webhook_url

    if not wh_url:
        _log.warning(
            "COPYLEAKS_WEBHOOK_URL not set — Copyleaks results will not be "
            "delivered. Set it to https://yourserver.com/api/plagiarism/webhook/{status}/{scanId}"
        )
        wh_url = f"https://placeholder.invalid/webhook/{{status}}/{scan_id}"

    # Copyleaks only replaces {status} in the URL — {scanId} must be filled in
    # by us before submitting so each scan has its own unique callback URL.
    wh_url = wh_url.replace("{scanId}", scan_id)

    payload = {
        "base64":   b64,
        "filename": f"content_{scan_id[:8]}.txt",
        "properties": {
            "scanning": {
                "copyleaks_db": True,
                "internet":     True,
                "ai_detection": True,
            },
            "webhooks": {
                "status": wh_url,
            },
        },
    }
    try:
        resp = requests.put(url, json=payload, headers=_headers(), timeout=60)
    except requests.RequestException as exc:
        raise CopyleaksError(f"Submit request failed: {exc}") from exc

    if resp.status_code not in (200, 201):
        raise CopyleaksError(
            f"Submit failed (HTTP {resp.status_code}) for scan {scan_id}: {resp.text[:300]}"
        )
    _log.info("Copyleaks scan submitted: scan_id=%s (awaiting webhook)", scan_id)


# ---------------------------------------------------------------------------
# Result parsing  (called by the webhook handler in plagiarism_api.py)
# ---------------------------------------------------------------------------

def parse_results(raw: dict) -> dict:
    """Extract structured fields from the Copyleaks webhook payload.

    Returns
    -------
    dict with keys:
        similarity_score  float 0-100
        ai_score          float 0-100 | None
        source_urls       list[{url, similarity, title}]
        highlights        list[{text, source_url}]
    """
    results = raw.get("results", raw)   # webhook may send results at top level

    # ── Similarity ───────────────────────────────────────────────────────────
    similarity = float(
        results.get("score", {}).get("aggregatedScore", 0)
        or results.get("score", {}).get("identicalWords", 0)
        or 0
    )

    # ── AI score ─────────────────────────────────────────────────────────────
    ai_score: Optional[float] = None
    ai_data = results.get("aiDetection")
    if isinstance(ai_data, dict):
        raw_ai = (
            ai_data.get("aiScore")
            or ai_data.get("ai", {}).get("totalScore")
            or ai_data.get("probability")
        )
        if raw_ai is not None:
            ai_score = round(float(raw_ai), 2)

    # ── Source URLs ───────────────────────────────────────────────────────────
    sources: list[dict] = []
    for result in results.get("internet", []):
        url_ = result.get("url", "")
        if not url_:
            continue
        matched = float(result.get("matchedWords") or result.get("identicalWords") or 0)
        total   = float(result.get("totalWords") or 1)
        sim     = round((matched / total) * 100, 2)
        sources.append({
            "url":        url_,
            "similarity": sim,
            "title":      result.get("title", ""),
        })

    # ── Highlights — use introduction text each source provides ───────────────
    highlights: list[dict] = []
    for result in results.get("internet", [])[:5]:
        src_url = result.get("url", "")
        # Use introduction snippet if available
        intro = (result.get("introduction") or "").strip()
        if intro and src_url:
            highlights.append({
                "text":       intro[:300],
                "source_url": src_url,
            })
        # Also check nested matches array if present
        for match in result.get("matches", [])[:5]:
            text_val = (
                match.get("text", {}).get("value")
                or match.get("comparison", {}).get("value")
                or ""
            ).strip()
            if text_val and src_url:
                highlights.append({
                    "text":       text_val,
                    "source_url": src_url,
                })

    return {
        "similarity_score": round(similarity, 2),
        "ai_score":         ai_score,
        "source_urls":      sources[:20],
        "highlights":       highlights[:50],
    }
