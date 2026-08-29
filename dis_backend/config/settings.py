"""
DIS – Configuration System
All tenant config loaded from YAML files.
Global settings from environment variables (.env).
"""
from __future__ import annotations
import glob, os, re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import AliasChoices, BaseModel, Field, ConfigDict
from pydantic_settings import BaseSettings


# ── Sub-models ────────────────────────────────────────────────────────────────

class AdminUser(BaseModel):
    user_id: str
    name: str = ""
    enabled: bool = True

class UserConfig(BaseModel):
    user_id: str
    name: str = ""
    role: str = "user"  # client_admin | user. super_admin lives only in platform.yaml
    enabled: bool = True

class PlatformConfig(BaseModel):
    name: str = "DIS Platform"
    environment: str = "development"
    demo_secret: str = "demo_secret"
    super_admins: List[AdminUser] = []

    def is_super_admin(self, user_id: str) -> bool:
        return any(u.user_id.lower() == user_id.lower() and u.enabled for u in self.super_admins)

    def get_super_admin(self, user_id: str) -> Optional[AdminUser]:
        return next((u for u in self.super_admins if u.user_id.lower() == user_id.lower() and u.enabled), None)


class ClientConfig(BaseModel):
    client_id: str
    display_name: str
    allowed_file_types: List[str] = ["pdf", "docx", "txt"]
    max_file_size_mb: int = 50
    namespace_prefix: str = ""
    restricted_content: bool = False
    admins: List[AdminUser] = []

class IngestionConfig(BaseModel):
    max_concurrent_files: int = 10
    dedup_enabled: bool = True
    quarantine_on_fail: bool = True
    schema_validation: bool = True

class ProcessingConfig(BaseModel):
    # local_sequential = old safe behavior
    # local_parallel   = process multiple files concurrently inside FastAPI background task
    # sqs_worker       = reserved for AWS SQS worker deployment
    mode: str = "local_parallel"
    max_workers: int = 5
    queue_enabled: bool = False
    folder_scan_return_results_limit: int = 200

    # v14 performance guards for large PPT/PDF files.
    # These keep first-pass ingestion fast; full/deep extraction can be enabled later.
    fast_pptx_enabled: bool = True
    max_pptx_slides: int = 80
    pptx_extract_images: bool = False
    max_extracted_chars: int = 250000

# Default model for every text step when a client YAML omits the key.
#
# Sonnet 4.5 on the `global.` inference profile. Not the newest Sonnet — the only
# Anthropic text model this account invokes RELIABLY (3/3 consecutive InvokeModel
# calls in both ap-south-1 and us-east-1). Sonnet 5, Opus 5 and Sonnet 4.6 are listed
# by Bedrock in both regions but each produced one spurious success and then failed
# every repeat, so they are not usable yet; a single successful probe is not evidence
# of availability. Switch to Sonnet 5 (1M context) once its access grant lands and it
# measures clean from the target environment.
#
# Availability is per-region AND per-role, so this can still be wrong in a given
# environment — on 2026-08-12 a deployed role could not invoke it and, because
# call_llm THEN returned a valid-JSON stub on any exception, the failure was silent:
# 8 of 20 days came back with concept_type "Unknown" and every AM.I.B ACS code was
# orphaned while the job reported success. Two guards now exist so that cannot
# repeat quietly:
#
#   * services/digests/build.py preflights the extractor model once per build and
#     fails the whole build with the AWS error if it cannot be invoked;
#   * a block whose days all failed is refused rather than persisted, and a
#     partially-failed one carries a user-visible warning.
#
# To run a different model in a given environment, set DIS_MODEL_TEXT_ALL (or a
# per-step DIS_MODEL_<STEP>) there rather than editing this file.
_TEXT_MODEL = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"


class ModelConfig(BaseModel):
    embedding: str = "amazon.titan-embed-text-v2:0"
    classification: str = _TEXT_MODEL
    metadata_extraction: str = _TEXT_MODEL
    structure_extraction: str = _TEXT_MODEL
    quality_check: str = _TEXT_MODEL
    vision: str = _TEXT_MODEL
    # Per-day digest extraction (MAP) for block-wide CDD/Blueprint. The design pins
    # this to a cheap model (Haiku) so the digest cache is shared across quality
    # tiers (D2) — only the REDUCE model varies by tier, on the app/promptops side.
    # Haiku is not invokable on this account (no Bedrock model access in either
    # region), so this falls back to the shared text model.
    digest_extraction: str = _TEXT_MODEL

class PipelineConfig(BaseModel):
    # For local/dev, keep llm_provider=mock and USE_BEDROCK=false in .env.
    # If USE_BEDROCK=true, Bedrock is used. If USE_BEDROCK=false and ANTHROPIC_API_KEY is set, direct Anthropic is used.
    llm_provider: str = "mock"  # mock | bedrock | anthropic
    bedrock_enabled: bool = False
    anthropic_enabled: bool = False
    models: ModelConfig = ModelConfig()
    llm_temperature: float = 0.0
    chunking_strategy: str = "semantic"
    chunk_size: int = 512
    chunk_overlap: int = 64
    vision_enabled: bool = True
    # Digest MAP fan-out (plan D4/D7). When true, block digest builds run as a
    # LangGraph Send fan-out (per-day checkpoint/resume, bounded concurrency)
    # instead of the sequential loop; falls back to sequential if langgraph is
    # absent. Off by default so the verified sequential path stays the default.
    digest_fanout_enabled: bool = False
    digest_fanout_max_concurrency: int = 5
    # D7 checkpointer DSN for cross-process per-day resume. Blank ⇒ no checkpointer
    # (in-process fan-out only). Production sets this AND installs
    # langgraph-checkpoint-postgres; NEVER point it at the shared prod RDS from a
    # dev container. A blank DSN keeps the seam inert and safe.
    digest_fanout_checkpoint_dsn: str = ""

class MetadataField(BaseModel):
    name: str
    type: str = "string"          # string | enum | list | number | date | boolean
    values: List[str] = []        # for enum type
    hint: str = ""
    applies_to: List[str] = []    # restrict to certain doc_types

class MetadataSchema(BaseModel):
    required_fields: List[MetadataField] = []
    optional_fields: List[MetadataField] = []


class MetadataFrameworkConfig(BaseModel):
    """Phase 0 passthrough for client YAML ``metadata_framework``.

    Intentionally permissive: nested framework structure (fields, taxonomies,
    chunking policies, etc.) is accepted as open content so later phases can
    tighten types without rewriting TenantConfig loading.
    """
    model_config = ConfigDict(extra="allow")


class DocumentTypeRule(BaseModel):
    doc_type: str
    filename_keywords: List[str] = []
    text_keywords: List[str] = []
    regex_patterns: List[str] = []
    priority: int = 100

class StructurePatternConfig(BaseModel):
    day_regex: str = r"\b(?:B\d+D(\d+)|Day\s*(\d+)|D(\d+)|Session\s*(\d+))\b"
    block_regex: str = r"\b(?:Block|BLK)\s*0*(\d+)\b"
    week_regex: str = r"\bWeek\s*(\d+)\b"
    table_schedule_keywords: List[str] = ["day", "lesson", "topic", "project", "quiz", "exam", "assignment"]
    activity_keywords: List[str] = ["project", "activity", "lab", "hangar", "assignment"]
    assignment_keywords: List[str] = ["assignment", "homework", "reading", "worksheet", "prepare"]
    assessment_keywords: List[str] = ["quiz", "exam", "final", "assessment", "test"]

class DocumentProcessingConfig(BaseModel):
    # This is the main client profile switch. No code change is needed when a new
    # client uses different labels/keywords; change config/clients/<client_id>.yaml.
    profile: str = "generic_academic"
    enabled_document_types: List[str] = [
        "course_calendar", "syllabus", "lesson_slide_deck", "project_activity",
        "project_key", "quiz_exam", "quiz_answer_key", "study_questions",
        "ebook_reference", "instructor_guide", "student_handout",
        # Aggregate knowledge-test performance data (e.g. an AKTR missed-code
        # rollup). Not in restricted_document_types below: it holds no questions and
        # no answers, so it is design input, not exam content.
        "knowledge_test_report", "other"
    ]
    # These document types are hidden from normal user context retrieval.
    # Client admins and super admins can still see/retrieve them.
    restricted_document_types: List[str] = ["quiz_answer_key", "project_key"]
    document_type_rules: List[DocumentTypeRule] = []
    structure_patterns: StructurePatternConfig = StructurePatternConfig()
    unit_type_map: Dict[str, str] = {
        "course_calendar": "calendar_day",
        "syllabus": "syllabus_section",
        "lesson_slide_deck": "slide",
        "project_activity": "project_task",
        "project_key": "answer_key_item",
        "quiz_exam": "quiz_question",
        "quiz_answer_key": "answer_key_item",
        "study_questions": "study_question",
        "ebook_reference": "page",
        "instructor_guide": "guide_section",
        "student_handout": "chunk",
        "knowledge_test_report": "knowledge_test_item",
        "other": "chunk",
    }
    fallback_doc_type: str = "other"

class S3Config(BaseModel):
    region: str = "us-east-1"
    kms_key_id: str = ""
    endpoint_url: str = ""        # blank = real AWS

class AzureConfig(BaseModel):
    account_name: str = ""
    account_key_secret: str = "AZURE_STORAGE_KEY"
    container_raw: str = "dis-raw"
    container_processed: str = "dis-processed"

class GCPConfig(BaseModel):
    project_id: str = ""
    credentials_secret: str = "GCP_SERVICE_ACCOUNT_JSON"
    bucket_raw: str = "dis-raw"
    bucket_processed: str = "dis-processed"

class LocalStorageConfig(BaseModel):
    base_path: str = "/tmp/dis_storage"

class StorageConfig(BaseModel):
    provider: str = "s3"         # fixed to s3 for CAS/DIS production integration
    raw_bucket: str = "dis-raw"
    processed_bucket: str = "dis-processed"
    # Optional folder/prefix inside the bucket, e.g. "DIS" for s3://bucket/DIS/...
    base_prefix: str = ""
    vector_index: str = "dis-index"
    retention_days: int = 365
    s3: S3Config = S3Config()
    azure: AzureConfig = AzureConfig()
    gcp: GCPConfig = GCPConfig()
    local: LocalStorageConfig = LocalStorageConfig()

class RetrievalConfig(BaseModel):
    max_results: int = 20
    result_size_cap: int = 50
    # Ceiling on the per-request `retrieval.token_budget` a caller may ask for.
    #
    # This was a magic `20000` inlined in the packing call, which silently overrode
    # any larger request. The caller is the side that knows which model the context
    # is destined for, so a fixed ceiling here can only be wrong: it made a CAS
    # request for 100k arrive as 20k, and the units past that point were dropped by
    # DIS's own ranking BEFORE the caller's block filtering and whole-document
    # regrouping ran — producing a set that looks whole and is not.
    #
    # Kept as a configurable backstop rather than removed so one runaway request
    # cannot pack an unbounded response, but sized so it is not reached in practice.
    token_budget_cap: int = 200_000
    # Restricted content is role-filtered using metadata/access_level.
    restricted_content_filter: bool = True
    # Config-driven UI and retrieval defaults used by Content AI Studio.
    source_ui: Dict[str, Any] = {}
    source_type_mapping: Dict[str, List[str]] = {}
    purpose_labels: Dict[str, str] = {}

class SecurityConfig(BaseModel):
    # Backend uses JWT. mTLS is intentionally not modeled in DIS config.
    require_jwt: bool = True
    jwt_audience: str = "dis-api"


class EmbeddingConfig(BaseModel):
    # Avoid pydantic protected namespace warning for field name model_id.
    model_config = ConfigDict(protected_namespaces=())

    enabled: bool = False
    provider: str = "bedrock"
    model_id: str = "amazon.titan-embed-text-v2:0"
    dimension: int = 1024
    region: str = "us-east-1"
    # See the client YAMLs: Titan v2 accepts 8,192 tokens, and the old 50000
    # exceeded that for long units — which failed the embedding and then indexed
    # the unit with an empty vector rather than failing the job.
    max_input_chars: int = 24000

# ---------------------------------------------------------------------------
# Where the backing stores live
#
# The client YAML is the ONLY source of a store's connection details. For AIM
# that is config/clients/aim.yaml: structure_store.url names the DIS Postgres,
# vector_store.endpoint/index_name name the OpenSearch domain and index. Every
# environment — local, dev and prod — reads the same file and therefore the same
# stores; DIS is one shared corpus, not one per environment.
#
# There used to be a second, silent path: DIS_STRUCTURE_STORE_URL /
# DIS_VECTOR_STORE_ENDPOINT / DIS_VECTOR_STORE_INDEX (plus _<CLIENT> forms) that
# outranked the YAML. It was removed on 2026-08-27, the day it cost an afternoon:
# dev exported DIS_STRUCTURE_STORE_URL pointing at an empty sibling database, so
# every Block-9 digest build died with "No calendar found for block 'Block 9'"
# while the committed YAML — the only file anyone thought to read — named the
# populated store. An override that repoints a database without appearing in any
# tracked file makes the deployment undiagnosable from the repo, and env vars are
# invisible in code review in a way a YAML diff is not.
#
# What the environment still decides (deliberately, and none of it a store
# location): the pipeline's model ids (DIS_MODEL_*, below — model availability is
# a per-region, per-principal fact the YAML cannot settle) and credentials
# (AWS_*/DIS_BEDROCK_*, which must never be committed).
#
# ``${VAR}`` / ``${VAR:-fallback}`` placeholders in the YAML are still expanded,
# because there the YAML itself names the variable: the file stays the record of
# where a store lives, and the secret it needs can live outside git.
# ---------------------------------------------------------------------------

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _resolve_env_placeholders(value: str) -> str:
    """Expand ``${VAR}`` and ``${VAR:-fallback}`` in *value*.

    An unset variable with no fallback expands to "" — the same "unconfigured"
    signal an absent YAML key gives, so ``enabled: true`` with a blank url fails
    loudly at connect time rather than silently connecting somewhere unintended.
    """
    def sub(m: "re.Match[str]") -> str:
        return os.getenv(m.group(1)) or (m.group(2) if m.group(2) is not None else "")
    return _ENV_PLACEHOLDER.sub(sub, value)


def _expand_env_in_tree(node: Any) -> Any:
    """Recursively expand env placeholders in every string in a config tree."""
    if isinstance(node, str):
        return _resolve_env_placeholders(node)
    if isinstance(node, dict):
        return {k: _expand_env_in_tree(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_expand_env_in_tree(v) for v in node]
    return node


# Which Bedrock model each pipeline step uses, overridable per environment.
#
# Model availability is an environment fact, not a code fact: an ID can be
# end-of-life in one region, provider-legacy in another, and require a model-access
# grant the calling role may not hold. Baking one ID into a committed YAML forces
# every environment onto it. When call_llm still returned a valid-JSON stub on
# failure, an unavailable model degraded into complete-looking output with every
# extracted field empty — observed 2026-08-12, where 8 of 20 days produced
# concept_type "Unknown" and orphaned every AM.I.B ACS code. call_llm now raises
# LLMCallFailed instead, so that specific silence is closed; the ID being wrong for
# an environment is still an environment fact this file cannot settle.
#
#   DIS_MODEL_DIGEST_EXTRACTION / DIS_MODEL_DIGEST_EXTRACTION_<CLIENT>
#   DIS_MODEL_CLASSIFICATION, DIS_MODEL_METADATA_EXTRACTION,
#   DIS_MODEL_STRUCTURE_EXTRACTION, DIS_MODEL_QUALITY_CHECK, DIS_MODEL_VISION
#   DIS_MODEL_TEXT_ALL  — sets every text step at once (checked last)
_TEXT_STEPS = ("classification", "metadata_extraction", "structure_extraction",
               "quality_check", "vision", "digest_extraction")


def _client_suffix(client_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", str(client_id or "")).upper()


def _env_for(stem: str, suffix: str) -> str:
    """Per-client variable if set, else the global one. Blank counts as unset."""
    return ((os.getenv(f"{stem}_{suffix}") if suffix else None) or os.getenv(stem) or "").strip()


def _apply_model_env_overrides(converted: Dict[str, Any], client_id: str) -> None:
    """Select each pipeline step's model from the environment, in place.

    Per-step variables win over ``DIS_MODEL_TEXT_ALL``, which exists because the
    common case is "this environment can invoke exactly one text model" and
    repeating it six times invites the six from drifting apart.
    """
    suffix = _client_suffix(client_id)
    pipeline = converted.get("pipeline")
    if not isinstance(pipeline, dict):
        return
    models = pipeline.get("models")
    if not isinstance(models, dict):
        models = {}
        pipeline["models"] = models

    all_text = _env_for("DIS_MODEL_TEXT_ALL", suffix)
    for step in _TEXT_STEPS:
        value = _env_for(f"DIS_MODEL_{step.upper()}", suffix) or all_text
        if value:
            models[step] = value


class StructureStoreConfig(BaseModel):
    # Tenant-specific structured store. Default provider is postgres/RDS.
    # Use this when a client wants a different structure DB later.
    enabled: bool = False
    provider: str = "postgres"  # postgres | dynamodb | mongodb | snowflake | custom_api
    url: str = ""
    schema_name: str = "dis"
    auto_create_schema: bool = True
    # CurriculumProfile selector (plan §5.5 / D8). Picks how the block-wide digest
    # pipeline enumerates coverage units + reads the declared coverage set for this
    # tenant. Blank ⇒ fall back to the client id (AIM resolves to the AIM profile).
    curriculum_profile: str = ""

class VectorStoreConfig(BaseModel):
    # Tenant-specific vector store. Default provider is OpenSearch.
    # Add provider adapters in services/adapters/vector_store.py.
    enabled: bool = False
    provider: str = "opensearch"  # opensearch | pinecone | qdrant | weaviate | custom_api
    endpoint: str = ""
    index_name: str = "dis-content-dev"
    region: str = "us-east-1"
    auth_mode: str = "aws_iam"  # aws_iam | basic | api_key
    username: str = ""
    password: str = ""
    api_key_secret: str = ""
    auto_create_index: bool = True


class DeduplicationConfig(BaseModel):
    # Client-level duplicate index. For POC this is one JSON per client/env.
    enabled: bool = True
    mode: str = "client_manifest_json"
    hash_algorithm: str = "sha256"
    manifest_s3_key: str = ""
    manifest_backup_prefix: str = ""
    duplicate_policy: str = "mark_duplicate_and_skip_processing"
    allow_retry_if_previous_failed: bool = False

class MonitoringConfig(BaseModel):
    slo_ingestion_p95_ms: int = 10000
    slo_retrieval_p95_ms: int = 500


# ── Tenant Config ─────────────────────────────────────────────────────────────

class TenantConfig(BaseModel):
    # Free-form client-specific rules. Example: AIM file classification, version, and visibility rules.
    # This keeps client behavior config-driven without adding new Pydantic models every time.
    client_rules: Dict[str, Any] = {}

    tenant_id: str
    display_name: str
    namespace: str
    is_active: bool = True
    # v8 simplification: one client/workspace per tenant by default.
    # If client_id is omitted, DIS uses default_client_id; if that is omitted, tenant_id.
    default_client_id: str = ""
    users: List[UserConfig] = []
    clients: List[ClientConfig] = []
    ingestion: IngestionConfig = IngestionConfig()
    processing: ProcessingConfig = ProcessingConfig()
    pipeline: PipelineConfig = PipelineConfig()
    document_processing: DocumentProcessingConfig = DocumentProcessingConfig()
    metadata_schemas: Dict[str, MetadataSchema] = {}
    # Phase 0: client YAML `metadata_framework` survives load for later phases.
    metadata_framework: MetadataFrameworkConfig = Field(default_factory=MetadataFrameworkConfig)
    storage: StorageConfig = StorageConfig()
    retrieval: RetrievalConfig = RetrievalConfig()
    security: SecurityConfig = SecurityConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    # Provider-based stores. These are the only DB/vector config sections.
    structure_store: StructureStoreConfig = StructureStoreConfig()
    vector_store: VectorStoreConfig = VectorStoreConfig()
    deduplication: DeduplicationConfig = DeduplicationConfig()
    monitoring: MonitoringConfig = MonitoringConfig()

    def effective_client_id(self, client_id: str = "") -> str:
        return client_id or self.default_client_id or self.tenant_id

    def get_client(self, client_id: str = "") -> Optional[ClientConfig]:
        cid = self.effective_client_id(client_id)
        client = next((c for c in self.clients if c.client_id == cid), None)
        if client:
            return client
        # Backward-safe one-client mode: if clients are not configured, create a virtual tenant client.
        if not self.clients and cid == self.effective_client_id(""):
            return ClientConfig(client_id=cid, display_name=self.display_name, namespace_prefix=cid)
        return None

    def get_namespace(self, client_id: str = "") -> str:
        client = self.get_client(client_id)
        prefix = client.namespace_prefix if client else self.effective_client_id(client_id)
        return f"{self.namespace}/{prefix}" if prefix else self.namespace

    def get_metadata_schema(self, client_id: str = "") -> MetadataSchema:
        cid = self.effective_client_id(client_id)
        return self.metadata_schemas.get(cid, MetadataSchema())

    def get_user(self, user_id: str) -> Optional[UserConfig]:
        return next((u for u in self.users if u.user_id.lower() == user_id.lower() and u.enabled), None)

    def get_user_role(self, user_id: str) -> str:
        u = self.get_user(user_id)
        return u.role if u else ""

    def is_admin(self, client_id: str, user_id: str) -> bool:
        role = self.get_user_role(user_id)
        return role in ("super_admin", "client_admin")


# ── Global Settings ───────────────────────────────────────────────────────────

class GlobalSettings(BaseSettings):
    app_name: str = "DIS Ingestion System"
    app_version: str = "2.0.0"
    environment: str = "development"
    debug: bool = False
    api_prefix: str = "/v1"
    allowed_origins: List[str] = ["*"]

    # Auth
    jwt_secret: str = "CHANGE_ME_IN_PRODUCTION"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 480       # 8 hours

    # AWS (used when storage provider = s3 or for Bedrock)
    # Accepts AWS_REGION *or* AWS_DEFAULT_REGION. Previously this read only
    # AWS_REGION, while the CAS side reads AWS_DEFAULT_REGION (promptops_app/core/
    # config.py) — so a .env setting only AWS_DEFAULT_REGION=ap-south-1 left DIS
    # silently on the us-east-1 default, where the Bedrock text models are not
    # provisioned (`anthropic.claude-3-sonnet-20240229-v1:0` →
    # ResourceNotFoundException). Both services must resolve the same region from one
    # variable; AWS_REGION still wins when both are set, matching the AWS SDK's own
    # precedence.
    aws_region: str = Field(
        default="us-east-1",
        validation_alias=AliasChoices("AWS_REGION", "AWS_DEFAULT_REGION"),
    )
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    # Required when the configured credentials are temporary STS credentials — the
    # key/secret alone are rejected without it. There was no field for this at all,
    # so a .env supplying AWS_SESSION_TOKEN was silently ignored and every signed
    # request failed authentication.
    aws_session_token: Optional[str] = None
    aws_endpoint_url: str = ""          # LocalStack override

    # ── Bedrock-only credentials (optional) ──────────────────────────────────
    # Model access is granted per IAM principal, and the principal that can invoke
    # the models is not necessarily the one that owns the storage. Measured on
    # 2026-08-12: `promptops-contentAI-Dev` (account 498628474556) invokes Sonnet 5,
    # Opus 5, Haiku 4.5, Sonnet 4.6 and Sonnet 4.5 — 3/3 in both regions — while
    # `nandkishor-ai-project-access` (account 410453487786), which owns DIS's S3
    # bucket and OpenSearch domain, can only invoke Sonnet 4.5.
    #
    # DIS otherwise uses ONE credential set for Bedrock, S3 and OpenSearch, so
    # swapping AWS_* wholesale would buy model access at the cost of the digest store
    # — trading a model problem for a storage problem. These let the model calls use
    # one principal while storage keeps the other, mirroring what CAS already does
    # for the shared bucket via DIS_S3_ACCESS_KEY_ID.
    #
    # ALL OPTIONAL: blank ⇒ fall back to the AWS_* pair above, which is exactly
    # today's behaviour. Set them in dis_backend/.env, copying the values from the
    # root .env (the container cannot read that file itself — its compose env_file is
    # dis_backend/.env, and adding the root file there would override the storage
    # credentials too).
    bedrock_access_key_id: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("DIS_BEDROCK_ACCESS_KEY_ID"))
    bedrock_secret_access_key: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("DIS_BEDROCK_SECRET_ACCESS_KEY"))
    bedrock_session_token: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("DIS_BEDROCK_SESSION_TOKEN"))
    bedrock_region: str = Field(
        default="", validation_alias=AliasChoices("DIS_BEDROCK_REGION"))

    # Bedrock / Anthropic
    use_bedrock: bool = True            # True = Bedrock, False = direct Anthropic API
    anthropic_api_key: Optional[str] = None

    def bedrock_client_kwargs(self) -> Dict[str, Any]:
        """boto3 kwargs for a bedrock-runtime client.

        Prefers the Bedrock-only credentials when configured, else the shared AWS_*
        pair. Credentials are passed explicitly rather than left to boto3's ambient
        chain: this deployment has no instance role, and an implicit fallback there
        raised NoCredentialsError inside call_llm, which at the time swallowed it and
        returned a valid-JSON stub — a whole block of empty digests reported as
        success. call_llm raises LLMCallFailed now, but passing credentials explicitly
        is still what keeps that error from happening at all.
        """
        key = (self.bedrock_access_key_id or "").strip() or self.aws_access_key_id
        secret = ((self.bedrock_secret_access_key or "").strip()
                  or self.aws_secret_access_key)
        token = ((self.bedrock_session_token or "").strip()
                 # Only pair the shared token with the shared key: a token belonging
                 # to a different principal than the key is rejected outright.
                 or (self.aws_session_token if not (self.bedrock_access_key_id or "").strip() else None))
        kwargs: Dict[str, Any] = {
            "region_name": (self.bedrock_region or "").strip() or self.aws_region,
        }
        if self.aws_endpoint_url:
            kwargs["endpoint_url"] = self.aws_endpoint_url
        if key:
            kwargs["aws_access_key_id"] = key
            kwargs["aws_secret_access_key"] = secret
            if token:
                kwargs["aws_session_token"] = token
        return kwargs

    # OpenSearch
    opensearch_endpoint: str = "http://localhost:9200"
    opensearch_username: str = "admin"
    opensearch_password: str = "admin"

    # Database (PostgreSQL)
    db_url: str = "postgresql+asyncpg://dis:dis@localhost:5432/dis"

    # Redis / DynamoDB for token tracking
    use_dynamodb: bool = False          # False = in-memory (dev), True = DynamoDB
    dynamodb_endpoint: str = ""         # blank = real AWS

    # Studio / service-to-service auth
    studio_api_key: Optional[str] = None
    dis_service_token: Optional[str] = "dev-dis-token"

    # Config
    # v9: super admin/global auth is in config/platform.yaml.
    # each client/workspace is in config/clients/<client_id>.yaml.
    platform_config_path: str = str(Path(__file__).parent / "platform.yaml")
    client_config_dir: str = str(Path(__file__).parent / "clients")
    # Backward compatibility only; ignored when config/clients/*.yaml exists.
    tenant_config_dir: str = str(Path(__file__).parent / "tenants")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


# ── Tenant Registry ───────────────────────────────────────────────────────────

class TenantRegistry:
    """Client registry.

    v9 naming: one client/workspace = one internal tenant config.
    Super admins live in platform.yaml; client admins/users live in config/clients/<client_id>.yaml.
    Old config/tenants/tenant_*.yaml is still supported only if config/clients is empty.
    """
    def __init__(self, config_dir: str):
        settings = get_settings()
        self._tenants: Dict[str, TenantConfig] = {}
        self._namespaces: Dict[str, str] = {}
        self.platform: PlatformConfig = self._load_platform(settings.platform_config_path)
        self._load_all(settings.client_config_dir, config_dir)

    def _load_platform(self, path: str) -> PlatformConfig:
        p = Path(path)
        if not p.exists():
            return PlatformConfig()
        with p.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        # accept either {platform:{...}, auth:{super_admins:[...]}} or flat
        platform_raw = raw.get("platform", {}) if isinstance(raw, dict) else {}
        auth_raw = raw.get("auth", {}) if isinstance(raw, dict) else {}
        merged = {
            "name": platform_raw.get("name", raw.get("name", "DIS Platform")),
            "environment": platform_raw.get("environment", raw.get("environment", "development")),
            "demo_secret": auth_raw.get("demo_secret", raw.get("demo_secret", "demo_secret")),
            "super_admins": auth_raw.get("super_admins", raw.get("super_admins", [])),
        }
        return PlatformConfig(**merged)

    def _client_raw_to_tenant(self, raw: Dict[str, Any]) -> TenantConfig:
        client_meta = raw.get("client", {})
        if not client_meta:
            # Already in old TenantConfig shape
            return TenantConfig(**raw)
        client_id = client_meta.get("client_id") or raw.get("client_id")
        if not client_id:
            raise ValueError("Client config must contain client.client_id")
        display_name = client_meta.get("client_name") or client_meta.get("display_name") or client_id
        namespace = client_meta.get("namespace") or f"{client_id}_ns"
        auth_raw = raw.get("auth", {})
        users: List[Dict[str, Any]] = []
        for u in auth_raw.get("client_admins", []):
            users.append({"user_id": u["user_id"], "name": u.get("name", ""), "role": "client_admin", "enabled": u.get("enabled", True)})
        for u in auth_raw.get("users", []):
            users.append({"user_id": u["user_id"], "name": u.get("name", ""), "role": "user", "enabled": u.get("enabled", True)})
        client_obj = {
            "client_id": client_id,
            "display_name": display_name,
            "allowed_file_types": client_meta.get("allowed_file_types", ["pdf", "docx", "doc", "pptx", "xlsx", "csv", "txt", "jpg", "jpeg", "png", "zip"]),
            "max_file_size_mb": client_meta.get("max_file_size_mb", 250),
            "namespace_prefix": client_meta.get("namespace_prefix", client_id),
            "restricted_content": client_meta.get("restricted_content", False),
            "admins": auth_raw.get("client_admins", []),
        }
        converted = {
            "tenant_id": client_id,
            "display_name": display_name,
            "namespace": namespace,
            "is_active": client_meta.get("enabled", True),
            "default_client_id": client_id,
            "users": users,
            "clients": [client_obj],
        }
        for key in ["ingestion", "processing", "pipeline", "document_processing", "metadata_schemas", "metadata_framework", "storage", "retrieval", "security", "embedding", "structure_store", "vector_store", "deduplication", "monitoring", "client_rules"]:
            if key in raw:
                converted[key] = raw[key]

        # Placeholders only: the store locations themselves come from the YAML and
        # nothing in the environment may repoint them. See the header comment above
        # "Where the backing stores live".
        converted = _expand_env_in_tree(converted)
        _apply_model_env_overrides(converted, client_id)

        # Client-specific rule blocks stay available at runtime through cfg.client_rules.
        # Example: config/clients/aim.yaml -> aim_content_rules.
        if "client_rules" not in converted:
            converted["client_rules"] = {}
        for rule_key in ("aim_content_rules", "content_ingestion_rules", "blueprint_rules", "course_generation_rules"):
            if rule_key in raw:
                converted["client_rules"][rule_key] = raw[rule_key]

        # If metadata schema is stored under 'default', move it to client id for old helpers.
        if "metadata_schemas" in converted and isinstance(converted["metadata_schemas"], dict):
            schemas = converted["metadata_schemas"]
            if "default" in schemas and client_id not in schemas:
                schemas[client_id] = schemas["default"]
        tenant = TenantConfig(**converted)

        # Final product rule: DIS stores all raw files, processed payloads,
        # clean source content, and source index JSON in S3 only. Local storage
        # is intentionally not used for Source Library persistence.
        tenant.storage.provider = "s3"

        # One bucket can be used for both raw and processed objects, or separate
        # buckets can be supplied. DIS_S3_BUCKET is the common/simple option.
        common_bucket = os.environ.get("DIS_S3_BUCKET", "").strip()
        raw_bucket = os.environ.get("DIS_RAW_BUCKET", "").strip()
        processed_bucket = os.environ.get("DIS_PROCESSED_BUCKET", "").strip()
        if common_bucket:
            tenant.storage.raw_bucket = common_bucket
            tenant.storage.processed_bucket = common_bucket
        if raw_bucket:
            tenant.storage.raw_bucket = raw_bucket
        if processed_bucket:
            tenant.storage.processed_bucket = processed_bucket

        base_prefix = os.environ.get("DIS_S3_BASE_PREFIX", "").strip()
        if base_prefix:
            tenant.storage.base_prefix = base_prefix

        # DIS-specific only. AWS_REGION is deliberately NOT consulted here.
        #
        # A bucket's region is a property of the bucket, not of the process's
        # default AWS region, and AWS_REGION is set to wherever Bedrock runs
        # (ap-south-1 in this deployment). Reading it here silently overrode the
        # explicit, correct `region: us-east-1` in every client YAML, so DIS
        # addressed a us-east-1 bucket through the ap-south-1 endpoint. S3
        # answered with a redirect to the regional us-east-1 host, and where that
        # host is unreachable the result was EndpointConnectionError after
        # botocore's retries — surfacing in CAS as a source-library lookup that
        # simply never returned.
        #
        # Every neighbouring override above and below uses a DIS_ prefix for
        # exactly this reason: a generic SDK variable must not reach in and
        # reconfigure per-tenant storage. DIS_AWS_REGION is kept for
        # compatibility; DIS_S3_REGION is the accurate name for what it sets.
        region = os.environ.get("DIS_S3_REGION", os.environ.get("DIS_AWS_REGION", "")).strip()
        if region:
            tenant.storage.s3.region = region

        endpoint_url = os.environ.get("DIS_S3_ENDPOINT_URL", "").strip()
        if endpoint_url:
            tenant.storage.s3.endpoint_url = endpoint_url

        kms_key = os.environ.get("DIS_S3_KMS_KEY_ID", "").strip()
        if kms_key:
            tenant.storage.s3.kms_key_id = kms_key

        return tenant

    def _load_all(self, client_config_dir: str, legacy_tenant_dir: str) -> None:
        client_paths = sorted(glob.glob(os.path.join(client_config_dir, "*.yaml")))
        if client_paths:
            for path in client_paths:
                with open(path, encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                tenant = self._client_raw_to_tenant(raw)
                self._register(tenant)
            return

        # Legacy fallback.
        for path in glob.glob(os.path.join(legacy_tenant_dir, "tenant_*.yaml")):
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            tenant = TenantConfig(**raw)
            self._register(tenant)

    def _register(self, tenant: TenantConfig) -> None:
        existing = self._namespaces.get(tenant.namespace)
        if existing and existing != tenant.tenant_id:
            raise ValueError(f"Namespace collision: '{tenant.namespace}' already owned by '{existing}'")
        self._tenants[tenant.tenant_id] = tenant
        self._namespaces[tenant.namespace] = tenant.tenant_id

    def get(self, tenant_id: str) -> TenantConfig:
        cfg = self._tenants.get(tenant_id)
        if not cfg:
            raise KeyError(f"Unknown client: {tenant_id}")
        if not cfg.is_active:
            raise PermissionError(f"Client '{tenant_id}' is inactive")
        return cfg

    def first_active_client_id(self) -> str:
        for t in self._tenants.values():
            if t.is_active:
                return t.tenant_id
        raise KeyError("No active clients configured")

    def all_tenants(self) -> List[TenantConfig]:
        return list(self._tenants.values())

    def reload(self, config_dir: str) -> None:
        settings = get_settings()
        self._tenants.clear()
        self._namespaces.clear()
        self.platform = self._load_platform(settings.platform_config_path)
        self._load_all(settings.client_config_dir, settings.tenant_config_dir)


# ── Singletons ────────────────────────────────────────────────────────────────

@lru_cache()
def get_settings() -> GlobalSettings:
    return GlobalSettings()

_registry: Optional[TenantRegistry] = None

def get_tenant_registry() -> TenantRegistry:
    global _registry
    if _registry is None:
        _registry = TenantRegistry(get_settings().tenant_config_dir)
    return _registry

def get_platform_config() -> PlatformConfig:
    return get_tenant_registry().platform

def get_tenant_config(tenant_id: str) -> TenantConfig:
    return get_tenant_registry().get(tenant_id)
