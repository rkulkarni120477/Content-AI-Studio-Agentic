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

import os
from typing import Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    max_source_chars: int = Field(
        default=6000,
        alias="PROMPTOPS_MAX_SOURCE_CHARS",
        gt=0,
    )
    max_context_chars: int = Field(
        default=30000,
        alias="PROMPTOPS_MAX_CONTEXT_CHARS",
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
    enable_cost_tracking: bool = Field(
        default=True,
        alias="PROMPTOPS_ENABLE_COST_TRACKING",
    )
    model_pricing_override: Optional[str] = Field(
        default=None,
        alias="PROMPTOPS_MODEL_PRICING",
    )

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


# Module-level singleton — import and use this everywhere.
settings = AppSettings()


# ---------------------------------------------------------------------------
# Backward-compatible flat constants
# Existing code that does `from promptops_app.core.config import EDITOR_PAGE_SIZE`
# continues to work without changes.
# ---------------------------------------------------------------------------

MAX_SOURCE_CHARS   = settings.max_source_chars
MAX_CONTEXT_CHARS  = settings.max_context_chars

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

def clip_text(
    text: str,
    limit: int,
    suffix: str = "\n...[truncated for faster generation]",
) -> str:
    """Trim *text* to *limit* characters with a visible truncation marker."""
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + suffix


_clip_text = clip_text  # private alias used throughout the codebase


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
