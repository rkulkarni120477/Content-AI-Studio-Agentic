"""Per-tenant UI label overrides — the shared contract for `projects.ui_labels`.

Display-only. The frontend substitutes these words into on-screen labels
("Select Title" → "Select Block"); no server code branches on them, and no
prompt sent to a model sees them. A missing or blank value always means
"use the default wording", which is what keeps the feature non-breaking.

Stored as a JSON object in a TEXT column so adding a fifth label later needs
no migration. Kept here (rather than inline in each schema) so the tenant API,
the project API, and the frontend all agree on the same four keys.
"""

from __future__ import annotations

import json
from typing import Any

# The renameable vocabulary. Must stay in sync with DEFAULT_LABELS in
# frontend/src/config/tenantLabels.js.
UI_LABEL_KEYS: tuple[str, ...] = ("title", "style", "cdd", "blueprint")

# Generous but bounded: long enough for "Title Design Document", short enough
# that a pasted essay can't wreck the sidebar layout.
MAX_LABEL_LENGTH = 40


def parse_ui_labels(value: Any) -> dict[str, str]:
    """Coerce a stored column value into a clean {key: word} dict.

    Accepts the JSON string from the DB, an already-decoded dict, or None.
    Anything unparseable degrades to `{}` (= use defaults) rather than raising,
    so a malformed row can never break a page render.
    """
    if value is None or value == "":
        return {}

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}

    if not isinstance(value, dict):
        return {}

    return sanitize_ui_labels(value)


def sanitize_ui_labels(labels: dict[str, Any]) -> dict[str, str]:
    """Keep only known keys with non-empty string values, trimmed and capped.

    Dropping blanks rather than storing "" means the frontend's "falsy → use
    default" fallback and the stored data agree: an omitted key and a cleared
    box are the same thing.
    """
    clean: dict[str, str] = {}
    for key in UI_LABEL_KEYS:
        raw = labels.get(key)
        if not isinstance(raw, str):
            continue
        word = raw.strip()[:MAX_LABEL_LENGTH].strip()
        if word:
            clean[key] = word
    return clean


def dump_ui_labels(labels: dict[str, Any] | None) -> str | None:
    """Serialize for storage. An empty result stores NULL, not "{}"."""
    clean = sanitize_ui_labels(labels or {})
    return json.dumps(clean, ensure_ascii=False) if clean else None
