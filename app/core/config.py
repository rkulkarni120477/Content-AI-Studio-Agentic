"""
Centralised application configuration.

All environment variables are declared here as typed Pydantic fields.
Import the ``settings`` singleton everywhere instead of reading os.environ
directly — this ensures type safety and a single source of truth.

Usage
-----
    from app.core.config import settings

    db_url  = settings.database_url
    timeout = settings.llm_timeout_seconds
    key     = settings.openai_api_key_value   # safe plain-text accessor

Environment variable resolution order (highest priority first):
  1. Real environment variables set in the shell / Docker env
  2. Values in the .env file at the project root
  3. Default values defined in this file

Security note
-------------
Fields typed as ``SecretStr`` are never printed in logs or tracebacks.
Use the ``_value`` property accessors to get the raw string when required
by an external library — and never log those values.
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Reserved organization code meaning "platform administrator login" (not a tenant).
PLATFORM_SLUG = "platform"


class AppSettings(BaseSettings):
    """
    Pydantic settings model — validates and exposes all configuration.

    Every field maps to an environment variable via its ``alias``.
    Missing required fields raise a ValidationError at startup (fail-fast).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,   # DATABASE_URL and database_url both work
        extra="ignore",         # ignore unknown env vars gracefully
        populate_by_name=True,  # allow both field name and alias
    )

    # ── Application metadata ──────────────────────────────────────────────────
    app_version: str = Field(default="1.0.0", alias="APP_VERSION")
    app_env: str = Field(
        default="development",
        alias="APP_ENV",
        description="Deployment environment: development | staging | production",
    )

    # ── CORS — allowed frontend origins ──────────────────────────────────────
    # In production, set this to your exact React frontend domain.
    # Multiple origins can be separated by commas in the env var.
    allowed_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        alias="ALLOWED_ORIGINS",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+psycopg2://postgres:password@localhost:5432/contentai",
        alias="DATABASE_URL",
    )

    # ── Redis / Celery ────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        alias="REDIS_URL",
    )
    celery_broker_url: str = Field(
        default="redis://localhost:6379/0",
        alias="CELERY_BROKER_URL",
    )
    celery_result_backend: str = Field(
        default="redis://localhost:6379/1",
        alias="CELERY_RESULT_BACKEND",
    )

    # ── Security ──────────────────────────────────────────────────────────────
    jwt_secret_key: SecretStr = Field(
        ...,  # required — no default; must be set in .env
        alias="JWT_SECRET_KEY",
        description="HS256 signing secret for JWT tokens. Use a long random string.",
    )
    jwt_token_expire_days: int = Field(
        default=7,
        alias="JWT_TOKEN_EXPIRE_DAYS",
        gt=0,
    )

    # ── Microsoft Entra ID (Azure AD) OAuth ───────────────────────────────────
    # Platform-wide default Azure app; a tenant (Project) may override per-tenant.
    # Nothing in the Microsoft sign-in flow works until these are set in .env.
    microsoft_auth_enabled: bool = Field(default=False, alias="MICROSOFT_AUTH_ENABLED")
    azure_client_id: Optional[str] = Field(default=None, alias="AZURE_CLIENT_ID")
    azure_client_secret: Optional[SecretStr] = Field(default=None, alias="AZURE_CLIENT_SECRET")
    azure_tenant_id: str = Field(default="common", alias="AZURE_TENANT_ID")
    azure_redirect_uri: Optional[str] = Field(default=None, alias="AZURE_REDIRECT_URI")
    azure_new_user_role: str = Field(default="author", alias="AZURE_NEW_USER_ROLE")

    # ── Login options ─────────────────────────────────────────────────────────
    local_login_enabled: bool = Field(default=True, alias="LOCAL_LOGIN_ENABLED")
    frontend_url: str = Field(default="http://localhost:5173", alias="FRONTEND_URL")

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
        default=SecretStr(""),
        alias="AWS_ACCESS_KEY_ID",
    )
    aws_secret_access_key: SecretStr = Field(
        default=SecretStr(""),
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

    # ── Pagination defaults (overridable per-request via query params) ────────
    editor_page_size: int     = Field(default=25,  alias="PROMPTOPS_EDITOR_PAGE_SIZE",     gt=0)
    workflow_page_size: int   = Field(default=100, alias="PROMPTOPS_WORKFLOW_PAGE_SIZE",   gt=0)
    analytics_page_size: int  = Field(default=50,  alias="PROMPTOPS_ANALYTICS_PAGE_SIZE",  gt=0)
    document_page_size: int   = Field(default=20,  alias="PROMPTOPS_DOCUMENT_PAGE_SIZE",   gt=0)
    generation_page_size: int = Field(default=20,  alias="PROMPTOPS_GENERATION_PAGE_SIZE", gt=0)

    # ── Context limits ────────────────────────────────────────────────────────
    max_source_chars: int = Field(default=6_000,  alias="PROMPTOPS_MAX_SOURCE_CHARS",  gt=0)
    max_context_chars: int = Field(default=30_000, alias="PROMPTOPS_MAX_CONTEXT_CHARS", gt=0)

    # ── Copyleaks plagiarism API ───────────────────────────────────────────────
    copyleaks_email: str = Field(default="", alias="COPYLEAKS_EMAIL")
    copyleaks_api_key: SecretStr = Field(default=SecretStr(""), alias="COPYLEAKS_API_KEY")
    copyleaks_product: str = Field(default="businesses", alias="COPYLEAKS_PRODUCT")

    # ── Observability ─────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO", alias="PROMPTOPS_LOG_LEVEL")
    log_color: bool = Field(default=False,  alias="PROMPTOPS_LOG_COLOR")
    enable_prompt_logging: bool = Field(
        default=False,
        alias="PROMPTOPS_LOG_FULL_PROMPTS",
        description="If True, full LLM prompts are written to the log. Disable in production.",
    )

    # ── Feature flags ─────────────────────────────────────────────────────────
    sync_quality_checks: bool = Field(
        default=False,
        alias="PROMPTOPS_SYNC_QUALITY_CHECKS",
    )
    # Reverse pipeline (IMSCC course import). Default OFF: when false the imports
    # router is not mounted and the create-course modal hides the Import option,
    # so the app is behaviourally identical to today. See reverse_cas.md.
    import_courses_enabled: bool = Field(
        default=False,
        alias="IMPORT_COURSES_ENABLED",
    )
    approval_sla_hours: int = Field(
        default=24,
        alias="PROMPTOPS_APPROVAL_SLA_HOURS",
        gt=0,
    )

    # ── DIS / Source Library integration ─────────────────────────────────────
    dis_enabled: bool = Field(default=True, alias="DIS_ENABLED")
    dis_api_base_url: str = Field(default="http://localhost:8010/v1", alias="DIS_API_BASE_URL")
    dis_api_timeout_seconds: int = Field(default=300, alias="DIS_API_TIMEOUT_SECONDS", gt=0)
    dis_service_token: str = Field(default="dev-dis-token", alias="DIS_SERVICE_TOKEN")
    dis_default_tenant_id: str = Field(default="aim", alias="DIS_DEFAULT_TENANT_ID")
    dis_default_client_id: str = Field(default="", alias="DIS_DEFAULT_CLIENT_ID")
    dis_available_clients: str = Field(default="aim,cengage", alias="DIS_AVAILABLE_CLIENTS")
    dis_super_admin_usernames: str = Field(default="", alias="DIS_SUPER_ADMIN_USERNAMES")
    dis_super_admin_roles: str = Field(default="", alias="DIS_SUPER_ADMIN_ROLES")
    dis_user_client_map: str = Field(default="", alias="DIS_USER_CLIENT_MAP")
    dis_access_config_path: str = Field(default="", alias="DIS_ACCESS_CONFIG_PATH")

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        """Normalise log level to uppercase so INFO and info both work."""
        return value.upper() if isinstance(value, str) else value

    # ── Safe plain-text accessors ─────────────────────────────────────────────
    # Use these when an external library needs the raw string.
    # NEVER log or display the returned values.

    @property
    def openai_api_key_value(self) -> Optional[str]:
        """Return the raw OpenAI API key string, or None if not configured."""
        return self.openai_api_key.get_secret_value() if self.openai_api_key else None

    @property
    def aws_access_key_value(self) -> str:
        """Return the raw AWS access key ID string."""
        return self.aws_access_key_id.get_secret_value()

    @property
    def aws_secret_key_value(self) -> str:
        """Return the raw AWS secret access key string."""
        return self.aws_secret_access_key.get_secret_value()

    @property
    def jwt_secret_value(self) -> str:
        """Return the raw JWT signing secret string."""
        return self.jwt_secret_key.get_secret_value()

    @property
    def azure_client_secret_value(self) -> Optional[str]:
        """Return the raw Azure client secret string, or None if not configured."""
        return self.azure_client_secret.get_secret_value() if self.azure_client_secret else None

    def microsoft_ready(self) -> bool:
        """True when the platform-wide Azure app credentials are present."""
        return bool(self.microsoft_auth_enabled and self.azure_client_id and self.azure_client_secret_value)

    @property
    def copyleaks_api_key_value(self) -> str:
        """Return the raw Copyleaks API key string."""
        return self.copyleaks_api_key.get_secret_value() if self.copyleaks_api_key else ""

    @property
    def is_production(self) -> bool:
        """True when running in the production environment."""
        return self.app_env.lower() == "production"


# ---------------------------------------------------------------------------
# Module-level singleton — import and use this everywhere.
# Instantiation validates all fields at import time (fail-fast on bad config).
# ---------------------------------------------------------------------------
settings = AppSettings()
