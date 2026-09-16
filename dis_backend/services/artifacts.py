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

from config.settings import S3Config, TenantConfig, get_settings

log = logging.getLogger(__name__)

#: Bounded because every caller in this module is interactive: these are small
#: JSON artifacts and key listings, not the large multi-part transfers that
#: storage/provider.py deliberately gives long timeouts to. Three attempts still
#: absorbs a transient blip, where provider.py's five 30-second connect attempts
#: turn one bad endpoint into a two-minute stall inside a request someone is
#: waiting on.
S3_CONNECT_TIMEOUT_SECONDS = 10
S3_READ_TIMEOUT_SECONDS = 30
S3_MAX_ATTEMPTS = 3


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
        self.raw_bucket = getattr(tenant_cfg.storage, "raw_bucket", None) or self.processed_bucket
        self.base_prefix = getattr(tenant_cfg.storage, "base_prefix", "")

    def _logical_key(self, key: str) -> str:
        return _strip_prefix(self.base_prefix, (key or "").replace("\\", "/").lstrip("/"))

    def _bucket_for_prefix(self, prefix: str) -> str:
        """Route listings the same way S3Provider routes objects: processed/ vs raw/."""
        logical = self._logical_key(prefix)
        if logical.startswith("processed/"):
            return self.processed_bucket
        return self.raw_bucket

    def job_prefix(self, namespace: str, job_id: str) -> str:
        env = self.settings.environment or "development"
        return f"processed/{namespace}/{env}/{job_id}"

    def _s3_client(self):
        """Return a cached boto3 S3 client.

        Creating a boto3 client is expensive (~1s: credential + endpoint
        resolution). Retrieval reads one content.json per source document, so a
        per-call client made every read the bottleneck (~1s x N docs). Caching it
        on the instance makes each read a fast GET. Behavior is unchanged.
        """
        client = getattr(self, "_cached_s3", None)
        if client is not None:
            return client
        import boto3
        from botocore.config import Config

        cfg = self.tenant_cfg.storage.s3
        # The region comes from the tenant's own storage config, never from
        # settings.aws_region.
        #
        # settings.aws_region is where Bedrock runs, which is not where the
        # buckets are. Using it as the fallback addressed a us-east-1 bucket
        # through another region's host whenever the tenant config was not fully
        # resolved, and that does not fail cleanly: botocore retried the
        # unroutable endpoint and raised EndpointConnectionError after 70-140
        # seconds, surfacing in CAS as a retrieval that "didn't work" with no
        # usable reason. Guessing an unrelated region is worse than having no
        # preference, because it produces a plausible-looking endpoint for the
        # wrong place.
        #
        # The fallback reads S3Config's own declared default rather than
        # repeating a literal, so the default lives in exactly one place and
        # cannot drift from the schema.
        kwargs = {"region_name": cfg.region or S3Config.model_fields["region"].default}
        if cfg.endpoint_url:
            kwargs["endpoint_url"] = cfg.endpoint_url
        if self.settings.aws_access_key_id:
            kwargs["aws_access_key_id"] = self.settings.aws_access_key_id
            kwargs["aws_secret_access_key"] = self.settings.aws_secret_access_key
        kwargs["config"] = Config(
            connect_timeout=S3_CONNECT_TIMEOUT_SECONDS,
            read_timeout=S3_READ_TIMEOUT_SECONDS,
            retries={"max_attempts": S3_MAX_ATTEMPTS, "mode": "standard"},
        )
        client = boto3.client("s3", **kwargs)
        self._cached_s3 = client
        return client

    def write_json(self, key: str, payload: Any) -> str:
        data = json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default).encode("utf-8")
        if self.provider == "s3":
            cfg = self.tenant_cfg.storage.s3
            s3 = self._s3_client()
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
            s3 = self._s3_client()
            storage_key = _join_prefix(self.base_prefix, key)
            return json.loads(s3.get_object(Bucket=self.processed_bucket, Key=storage_key)["Body"].read())
        raise RuntimeError("DIS storage is S3-only. Cannot read local artifact storage.")

    def list_keys(self, prefix: str, suffix: str = "") -> List[str]:
        if self.provider == "s3":
            s3 = self._s3_client()
            bucket = self._bucket_for_prefix(prefix)
            keys: List[str] = []
            token = None
            while True:
                args = {"Bucket": bucket, "Prefix": _join_prefix(self.base_prefix, prefix)}
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

    def list_common_prefixes(self, prefix: str) -> List[str]:
        """Immediate child 'folders' under ``prefix`` (S3 Delimiter=/).

        Returns logical keys (no ``storage.base_prefix``), each with a trailing slash.
        """
        if self.provider != "s3":
            raise RuntimeError("DIS storage is S3-only. Cannot list local artifact storage.")
        s3 = self._s3_client()
        bucket = self._bucket_for_prefix(prefix)
        norm = (prefix or "").replace("\\", "/").lstrip("/")
        if norm and not norm.endswith("/"):
            norm += "/"
        storage_prefix = _join_prefix(self.base_prefix, norm)
        out: List[str] = []
        token = None
        while True:
            args = {"Bucket": bucket, "Prefix": storage_prefix, "Delimiter": "/"}
            if token:
                args["ContinuationToken"] = token
            resp = s3.list_objects_v2(**args)
            for item in resp.get("CommonPrefixes") or []:
                k = _strip_prefix(self.base_prefix, item.get("Prefix") or "")
                if not k:
                    continue
                out.append(k if k.endswith("/") else f"{k}/")
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return out


def write_step_artifact(tenant_cfg: TenantConfig, state: Dict[str, Any], step: str) -> str:
    writer = ArtifactWriter(tenant_cfg)
    prefix = writer.job_prefix(state.get("namespace", "unknown"), state.get("job_id", "unknown"))
    key = f"{prefix}/steps/{step}.json"
    return writer.write_json(key, safe_state_snapshot({**state, "artifact_step": step}))
