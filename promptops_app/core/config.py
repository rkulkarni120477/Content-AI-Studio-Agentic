"""Centralized application configuration — single source of truth for all env vars.

Usage
-----
    from promptops_app.core.config import settings

    db_url  = settings.database_url
    timeout = settings.llm_timeout_seconds
    key     = settings.openai_api_key_value   # plain string, never log this

Resolution order (highest priority first):
  1. Real environment variables
  2. Values in the .env file in the project root
  3. Defaults defined in AppSettings below

Fail-fast: invalid or missing *required* field values raise ``ValidationError``
at import time so the problem surfaces immediately rather than at runtime.
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(__name__)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,   # DATABASE_URL and database_url both work
        extra="ignore",         # ignore unknown env vars — don't fail on them
        populate_by_name=True,  # allow both the field name and its alias
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+psycopg2://postgres:password@localhost:5432/promptops",
        alias="DATABASE_URL",
    )

    # ── Redis / Celery ────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        alias="REDIS_URL",
    )

    # ── Copyleaks plagiarism API ───────────────────────────────────────────────
    copyleaks_email: str = Field(
        default="",
        alias="COPYLEAKS_EMAIL",
    )
    copyleaks_api_key: SecretStr = Field(
        default="",
        alias="COPYLEAKS_API_KEY",
    )
    copyleaks_product: str = Field(
        default="businesses",
        alias="COPYLEAKS_PRODUCT",   # "businesses" or "education"
    )
    copyleaks_webhook_url: str = Field(
        default="",
        alias="COPYLEAKS_WEBHOOK_URL",
        # Template URL with {status} and {scanId} placeholders filled by Copyleaks.
        # Example: https://yourserver.com/api/plagiarism/webhook/{status}/{scanId}
    )

    # ── Security ──────────────────────────────────────────────────────────────
    jwt_secret_key: SecretStr = Field(
        default="monolith_secret_key_hardcoded",
        alias="JWT_SECRET_KEY",
    )

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: Optional[SecretStr] = Field(
        default=None,
        alias="OPENAI_API_KEY",
    )
    openai_model: str = Field(
        default="gpt-4o",
        alias="OPENAI_MODEL",
    )

    # ── AWS Bedrock ───────────────────────────────────────────────────────────
    aws_access_key_id: SecretStr = Field(
        default="",
        alias="AWS_ACCESS_KEY_ID",
    )
    aws_secret_access_key: SecretStr = Field(
        default="",
        alias="AWS_SECRET_ACCESS_KEY",
    )
    aws_region: str = Field(
        default="ap-south-1",
        alias="AWS_DEFAULT_REGION",
    )
    bedrock_model_id: str = Field(
        default="global.anthropic.claude-sonnet-4-5-20250929-v1:0",
        alias="BEDROCK_MODEL_ID",
    )

    # ── LLM behaviour ─────────────────────────────────────────────────────────
    default_model: str = Field(
        default="GPT-5.4",
        alias="PROMPTOPS_DEFAULT_MODEL",
    )
    fallback_model: str = Field(
        default="Sonnet 4.5 (Bedrock)",
        alias="PROMPTOPS_FALLBACK_MODEL",
    )
    llm_timeout_seconds: int = Field(
        default=120,
        alias="PROMPTOPS_API_TIMEOUT_SECONDS",
        gt=0,
    )
    llm_retry_count: int = Field(
        default=1,
        alias="PROMPTOPS_LLM_MAX_RETRIES",
        ge=0,
    )
    llm_fallback_enabled: bool = Field(
        default=True,
        alias="PROMPTOPS_LLM_FALLBACK_ENABLED",
    )

    # ── Context / chunking ────────────────────────────────────────────────────
    # 0 = no cap (default) — full source-document content reaches the LLM.
    # Set a positive value via the env var only if generation cost/latency
    # requires capping input size again.
    max_source_chars: int = Field(
        default=0,
        alias="PROMPTOPS_MAX_SOURCE_CHARS",
        ge=0,
    )
    max_context_chars: int = Field(
        default=0,
        alias="PROMPTOPS_MAX_CONTEXT_CHARS",
        ge=0,
    )

    # ── Token-based truncation (P6.1, F11) ──────────────────────────────────────
    # Truncation is token-based and ON by default. The flag is a master on/off:
    #   enabled (default / true) → clip source & context to the token limits below
    #                              (real token count via tiktoken).
    #   false                    → NO truncation at all — full text reaches the LLM
    #                              (accepts the cost / context-overflow tradeoff).
    # Read at startup — recreate containers to pick up a change.
    token_limit_enabled: bool = Field(
        default=False,
        alias="PROMPTOPS_TOKEN_LIMIT_ENABLED",
    )
    max_source_tokens: int = Field(
        default=1500,            # per source document
        alias="PROMPTOPS_MAX_SOURCE_TOKENS",
        gt=0,
    )
    max_context_tokens: int = Field(
        default=7500,            # combined supplementary context
        alias="PROMPTOPS_MAX_CONTEXT_TOKENS",
        gt=0,
    )
    token_encoding: str = Field(
        default="cl100k_base",   # exact for OpenAI; close approx for Claude/Bedrock
        alias="PROMPTOPS_TOKEN_ENCODING",
    )
    chars_per_token: float = Field(
        default=4.0,             # only for the char fallback when tiktoken is absent
        alias="PROMPTOPS_CHARS_PER_TOKEN",
        gt=0,
    )

    # ── Pagination ────────────────────────────────────────────────────────────
    editor_page_size: int     = Field(default=25,  alias="PROMPTOPS_EDITOR_PAGE_SIZE",     gt=0)
    workflow_page_size: int   = Field(default=100, alias="PROMPTOPS_WORKFLOW_PAGE_SIZE",   gt=0)
    analytics_page_size: int  = Field(default=50,  alias="PROMPTOPS_ANALYTICS_PAGE_SIZE",  gt=0)
    document_page_size: int   = Field(default=20,  alias="PROMPTOPS_DOCUMENT_PAGE_SIZE",   gt=0)
    feedback_page_size: int   = Field(default=25,  alias="PROMPTOPS_FEEDBACK_PAGE_SIZE",   gt=0)
    generation_page_size: int = Field(default=20,  alias="PROMPTOPS_GENERATION_PAGE_SIZE", gt=0)

    # ── Background workers ────────────────────────────────────────────────────
    max_background_workers: int = Field(
        default=3,
        alias="PROMPTOPS_MAX_BACKGROUND_WORKERS",
        gt=0,
        le=20,
    )
    # Route generation/import jobs through Celery instead of the in-process
    # ThreadPoolExecutor (job_runner). Default False keeps the historical
    # behaviour; flipping this to True (PROMPTOPS_USE_CELERY=1) enqueues jobs
    # onto Celery so they survive restarts and scale across worker replicas.
    # Doubles as a runtime kill switch: if Celery misbehaves, unset the env var
    # (no code change) and jobs fall straight back to the threadpool. The
    # dispatch layer (promptops_app/jobs/dispatch.py) also falls back
    # automatically if the broker is unreachable at submit time.
    use_celery: bool = Field(
        default=False,
        alias="PROMPTOPS_USE_CELERY",
    )

    # ── Quality checks ────────────────────────────────────────────────────────
    sync_quality_checks: bool = Field(
        default=False,
        alias="PROMPTOPS_SYNC_QUALITY_CHECKS",
    )
    sync_plagiarism_checks: bool = Field(
        default=False,
        alias="PROMPTOPS_SYNC_PLAGIARISM_CHECKS",
    )

    # ── Observability ─────────────────────────────────────────────────────────
    log_level: str = Field(
        default="INFO",
        alias="PROMPTOPS_LOG_LEVEL",
    )
    log_color: bool = Field(
        default=False,
        alias="PROMPTOPS_LOG_COLOR",
    )
    enable_prompt_logging: bool = Field(
        default=False,
        alias="PROMPTOPS_LOG_FULL_PROMPTS",
    )

    # ── Cost tracking ─────────────────────────────────────────────────────────
    model_pricing_override: Optional[str] = Field(
        default=None,
        alias="PROMPTOPS_MODEL_PRICING",
    )

    # ── Phoenix observability (self-hosted LLM tracing) ───────────────────────
    phoenix_collector_endpoint: str = Field(
        default="http://localhost:6006/v1/traces", alias="PHOENIX_COLLECTOR_ENDPOINT",
    )
    phoenix_project_name: str = Field(default="content-ai-studio", alias="PHOENIX_PROJECT_NAME")
    phoenix_api_key: str = Field(default="", alias="PHOENIX_API_KEY")

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, v: object) -> object:
        return v.upper() if isinstance(v, str) else v

    # ── Safe plaintext accessors ───────────────────────────────────────────────
    # Use these when you need the raw string value.
    # NEVER log or display these values in the UI.

    @property
    def openai_api_key_value(self) -> Optional[str]:
        """Plain-text OpenAI API key, or None if not configured."""
        return self.openai_api_key.get_secret_value() if self.openai_api_key else None

    @property
    def aws_access_key_value(self) -> str:
        """Plain-text AWS access key ID."""
        return self.aws_access_key_id.get_secret_value()

    @property
    def aws_secret_key_value(self) -> str:
        """Plain-text AWS secret access key."""
        return self.aws_secret_access_key.get_secret_value()

    @property
    def jwt_secret_value(self) -> str:
        """Plain-text JWT signing secret."""
        return self.jwt_secret_key.get_secret_value()

    @property
    def copyleaks_api_key_value(self) -> str:
        """Plain-text Copyleaks API key."""
        return self.copyleaks_api_key.get_secret_value() if self.copyleaks_api_key else ""


# Module-level singleton — import and use this everywhere.
settings = AppSettings()


# ---------------------------------------------------------------------------
# Backward-compatible flat constants
# Existing code that does `from promptops_app.core.config import EDITOR_PAGE_SIZE`
# continues to work without changes.
# ---------------------------------------------------------------------------

MAX_SOURCE_CHARS   = settings.max_source_chars
MAX_CONTEXT_CHARS  = settings.max_context_chars

MAX_SOURCE_TOKENS  = settings.max_source_tokens
MAX_CONTEXT_TOKENS = settings.max_context_tokens

EDITOR_PAGE_SIZE     = settings.editor_page_size
WORKFLOW_PAGE_SIZE   = settings.workflow_page_size
ANALYTICS_PAGE_SIZE  = settings.analytics_page_size
DOCUMENT_PAGE_SIZE   = settings.document_page_size
FEEDBACK_PAGE_SIZE   = settings.feedback_page_size
GENERATION_PAGE_SIZE = settings.generation_page_size

SYNC_QUALITY_CHECKS    = settings.sync_quality_checks
SYNC_PLAGIARISM_CHECKS = settings.sync_plagiarism_checks

API_TIMEOUT_SECONDS  = settings.llm_timeout_seconds
LLM_MAX_RETRIES      = settings.llm_retry_count
LLM_FALLBACK_ENABLED = settings.llm_fallback_enabled

LOG_LEVEL        = settings.log_level
LOG_FULL_PROMPTS = settings.enable_prompt_logging

# PROMPTOPS_-prefixed aliases kept for any remaining legacy imports
PROMPTOPS_MAX_SOURCE_CHARS       = MAX_SOURCE_CHARS
PROMPTOPS_MAX_CONTEXT_CHARS      = MAX_CONTEXT_CHARS
PROMPTOPS_MAX_SOURCE_TOKENS      = MAX_SOURCE_TOKENS
PROMPTOPS_MAX_CONTEXT_TOKENS     = MAX_CONTEXT_TOKENS
PROMPTOPS_EDITOR_PAGE_SIZE       = EDITOR_PAGE_SIZE
PROMPTOPS_WORKFLOW_PAGE_SIZE     = WORKFLOW_PAGE_SIZE
PROMPTOPS_ANALYTICS_PAGE_SIZE    = ANALYTICS_PAGE_SIZE
PROMPTOPS_DOCUMENT_PAGE_SIZE     = DOCUMENT_PAGE_SIZE
PROMPTOPS_FEEDBACK_PAGE_SIZE     = FEEDBACK_PAGE_SIZE
PROMPTOPS_GENERATION_PAGE_SIZE   = GENERATION_PAGE_SIZE
PROMPTOPS_SYNC_QUALITY_CHECKS    = SYNC_QUALITY_CHECKS
PROMPTOPS_SYNC_PLAGIARISM_CHECKS = SYNC_PLAGIARISM_CHECKS
PROMPTOPS_API_TIMEOUT_SECONDS    = API_TIMEOUT_SECONDS
PROMPTOPS_LLM_MAX_RETRIES        = LLM_MAX_RETRIES
PROMPTOPS_LLM_FALLBACK_ENABLED   = LLM_FALLBACK_ENABLED
PROMPTOPS_LOG_LEVEL              = LOG_LEVEL
PROMPTOPS_LOG_FULL_PROMPTS       = LOG_FULL_PROMPTS


# ---------------------------------------------------------------------------
# Text clipping utility — kept here so existing imports don't break
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=4)
def _get_token_encoder(encoding_name: str):
    """Return a cached tiktoken encoder, or None if tiktoken/the encoding is
    unavailable (logged once — lru_cache also caches the None result). A None
    return signals clip_tokens to use its character-based fallback."""
    try:
        import tiktoken
        return tiktoken.get_encoding(encoding_name)
    except Exception:
        _log.warning(
            "PROMPTOPS_TOKEN_LIMIT_ENABLED is on but tiktoken/encoding %r is "
            "unavailable — falling back to character-based truncation.",
            encoding_name,
        )
        return None


def clip_text(
    text: str,
    limit: int,
    suffix: str = "\n...[truncated for faster generation]",
) -> str:
    """Trim *text* to *limit* characters with a visible truncation marker.

    Pure character-based truncation. ``limit <= 0`` means no cap. Kept as the
    historical utility and as :func:`clip_tokens`' fallback when tiktoken is
    unavailable.
    """
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + suffix


_clip_text = clip_text  # private alias used throughout the codebase


def clip_tokens(
    text: str,
    max_tokens: int,
    suffix: str = "\n...[truncated for faster generation]",
) -> str:
    """Trim *text* to at most *max_tokens* real tokens (via tiktoken).

    Behaviour is governed by ``PROMPTOPS_TOKEN_LIMIT_ENABLED``:

    * **enabled** — truncate to *max_tokens* tokens.
    * **false (the field default, and unset in this deployment)** — no truncation
      at all; the full text is returned (the caller accepts the cost /
      context-overflow tradeoff).

    Note the default carefully: with the flag off — which is the current state
    everywhere — this function is a NO-OP and every budget expressed through it
    is advisory. That is correct for a cost/quality preference and wrong for a
    safety bound. When the cap exists to stop something unbounded reaching a
    prompt, use :func:`clip_tokens_strict`, which cannot be switched off.

    ``max_tokens <= 0`` means no cap. If tiktoken (or the configured encoding)
    is unavailable, or token encoding/decoding raises, this degrades to
    character-based truncation at ``max_tokens * chars_per_token`` characters —
    so input stays bounded and a tokenizer problem can never break generation.
    """
    text = text or ""
    if not settings.token_limit_enabled:
        return text  # explicit opt-out → send the full, untrimmed text
    if max_tokens <= 0:
        return text  # no cap

    enc = _get_token_encoder(settings.token_encoding)
    if enc is None:
        # tiktoken absent → keep input bounded via the char equivalent.
        return clip_text(text, int(max_tokens * settings.chars_per_token), suffix)
    try:
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return enc.decode(tokens[:max_tokens]).rstrip() + suffix
    except Exception:
        _log.warning("Token truncation failed; using char fallback.", exc_info=True)
        return clip_text(text, int(max_tokens * settings.chars_per_token), suffix)


def count_tokens(text: str) -> int:
    """Return the token count of *text*, or a character-based estimate.

    Counterpart to :func:`clip_tokens`, which trims to a token budget — this
    only measures, for callers that must decide whether a piece of text can be
    produced at all (see app.services.cdd_regen_context.assert_can_emit).

    Uses the same encoder and the same ``chars_per_token`` fallback as
    clip_tokens, so a measurement here and a trim there agree. Unlike
    clip_tokens this deliberately ignores ``token_limit_enabled``: that flag
    opts out of *truncating* input, not out of knowing how big something is,
    and a caller guarding against silent output truncation still needs a real
    number when it is off.
    """
    text = text or ""
    if not text:
        return 0
    enc = _get_token_encoder(settings.token_encoding)
    if enc is None:
        return int(len(text) / max(settings.chars_per_token, 1))
    try:
        return len(enc.encode(text))
    except Exception:
        _log.warning("Token counting failed; using char estimate.", exc_info=True)
        return int(len(text) / max(settings.chars_per_token, 1))


def clip_tokens_strict(text: str, max_tokens: int) -> str:
    """Trim *text* to *max_tokens*, whatever ``token_limit_enabled`` says.

    The counterpart to :func:`clip_tokens` for caps that are SAFETY bounds rather
    than cost preferences. ``token_limit_enabled`` defaults to False and is unset
    in this deployment, which makes clip_tokens a no-op — fine for "send the full
    source, accept the cost", and not fine for "this document must not be allowed
    to consume the whole prompt". A mis-tagged reference PDF in the AIM corpus
    runs to 646 units and ~79,000 tokens; a bound that an unrelated flag can
    switch off is not a bound.

    Same encoder and same character-based fallback as clip_tokens, so a strict
    trim and a soft one agree on what a token is.
    """
    text = text or ""
    if not text or max_tokens <= 0:
        return text
    # int(), because chars_per_token is a float (4.0) and a float is not a valid
    # slice index. Without it this raised TypeError on exactly the path that has
    # to work when the tokenizer does not — a safety net that fails when the
    # thing it backs up fails is not a safety net.
    char_cap = int(max_tokens * max(settings.chars_per_token, 1))
    enc = _get_token_encoder(settings.token_encoding)
    if enc is None:
        return text[:char_cap]
    try:
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return enc.decode(tokens[:max_tokens])
    except Exception:
        _log.warning("Strict token clip failed; using char estimate.", exc_info=True)
        return text[:char_cap]


# ---------------------------------------------------------------------------
# Backward-compatible helpers — kept so existing imports don't break
# ---------------------------------------------------------------------------

def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean flag directly from the environment.

    Kept for backward compatibility with code that imported this helper from
    the old config module.  New code should read from ``settings`` instead.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
