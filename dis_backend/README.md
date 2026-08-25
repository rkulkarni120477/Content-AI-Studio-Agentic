# DIS Ingestion System - Agent Based Final

This version uses one agent per pipeline step and a config-driven tenant/client model.

## Important design decisions

How: add one line to dis_backend/.env:
  DIS_MAX_CONCURRENT_PIPELINES=3

- API title: `DIS Ingestion System`
- Pipeline orchestration: `services/pipeline/graph.py`
- Agent registry/order: `services/agents/registry.py`
- One step = one agent file in `services/agents/`
- Client-specific extraction rules live in `config/clients/<client_id>.yaml`
- Structured DB is configured only through `structure_store`
- Vector DB is configured only through `vector_store`
- `database`, `opensearch`, `studio_integration`, `mtls_enabled`, and license-filter config were removed to avoid confusion.

## Current safe test mode

```yaml
pipeline:
  llm_provider: mock
  bedrock_enabled: false
  anthropic_enabled: false
  vision_enabled: false

embedding:
  enabled: false

structure_store:
  enabled: true
  provider: postgres
  url: "postgresql://dis_user:<PASSWORD>@<RDS_ENDPOINT>:5432/dis_db"
  schema_name: dis
  auto_create_schema: true

vector_store:
  enabled: false
```

## Bedrock without embeddings

Yes, you can use Bedrock for classification/metadata/structure/quality and still keep `embedding.enabled=false`. In that mode DIS uses LLMs for extraction steps but skips vector creation and vector DB indexing.

## Restricted content

Use `document_processing.restricted_document_types` to hide answer keys/project keys from normal users. Client admins and super admins can still retrieve them.

## Deduplication

When `ingestion.dedup_enabled=true`, DIS stores a hash manifest under `_dedup/file_hashes/<sha>.json`. If the same file is uploaded again, downstream heavy agents are skipped and the job is marked duplicate.


## Latest API cleanup

Removed from DIS API/config:

- `POST /v1/ingest/jobs/{job_id}/retry`
- `POST /v1/context/units`
- `/v1/admin/tokens/{user_id}` and `token_limits` config

Reason: DIS is an ingestion/context system. It should ingest, structure, store, and retrieve context. Token budget and generation controls belong to Content AI Studio. Retry can be handled later through explicit re-upload/re-scan or a proper queue worker. Studio should use `GET /v1/context/sources`, `GET /v1/context/sources/{job_id}/structure`, and `POST /v1/context/retrieve`.
