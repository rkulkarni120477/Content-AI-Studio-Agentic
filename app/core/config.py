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

    # ── Editor asset storage (S3) ─────────────────────────────────────────────
    # Editor images live in the DIS S3 bucket. That bucket is in its own AWS
    # account/region (us-east-1), which differs from the Bedrock AWS creds/region
    # above (ap-south-1) — so assets get their OWN region + credentials, falling
    # back to the main AWS ones only when left blank. Leave the bucket blank to
    # disable upload (the editor still supports image URLs).
    assets_s3_bucket: str = Field(default="", alias="DIS_S3_BUCKET")
    assets_s3_base_prefix: str = Field(default="", alias="DIS_S3_BASE_PREFIX")
    assets_s3_region: str = Field(default="", alias="DIS_S3_REGION")
    assets_s3_access_key_id: SecretStr = Field(
        default=SecretStr(""), alias="DIS_S3_ACCESS_KEY_ID",
    )
    assets_s3_secret_access_key: SecretStr = Field(
        default=SecretStr(""), alias="DIS_S3_SECRET_ACCESS_KEY",
    )
    aws_endpoint_url: str = Field(default="", alias="AWS_ENDPOINT_URL")
    assets_max_upload_mb: int = Field(
        default=5, alias="CAS_ASSETS_MAX_UPLOAD_MB", gt=0,
    )
    # When True, uploads set a per-object public-read ACL. Leave False when the
    # bucket serves the cas-assets/ prefix via a bucket POLICY (the case for the
    # shared DIS bucket, which has ACLs disabled) — avoids a doomed ACL PutObject.
    assets_s3_public_acl: bool = Field(
        default=False, alias="CAS_ASSETS_PUBLIC_ACL",
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
    # CE Agent Review (checklist-based content review). Default OFF: when false the
    # review-checklist / reviews routers are not mounted and the Editor's AI Review
    # section is hidden, so the app is behaviourally identical to today. See
    # docs/ce-agent-review-plan.md.
    ce_review_enabled: bool = Field(
        default=False,
        alias="CE_REVIEW_ENABLED",
    )
    # CE review retention window (days) for finding/result detail. 0 = off (keep
    # forever, default). When > 0, old detail is pruned at app startup.
    ce_review_retention_days: int = Field(
        default=0,
        alias="CE_REVIEW_RETENTION_DAYS",
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
    # How long to wait for a block digest build, which is started and then polled
    # (DISClient.build_digests_sync) rather than held open on one request. Default 40
    # minutes: a cold 20-day block measured ~10 minutes even with the MAP fan-out, and
    # this must stay comfortably under the stranded-job reaper's window
    # (promptops_app.jobs.reaper.DEFAULT_STALE_AFTER_MINUTES, 60) so the reaper never
    # marks a job stranded while its build is still legitimately running.
    dis_digest_build_deadline_seconds: int = Field(
        default=2400, alias="DIS_DIGEST_BUILD_DEADLINE_SECONDS", gt=0)
    dis_digest_build_poll_seconds: int = Field(
        default=5, alias="DIS_DIGEST_BUILD_POLL_SECONDS", gt=0)
    dis_service_token: str = Field(default="dev-dis-token", alias="DIS_SERVICE_TOKEN")
    dis_default_tenant_id: str = Field(default="aim", alias="DIS_DEFAULT_TENANT_ID")
    dis_default_client_id: str = Field(default="", alias="DIS_DEFAULT_CLIENT_ID")
    # Blank by default so config/dis_access.json's own available_clients list
    # (which tenant creation now writes new clients into) is actually the
    # fallback it's meant to be — a non-empty default here would always win
    # over the file, per load_dis_access_config's own env-overrides-file logic.
    dis_available_clients: str = Field(default="", alias="DIS_AVAILABLE_CLIENTS")
    dis_super_admin_usernames: str = Field(default="", alias="DIS_SUPER_ADMIN_USERNAMES")

    # ── Phoenix observability (self-hosted LLM tracing) ───────────────────────
    # phoenix.otel.register() reads PHOENIX_COLLECTOR_ENDPOINT/PHOENIX_PROJECT_NAME
    # directly too — these fields exist so app/core/phoenix_client.py's server-side
    # fetch (the trace-detail endpoint) doesn't need its own separate env-parsing.
    phoenix_collector_endpoint: str = Field(
        default="http://localhost:6006/v1/traces", alias="PHOENIX_COLLECTOR_ENDPOINT",
    )
    phoenix_base_url: str = Field(default="http://localhost:6006", alias="PHOENIX_BASE_URL")
    phoenix_project_name: str = Field(default="content-ai-studio", alias="PHOENIX_PROJECT_NAME")
    phoenix_api_key: str = Field(default="", alias="PHOENIX_API_KEY")
    dis_super_admin_roles: str = Field(default="", alias="DIS_SUPER_ADMIN_ROLES")
    dis_user_client_map: str = Field(default="", alias="DIS_USER_CLIENT_MAP")
    dis_access_config_path: str = Field(default="", alias="DIS_ACCESS_CONFIG_PATH")

    # ── Digest pipeline (block-wide CDD/Blueprint) ────────────────────────────
    # Master switch for the enumerate→map→reduce→verify digest path. Default OFF:
    # when false, CDD generation is byte-identical to today (legacy single call).
    # The allowlist scopes the flag to specific clients so AIM can flip on first
    # while every other tenant stays on the legacy path (multi-tenant safety).
    digest_pipeline_enabled: bool = Field(default=False, alias="DIGEST_PIPELINE_ENABLED")
    digest_pipeline_clients: str = Field(default="aim", alias="DIGEST_PIPELINE_CLIENTS")

    # ── Prompt guidance (DB-maintained prompt → MAP/REDUCE guidance) ──────────
    # How much of the course's selected CDD/Blueprint prompt reaches the single
    # distillation call, and how much guidance it may emit. These were previously
    # hardcoded at 6000/2500/12 — far too small for a real block prompt (AIM's
    # Block 2 CDD template is ~15.7k chars), which silently dropped ~60% of the
    # admin's own instructions before the distiller ever saw them. A prompt longer
    # than the input cap is now WINDOWED (up to `..._max_windows` chunks, each
    # distilled and merged) rather than truncated, so raising these trades cost for
    # fidelity instead of correctness.
    prompt_guidance_max_prompt_chars: int = Field(
        default=48000, alias="PROMPT_GUIDANCE_MAX_PROMPT_CHARS", gt=0)
    prompt_guidance_max_chars: int = Field(
        default=8000, alias="PROMPT_GUIDANCE_MAX_CHARS", gt=0)
    prompt_guidance_max_items: int = Field(
        default=24, alias="PROMPT_GUIDANCE_MAX_ITEMS", gt=0)
    prompt_guidance_max_windows: int = Field(
        default=4, alias="PROMPT_GUIDANCE_MAX_WINDOWS", gt=0)
    # In-process memo of distillations, keyed by a content hash of the prompt text.
    # A block-wide generation is one distillation call per request; the prompt
    # changes rarely, so this removes a redundant LLM call (and its latency) from
    # every repeat generation. Content-keyed ⇒ an edited prompt misses naturally.
    prompt_guidance_cache_size: int = Field(
        default=64, alias="PROMPT_GUIDANCE_CACHE_SIZE", ge=0)

    # ── User directives (the generation form's own inputs → MAP/REDUCE) ───────
    # How much of the selected style reaches each stage. Two caps, not one,
    # because the two stages have very different economics: REDUCE is a handful
    # of calls, MAP is one call PER DAY (20 on AIM's Block 2), so a character
    # sent to MAP costs ~20x what the same character costs at REDUCE — and it
    # also eats the MAP context headroom that mapper.py's source budget reserves
    # for "the prompt scaffold, rubric and guidance".
    #
    # Bounding this is not optional: build_style_context (database.py) embeds
    # whole style reference DOCUMENTS when a style has no generated_summary, so
    # an uncapped style could be tens of thousands of characters and would fail
    # every day of a build with a hard "Input is too long" from Bedrock.
    block_wide_style_chars_reduce: int = Field(
        default=12000, alias="BLOCK_WIDE_STYLE_CHARS_REDUCE", gt=0)
    block_wide_style_chars_map: int = Field(
        default=2000, alias="BLOCK_WIDE_STYLE_CHARS_MAP", gt=0)

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        """Normalise log level to uppercase so INFO and info both work."""
        return value.upper() if isinstance(value, str) else value

    def digest_pipeline_on_for(self, client_id: str) -> bool:
        """Whether the digest pipeline is active for *client_id*.

        Master switch AND explicit membership in the allowlist. Fails CLOSED:
        an empty or unset DIGEST_PIPELINE_CLIENTS means no clients, not every
        client. The previous behaviour (empty ⇒ all) was fail-open — the exact
        opposite of what clearing an allowlist reads as to an operator, and
        dormant only because the shipped default ("aim") is non-empty. Naming
        clients explicitly is the only way to enable this for anyone; there is
        no wildcard.
        """
        if not self.digest_pipeline_enabled:
            return False
        allow = {c.strip().lower() for c in (self.digest_pipeline_clients or "").split(",") if c.strip()}
        return bool(allow) and (client_id or "").strip().lower() in allow

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
