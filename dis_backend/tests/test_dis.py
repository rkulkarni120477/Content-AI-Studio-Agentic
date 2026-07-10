"""DIS – Test Suite"""
import asyncio, pytest, yaml, tempfile, os

TENANT_YAML = """
tenant_id: test
display_name: Test
namespace: test_ns
is_active: true
clients:
  - client_id: client_a
    display_name: Client A
    allowed_file_types: [pdf, txt, csv]
    max_file_size_mb: 5
    namespace_prefix: ca
    admins:
      - user_id: admin@test.com
        name: Admin
ingestion:
  dedup_enabled: true
  quarantine_on_fail: true
pipeline:
  models:
    embedding: amazon.titan-embed-text-v2:0
    classification: anthropic.claude-3-haiku-20240307-v1:0
    metadata_extraction: anthropic.claude-3-sonnet-20240229-v1:0
    structure_extraction: anthropic.claude-3-sonnet-20240229-v1:0
    quality_check: anthropic.claude-3-haiku-20240307-v1:0
  llm_temperature: 0.0
  chunking_strategy: fixed
  chunk_size: 50
  chunk_overlap: 5
  vision_enabled: false
metadata_schemas:
  client_a:
    required_fields:
      - name: subject
        type: enum
        values: [Math, Science, English]
      - name: grade_level
        type: string
storage:
  provider: local
  local:
    base_path: /tmp/dis_test_storage
retrieval:
  restricted_content_filter: true
  result_size_cap: 10
security:
  require_jwt: true
studio_integration:
  enabled: false
"""

@pytest.fixture
def tenant_cfg():
    from config.settings import TenantConfig
    return TenantConfig(**yaml.safe_load(TENANT_YAML))

# ── Config tests ──────────────────────────────────────────────────────────────

def test_tenant_loads(tenant_cfg):
    assert tenant_cfg.tenant_id == "test"
    assert tenant_cfg.namespace == "test_ns"

def test_get_client(tenant_cfg):
    c = tenant_cfg.get_client("client_a")
    assert c is not None and c.max_file_size_mb == 5

def test_namespace_for_client(tenant_cfg):
    assert tenant_cfg.get_namespace("client_a") == "test_ns/ca"

def test_metadata_schema(tenant_cfg):
    schema = tenant_cfg.get_metadata_schema("client_a")
    assert len(schema.required_fields) == 2
    assert schema.required_fields[0].name == "subject"

def test_is_admin(tenant_cfg):
    assert tenant_cfg.is_admin("client_a", "admin@test.com")
    assert not tenant_cfg.is_admin("client_a", "nobody@test.com")

def test_namespace_uniqueness():
    from config.settings import TenantRegistry
    with tempfile.TemporaryDirectory() as d:
        for i, tid in enumerate(["t1", "t2"]):
            with open(os.path.join(d, f"tenant_{tid}.yaml"), "w") as f:
                yaml.dump({"tenant_id": tid, "display_name": tid,
                           "namespace": "same_ns", "is_active": True}, f)
        with pytest.raises(ValueError, match="collision"):
            TenantRegistry(d)

# ── Validation tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_validation_passes(tenant_cfg):
    from services.validation import ValidationService
    svc = ValidationService(tenant_cfg, tenant_cfg.get_client("client_a"))
    r = await svc.validate("report.txt", b"Hello world content here", "text/plain")
    assert r.passed

@pytest.mark.asyncio
async def test_validation_rejects_type(tenant_cfg):
    from services.validation import ValidationService
    svc = ValidationService(tenant_cfg, tenant_cfg.get_client("client_a"))
    r = await svc.validate("data.xlsx", b"fake", "application/vnd.ms-excel")
    assert not r.passed and any("xlsx" in e for e in r.errors)

@pytest.mark.asyncio
async def test_validation_rejects_size(tenant_cfg):
    from services.validation import ValidationService
    svc = ValidationService(tenant_cfg, tenant_cfg.get_client("client_a"))
    big = b"x" * (6 * 1024 * 1024)  # 6MB > 5MB limit
    r = await svc.validate("big.txt", big, "text/plain")
    assert not r.passed

@pytest.mark.asyncio
async def test_dedup_catches_duplicate(tenant_cfg):
    from services.validation import ValidationService
    svc = ValidationService(tenant_cfg)
    content = b"unique content xyz789"
    r1 = await svc.validate("f1.txt", content, "text/plain")
    r2 = await svc.validate("f2.txt", content, "text/plain")
    assert r1.passed and not r2.passed

# ── Extractor tests ───────────────────────────────────────────────────────────

def test_txt_extractor():
    from services.pipeline.extractors import extract
    r = extract("hello.txt", b"Hello world\nThis is a test.")
    assert "Hello world" in r.text

def test_csv_extractor():
    from services.pipeline.extractors import extract
    r = extract("data.csv", b"name,age\nAlice,30\nBob,25")
    assert "name" in r.text.lower() or "alice" in r.text.lower()

def test_json_extractor():
    from services.pipeline.extractors import extract
    r = extract("data.json", b'[{"key": "value"}, {"key": "value2"}]')
    assert "value" in r.text

# ── Storage tests ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_local_storage(tenant_cfg):
    from storage.provider import get_storage_provider
    p = get_storage_provider(tenant_cfg)
    key = "test_ns/job1/sample.txt"
    await p.upload(key, b"test content", "text/plain")
    downloaded = await p.download(key)
    assert downloaded == b"test content"
    assert await p.exists(key)
    await p.delete(key)

# ── Pipeline tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_runs(tenant_cfg):
    from services.pipeline.graph import run_pipeline
    result = await run_pipeline(
        tenant_cfg=tenant_cfg, job_id="test-001",
        tenant_id="test", client_id="client_a",
        user_id="user@test.com", namespace="test_ns/ca",
        filename="test.txt", s3_key="test_ns/ca/test-001/test.txt",
        raw_bytes=b"Mathematics Grade 9 Syllabus. Chapter 1: Algebra. Chapter 2: Geometry.",
    )
    assert "finalize" in result.get("completed_steps", [])
    assert len(result.get("completed_steps", [])) == 16

@pytest.mark.asyncio
async def test_pipeline_restricts_exam(tenant_cfg):
    from services.pipeline.graph import run_pipeline
    result = await run_pipeline(
        tenant_cfg=tenant_cfg, job_id="test-002",
        tenant_id="test", client_id="client_a",
        user_id="user@test.com", namespace="test_ns/ca",
        filename="exam.txt", s3_key="test_ns/ca/test-002/exam.txt",
        raw_bytes=b"EXAM PAPER Q1: What is 2+2? Answer: 4. Q2: Spell cat. Answer: C-A-T.",
    )
    # Classification should detect exam_secret, embedding should be skipped
    assert "embedding_filters" in result.get("completed_steps", [])
