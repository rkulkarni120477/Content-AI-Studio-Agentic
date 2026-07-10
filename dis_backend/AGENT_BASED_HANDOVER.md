# DIS Agent-Based Handover Guide

## High-level architecture

DIS ingests documents, extracts text, classifies document type, extracts academic structure, creates content units, stores artifacts, optionally writes to a structured store, optionally creates embeddings, and optionally indexes in a vector store.

## Agent flow

The order is defined in `services/agents/registry.py` and executed from `services/pipeline/graph.py`.

1. Source Discovery Agent
2. Upload Intake Agent
3. File Type Classification Agent
4. Raw Storage Agent
5. Deduplication Agent
6. Extractor Selection Agent
7. Content Extraction Agent
8. Visual Understanding Agent
9. Content Classification Agent
10. Metadata Extraction Agent
11. Metadata Tagging Agent
12. Structure Extraction Agent
13. Specialized Structure Extraction Agent
14. Content Unit Creation Agent
15. Quality Check Agent
16. Studio Payload Preparation Agent
17. Processed Storage Agent
18. Structure Store Upsert Agent
19. Embedding Generation Agent
20. Vector Store Upsert Agent
21. Validation Report Agent
22. Checkpoint Save Agent
23. Finalize Agent

## Provider model

Use only these sections for DB/vector providers:

```yaml
structure_store:
  enabled: true
  provider: postgres

vector_store:
  enabled: true
  provider: opensearch
```

If another client wants Pinecone/Qdrant/DynamoDB/etc., add an adapter in `services/adapters/` and update config. API and pipeline stay the same.

## LLM model usage

- `pipeline.llm_provider=mock`: rule-based extraction, no LLM call.
- `pipeline.llm_provider=bedrock` and `pipeline.bedrock_enabled=true`: classification, metadata extraction, structure extraction, and quality check can call Bedrock models.
- `embedding.enabled=false`: only embedding generation is skipped; other LLM steps can still run.

## Context APIs

Content AI Studio calls DIS:

- `GET /v1/context/sources`
- `POST /v1/context/retrieve`
- `POST /v1/context/units`

DIS does not push to Studio in this backend version.


## Latest API cleanup

Removed from DIS API/config:

- `POST /v1/ingest/jobs/{job_id}/retry`
- `POST /v1/context/units`
- `/v1/admin/tokens/{user_id}` and `token_limits` config

Reason: DIS is an ingestion/context system. It should ingest, structure, store, and retrieve context. Token budget and generation controls belong to Content AI Studio. Retry can be handled later through explicit re-upload/re-scan or a proper queue worker. Studio should use `GET /v1/context/sources`, `GET /v1/context/sources/{job_id}/structure`, and `POST /v1/context/retrieve`.
