"""Artifact writer for DIS pipeline.

Stores every step output as JSON in S3.
All Source Library persistence is S3-only: raw files, payload.json,
source_content/content.json, and source_index/source_list.json.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, Iterable, List

from config.settings import TenantConfig, get_settings

log = logging.getLogger(__name__)


def _join_prefix(prefix: str, key: str) -> str:
    p = (prefix or "").strip().strip("/")
    k = (key or "").replace("\\", "/").lstrip("/")
    if not p:
        return k
    if k == p or k.startswith(p + "/"):
        return k
    return f"{p}/{k}"


def _strip_prefix(prefix: str, key: str) -> str:
    p = (prefix or "").strip().strip("/")
    k = (key or "").replace("\\", "/").lstrip("/")
    if p and k.startswith(p + "/"):
        return k[len(p) + 1:]
    return k


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return f"<bytes:{len(obj)}>"
    try:
        return str(obj)
    except Exception:
        return None


def safe_state_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return a JSON-safe state snapshot without heavy binary fields."""
    omit = {"raw_bytes"}
    snap = {k: v for k, v in state.items() if k not in omit}
    if "raw_text" in snap and isinstance(snap["raw_text"], str):
        snap["raw_text_preview"] = snap["raw_text"][:2000]
        snap["raw_text_length"] = len(snap["raw_text"])
        snap.pop("raw_text", None)
    return snap


class ArtifactWriter:
    def __init__(self, tenant_cfg: TenantConfig):
        self.tenant_cfg = tenant_cfg
        self.settings = get_settings()
        self.provider = tenant_cfg.storage.provider.lower()
        self.base_path = Path(tenant_cfg.storage.local.base_path)
        self.processed_bucket = tenant_cfg.storage.processed_bucket
        self.base_prefix = getattr(tenant_cfg.storage, "base_prefix", "")

    def job_prefix(self, namespace: str, job_id: str) -> str:
        env = self.settings.environment or "development"
        return f"processed/{namespace}/{env}/{job_id}"

    def write_json(self, key: str, payload: Any) -> str:
        data = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default).encode("utf-8")
        if self.provider == "s3":
            import boto3
            cfg = self.tenant_cfg.storage.s3
            kwargs = {"region_name": cfg.region or self.settings.aws_region}
            if cfg.endpoint_url:
                kwargs["endpoint_url"] = cfg.endpoint_url
            if self.settings.aws_access_key_id:
                kwargs["aws_access_key_id"] = self.settings.aws_access_key_id
                kwargs["aws_secret_access_key"] = self.settings.aws_secret_access_key
            s3 = boto3.client("s3", **kwargs)
            extra = {"ContentType": "application/json"}
            if cfg.kms_key_id:
                extra["ServerSideEncryption"] = "aws:kms"
                extra["SSEKMSKeyId"] = cfg.kms_key_id
            storage_key = _join_prefix(self.base_prefix, key)
            s3.put_object(Bucket=self.processed_bucket, Key=storage_key, Body=data, **extra)
            return f"s3://{self.processed_bucket}/{storage_key}"

        raise RuntimeError("DIS storage is S3-only. Set storage.provider=s3 and DIS_S3_BUCKET/DIS_RAW_BUCKET/DIS_PROCESSED_BUCKET.")

    def read_json(self, key: str) -> Any:
        if self.provider == "s3":
            import boto3
            cfg = self.tenant_cfg.storage.s3
            kwargs = {"region_name": cfg.region or self.settings.aws_region}
            if cfg.endpoint_url:
                kwargs["endpoint_url"] = cfg.endpoint_url
            s3 = boto3.client("s3", **kwargs)
            storage_key = _join_prefix(self.base_prefix, key)
            return json.loads(s3.get_object(Bucket=self.processed_bucket, Key=storage_key)["Body"].read())
        raise RuntimeError("DIS storage is S3-only. Cannot read local artifact storage.")

    def list_keys(self, prefix: str, suffix: str = "") -> List[str]:
        if self.provider == "s3":
            import boto3
            cfg = self.tenant_cfg.storage.s3
            kwargs = {"region_name": cfg.region or self.settings.aws_region}
            if cfg.endpoint_url:
                kwargs["endpoint_url"] = cfg.endpoint_url
            s3 = boto3.client("s3", **kwargs)
            keys: List[str] = []
            token = None
            while True:
                args = {"Bucket": self.processed_bucket, "Prefix": _join_prefix(self.base_prefix, prefix)}
                if token:
                    args["ContinuationToken"] = token
                resp = s3.list_objects_v2(**args)
                for obj in resp.get("Contents", []):
                    k = obj["Key"]
                    if not suffix or k.endswith(suffix):
                        keys.append(_strip_prefix(self.base_prefix, k))
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
            return keys

        raise RuntimeError("DIS storage is S3-only. Cannot list local artifact storage.")


def write_step_artifact(tenant_cfg: TenantConfig, state: Dict[str, Any], step: str) -> str:
    writer = ArtifactWriter(tenant_cfg)
    prefix = writer.job_prefix(state.get("namespace", "unknown"), state.get("job_id", "unknown"))
    key = f"{prefix}/steps/{step}.json"
    return writer.write_json(key, safe_state_snapshot({**state, "artifact_step": step}))
