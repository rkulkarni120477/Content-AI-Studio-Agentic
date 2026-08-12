"""Token accounting for DIS ingestion.

Token limits were intentionally removed from DIS.
DIS prepares ingestion/context; budget enforcement can be handled by Content AI Studio.
This module remains as a no-op compatibility layer because some agents record LLM
usage for step artifacts/logging. It never blocks processing.
"""
from __future__ import annotations
from datetime import datetime
from typing import Dict, List


def estimate_tokens_for_file(pages: int = 1, has_images: bool = False) -> int:
    # Kept for backward compatibility only. No quota enforcement uses this value.
    return max(1, pages) * (1200 + (800 if has_images else 0))


class TokenLimitError(Exception):
    pass


class TokenGuard:
    """No-op token guard.

    check_or_raise() never raises. record_usage() only records local metrics for the
    current pipeline run. No user/client daily quota is enforced in DIS.
    """

    def __init__(self, tenant_cfg, user_id: str = ""):
        self.tenant_cfg = tenant_cfg
        self.user_id = user_id
        self.usage_events: List[Dict] = []
        self.tokens_used = 0
        # Input and output are priced differently (e.g. Sonnet $3 vs $15 per 1M), so
        # the combined total this class has always tracked cannot be costed. CAS owns
        # budget/attribution (see the module docstring), so it needs the split.
        self.tokens_in = 0
        self.tokens_out = 0
        self.models: Dict[str, int] = {}

    def check_or_raise(self, estimated_tokens: int, step: str = "") -> None:
        return None

    def record_usage(self, actual_tokens: int, step: str = "",
                     tokens_in: int = 0, tokens_out: int = 0, model: str = "") -> None:
        """Record one call's usage.

        ``tokens_in``/``tokens_out``/``model`` are optional so the historical
        single-total callers keep working unchanged; supply them where available so
        CAS can price the spend (input and output have different rates) and attribute
        it to the model that actually ran.
        """
        actual_tokens = int(actual_tokens or 0)
        self.tokens_used += actual_tokens
        self.tokens_in += int(tokens_in or 0)
        self.tokens_out += int(tokens_out or 0)
        if model:
            self.models[model] = self.models.get(model, 0) + 1
        self.usage_events.append({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "step": step,
            "tokens": actual_tokens,
            "tokens_in": int(tokens_in or 0),
            "tokens_out": int(tokens_out or 0),
            "model": model,
        })

    def usage_summary(self) -> Dict:
        """Per-run totals for CAS to record against a budget. ``model`` is the one
        that did the most calls — ingestion runs every text step on the same
        configured model, so a single label is accurate in practice, and the
        per-model counts are carried alongside for the case where it is not."""
        top = max(self.models.items(), key=lambda kv: kv[1])[0] if self.models else ""
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_used,
            "calls": len([e for e in self.usage_events if e.get("tokens")]),
            "model": top,
            "models": dict(self.models),
        }

    def get_status(self) -> Dict:
        return {
            "user_id": self.user_id,
            "tokens_used": self.tokens_used,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "limit_enforced": False,
            "message": "DIS token quota is disabled; Studio owns generation/budget controls.",
        }
