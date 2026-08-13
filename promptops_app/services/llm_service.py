"""LLM reliability service.

All LLM calls must go through generate_text() or generate_with_metadata().
Direct use of llm_client functions (call_llm, call_openai, call_bedrock) from
pages or services is deprecated — route through this module instead.

Reliability pipeline per call:
  1. Try primary model (selected by model_choice).
  2. On timeout: retry once with the same model.
  3. If still failing: try cross-provider fallback (OpenAI ↔ Bedrock).
  4. If fallback also fails: return a user-safe error string.

Observability — every call returns LLMResult tracking:
  - model actually used
  - prompt/completion token counts (when provided by the API)
  - total wall-clock duration
  - status  : success | retry_success | fallback_success | error
  - error_type: timeout | rate_limit | auth | provider | unknown

Configuration (via .env):
  PROMPTOPS_LLM_MAX_RETRIES       (default: 1)
  PROMPTOPS_LLM_FALLBACK_ENABLED  (default: true)
"""

import logging
import time
from typing import TYPE_CHECKING, Optional

from promptops_app.database import settings
from promptops_app.core.config import settings as _cfg
from promptops_app.core.llm_client import (
    LLMAuthError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResult,
    LLMTimeoutError,
    _call_bedrock_raw,
    _call_openai_raw,
)
from promptops_app.services.budget_service import BudgetExceededError

if TYPE_CHECKING:
    from promptops_app.services.usage_service import UsageLogContext

_log = logging.getLogger(__name__)

# LLMResult is defined in llm_client.py (the module that logs it) and re-exported
# here since every existing caller imports it from this module — see
# llm_client.LLMResult's docstring for why it isn't defined in both places.


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_bedrock(model_choice: str) -> bool:
    """Return True when model_choice routes to AWS Bedrock.

    Uses the model catalog for an authoritative answer; falls back to a
    string heuristic for unrecognised names so legacy call sites still work.
    """
    from promptops_app.core.models import resolve_model
    return resolve_model(model_choice).provider == "bedrock"


def _classify(exc: Exception) -> str:
    if isinstance(exc, BudgetExceededError):
        return "quota"
    if isinstance(exc, LLMTimeoutError):
        return "timeout"
    if isinstance(exc, LLMRateLimitError):
        return "rate_limit"
    if isinstance(exc, LLMAuthError):
        return "auth"
    if isinstance(exc, LLMProviderError):
        return "provider"
    return "unknown"


_USER_MESSAGES = {
    "timeout":    "The AI model is taking too long to respond. Please try again in a moment.",
    "rate_limit": "The AI service is currently busy. Please wait a moment and try again.",
    "auth":       "AI service authentication error. Please contact your administrator.",
    "provider":   "The AI service returned an error. Please try again.",
    "quota":      "You've exceeded your usage budget for this period. Contact an admin to request an increase.",
    "unknown":    "Unable to generate content. Please try again or contact support.",
}


def _user_msg(error_type: str) -> str:
    return _USER_MESSAGES.get(error_type, _USER_MESSAGES["unknown"])


def _invoke_primary(model_choice: str, system: str, user: str,
                    usage_ctx: Optional["UsageLogContext"] = None,
                    max_tokens: Optional[int] = None) -> LLMResponse:
    """Dispatch to the correct provider using the model catalog.

    Raises LLMProviderError for unknown model names so the retry pipeline
    surfaces a clear error instead of silently routing elsewhere.
    """
    from promptops_app.core.models import validate_model
    try:
        m = validate_model(model_choice)
    except ValueError as exc:
        raise LLMProviderError(str(exc)) from exc

    if m.provider == "bedrock":
        return _call_bedrock_raw(system, user, model_id=m.api_model_id, usage_ctx=usage_ctx, max_tokens=max_tokens)
    return _call_openai_raw(system, user, model=m.api_model_id, usage_ctx=usage_ctx, max_tokens=max_tokens)


def _sibling_candidates(model_choice: str):
    """Same-provider models to try before crossing to the other provider.

    Most failures that reach the fallback are scoped to a MODEL ID, not to the
    provider: end-of-life (``ResourceNotFoundException``), provider-legacy, a
    missing inference-profile prefix (``ValidationException``), or no Bedrock model
    access for that ID (``AccessDeniedException``). All four were hit on this
    account. A sibling model reached over the same connection, credentials and
    region very likely succeeds, so trying Sonnet after an Opus failure is both
    cheaper and closer to what the caller asked for than jumping to OpenAI.

    Crossing providers also has costs that belong at the END of the chain, not the
    start: the prompts and JSON contracts are tuned per model family, per-request
    cost basis changes, and — the important one — content moves to a different
    vendor. That is a data-governance decision, and an exception handler is the
    wrong place to make it silently for instructor-only source material.

    Catalog order, minus the model that just failed.
    """
    from promptops_app.core.models import BEDROCK_MODELS, OPENAI_MODELS, resolve_model

    failed = resolve_model(model_choice)
    pool = BEDROCK_MODELS if failed.provider == "bedrock" else OPENAI_MODELS
    return [m for m in pool if m.api_model_id != failed.api_model_id]


def _invoke_model(model_def, system: str, user: str,
                  usage_ctx: Optional["UsageLogContext"] = None,
                  max_tokens: Optional[int] = None) -> LLMResponse:
    """Call one specific catalog model, capped to its own output ceiling.

    Takes ``usage_ctx`` for the same reason ``_invoke_primary`` does: cost
    attribution and budget enforcement live inside the raw callers, so a sibling
    attempt that skipped it would spend against a budget without recording it.
    """
    capped = (min(max_tokens, model_def.max_output_tokens)
              if max_tokens is not None else None)
    if model_def.provider == "bedrock":
        return _call_bedrock_raw(system, user, model_id=model_def.api_model_id,
                                 usage_ctx=usage_ctx, max_tokens=capped)
    return _call_openai_raw(system, user, model=model_def.api_model_id,
                            usage_ctx=usage_ctx, max_tokens=capped)


def _invoke_fallback(model_choice: str, system: str, user: str,
                     usage_ctx: Optional["UsageLogContext"] = None,
                     max_tokens: Optional[int] = None) -> LLMResponse:
    """Fall back to the opposite provider using its first catalog entry.

    This is the LAST resort in the chain — same-provider siblings are tried first
    (see _sibling_candidates). Reached when every model on the primary's provider
    failed, which points at the provider/credentials rather than any one model.

    ``max_tokens`` is capped to the FALLBACK model's own catalog ceiling, not
    dropped or shrunk arbitrarily — it's sized for the PRIMARY model (e.g.
    Opus 4.8's 32000) and a different fallback model's real provider limit can
    be lower (gpt-4o's 16384). Forwarding the primary's value unchanged doesn't
    get you a bigger response; the provider just rejects the request outright
    with a 400 before generating anything (caught live: a Bedrock failure fell
    back to OpenAI with max_tokens=32000, which OpenAI rejected, sinking the
    whole call and losing the fallback entirely). Capping to the fallback's own
    ceiling is what makes it possible to get its fullest real result at all."""
    from promptops_app.core.models import resolve_model, OPENAI_MODELS, BEDROCK_MODELS

    def _capped(requested: Optional[int], fallback_model) -> Optional[int]:
        if requested is None or fallback_model is None:
            return requested
        return min(requested, fallback_model.max_output_tokens)

    m = resolve_model(model_choice)   # safe — never raises

    if m.provider == "bedrock":
        # Primary was Bedrock → fall back to first OpenAI model in catalog
        if not settings.openai_api_key:
            raise LLMAuthError("Fallback OpenAI key not configured.")
        fallback = OPENAI_MODELS[0] if OPENAI_MODELS else None
        fallback_id = fallback.api_model_id if fallback else settings.openai_model
        return _call_openai_raw(system, user, model=fallback_id, usage_ctx=usage_ctx,
                                max_tokens=_capped(max_tokens, fallback))
    else:
        # Primary was OpenAI → fall back to first Bedrock model in catalog
        if not (settings.aws_access_key and settings.aws_secret_key):
            raise LLMAuthError("Fallback Bedrock credentials not configured.")
        fallback = BEDROCK_MODELS[0] if BEDROCK_MODELS else None
        fallback_id = fallback.api_model_id if fallback else settings.bedrock_model_id
        return _call_bedrock_raw(system, user, model_id=fallback_id, usage_ctx=usage_ctx,
                                 max_tokens=_capped(max_tokens, fallback))


def _make_result(resp: LLMResponse, start: float, status: str) -> LLMResult:
    return LLMResult(
        text=resp.text,
        model=resp.model,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        total_duration_s=time.monotonic() - start,
        status=status,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_with_metadata(
    model_choice: str,
    system_prompt: str,
    user_prompt: str,
    usage_ctx: Optional["UsageLogContext"] = None,
    max_tokens: Optional[int] = None,
) -> LLMResult:
    """Full reliability pipeline. Never raises — always returns an LLMResult.

    On error: is_error=True, text holds a user-safe message, error_type
    describes the failure category for downstream logging or UI display.

    usage_ctx is forwarded to _invoke_primary/_invoke_fallback, which forward it
    to the raw provider functions — that is where each attempt is actually
    logged to llm_usage_logs now (one row per attempt), not here. Logging here
    as well would double-count every call's cost; see llm_client.py's
    _call_openai_raw/_call_bedrock_raw docstrings.

    ``max_tokens`` overrides the output cap (e.g. block-wide reduce headroom);
    None keeps the historical default so existing callers are unchanged.
    """
    start = time.monotonic()
    last_exc: Optional[Exception] = None
    last_error_type = "unknown"

    # ── Attempt 1: primary model ─────────────────────────────────────────────
    try:
        resp = _invoke_primary(model_choice, system_prompt, user_prompt, usage_ctx, max_tokens=max_tokens)
        _log.debug("LLM success [model=%s duration=%.1fs]", resp.model, time.monotonic() - start)
        return _make_result(resp, start, "success")
    except BudgetExceededError:
        # Must reach the HTTP layer as a real 402 (app/main.py's dedicated
        # handler), not be swallowed into a generic error-result string like
        # every other exception here — a quota breach is not "try again later."
        raise
    except Exception as exc:
        last_exc = exc
        last_error_type = _classify(exc)
        _log.warning(
            "LLM primary attempt failed [model=%s type=%s]: %s",
            model_choice, last_error_type, exc,
        )

    # ── Attempt 2: retry on timeout only ────────────────────────────────────
    if last_error_type == "timeout" and _cfg.llm_retry_count >= 1:
        _log.info("Retrying primary model after timeout [model=%s]", model_choice)
        try:
            resp = _invoke_primary(model_choice, system_prompt, user_prompt, usage_ctx, max_tokens=max_tokens)
            _log.info(
                "Primary succeeded on retry [model=%s duration=%.1fs]",
                resp.model, time.monotonic() - start,
            )
            return _make_result(resp, start, "retry_success")
        except BudgetExceededError:
            raise
        except Exception as exc:
            last_exc = exc
            last_error_type = _classify(exc)
            _log.warning(
                "LLM retry also failed [model=%s type=%s]: %s",
                model_choice, last_error_type, exc,
            )

    # ── Attempt 3: same-provider siblings, cheapest correction first ─────────
    # A failure that reaches here is usually scoped to the model ID (EOL, legacy,
    # wrong prefix, no model access) rather than the provider, so a sibling over the
    # same connection is the closest substitute — Sonnet for an Opus failure, not
    # GPT. Crossing providers changes prompt/JSON behaviour, cost basis and which
    # vendor sees the content, so it stays last.
    #
    # "quota" is excluded for the same reason the cross-provider step excludes it: a
    # budget breach is scope-based (project/course/user), so every sibling would hit
    # the identical breach. "auth" likewise — nothing downstream succeeds when the
    # credentials are the problem.
    if _cfg.llm_fallback_enabled and last_error_type not in ("auth", "quota"):
        for candidate in _sibling_candidates(model_choice):
            _log.info("Trying same-provider fallback [primary=%s candidate=%s]",
                      model_choice, candidate.display_name)
            try:
                resp = _invoke_model(candidate, system_prompt, user_prompt,
                                     usage_ctx, max_tokens=max_tokens)
                _log.warning(
                    "Same-provider fallback succeeded [primary=%s used=%s duration=%.1fs]",
                    model_choice, candidate.display_name, time.monotonic() - start,
                )
                return _make_result(resp, start, "fallback_success")
            except BudgetExceededError:
                # Must reach the HTTP layer as a real 402, not be retried against
                # another model that shares the same budget scope.
                raise
            except Exception as exc:
                last_exc = exc
                last_error_type = _classify(exc)
                _log.warning(
                    "Same-provider fallback failed [candidate=%s type=%s]: %s",
                    candidate.display_name, last_error_type, exc,
                )
                if last_error_type == "quota":
                    break

    # ── Attempt 4: cross-provider fallback (last resort) ─────────────────────
    # "quota" excluded same as "auth" — a budget breach is scope-based (project/
    # course/user), not provider-based, so the fallback provider would just hit
    # the identical breach again; skip the wasted attempt.
    fallback_label = "OpenAI" if _is_bedrock(model_choice) else "Bedrock"
    if _cfg.llm_fallback_enabled and last_error_type not in ("auth", "quota"):
        _log.info(
            "Trying fallback provider [primary=%s fallback=%s]",
            model_choice, fallback_label,
        )
        try:
            resp = _invoke_fallback(model_choice, system_prompt, user_prompt, usage_ctx, max_tokens=max_tokens)
            _log.info(
                "Fallback succeeded [provider=%s duration=%.1fs]",
                fallback_label, time.monotonic() - start,
            )
            return _make_result(resp, start, "fallback_success")
        except BudgetExceededError:
            raise
        except Exception as exc:
            last_exc = exc
            last_error_type = _classify(exc)
            _log.error(
                "LLM fallback also failed [primary=%s fallback=%s type=%s]: %s",
                model_choice, fallback_label, last_error_type, exc,
                exc_info=True,
            )

    # ── All attempts exhausted ───────────────────────────────────────────────
    _log.error(
        "All LLM attempts failed [model=%s type=%s duration=%.1fs last_exc=%s]",
        model_choice, last_error_type, time.monotonic() - start, last_exc,
        exc_info=last_exc,
    )
    return LLMResult(
        text=_user_msg(last_error_type),
        model=model_choice,
        total_duration_s=time.monotonic() - start,
        status="error",
        error_type=last_error_type,
    )


def generate_text(
    model_choice: str,
    system_prompt: str,
    user_prompt: str,
    usage_ctx: Optional["UsageLogContext"] = None,
) -> str:
    """Reliability-wrapped LLM call. Returns text on success.

    On error returns a string starting with "ERROR: " so that all existing
    callers using ``output.startswith("ERROR")`` continue to work unchanged.
    Pass usage_ctx to enable automatic usage logging in llm_usage_logs.
    """
    result = generate_with_metadata(model_choice, system_prompt, user_prompt, usage_ctx)
    if result.is_error:
        return f"ERROR: {result.text}"
    return result.text
