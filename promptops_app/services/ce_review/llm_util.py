"""Shared LLM helpers for CE review passes: model-aware output cap + escalation."""
from __future__ import annotations

from typing import Optional


def output_cap(model_choice: str) -> int:
    """The model's own output ceiling (avoids the flat 16k default truncating)."""
    try:
        from promptops_app.core.models import resolve_model
        return resolve_model(model_choice).max_output_tokens
    except Exception:
        return 16384


def high_output_model(exclude: str = "") -> Optional[str]:
    """Catalog model with the largest output budget, excluding *exclude* — used to
    retry a pass the selected model truncated. None if the catalog can't be read."""
    try:
        from promptops_app.core.models import MODEL_CATALOG, resolve_model
        excluded = resolve_model(exclude).display_name if exclude else ""
        cands = [m for m in MODEL_CATALOG if m.display_name != excluded]
        if not cands:
            return None
        best = max(cands, key=lambda m: (m.max_output_tokens,
                                         "structured" in tuple(getattr(m, "tags", ()) or ())))
        return best.display_name
    except Exception:
        return None
