"""Optional RDS/OpenSearch/embedding integration for DIS.

All steps are config-gated. When disabled, the pipeline writes a JSON artifact
with status=skipped so local/S3-first testing remains simple.
"""
from __future__ import annotations
import json
import logging
from typing import Any, Dict, List, Optional

from services.blocks import block_label, block_variants
from config.settings import TenantConfig, get_settings

log = logging.getLogger(__name__)

# Dynamic client fields are stored in metadata_json (RDS) and metadata.* (OpenSearch).
# This avoids creating different physical tables/indexes per client.
# For query performance, RDS gets a JSONB GIN index and OpenSearch gets dynamic_templates.


def _ensure_database_exists(psycopg_module, dsn: str) -> None:
    """Create the DSN's target database if it doesn't exist yet.

    Postgres has no CREATE DATABASE IF NOT EXISTS, and CREATE DATABASE cannot
    run inside a transaction or over a connection to the database being
    created — it needs its own autocommit connection to a different,
    already-existing database on the same server (the "postgres" maintenance
    database, always present on RDS). This is one level up from what
    auto_create_schema already does for schema/tables: a structure_store.url
    freshly pointed at a database that has never existed (e.g. a new client's own
    database) self-provisions on first upload instead of needing someone to run
    CREATE DATABASE by hand first.

    Requires the connecting role to have CREATEDB — if it doesn't, this fails
    loudly with Postgres's own permission-denied error (caught by
    rds_upsert's exception handler, not swallowed), the same "fail loud on
    misconfiguration" behaviour the rest of this module already has.

    Checks via a separate admin connection up front rather than trying
    ``psycopg.connect(dsn)`` first and creating the database on failure:
    connecting to a missing database raises a bare OperationalError with no
    SQLSTATE (confirmed against a real server — it's a connection-time FATAL,
    not a query error), so telling "database is missing" apart from "server
    unreachable" or "bad password" would mean matching on the error string.
    The extra connection this costs on every call is the same tradeoff
    ensure_schema already makes (full DDL every upsert, not just the first).
    """
    parsed = psycopg_module.conninfo.conninfo_to_dict(dsn)
    dbname = parsed.get("dbname")
    if not dbname or dbname == "postgres":
        return
    admin_dsn = psycopg_module.conninfo.make_conninfo(dsn, dbname="postgres")
    with psycopg_module.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone() is not None:
                return
            try:
                cur.execute(
                    psycopg_module.sql.SQL("CREATE DATABASE {}").format(psycopg_module.sql.Identifier(dbname))
                )
                log.info("dis_structure_store: auto-created database %r", dbname)
            except psycopg_module.errors.DuplicateDatabase:
                # Another process created it between our check and CREATE — fine,
                # this is the same race CREATE TABLE IF NOT EXISTS is immune to and
                # a plain CREATE DATABASE isn't; treat it as success either way.
                pass


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
        if getattr(cfg, "auto_create_schema", True):
            _ensure_database_exists(psycopg, dsn)
        # Same value that already partitions the S3 key for this upload
        # (api/routers/ingestion.py builds raw/<ns>/<environment>/<job_id>/...).
        # Stamped here, not read from state, so it can never be influenced by
        # request data — it is a fact about which server is running this code.
        environment = get_settings().environment or "development"
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                ensure_schema(cur, cfg.schema_name)
                upsert_job(cur, cfg.schema_name, state, environment)
                document_id = upsert_document(cur, cfg.schema_name, state, environment)
                unit_count = upsert_content_units(cur, cfg.schema_name, document_id, state, environment)
                if state.get("calendar_structure"):
                    calendar_days = upsert_calendar(cur, cfg.schema_name, document_id, state, environment)
                else:
                    calendar_days = 0
                if state.get("syllabus_structure"):
                    syllabus_rows = upsert_syllabus(cur, cfg.schema_name, document_id, state, environment)
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

    # Source Library compact catalog (replaces S3 source_list.json when
    # structure_store is enabled). job_id remains the document identity.
    cur.execute(f"""
    CREATE TABLE IF NOT EXISTS {schema}.source_index (
        id                BIGSERIAL PRIMARY KEY,
        job_id            TEXT NOT NULL,
        client_id         TEXT NOT NULL,
        tenant_id         TEXT NOT NULL,
        environment       TEXT NOT NULL,
        title             TEXT,
        source_file_name  TEXT,
        document_type     TEXT,
        purpose           TEXT,
        status            TEXT,
        visibility        TEXT,
        restricted        BOOLEAN DEFAULT FALSE,
        block             TEXT,
        day               TEXT,
        course_name       TEXT,
        module_name       TEXT,
        payload_key       TEXT,
        content_key       TEXT,
        raw_key           TEXT,
        extracted_chars   INTEGER,
        total_units       INTEGER,
        created_at        TIMESTAMPTZ,
        updated_at        TIMESTAMPTZ,
        record_json       JSONB NOT NULL,
        CONSTRAINT uq_source_index_client_env_job UNIQUE (client_id, environment, job_id)
    )""")
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_source_index_purpose_status "
        f"ON {schema}.source_index (client_id, environment, purpose, status)"
    )
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_source_index_block_day "
        f"ON {schema}.source_index (client_id, environment, block, day)"
    )
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_source_index_updated "
        f"ON {schema}.source_index (client_id, environment, updated_at DESC)"
    )
    cur.execute(
        f"CREATE INDEX IF NOT EXISTS idx_source_index_record_gin "
        f"ON {schema}.source_index USING GIN (record_json)"
    )

    # Dev/prod bifurcation: which server ingested this row (see rds_upsert's
    # `environment`). There is no Alembic for this schema — CREATE TABLE IF NOT
    # EXISTS above is a no-op on a table that already exists, so an existing
    # deployment needs these ALTERs to actually gain the column. Nullable: a
    # backfill (`UPDATE ... SET environment = 'prod' WHERE environment IS NULL`)
    # is a deliberate, one-time, human-run step — see
    # DIS_ENV_BIFURCATION_WORKFLOW.txt phase 6 — not something this function
    # should default silently on every startup.
    for table in ("dis_jobs", "dis_documents", "dis_content_units", "dis_course_calendars", "dis_calendar_days", "dis_syllabus"):
        cur.execute(f"ALTER TABLE {schema}.{table} ADD COLUMN IF NOT EXISTS environment text")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_dis_documents_environment ON {schema}.dis_documents(environment)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_dis_units_environment ON {schema}.dis_content_units(environment)")


def upsert_job(cur, schema: str, state: Dict[str, Any], environment: str):
    payload_url = state.get("artifact_urls", {}).get("studio_payload", "")
    validation_url = state.get("artifact_urls", {}).get("validation_report", "")
    cur.execute(f"""
        INSERT INTO {schema}.dis_jobs(job_id, tenant_id, client_id, status, source_file_name, raw_storage_url, payload_storage_url, validation_report_url, metadata_json, environment, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,now())
        ON CONFLICT(job_id) DO UPDATE SET status=EXCLUDED.status, payload_storage_url=EXCLUDED.payload_storage_url,
        validation_report_url=EXCLUDED.validation_report_url, metadata_json=EXCLUDED.metadata_json, updated_at=now()
    """, (state.get("job_id"), state.get("tenant_id"), state.get("client_id"), "completed", state.get("filename"), state.get("raw_storage_url"), payload_url, validation_url, json.dumps(state.get("doc_metadata", {})), environment))


def upsert_document(cur, schema: str, state: Dict[str, Any], environment: str) -> str:
    doc_id = f"doc_{state.get('job_id')}"
    meta = state.get("doc_metadata", {})
    cur.execute(f"""
        INSERT INTO {schema}.dis_documents(document_id, job_id, tenant_id, client_id, document_title, document_type, source_file_name, source_file_type, source_relative_path, raw_storage_url, payload_storage_url, metadata_json, environment)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
        ON CONFLICT(document_id) DO UPDATE SET metadata_json=EXCLUDED.metadata_json, payload_storage_url=EXCLUDED.payload_storage_url
    """, (doc_id, state.get("job_id"), state.get("tenant_id"), state.get("client_id"), meta.get("title") or state.get("filename"), state.get("doc_type"), state.get("filename"), state.get("file_type"), state.get("source_relative_path"), state.get("raw_storage_url"), state.get("artifact_urls", {}).get("studio_payload", ""), json.dumps(meta), environment))
    return doc_id


def upsert_content_units(cur, schema: str, document_id: str, state: Dict[str, Any], environment: str) -> int:
    count = 0
    for unit in state.get("content_units", []) or []:
        cur.execute(f"""
            INSERT INTO {schema}.dis_content_units(content_unit_id, document_id, job_id, tenant_id, client_id, unit_type, unit_number, title, text_content, visual_summary, keywords_json, topics_json, metadata_json, asset_urls_json, content_hash, environment)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)
            ON CONFLICT(content_unit_id) DO UPDATE SET title=EXCLUDED.title, text_content=EXCLUDED.text_content, metadata_json=EXCLUDED.metadata_json
        """, (unit.get("content_unit_id"), document_id, state.get("job_id"), state.get("tenant_id"), state.get("client_id"), unit.get("unit_type"), unit.get("unit_number"), unit.get("title"), unit.get("text"), unit.get("visual_summary"), json.dumps(unit.get("keywords", [])), json.dumps(unit.get("topics", [])), json.dumps(unit.get("metadata", {})), json.dumps(unit.get("assets", [])), unit.get("content_hash", ""), environment))
        count += 1
    return count


def upsert_calendar(cur, schema: str, document_id: str, state: Dict[str, Any], environment: str) -> int:
    cal = state.get("calendar_structure") or {}
    calendar_id = f"cal_{state.get('job_id')}"
    cur.execute(f"""
        INSERT INTO {schema}.dis_course_calendars(calendar_id, document_id, job_id, tenant_id, client_id, course_name, block, total_days, structure_json, environment)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
        ON CONFLICT(calendar_id) DO UPDATE SET structure_json=EXCLUDED.structure_json, total_days=EXCLUDED.total_days
    """, (calendar_id, document_id, state.get("job_id"), state.get("tenant_id"), state.get("client_id"), cal.get("course_name"), block_label(cal.get("block")), len(cal.get("days", [])), json.dumps(cal), environment))
    # Day rows are keyed by day_number, so two parsed days claiming the same number
    # overwrite each other. That is how a Block 13 calendar whose every row parsed as
    # "day 1" stored 2 rows for 11 days and reported success: the collapse is the
    # ON CONFLICT working exactly as written, and nothing counted what it ate.
    seen_days: set = set()
    collapsed: List[int] = []
    count = 0
    for day in cal.get("days", []):
        day_number = day.get("day_number", count + 1)
        if day_number in seen_days:
            collapsed.append(day_number)
        seen_days.add(day_number)
        day_id = f"{calendar_id}:day_{day_number}"
        cur.execute(f"""
            INSERT INTO {schema}.dis_calendar_days(calendar_day_id, calendar_id, document_id, job_id, tenant_id, client_id, block, day_number, week_number, topic, lesson_title, activities_json, assignments_json, assessments_json, source_text, source_location, environment)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s)
            ON CONFLICT(calendar_day_id) DO UPDATE SET topic=EXCLUDED.topic, lesson_title=EXCLUDED.lesson_title, source_text=EXCLUDED.source_text
        """, (day_id, calendar_id, document_id, state.get("job_id"), state.get("tenant_id"), state.get("client_id"), block_label(cal.get("block")), day_number, day.get("week_number"), day.get("topic"), day.get("lesson_title"), json.dumps(day.get("activities", [])), json.dumps(day.get("assignments", [])), json.dumps(day.get("assessments", [])), day.get("source_text"), day.get("source_location"), environment))
        count += 1
    if collapsed:
        # Loud, and carried in the state the pipeline reports: a calendar that stored
        # a third of its days is not a successful ingest, and the downstream symptom
        # (a Blueprint covering 2 of 11 days) gives no hint that the loss happened here.
        log.error("calendar %s: %s of %s parsed days collapsed onto duplicate day "
                  "numbers %s — the stored calendar is INCOMPLETE; the source almost "
                  "certainly did not parse (check the extractor that produced it)",
                  calendar_id, len(collapsed), count, sorted(set(collapsed)))
        state.setdefault("errors", []).append(
            f"calendar {calendar_id}: {len(collapsed)} of {count} parsed days shared a "
            f"day_number and were overwritten; stored {len(seen_days)} distinct days")
    return len(seen_days)


def upsert_syllabus(cur, schema: str, document_id: str, state: Dict[str, Any], environment: str) -> int:
    syl = state.get("syllabus_structure") or {}
    syl_id = f"syl_{state.get('job_id')}"
    cur.execute(f"""
        INSERT INTO {schema}.dis_syllabus(syllabus_id, document_id, job_id, tenant_id, client_id, course_name, block, course_description, learning_outcomes_json, materials_json, grading_policy, attendance_policy, assessment_policy, structure_json, environment)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s::jsonb,%s)
        ON CONFLICT(syllabus_id) DO UPDATE SET structure_json=EXCLUDED.structure_json, learning_outcomes_json=EXCLUDED.learning_outcomes_json
    """, (syl_id, document_id, state.get("job_id"), state.get("tenant_id"), state.get("client_id"), syl.get("course_name"), syl.get("block"), syl.get("course_description"), json.dumps(syl.get("learning_outcomes", [])), json.dumps(syl.get("materials", [])), syl.get("grading_policy"), syl.get("attendance_policy"), syl.get("assessment_policy"), json.dumps(syl), environment))
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
        clipped = 0
        for unit in state.get("content_units", []) or []:
            full = unit.get("title", "") + "\n" + unit.get("text", "")
            if len(full) > cfg.max_input_chars:
                # The model's own ceiling, so clipping is legitimate — but it must
                # not be silent: the clipped tail is content the semantic index will
                # never represent, and nothing downstream can tell it was dropped.
                clipped += 1
            text = full[:cfg.max_input_chars]
            body = json.dumps({"inputText": text, "dimensions": cfg.dimension, "normalize": True})
            resp = client.invoke_model(modelId=cfg.model_id, body=body)
            data = json.loads(resp["body"].read())
            embedded.append({**unit, "embedding": data.get("embedding", [])})
        state["embedding_ready_chunks"] = embedded
        if clipped:
            log.warning("embedding_input_clipped units=%d cap=%d model=%s — the clipped "
                        "tail is not represented in the semantic index",
                        clipped, cfg.max_input_chars, cfg.model_id)
        return {"status": "completed", "embeddings_created": len(embedded),
                "model_id": cfg.model_id, "dimension": cfg.dimension,
                "units_clipped": clipped}
    except Exception as exc:
        state["embedding_ready_chunks"] = []
        return {"status": "failed", "error": str(exc)}


def _build_bulk_actions(
    index_name: str, state: Dict[str, Any], units: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Build one OpenSearch bulk action per content unit.

    Extracted so the (pure) document-mapping is unit-testable without a live
    OpenSearch. Each action uses the default ``index`` op — create-or-replace by
    ``_id`` — identical semantics to the previous per-unit ``client.index()``
    call; only the transport (one batched request vs. N) changes.
    """
    actions: List[Dict[str, Any]] = []
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
        actions.append({
            "_index": index_name,
            "_id": unit.get("content_unit_id"),
            "_source": doc,
        })
    return actions


def opensearch_upsert(tenant_cfg: TenantConfig, state: Dict[str, Any]) -> Dict[str, Any]:
    cfg = tenant_cfg.vector_store
    if not cfg.enabled:
        return {"status": "skipped", "reason": "vector_store.enabled=false"}
    try:
        import importlib
        for _dep in ("opensearchpy", "requests_aws4auth", "boto3"):
            importlib.import_module(_dep)
    except Exception as exc:
        return {"status": "failed", "error": f"opensearch dependencies missing: {exc}"}
    try:
        if getattr(cfg, "provider", "opensearch") != "opensearch":
            return {"status": "skipped", "reason": f"Unsupported vector store provider: {cfg.provider}. Add adapter in services/adapters/vector_store.py"}
        # Decided BEFORE any connection is opened: if this document cannot be
        # indexed correctly there is nothing to gain from a TLS handshake first.
        #
        # An empty embedding_ready_chunks means one of two very different things:
        # embeddings are switched off (fine — index the text and rely on keyword
        # search), or generate_embeddings FAILED. Falling back to the raw units in
        # the second case indexes every unit with "embedding": [] — present in the
        # index, unreachable by the kNN search that block-wide generation depends
        # on, and reported as a completed job. 60 AIM units reached prod that way.
        ready = state.get("embedding_ready_chunks")
        raw = state.get("content_units", []) or []
        if getattr(tenant_cfg.embedding, "enabled", False) and raw and not ready:
            return {"status": "failed", "error":
                    "embeddings are enabled but none were produced for this document; "
                    "refusing to index unembedded units, which would be invisible to "
                    "semantic retrieval while reporting success"}
        # Reuse the OpenSearch write client (P4.3/F7) instead of building a new
        # one — and a fresh TLS handshake / AWS4Auth signing setup — per upsert.
        client = _vector_store_write_client(cfg)
        ensure_index(client, cfg.index_name, tenant_cfg.embedding.dimension)
        units = ready or raw

        # Issue a single batched request (helpers.bulk) instead of one
        # client.index() call per content unit (P6.2/F12). Default op_type
        # "index" preserves the previous create-or-replace-by-id semantics, and
        # refresh=False is passed through unchanged. helpers.bulk raises on any
        # item error (raise_on_error default True), so a failure still surfaces
        # as status="failed" via the outer except — same contract as before.
        actions = _build_bulk_actions(cfg.index_name, state, units)
        if actions:
            from opensearchpy import helpers
            indexed, _errors = helpers.bulk(client, actions, refresh=False)
        else:
            indexed = 0
        return {"status": "completed", "provider": "opensearch", "auth_mode": cfg.auth_mode, "index_name": cfg.index_name, "documents_indexed": indexed}
    except Exception as exc:
        log.exception("OpenSearch upsert failed")
        return {"status": "failed", "error": str(exc)}


def opensearch_delete_by_job(tenant_cfg: TenantConfig, job_id: str) -> Dict[str, Any]:
    """Delete every indexed chunk for one job_id. Used by Source Library document delete."""
    cfg = tenant_cfg.vector_store
    if not cfg.enabled:
        return {"status": "skipped", "reason": "vector_store.enabled=false"}
    try:
        client = _vector_store_read_client(cfg)
        if not client.indices.exists(index=cfg.index_name):
            return {"status": "skipped", "reason": "index does not exist", "index_name": cfg.index_name}
        resp = client.delete_by_query(
            index=cfg.index_name,
            body={"query": {"term": {"job_id": job_id}}},
            refresh=True,
        )
        return {"status": "completed", "index_name": cfg.index_name, "deleted": resp.get("deleted", 0)}
    except Exception as exc:
        log.exception("OpenSearch delete_by_query failed for job_id=%s", job_id)
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
    try:
        client.indices.create(index=index_name, body=body)
    except Exception as exc:  # noqa: BLE001
        # Concurrent fan-out writers can race exists()→create(); the loser gets a
        # resource_already_exists_exception. That's success, not failure — swallow
        # it so a cold-block parallel build doesn't spuriously mark days failed.
        if "resource_already_exists" in str(exc).lower():
            return
        raise


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
# Write-path OpenSearch clients (P4.3/F7). Kept in a separate cache from the read
# path so ingestion is never coupled to read-path changes, but following the same
# reuse rule: basic-auth clients are cached per endpoint; SigV4 clients are built
# per call so rotating instance-role credentials never go stale.
_OS_WRITE_CLIENTS: Dict[str, Any] = {}


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


def _vector_store_write_client(cfg):
    """Build (or reuse) an OpenSearch client for the ingestion WRITE path.

    Same reuse rule as :func:`_vector_store_read_client` (basic-auth cached per
    endpoint; SigV4 built per call so rotating instance-role credentials never go
    stale), but kept in its own cache (``_OS_WRITE_CLIENTS``) with the write
    path's longer 60s timeout, so ingestion is never coupled to read-path config.
    """
    from opensearchpy import OpenSearch, RequestsHttpConnection
    endpoint = cfg.endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    if cfg.auth_mode == "basic":
        cache_key = f"basic-write::{endpoint}"
        client = _OS_WRITE_CLIENTS.get(cache_key)
        if client is None:
            client = OpenSearch(
                hosts=[{"host": endpoint, "port": 443}],
                http_auth=((cfg.username or "").strip(), (cfg.password or "").strip()),
                use_ssl=True, verify_certs=True,
                connection_class=RequestsHttpConnection, timeout=60, max_retries=2, retry_on_timeout=True,
            )
            _OS_WRITE_CLIENTS[cache_key] = client
        return client
    import boto3
    from requests_aws4auth import AWS4Auth
    session = boto3.Session(region_name=cfg.region)
    credentials = session.get_credentials()
    auth = AWS4Auth(credentials.access_key, credentials.secret_key, cfg.region, "es", session_token=credentials.token)
    return OpenSearch(
        hosts=[{"host": endpoint, "port": 443}],
        http_auth=auth, use_ssl=True, verify_certs=True,
        connection_class=RequestsHttpConnection, timeout=60, max_retries=2, retry_on_timeout=True,
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


# =============================================================================
# Day-digest store (block-wide CDD / Blueprint, design decision D1).
#
# Digests are persisted as ordinary documents in the SAME OpenSearch index with
# `unit_type="day_digest"` — no new index, no RDS migration. They are fetched by
# an exact bool/filter (NOT kNN): a completeness task wants every day of a block,
# not the top-k most similar. The full digest JSON rides under `metadata.*`.
#
# These reuse `_vector_store_read_client` (a fully-capable client; the "read"
# naming only means its SigV4 credentials are not cached). Digest volume is tiny
# (~20 docs/block), so a dedicated write client is not warranted.
# =============================================================================

DIGEST_UNIT_TYPE = "day_digest"


def _digest_projection_text(digest: Dict[str, Any]) -> str:
    """Compact human/BM25-readable projection stored in the `text` field."""
    parts = [
        f"Day {digest.get('day_number')}: {digest.get('topic') or ''}",
        digest.get("derived_objective") or "",
        "ACS: " + ", ".join(digest.get("acs_codes") or []),
    ]
    return "\n".join(p for p in parts if p).strip()


def upsert_digest(tenant_cfg: TenantConfig, digest: Dict[str, Any]) -> Dict[str, Any]:
    """Index (idempotently) one per-day digest. Doc id = digest_id, so a rebuild
    of the same day overwrites in place."""
    from datetime import datetime, timezone

    cfg = tenant_cfg.vector_store
    if not cfg.enabled:
        return {"status": "skipped", "reason": "vector_store.enabled=false"}
    if getattr(cfg, "provider", "opensearch") != "opensearch":
        return {"status": "skipped", "reason": f"Unsupported vector store provider: {cfg.provider}"}
    digest_id = digest.get("digest_id")
    if not digest_id:
        return {"status": "failed", "error": "digest missing digest_id"}
    try:
        client = _vector_store_read_client(cfg)
        ensure_index(client, cfg.index_name, tenant_cfg.embedding.dimension)
        doc = {
            "content_unit_id": digest_id,
            "client_id": digest.get("client_id"),
            "block": digest.get("block"),
            "day_number": digest.get("day_number"),
            "unit_type": DIGEST_UNIT_TYPE,
            "title": f"Day {digest.get('day_number')} digest",
            "text": _digest_projection_text(digest),
            "metadata": digest,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        client.index(index=cfg.index_name, id=digest_id, body=doc, refresh=False)
        return {"status": "completed", "index_name": cfg.index_name, "digest_id": digest_id}
    except Exception as exc:
        log.exception("Digest upsert failed for %s", digest_id)
        return {"status": "failed", "error": str(exc)}


def fetch_digests(tenant_cfg: TenantConfig, block: str, client_id: str) -> List[Dict[str, Any]]:
    """Return all persisted day-digest JSON blobs for a block (exact filter, not
    kNN). Empty list if the store/index is absent. Read-only; raises on query
    error so the caller can decide to rebuild."""
    cfg = tenant_cfg.vector_store
    if not cfg.enabled:
        return []
    client = _vector_store_read_client(cfg)
    if not client.indices.exists(index=cfg.index_name):
        return []
    body = {
        "size": 500,
        "_source": {"excludes": ["embedding"]},
        # terms-over-variants, not a single term: `block` is a keyword field holding
        # whatever spelling the build was started with, so a cache keyed on the raw
        # string silently misses (then re-runs every day's MAP, billed again) when the
        # same block is asked for as 'Block 09' one time and 'Block 9' the next.
        "query": {"bool": {"filter": [
            {"term": {"client_id": client_id}},
            {"terms": {"block": block_variants(block)}},
            {"term": {"unit_type": DIGEST_UNIT_TYPE}},
        ]}},
    }
    resp = client.search(index=cfg.index_name, body=body)
    out: List[Dict[str, Any]] = []
    for h in resp.get("hits", {}).get("hits", []):
        meta = (h.get("_source", {}) or {}).get("metadata")
        if isinstance(meta, dict):
            out.append(meta)
    return out


def delete_digests(tenant_cfg: TenantConfig, block: str, client_id: str) -> Dict[str, Any]:
    """Delete every day-digest for a block (force-rebuild support)."""
    cfg = tenant_cfg.vector_store
    if not cfg.enabled:
        return {"status": "skipped", "reason": "vector_store.enabled=false"}
    try:
        client = _vector_store_read_client(cfg)
        if not client.indices.exists(index=cfg.index_name):
            return {"status": "skipped", "reason": "index does not exist"}
        resp = client.delete_by_query(
            index=cfg.index_name,
            body={"query": {"bool": {"filter": [
                {"term": {"client_id": client_id}},
                # Same variant set as fetch_digests: a force-rebuild that deleted only
                # one spelling would leave the others behind, and fetch would then
                # serve the stale ones the rebuild existed to replace.
                {"terms": {"block": block_variants(block)}},
                {"term": {"unit_type": DIGEST_UNIT_TYPE}},
            ]}}},
            refresh=True,
        )
        return {"status": "completed", "deleted": resp.get("deleted", 0)}
    except Exception as exc:
        log.exception("Digest delete failed for block=%s", block)
        return {"status": "failed", "error": str(exc)}
