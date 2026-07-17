"""Optional RDS/OpenSearch/embedding integration for DIS.

All steps are config-gated. When disabled, the pipeline writes a JSON artifact
with status=skipped so local/S3-first testing remains simple.
"""
from __future__ import annotations
import json
import logging
from typing import Any, Dict, List, Optional

from config.settings import TenantConfig, get_settings

log = logging.getLogger(__name__)

# Dynamic client fields are stored in metadata_json (RDS) and metadata.* (OpenSearch).
# This avoids creating different physical tables/indexes per client.
# For query performance, RDS gets a JSONB GIN index and OpenSearch gets dynamic_templates.


def rds_upsert(tenant_cfg: TenantConfig, state: Dict[str, Any]) -> Dict[str, Any]:
    cfg = tenant_cfg.structure_store
    if not cfg.enabled:
        return {"status": "skipped", "reason": "structure_store.enabled=false"}
    try:
        import psycopg
    except Exception as exc:
        return {"status": "failed", "error": f"psycopg not installed: {exc}"}

    try:
        if getattr(cfg, "provider", "postgres") != "postgres":
            return {"status": "skipped", "reason": f"Unsupported structure store provider: {cfg.provider}. Add adapter in services/adapters/structure_store.py"}
        dsn = cfg.url or get_settings().db_url.replace("postgresql+asyncpg://", "postgresql://")
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                ensure_schema(cur, cfg.schema_name)
                upsert_job(cur, cfg.schema_name, state)
                document_id = upsert_document(cur, cfg.schema_name, state)
                unit_count = upsert_content_units(cur, cfg.schema_name, document_id, state)
                if state.get("calendar_structure"):
                    calendar_days = upsert_calendar(cur, cfg.schema_name, document_id, state)
                else:
                    calendar_days = 0
                if state.get("syllabus_structure"):
                    syllabus_rows = upsert_syllabus(cur, cfg.schema_name, document_id, state)
                else:
                    syllabus_rows = 0
                conn.commit()
        return {"status": "completed", "document_id": document_id, "content_units_inserted": unit_count, "calendar_days_inserted": calendar_days, "syllabus_rows_inserted": syllabus_rows}
    except Exception as exc:
        log.exception("RDS upsert failed")
        return {"status": "failed", "error": str(exc)}


def ensure_schema(cur, schema: str):
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS {schema}.dis_jobs (
        job_id text PRIMARY KEY, tenant_id text, client_id text, status text,
        source_file_name text, raw_storage_url text, payload_storage_url text,
        validation_report_url text, metadata_json jsonb, created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now()
    )""")
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS {schema}.dis_documents (
        document_id text PRIMARY KEY, job_id text, tenant_id text, client_id text,
        document_title text, document_type text, source_file_name text, source_file_type text,
        source_relative_path text, raw_storage_url text, payload_storage_url text,
        metadata_json jsonb, created_at timestamptz DEFAULT now()
    )""")
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS {schema}.dis_content_units (
        content_unit_id text PRIMARY KEY, document_id text, job_id text, tenant_id text, client_id text,
        unit_type text, unit_number int, title text, text_content text, visual_summary text,
        keywords_json jsonb, topics_json jsonb, metadata_json jsonb, asset_urls_json jsonb, content_hash text,
        created_at timestamptz DEFAULT now()
    )""")
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS {schema}.dis_course_calendars (
        calendar_id text PRIMARY KEY, document_id text, job_id text, tenant_id text, client_id text,
        course_name text, block text, total_days int, structure_json jsonb, created_at timestamptz DEFAULT now()
    )""")
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS {schema}.dis_calendar_days (
        calendar_day_id text PRIMARY KEY, calendar_id text, document_id text, job_id text, tenant_id text, client_id text,
        block text, day_number int, week_number int, topic text, lesson_title text,
        activities_json jsonb, assignments_json jsonb, assessments_json jsonb, source_text text, source_location text,
        created_at timestamptz DEFAULT now()
    )""")
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS {schema}.dis_syllabus (
        syllabus_id text PRIMARY KEY, document_id text, job_id text, tenant_id text, client_id text,
        course_name text, block text, course_description text, learning_outcomes_json jsonb,
        materials_json jsonb, grading_policy text, attendance_policy text, assessment_policy text,
        structure_json jsonb, created_at timestamptz DEFAULT now()
    )""")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_dis_units_tenant_client ON {schema}.dis_content_units(tenant_id, client_id)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_dis_documents_tenant_type ON {schema}.dis_documents(tenant_id, client_id, document_type)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_dis_documents_metadata_gin ON {schema}.dis_documents USING GIN(metadata_json)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_dis_units_metadata_gin ON {schema}.dis_content_units USING GIN(metadata_json)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_dis_calendar_days ON {schema}.dis_calendar_days(tenant_id, client_id, block, day_number)")


def upsert_job(cur, schema: str, state: Dict[str, Any]):
    payload_url = state.get("artifact_urls", {}).get("studio_payload", "")
    validation_url = state.get("artifact_urls", {}).get("validation_report", "")
    cur.execute(f"""
        INSERT INTO {schema}.dis_jobs(job_id, tenant_id, client_id, status, source_file_name, raw_storage_url, payload_storage_url, validation_report_url, metadata_json, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,now())
        ON CONFLICT(job_id) DO UPDATE SET status=EXCLUDED.status, payload_storage_url=EXCLUDED.payload_storage_url,
        validation_report_url=EXCLUDED.validation_report_url, metadata_json=EXCLUDED.metadata_json, updated_at=now()
    """, (state.get("job_id"), state.get("tenant_id"), state.get("client_id"), "completed", state.get("filename"), state.get("raw_storage_url"), payload_url, validation_url, json.dumps(state.get("doc_metadata", {}))))


def upsert_document(cur, schema: str, state: Dict[str, Any]) -> str:
    doc_id = f"doc_{state.get('job_id')}"
    meta = state.get("doc_metadata", {})
    cur.execute(f"""
        INSERT INTO {schema}.dis_documents(document_id, job_id, tenant_id, client_id, document_title, document_type, source_file_name, source_file_type, source_relative_path, raw_storage_url, payload_storage_url, metadata_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
        ON CONFLICT(document_id) DO UPDATE SET metadata_json=EXCLUDED.metadata_json, payload_storage_url=EXCLUDED.payload_storage_url
    """, (doc_id, state.get("job_id"), state.get("tenant_id"), state.get("client_id"), meta.get("title") or state.get("filename"), state.get("doc_type"), state.get("filename"), state.get("file_type"), state.get("source_relative_path"), state.get("raw_storage_url"), state.get("artifact_urls", {}).get("studio_payload", ""), json.dumps(meta)))
    return doc_id


def upsert_content_units(cur, schema: str, document_id: str, state: Dict[str, Any]) -> int:
    count = 0
    for unit in state.get("content_units", []) or []:
        cur.execute(f"""
            INSERT INTO {schema}.dis_content_units(content_unit_id, document_id, job_id, tenant_id, client_id, unit_type, unit_number, title, text_content, visual_summary, keywords_json, topics_json, metadata_json, asset_urls_json, content_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)
            ON CONFLICT(content_unit_id) DO UPDATE SET title=EXCLUDED.title, text_content=EXCLUDED.text_content, metadata_json=EXCLUDED.metadata_json
        """, (unit.get("content_unit_id"), document_id, state.get("job_id"), state.get("tenant_id"), state.get("client_id"), unit.get("unit_type"), unit.get("unit_number"), unit.get("title"), unit.get("text"), unit.get("visual_summary"), json.dumps(unit.get("keywords", [])), json.dumps(unit.get("topics", [])), json.dumps(unit.get("metadata", {})), json.dumps(unit.get("assets", [])), unit.get("content_hash", "")))
        count += 1
    return count


def upsert_calendar(cur, schema: str, document_id: str, state: Dict[str, Any]) -> int:
    cal = state.get("calendar_structure") or {}
    calendar_id = f"cal_{state.get('job_id')}"
    cur.execute(f"""
        INSERT INTO {schema}.dis_course_calendars(calendar_id, document_id, job_id, tenant_id, client_id, course_name, block, total_days, structure_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
        ON CONFLICT(calendar_id) DO UPDATE SET structure_json=EXCLUDED.structure_json, total_days=EXCLUDED.total_days
    """, (calendar_id, document_id, state.get("job_id"), state.get("tenant_id"), state.get("client_id"), cal.get("course_name"), cal.get("block"), len(cal.get("days", [])), json.dumps(cal)))
    count = 0
    for day in cal.get("days", []):
        day_id = f"{calendar_id}:day_{day.get('day_number', count+1)}"
        cur.execute(f"""
            INSERT INTO {schema}.dis_calendar_days(calendar_day_id, calendar_id, document_id, job_id, tenant_id, client_id, block, day_number, week_number, topic, lesson_title, activities_json, assignments_json, assessments_json, source_text, source_location)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)
            ON CONFLICT(calendar_day_id) DO UPDATE SET topic=EXCLUDED.topic, lesson_title=EXCLUDED.lesson_title, source_text=EXCLUDED.source_text
        """, (day_id, calendar_id, document_id, state.get("job_id"), state.get("tenant_id"), state.get("client_id"), cal.get("block"), day.get("day_number"), day.get("week_number"), day.get("topic"), day.get("lesson_title"), json.dumps(day.get("activities", [])), json.dumps(day.get("assignments", [])), json.dumps(day.get("assessments", [])), day.get("source_text"), day.get("source_location")))
        count += 1
    return count


def upsert_syllabus(cur, schema: str, document_id: str, state: Dict[str, Any]) -> int:
    syl = state.get("syllabus_structure") or {}
    syl_id = f"syl_{state.get('job_id')}"
    cur.execute(f"""
        INSERT INTO {schema}.dis_syllabus(syllabus_id, document_id, job_id, tenant_id, client_id, course_name, block, course_description, learning_outcomes_json, materials_json, grading_policy, attendance_policy, assessment_policy, structure_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s::jsonb)
        ON CONFLICT(syllabus_id) DO UPDATE SET structure_json=EXCLUDED.structure_json, learning_outcomes_json=EXCLUDED.learning_outcomes_json
    """, (syl_id, document_id, state.get("job_id"), state.get("tenant_id"), state.get("client_id"), syl.get("course_name"), syl.get("block"), syl.get("course_description"), json.dumps(syl.get("learning_outcomes", [])), json.dumps(syl.get("materials", [])), syl.get("grading_policy"), syl.get("attendance_policy"), syl.get("assessment_policy"), json.dumps(syl)))
    return 1


def generate_embeddings(tenant_cfg: TenantConfig, state: Dict[str, Any]) -> Dict[str, Any]:
    cfg = tenant_cfg.embedding
    if not cfg.enabled:
        state["embedding_ready_chunks"] = []
        return {"status": "skipped", "reason": "embedding.enabled=false"}
    try:
        import boto3
        client = boto3.client("bedrock-runtime", region_name=cfg.region or tenant_cfg.storage.s3.region or get_settings().aws_region)
        embedded = []
        for unit in state.get("content_units", []) or []:
            text = (unit.get("title", "") + "\n" + unit.get("text", ""))[:cfg.max_input_chars]
            body = json.dumps({"inputText": text, "dimensions": cfg.dimension, "normalize": True})
            resp = client.invoke_model(modelId=cfg.model_id, body=body)
            data = json.loads(resp["body"].read())
            embedded.append({**unit, "embedding": data.get("embedding", [])})
        state["embedding_ready_chunks"] = embedded
        return {"status": "completed", "embeddings_created": len(embedded), "model_id": cfg.model_id, "dimension": cfg.dimension}
    except Exception as exc:
        state["embedding_ready_chunks"] = []
        return {"status": "failed", "error": str(exc)}


def opensearch_upsert(tenant_cfg: TenantConfig, state: Dict[str, Any]) -> Dict[str, Any]:
    cfg = tenant_cfg.vector_store
    if not cfg.enabled:
        return {"status": "skipped", "reason": "vector_store.enabled=false"}
    try:
        from opensearchpy import OpenSearch, RequestsHttpConnection
        from requests_aws4auth import AWS4Auth
        import boto3
    except Exception as exc:
        return {"status": "failed", "error": f"opensearch dependencies missing: {exc}"}
    try:
        if getattr(cfg, "provider", "opensearch") != "opensearch":
            return {"status": "skipped", "reason": f"Unsupported vector store provider: {cfg.provider}. Add adapter in services/adapters/vector_store.py"}
        endpoint = cfg.endpoint.replace("https://", "").replace("http://", "").rstrip("/")
        if cfg.auth_mode == "basic":
            username = (cfg.username or "").strip()
            password = (cfg.password or "").strip()
            client = OpenSearch(
                hosts=[{"host": endpoint, "port": 443}],
                http_auth=(username, password),
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
                timeout=60,
                max_retries=2,
                retry_on_timeout=True,
            )
        else:
            session = boto3.Session(region_name=cfg.region)
            credentials = session.get_credentials()
            auth = AWS4Auth(credentials.access_key, credentials.secret_key, cfg.region, "es", session_token=credentials.token)
            client = OpenSearch(
                hosts=[{"host": endpoint, "port": 443}],
                http_auth=auth,
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
                timeout=60,
                max_retries=2,
                retry_on_timeout=True,
            )
        ensure_index(client, cfg.index_name, tenant_cfg.embedding.dimension)
        units = state.get("embedding_ready_chunks") or state.get("content_units", []) or []
        count = 0
        for unit in units:
            meta = unit.get("metadata", {}) or {}
            doc = {
                "content_unit_id": unit.get("content_unit_id"),
                "job_id": state.get("job_id"),
                "tenant_id": state.get("tenant_id"),
                "client_id": state.get("client_id"),
                "source_file_name": state.get("filename"),
                "source_file_type": state.get("file_type"),
                "document_type": state.get("doc_type"),
                "course_name": meta.get("course_name") or meta.get("course") or state.get("doc_metadata", {}).get("course_name"),
                "block": meta.get("block") or state.get("doc_metadata", {}).get("block"),
                "day_number": meta.get("day_number"),
                "unit_type": unit.get("unit_type"),
                "unit_number": unit.get("unit_number"),
                "title": unit.get("title"),
                "text": unit.get("text"),
                "visual_summary": unit.get("visual_summary"),
                "keywords": unit.get("keywords", []),
                "topics": unit.get("topics", []),
                "metadata": meta,
                "embedding": unit.get("embedding", []),
            }
            client.index(index=cfg.index_name, id=unit.get("content_unit_id"), body=doc, refresh=False)
            count += 1
        return {"status": "completed", "provider": "opensearch", "auth_mode": cfg.auth_mode, "index_name": cfg.index_name, "documents_indexed": count}
    except Exception as exc:
        log.exception("OpenSearch upsert failed")
        return {"status": "failed", "error": str(exc)}


def ensure_index(client, index_name: str, dimension: int):
    """Create OpenSearch index with common DIS fields + dynamic metadata fields.

    Config only passes index_name/endpoint. The structure lives here.
    Different client-specific fields are stored under metadata.* and handled by
    dynamic_templates, so AIM and Academian can have different metadata keys.
    """
    if client.indices.exists(index=index_name):
        return
    body = {
        "settings": {
            "index": {
                "knn": True,
                "number_of_shards": 1,
                "number_of_replicas": 1,
            }
        },
        "mappings": {
            "dynamic": True,
            "dynamic_templates": [
                {
                    "metadata_strings": {
                        "path_match": "metadata.*",
                        "match_mapping_type": "string",
                        "mapping": {
                            "type": "text",
                            "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
                        },
                    }
                },
                {
                    "metadata_numbers": {
                        "path_match": "metadata.*",
                        "match_mapping_type": "long",
                        "mapping": {"type": "long"},
                    }
                },
                {
                    "metadata_booleans": {
                        "path_match": "metadata.*",
                        "match_mapping_type": "boolean",
                        "mapping": {"type": "boolean"},
                    }
                },
            ],
            "properties": {
                "tenant_id": {"type": "keyword"},
                "client_id": {"type": "keyword"},
                "job_id": {"type": "keyword"},
                "document_id": {"type": "keyword"},
                "content_unit_id": {"type": "keyword"},
                "document_type": {"type": "keyword"},
                "source_file_type": {"type": "keyword"},
                "source_file_name": {"type": "keyword"},
                "course_name": {"type": "keyword"},
                "block": {"type": "keyword"},
                "day_number": {"type": "integer"},
                "unit_type": {"type": "keyword"},
                "unit_number": {"type": "integer"},
                "title": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 256}}},
                "text": {"type": "text"},
                "visual_summary": {"type": "text"},
                "keywords": {"type": "keyword"},
                "topics": {"type": "keyword"},
                "metadata": {"type": "object", "enabled": True},
                "embedding": {"type": "knn_vector", "dimension": dimension},
                "created_at": {"type": "date"},
            },
        },
    }
    client.indices.create(index=index_name, body=body)


# =============================================================================
# Query-time vector retrieval (READ path).
#
# These functions are used by services/context_retrieval.py to rank content by
# semantic meaning. They are strictly read-only (never write/modify the index)
# and they raise on failure so the caller can fall back to S3 keyword scoring.
# Security is NOT enforced here: the caller cross-checks every hit's job_id
# against the S3 source-index allow set, so gating stays identical to the S3
# retrieval path even if OpenSearch metadata is incomplete.
# =============================================================================

# Reused clients. Building a boto3/OpenSearch client per query adds a TLS
# handshake (and AWS4Auth signing setup) to every retrieval; caching removes
# that from the hot path. Basic-auth OpenSearch clients and Bedrock clients are
# safe to reuse process-wide. AWS-SigV4 OpenSearch clients are intentionally NOT
# cached so rotating instance-role credentials never go stale (see below).
_BEDROCK_CLIENTS: Dict[str, Any] = {}
_OS_READ_CLIENTS: Dict[str, Any] = {}


def _bedrock_runtime_client(region: str):
    """Return a cached bedrock-runtime client for `region` with bounded retries.

    Explicit connect/read timeouts + a small retry budget keep a slow or
    throttling Bedrock endpoint from stacking latency on the retrieval path.
    """
    client = _BEDROCK_CLIENTS.get(region)
    if client is None:
        import boto3
        from botocore.config import Config
        client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(retries={"max_attempts": 3, "mode": "standard"},
                          connect_timeout=5, read_timeout=30),
        )
        _BEDROCK_CLIENTS[region] = client
    return client


def embed_query(tenant_cfg: TenantConfig, text: str) -> List[float]:
    """Embed a single query string using the SAME model/params as ingestion.

    Parity with generate_embeddings() (model_id, dimensions, normalize) is
    required for kNN distances to be meaningful. Returns [] when embeddings are
    disabled or the text is empty. Any Bedrock error propagates to the caller
    (which falls back to keyword retrieval).
    """
    cfg = tenant_cfg.embedding
    text = (text or "").strip()
    if not cfg.enabled or not text:
        return []
    region = cfg.region or tenant_cfg.storage.s3.region or get_settings().aws_region
    client = _bedrock_runtime_client(region)
    body = json.dumps({"inputText": text[: cfg.max_input_chars], "dimensions": cfg.dimension, "normalize": True})
    resp = client.invoke_model(modelId=cfg.model_id, body=body)
    data = json.loads(resp["body"].read())
    return data.get("embedding", []) or []


def _vector_store_read_client(cfg):
    """Build (or reuse) a read-only OpenSearch client from vector_store config.

    Mirrors the connection logic in opensearch_upsert but is kept separate so
    the ingestion write path is never affected by read-path changes. Basic-auth
    clients are cached per endpoint. SigV4 clients are built per call because
    caching them would freeze credentials that rotate on the instance role.
    """
    from opensearchpy import OpenSearch, RequestsHttpConnection
    endpoint = cfg.endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    if cfg.auth_mode == "basic":
        cache_key = f"basic::{endpoint}"
        client = _OS_READ_CLIENTS.get(cache_key)
        if client is None:
            client = OpenSearch(
                hosts=[{"host": endpoint, "port": 443}],
                http_auth=((cfg.username or "").strip(), (cfg.password or "").strip()),
                use_ssl=True, verify_certs=True,
                connection_class=RequestsHttpConnection, timeout=30, max_retries=2, retry_on_timeout=True,
            )
            _OS_READ_CLIENTS[cache_key] = client
        return client
    import boto3
    from requests_aws4auth import AWS4Auth
    session = boto3.Session(region_name=cfg.region)
    credentials = session.get_credentials()
    auth = AWS4Auth(credentials.access_key, credentials.secret_key, cfg.region, "es", session_token=credentials.token)
    return OpenSearch(
        hosts=[{"host": endpoint, "port": 443}],
        http_auth=auth, use_ssl=True, verify_certs=True,
        connection_class=RequestsHttpConnection, timeout=30, max_retries=2, retry_on_timeout=True,
    )


def vector_search(tenant_cfg: TenantConfig, client_id: str, query_text: str, query_embedding: List[float],
                  size: int = 40, allowed_job_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Hybrid semantic + keyword search over the tenant OpenSearch index.

    - Semantic: kNN over the `embedding` field (meaning match).
    - Keyword: BM25 `multi_match` over title/text/keywords (exact-term match,
      e.g. course codes like "B2D4"). Combined as a bool query so both signals
      contribute to the score.
    - Isolation/security: filters by `client_id`, and — when `allowed_job_ids`
      is provided — by that allow-set INSIDE the query. Filtering server-side
      (rather than only in Python after retrieval) means the ANN ranks within
      the allowed set, so allowed hits can't be truncated behind disallowed
      ones. `allowed_job_ids=None` means no job restriction; an empty list
      restricts to nothing and returns [].

    Returns hit `_source` dicts (embedding excluded) each with an added
    `_score`, ordered by relevance. Read-only. Raises on connection/query error.
    """
    cfg = tenant_cfg.vector_store
    if not cfg.enabled or not query_embedding:
        return []
    if allowed_job_ids is not None:
        # Drop falsy job_ids: a None/"" in a `terms` filter is invalid and would
        # error the query. If nothing is left, the allow-set is effectively empty.
        allowed_job_ids = [j for j in allowed_job_ids if j]
        if not allowed_job_ids:
            return []
    client = _vector_store_read_client(cfg)
    should: List[Dict[str, Any]] = []
    if (query_text or "").strip():
        should.append({
            "multi_match": {
                "query": query_text,
                "fields": ["title^2", "text", "keywords^1.5", "visual_summary^1.2", "topics"],
                "type": "best_fields",
                "tie_breaker": 0.3,
            }
        })
    filter_clauses: List[Dict[str, Any]] = [{"term": {"client_id": client_id}}]
    if allowed_job_ids is not None:
        filter_clauses.append({"terms": {"job_id": list(allowed_job_ids)}})
    # Over-fetch candidates (k >= size, generous floor). The default nmslib
    # engine applies the bool `filter` post-ANN, so a generous k keeps recall
    # healthy once the allow-set narrows results. For large multi-tenant scale,
    # move `embedding` to the Lucene engine for true pre-filtered kNN.
    knn_k = max(size, 100)
    body = {
        "size": size,
        "query": {
            "bool": {
                "must": [{"knn": {"embedding": {"vector": query_embedding, "k": knn_k}}}],
                "should": should,
                "filter": filter_clauses,
            }
        },
        "_source": {"excludes": ["embedding"]},
    }
    resp = client.search(index=cfg.index_name, body=body)
    out: List[Dict[str, Any]] = []
    for h in resp.get("hits", {}).get("hits", []):
        src = h.get("_source", {}) or {}
        src["_score"] = h.get("_score", 0.0)
        out.append(src)
    return out
