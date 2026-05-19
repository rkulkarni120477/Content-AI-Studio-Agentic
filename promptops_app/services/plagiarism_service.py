"""Copyleaks API client for plagiarism and AI-content detection.

Scan flow
---------
1. ``_get_access_token()``  — login once, cache JWT until near-expiry
2. ``submit_scan()``        — POST base64-encoded content with a UUID scan_id
3. ``poll_until_done()``    — GET scan status with exponential back-off
4. ``fetch_results()``      — GET full result payload
5. ``parse_results()``      — extract similarity_score, ai_score, sources, highlights

Environment variables (never hardcoded)
----------------------------------------
COPYLEAKS_EMAIL     – account email
COPYLEAKS_API_KEY   – account API key
COPYLEAKS_PRODUCT   – "businesses" (default) or "education"
"""
from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from promptops_app.core.config import settings

_log = logging.getLogger(__name__)

# ── Copyleaks endpoint constants ──────────────────────────────────────────────
_AUTH_URL  = "https://id.copyleaks.com/v3/account/login/api"
_API_BASE  = "https://api.copyleaks.com"

# ── Polling parameters ────────────────────────────────────────────────────────
_POLL_INITIAL_WAIT = 20    # seconds before first poll
_POLL_MAX_WAIT     = 60    # max seconds between polls
_POLL_BACKOFF      = 1.5   # multiplier per retry
_POLL_TIMEOUT      = 600   # total seconds before giving up (~10 min)

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
    """UUID hex — unique identifier sent to Copyleaks and stored in the DB."""
    return uuid.uuid4().hex


def submit_scan(content: str, scan_id: str) -> None:
    """Encode and submit *content* for scanning under *scan_id*.

    Copyleaks accepts base-64 encoded plain-text files.
    The scan begins asynchronously on Copyleaks' side.
    """
    url  = f"{_API_BASE}/v3/{_product()}/submit/file/{scan_id}"
    b64  = base64.b64encode(content.encode("utf-8")).decode("ascii")

    payload = {
        "base64":   b64,
        "filename": f"content_{scan_id[:8]}.txt",
        "properties": {
            "scanning": {
                "copyleaks_db": True,
                "internet":     True,
                "ai_detection": True,
            },
        },
    }
    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=60)
    except requests.RequestException as exc:
        raise CopyleaksError(f"Submit request failed: {exc}") from exc

    if resp.status_code not in (200, 201):
        raise CopyleaksError(
            f"Submit failed (HTTP {resp.status_code}) for scan {scan_id}: {resp.text[:300]}"
        )
    _log.info("Copyleaks scan submitted: scan_id=%s", scan_id)


# ---------------------------------------------------------------------------
# Status polling
# ---------------------------------------------------------------------------

def _get_status(scan_id: str) -> Optional[str]:
    """Return the status string for *scan_id*, or None if not yet visible."""
    url = f"{_API_BASE}/v3/{_product()}/scans/status"
    try:
        resp = requests.post(
            url,
            json={"scansIds": [scan_id]},
            headers=_headers(),
            timeout=30,
        )
    except requests.RequestException as exc:
        _log.warning("Status request failed for %s: %s", scan_id, exc)
        return None

    if resp.status_code != 200:
        _log.warning("Status HTTP %d for %s: %s", resp.status_code, scan_id, resp.text[:200])
        return None

    data = resp.json()
    # Response is a list of {id, status} objects
    if isinstance(data, list):
        for item in data:
            if item.get("id") == scan_id:
                return str(item.get("status", ""))
    return None


def poll_until_done(scan_id: str) -> None:
    """Block (inside a Celery task) until the scan completes or times out.

    Uses exponential back-off so we don't hammer the API.
    Raises ``CopyleaksError`` on terminal failure or timeout.
    """
    wait      = _POLL_INITIAL_WAIT
    elapsed   = 0.0
    attempt   = 0

    while elapsed < _POLL_TIMEOUT:
        time.sleep(wait)
        elapsed += wait
        attempt += 1

        status = _get_status(scan_id)
        _log.debug(
            "Poll %d — scan_id=%s status=%r elapsed=%.0fs",
            attempt, scan_id, status, elapsed,
        )

        if status in ("Completed", "2", 2):
            _log.info("Scan %s completed after %.0fs (%d polls)", scan_id, elapsed, attempt)
            return

        if status in ("Error", "Deleted", "4", 4):
            raise CopyleaksError(f"Scan {scan_id} ended with terminal status '{status}'")

        # Back-off up to the cap
        wait = min(wait * _POLL_BACKOFF, _POLL_MAX_WAIT)

    raise CopyleaksError(
        f"Scan {scan_id} did not complete within {_POLL_TIMEOUT}s "
        f"({attempt} polls). Try again or check the Copyleaks dashboard."
    )


# ---------------------------------------------------------------------------
# Result retrieval
# ---------------------------------------------------------------------------

def fetch_results(scan_id: str) -> dict:
    """Fetch the full result payload for a completed scan."""
    url = f"{_API_BASE}/v3/{_product()}/scans/{scan_id}/result"
    try:
        resp = requests.get(url, headers=_headers(), timeout=30)
    except requests.RequestException as exc:
        raise CopyleaksError(f"Result fetch request failed: {exc}") from exc

    if resp.status_code != 200:
        raise CopyleaksError(
            f"Result fetch failed (HTTP {resp.status_code}) for {scan_id}: {resp.text[:300]}"
        )
    return resp.json()


# ---------------------------------------------------------------------------
# Result parsing
# ---------------------------------------------------------------------------

def parse_results(raw: dict) -> dict:
    """Extract structured fields from the raw Copyleaks result payload.

    Returns
    -------
    dict with keys:
        similarity_score  float 0-100
        ai_score          float 0-100 | None
        source_urls       list[{url, similarity, title}]
        highlights        list[{text, source_url}]   (top matches only)
    """
    results = raw.get("results", {})

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
        url_ = (
            result.get("url")
            or result.get("metadata", {}).get("url", "")
        )
        if url_:
            sources.append({
                "url":        url_,
                "similarity": round(float(
                    result.get("score", {}).get("percentSimilar", 0)
                ), 2),
                "title": result.get("metadata", {}).get("title", ""),
            })

    # ── Sentence-level highlights (top-5 sources, top-10 matches each) ───────
    highlights: list[dict] = []
    for result in results.get("internet", [])[:5]:
        src_url = result.get("url") or result.get("metadata", {}).get("url", "")
        for match in result.get("matches", [])[:10]:
            text_val = (
                match.get("text", {}).get("value")
                or match.get("comparison", {}).get("value")
                or ""
            )
            if text_val.strip():
                highlights.append({
                    "text":       text_val.strip(),
                    "source_url": src_url,
                })

    return {
        "similarity_score": round(similarity, 2),
        "ai_score":         ai_score,
        "source_urls":      sources[:20],
        "highlights":       highlights[:50],
    }


# ---------------------------------------------------------------------------
# High-level helper (used by the Celery task)
# ---------------------------------------------------------------------------

def run_full_scan(content: str, scan_id: str) -> dict:
    """Submit, wait, fetch, and parse in one call.

    Returns ``{"parsed": {...}, "raw": {...}}``.
    Raises ``CopyleaksError`` on any failure.
    """
    submit_scan(content, scan_id)
    poll_until_done(scan_id)
    raw    = fetch_results(scan_id)
    parsed = parse_results(raw)
    return {"parsed": parsed, "raw": raw}
