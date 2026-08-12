"""LLM usage logging and cost estimation service.

Responsibilities
----------------
* Persist one LLMUsageLog row per LLM call (token counts, cost, latency, scope).
* Estimate USD cost from token counts using configurable MODEL_PRICING.
* Provide UsageLogContext — a lightweight carrier for call-site metadata.

Usage
-----
Direct (caller has a DB session — generation jobs, style service):
    from promptops_app.services.usage_service import log_llm_usage, UsageLogContext
    ctx = UsageLogContext(user_name="alice", project_id=1, entity_type="generation")
    log_llm_usage(db, llm_result, ctx)

Via llm_service auto-logging (pages — no DB session at call site):
    generate_text(model_choice, sys, usr, usage_ctx=UsageLogContext(...))

Pricing
-------
MODEL_PRICING holds USD per 1 million tokens for each provider/model.
Override at runtime via PROMPTOPS_MODEL_PRICING env var (JSON string).
Example:
    PROMPTOPS_MODEL_PRICING='{"gpt-4o":{"input":5.00,"output":15.00}}'
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from promptops_app.core.config import settings as _cfg

if TYPE_CHECKING:
    from promptops_app.services.llm_service import LLMResult

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model pricing: USD per 1 million tokens
# ---------------------------------------------------------------------------

MODEL_PRICING: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-4o":          {"input": 5.00,   "output": 15.00},
    "gpt-4o-mini":     {"input": 0.15,   "output":  0.60},
    "gpt-4-turbo":     {"input": 10.00,  "output": 30.00},
    "gpt-4":           {"input": 30.00,  "output": 60.00},
    "gpt-3.5-turbo":   {"input":  0.50,  "output":  1.50},
    # Anthropic Claude (via AWS Bedrock)
    "claude-opus":     {"input": 15.00,  "output": 75.00},
    "claude-sonnet":   {"input":  3.00,  "output": 15.00},
    "claude-haiku":    {"input":  0.80,  "output":  4.00},
    # Fallback for unknown models
    "_default":        {"input":  5.00,  "output": 15.00},
}

# Allow runtime price overrides via PROMPTOPS_MODEL_PRICING (JSON string).
if _cfg.model_pricing_override:
    try:
        MODEL_PRICING.update(json.loads(_cfg.model_pricing_override))
    except Exception:
        pass


def _find_pricing(model_name: str) -> dict[str, float]:
    """Return pricing dict for *model_name* using substring matching."""
    lc = (model_name or "").lower()
    if "gpt-4o-mini"  in lc: return MODEL_PRICING["gpt-4o-mini"]
    if "gpt-4o"       in lc: return MODEL_PRICING["gpt-4o"]
    if "gpt-4-turbo"  in lc: return MODEL_PRICING["gpt-4-turbo"]
    if "gpt-4"        in lc: return MODEL_PRICING["gpt-4"]
    if "gpt-3.5"      in lc: return MODEL_PRICING["gpt-3.5-turbo"]
    if "claude-opus"  in lc: return MODEL_PRICING["claude-opus"]
    if "claude-haiku" in lc: return MODEL_PRICING["claude-haiku"]
    if "claude"       in lc or "sonnet" in lc: return MODEL_PRICING["claude-sonnet"]
    return MODEL_PRICING["_default"]


def estimate_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated USD cost from token counts. Returns 0.0 if tokens are unknown."""
    if not (input_tokens or output_tokens):
        return 0.0
    pricing = _find_pricing(model_name)
    return round(
        (input_tokens  or 0) / 1_000_000 * pricing["input"] +
        (output_tokens or 0) / 1_000_000 * pricing["output"],
        6,
    )


# ---------------------------------------------------------------------------
# Usage log context — metadata the call site provides
# ---------------------------------------------------------------------------

@dataclass
class UsageLogContext:
    """Metadata attached to each LLM call for traceability and cost attribution."""
    user_name: str = ""
    project_id: Optional[int] = None
    course_id: Optional[int] = None
    entity_type: str = ""          # generation | cdd | blueprint | evaluation | style | regen
    entity_id: Optional[str] = None
    prompt_template: str = ""
    prompt_version: str = ""


# ---------------------------------------------------------------------------
# Logging functions
# ---------------------------------------------------------------------------

def log_llm_usage(db, result: "LLMResult", ctx: UsageLogContext) -> None:
    """Write one LLMUsageLog row using *db*. Silently absorbs all errors.

    Pass the caller's existing SQLAlchemy session so the write shares
    the same connection pool slot.
    """
    try:
        from promptops_app.database import LLMUsageLog

        in_tok  = result.prompt_tokens or 0
        out_tok = result.completion_tokens or 0
        total   = (in_tok + out_tok) or None
        cost    = estimate_cost(result.model, in_tok, out_tok)
        dur_ms  = int(result.total_duration_s * 1000) if result.total_duration_s else None

        row = LLMUsageLog(
            user_id         = ctx.user_name or None,
            project_id      = ctx.project_id,
            course_id       = ctx.course_id,
            entity_type     = ctx.entity_type or None,
            entity_id       = str(ctx.entity_id) if ctx.entity_id is not None else None,
            prompt_template = ctx.prompt_template or None,
            prompt_version  = ctx.prompt_version or None,
            model_name      = result.model or "unknown",
            input_tokens    = result.prompt_tokens,
            output_tokens   = result.completion_tokens,
            total_tokens    = total,
            estimated_cost  = cost if total else None,
            duration_ms     = dur_ms,
            status          = result.status,
            error_message   = result.text if result.is_error else None,
            langfuse_trace_id = getattr(result, "langfuse_trace_id", None),
            created_at      = datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
    except Exception as exc:
        _log.warning("Failed to write LLM usage log: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


def log_llm_usage_autocommit(result: "LLMResult", ctx: UsageLogContext) -> None:
    """Log LLM usage using a freshly created session (for callers without DB access).

    Never raises. Used by llm_service.generate_with_metadata() when the caller
    provides a UsageLogContext but does not hold an open DB session.
    """
    try:
        from promptops_app.database import SessionLocal
        db = SessionLocal()
        try:
            log_llm_usage(db, result, ctx)
        finally:
            db.close()
    except Exception as exc:
        _log.warning("Failed to write LLM usage log (autocommit): %s", exc)
