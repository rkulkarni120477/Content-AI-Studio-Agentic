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

    def check_or_raise(self, estimated_tokens: int, step: str = "") -> None:
        return None

    def record_usage(self, actual_tokens: int, step: str = "") -> None:
        actual_tokens = int(actual_tokens or 0)
        self.tokens_used += actual_tokens
        self.usage_events.append({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "step": step,
            "tokens": actual_tokens,
        })

    def get_status(self) -> Dict:
        return {
            "user_id": self.user_id,
            "tokens_used": self.tokens_used,
            "limit_enforced": False,
            "message": "DIS token quota is disabled; Studio owns generation/budget controls.",
        }
