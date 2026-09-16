"""Postgres adapter for the Source Library compact catalog (source_index).

When structure_store.enabled, the catalogue lives in ``{schema}.source_index``
instead of S3 ``source_list.json``. File payloads and OpenSearch are unchanged.
``job_id`` remains the document identity; serial ``id`` is display-only.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config.settings import TenantConfig, get_settings

logger = logging.getLogger(__name__)

_UPSERT_SQL = """
INSERT INTO {schema}.source_index (
    job_id, client_id, tenant_id, environment,
    title, source_file_name, document_type, purpose, status, visibility,
    restricted, block, day, course_name, module_name,
    payload_key, content_key, raw_key, extracted_chars, total_units,
    created_at, updated_at, record_json
) VALUES (
    %(job_id)s, %(client_id)s, %(tenant_id)s, %(environment)s,
    %(title)s, %(source_file_name)s, %(document_type)s, %(purpose)s, %(status)s, %(visibility)s,
    %(restricted)s, %(block)s, %(day)s, %(course_name)s, %(module_name)s,
    %(payload_key)s, %(content_key)s, %(raw_key)s, %(extracted_chars)s, %(total_units)s,
    %(created_at)s, %(updated_at)s, %(record_json)s::jsonb
)
ON CONFLICT (client_id, environment, job_id) DO UPDATE SET
    tenant_id = EXCLUDED.tenant_id,
    title = EXCLUDED.title,
    source_file_name = EXCLUDED.source_file_name,
    document_type = EXCLUDED.document_type,
    purpose = EXCLUDED.purpose,
    status = EXCLUDED.status,
    visibility = EXCLUDED.visibility,
    restricted = EXCLUDED.restricted,
    block = EXCLUDED.block,
    day = EXCLUDED.day,
    course_name = EXCLUDED.course_name,
    module_name = EXCLUDED.module_name,
    payload_key = EXCLUDED.payload_key,
    content_key = EXCLUDED.content_key,
    raw_key = EXCLUDED.raw_key,
    extracted_chars = EXCLUDED.extracted_chars,
    total_units = EXCLUDED.total_units,
    created_at = COALESCE(EXCLUDED.created_at, {schema}.source_index.created_at),
    updated_at = EXCLUDED.updated_at,
    record_json = EXCLUDED.record_json
"""


def use_pg_source_index(tenant_cfg: TenantConfig) -> bool:
    cfg = getattr(tenant_cfg, "structure_store", None)
    if cfg is None or not getattr(cfg, "enabled", False):
        return False
    if getattr(cfg, "provider", "postgres") != "postgres":
        return False
    return bool(getattr(cfg, "url", "") or "")


def environment_name() -> str:
    return get_settings().environment or "development"


def index_ref(client_id: str, environment: Optional[str] = None) -> str:
    env = environment or environment_name()
    return f"pg:dis.source_index:{client_id}:{env}"


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def record_to_params(
    record: Dict[str, Any],
    *,
    tenant_id: str,
    client_id: str,
    environment: str,
) -> Dict[str, Any]:
    job_id = str(record.get("job_id") or record.get("document_id") or "").strip()
    if not job_id:
        raise ValueError("source_index record requires job_id")
    updated = _parse_ts(record.get("updated_at")) or datetime.now(timezone.utc)
    created = _parse_ts(record.get("created_at")) or updated
    return {
        "job_id": job_id,
        "client_id": str(client_id),
        "tenant_id": str(tenant_id),
        "environment": str(environment),
        "title": record.get("title"),
        "source_file_name": record.get("source_file_name"),
        "document_type": record.get("document_type"),
        "purpose": record.get("purpose"),
        "status": record.get("status"),
        "visibility": record.get("visibility"),
        "restricted": bool(record.get("restricted")),
        "block": record.get("block"),
        "day": record.get("day"),
        "course_name": record.get("course_name"),
        "module_name": record.get("module_name"),
        "payload_key": record.get("payload_key"),
        "content_key": record.get("content_key"),
        "raw_key": record.get("raw_key"),
        "extracted_chars": _as_int(record.get("extracted_chars")),
        "total_units": _as_int(record.get("total_units")),
        "created_at": created,
        "updated_at": updated,
        "record_json": json.dumps(record, ensure_ascii=False, default=str),
    }


def connect(tenant_cfg: TenantConfig):
    import psycopg

    cfg = tenant_cfg.structure_store
    dsn = cfg.url
    if not dsn:
        raise RuntimeError("structure_store.url is empty")
    return psycopg.connect(dsn)


def ensure_table(cur, schema: str) -> None:
    """Idempotent DDL for source_index (same as ensure_schema / sql/source_index.sql)."""
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
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


def _schema(tenant_cfg: TenantConfig) -> str:
    return tenant_cfg.structure_store.schema_name or "dis"


def read_sources(tenant_cfg: TenantConfig, client_id: str) -> List[Dict[str, Any]]:
    schema = _schema(tenant_cfg)
    env = environment_name()
    with connect(tenant_cfg) as conn:
        with conn.cursor() as cur:
            ensure_table(cur, schema)
            cur.execute(
                f"""
                SELECT record_json
                FROM {schema}.source_index
                WHERE client_id = %s AND environment = %s
                ORDER BY updated_at DESC NULLS LAST, job_id
                """,
                (client_id, env),
            )
            rows = cur.fetchall()
            conn.commit()
    sources: List[Dict[str, Any]] = []
    for (raw,) in rows:
        if isinstance(raw, dict):
            sources.append(raw)
        elif isinstance(raw, str):
            sources.append(json.loads(raw))
        else:
            sources.append(dict(raw))
    return sources


def read_job_ids(tenant_cfg: TenantConfig, client_id: str) -> List[str]:
    """Compact identity list for S3 orphan cleanup (Postgres is source of truth)."""
    schema = _schema(tenant_cfg)
    env = environment_name()
    with connect(tenant_cfg) as conn:
        with conn.cursor() as cur:
            ensure_table(cur, schema)
            cur.execute(
                f"""
                SELECT job_id FROM {schema}.source_index
                WHERE client_id = %s AND environment = %s
                """,
                (client_id, env),
            )
            rows = cur.fetchall()
            conn.commit()
    return [str(row[0]) for row in rows if row and row[0]]


def count_sources(tenant_cfg: TenantConfig, client_id: str) -> int:
    schema = _schema(tenant_cfg)
    env = environment_name()
    with connect(tenant_cfg) as conn:
        with conn.cursor() as cur:
            ensure_table(cur, schema)
            cur.execute(
                f"""
                SELECT COUNT(*) FROM {schema}.source_index
                WHERE client_id = %s AND environment = %s
                """,
                (client_id, env),
            )
            n = int(cur.fetchone()[0])
            conn.commit()
    return n


def upsert_record(tenant_cfg: TenantConfig, client_id: str, record: Dict[str, Any]) -> str:
    schema = _schema(tenant_cfg)
    env = environment_name()
    params = record_to_params(
        record,
        tenant_id=tenant_cfg.tenant_id,
        client_id=client_id,
        environment=env,
    )
    sql = _UPSERT_SQL.format(schema=schema)
    with connect(tenant_cfg) as conn:
        with conn.cursor() as cur:
            ensure_table(cur, schema)
            cur.execute(sql, params)
            conn.commit()
    return index_ref(client_id, env)


def replace_index(
    tenant_cfg: TenantConfig,
    client_id: str,
    sources: Sequence[Dict[str, Any]],
) -> str:
    """Full-replace semantics for repair/backfill: upsert all, delete missing job_ids."""
    schema = _schema(tenant_cfg)
    env = environment_name()
    keep: List[str] = []
    sql = _UPSERT_SQL.format(schema=schema)
    with connect(tenant_cfg) as conn:
        with conn.cursor() as cur:
            ensure_table(cur, schema)
            for record in sources:
                params = record_to_params(
                    record,
                    tenant_id=tenant_cfg.tenant_id,
                    client_id=client_id,
                    environment=env,
                )
                keep.append(params["job_id"])
                cur.execute(sql, params)
            if keep:
                cur.execute(
                    f"""
                    DELETE FROM {schema}.source_index
                    WHERE client_id = %s AND environment = %s
                      AND NOT (job_id = ANY(%s))
                    """,
                    (client_id, env, keep),
                )
            else:
                cur.execute(
                    f"""
                    DELETE FROM {schema}.source_index
                    WHERE client_id = %s AND environment = %s
                    """,
                    (client_id, env),
                )
            conn.commit()
    return index_ref(client_id, env)


def delete_record(tenant_cfg: TenantConfig, client_id: str, job_id: str) -> str:
    schema = _schema(tenant_cfg)
    env = environment_name()
    with connect(tenant_cfg) as conn:
        with conn.cursor() as cur:
            ensure_table(cur, schema)
            cur.execute(
                f"""
                DELETE FROM {schema}.source_index
                WHERE client_id = %s AND environment = %s AND job_id = %s
                """,
                (client_id, env, str(job_id)),
            )
            conn.commit()
    return index_ref(client_id, env)


def update_status(
    tenant_cfg: TenantConfig,
    client_id: str,
    job_id: str,
    status: str,
    extra: Optional[Dict[str, Any]] = None,
    only_if_status: Optional[str] = None,
) -> Tuple[bool, str]:
    """Update one row's status (+ optional extras). Returns (updated, ref)."""
    schema = _schema(tenant_cfg)
    env = environment_name()
    with connect(tenant_cfg) as conn:
        with conn.cursor() as cur:
            ensure_table(cur, schema)
            cur.execute(
                f"""
                SELECT record_json FROM {schema}.source_index
                WHERE client_id = %s AND environment = %s AND job_id = %s
                FOR UPDATE
                """,
                (client_id, env, str(job_id)),
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return False, ""
            raw = row[0]
            rec = raw if isinstance(raw, dict) else json.loads(raw)
            if only_if_status is not None and str(rec.get("status") or "").lower() != only_if_status.lower():
                conn.commit()
                return False, ""
            rec["status"] = status
            rec["updated_at"] = datetime.utcnow().isoformat()
            if extra:
                rec.update({k: v for k, v in extra.items() if v})
            params = record_to_params(
                rec,
                tenant_id=tenant_cfg.tenant_id,
                client_id=client_id,
                environment=env,
            )
            cur.execute(_UPSERT_SQL.format(schema=schema), params)
            conn.commit()
    return True, index_ref(client_id, env)
