"""Shared JSON-column coercion for SQLAlchemy Text fields stored as JSON strings."""

from __future__ import annotations

import json
from typing import Any, Optional


def parse_optional_json_dict(value: Any) -> Optional[dict]:
    """Coerce DB Text/JSON columns into dicts for Pydantic response models."""
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None
