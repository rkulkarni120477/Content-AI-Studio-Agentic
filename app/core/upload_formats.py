"""Which file types the Source Library accepts — policy, not code.

The list lives in ``config/upload_formats.json`` so changing it is an edit, not a
release. Both readers come through here: the upload endpoint enforces it, and
``GET /source-library/upload-policy`` serves the same object to the browser, so
the client-side filter and the server-side rule cannot drift apart.

Why any of this exists. A type the pipeline cannot read is not rejected somewhere
downstream — it is stored, listed as "Processed", and silently contributes
nothing to any generation. 110 of AIM's 121 .doc files were ingested that way and
went unnoticed for a month. A refusal at the door costs the user one re-save.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Used when the config file is missing or unreadable. Deliberately the same
#: shape as the file, and deliberately NOT permissive: if policy cannot be read,
#: refusing everything unusual is the safe direction — the failure this module
#: exists to prevent is accepting a file nothing can read.
_FALLBACK: dict[str, Any] = {
    "supported_extensions": ["pdf", "docx", "pptx", "xlsx", "csv", "txt", "md", "json"],
    "blocked_extensions": {},
}


def _config_path() -> Path:
    # app/core/upload_formats.py -> project root/config/upload_formats.json
    return Path(__file__).resolve().parents[2] / "config" / "upload_formats.json"


@lru_cache(maxsize=4)
def _load_cached(path: str, mtime: float) -> dict[str, Any]:
    """Keyed on mtime so an edit is picked up without a restart, while a hot
    request path still does not re-read and re-parse the file every time."""
    try:
        raw = json.loads(Path(path).read_text())
    except Exception as exc:  # noqa: BLE001 — policy must never break uploads outright
        log.warning("upload_formats: could not read %s (%s); using the built-in fallback",
                    path, exc)
        return dict(_FALLBACK)
    return {
        "supported_extensions": [str(e).strip().lower().lstrip(".")
                                 for e in raw.get("supported_extensions") or []],
        "blocked_extensions": {str(k).strip().lower().lstrip("."): str(v)
                               for k, v in (raw.get("blocked_extensions") or {}).items()},
    }


def load_upload_formats() -> dict[str, Any]:
    """The current policy: ``{supported_extensions: [...], blocked_extensions: {ext: reason}}``."""
    path = _config_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        log.warning("upload_formats: %s is missing; using the built-in fallback", path)
        return dict(_FALLBACK)
    return _load_cached(str(path), mtime)


def extension_of(filename: str) -> str:
    name = (filename or "").strip().lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def unsupported_reasons(filenames: list[str]) -> list[str]:
    """One human-readable reason per file this pipeline will not accept.

    Empty list means every file is acceptable. A blocked type gets its configured
    message — which should say what to do next — and anything simply absent from
    the supported list gets a generic refusal naming what IS accepted, so the user
    is never left guessing.
    """
    policy = load_upload_formats()
    supported = set(policy["supported_extensions"])
    blocked = policy["blocked_extensions"]

    reasons: list[str] = []
    for name in filenames:
        ext = extension_of(name)
        if ext in blocked:
            reasons.append(f"{name}: {blocked[ext]}")
        elif ext not in supported:
            accepted = ", ".join("." + e for e in sorted(supported))
            reasons.append(
                f"{name}: .{ext or 'unknown'} is not a supported document type. "
                f"Supported: {accepted}."
            )
    return reasons
