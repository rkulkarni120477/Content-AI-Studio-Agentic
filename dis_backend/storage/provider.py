"""
DIS – Multi-Cloud Storage Provider
====================================
Unified interface for S3 / Azure Blob / GCP Cloud Storage / Local filesystem.
Tenant YAML decides which backend is used.  Switch per tenant with zero code change.

Usage:
    provider = get_storage_provider(tenant_cfg)
    url = await provider.upload(key="aim_ns/job1/file.pdf", data=bytes)
    data = await provider.download(key="aim_ns/job1/file.pdf")
    await provider.delete(key="aim_ns/job1/file.pdf")
    presigned = await provider.presigned_url(key="...", expires=3600)
"""

from __future__ import annotations
import abc
import logging
import os
from pathlib import Path
from typing import Optional

from config.settings import StorageConfig, TenantConfig

log = logging.getLogger(__name__)


def _join_prefix(prefix: str, key: str) -> str:
    """Join an optional storage base prefix with a logical key safely.

    Example: prefix="DIS", key="aim/raw/file.pdf" -> "DIS/aim/raw/file.pdf".
    If the key is already prefixed, it is returned unchanged.
    """
    p = (prefix or "").strip().strip("/")
    k = (key or "").replace("\\", "/").lstrip("/")
    if not p:
        return k
    if k == p or k.startswith(p + "/"):
        return k
    return f"{p}/{k}"


# ── Abstract Base ─────────────────────────────────────────────────────────────

class StorageProvider(abc.ABC):

    @abc.abstractmethod
    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload bytes. Returns the storage URL/key."""

    @abc.abstractmethod
    async def download(self, key: str) -> bytes:
        """Download and return bytes."""

    @abc.abstractmethod
    async def delete(self, key: str) -> None:
        """Delete object."""

    @abc.abstractmethod
    async def presigned_url(self, key: str, expires: int = 3600) -> str:
        """Generate a pre-signed URL for direct upload or download."""

    @abc.abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if object exists."""


# ── AWS S3 ────────────────────────────────────────────────────────────────────

class S3Provider(StorageProvider):
    """Uses boto3. Works with real S3 or LocalStack (set endpoint_url)."""

    def __init__(self, cfg: StorageConfig, aws_access_key_id=None,
                 aws_secret_access_key=None, region="us-east-1"):
        import boto3
        kwargs = dict(region_name=cfg.s3.region or region)
        if cfg.s3.endpoint_url:
            kwargs["endpoint_url"] = cfg.s3.endpoint_url
        if aws_access_key_id:
            kwargs["aws_access_key_id"] = aws_access_key_id
            kwargs["aws_secret_access_key"] = aws_secret_access_key
        self._s3 = boto3.client("s3", **kwargs)
        self._raw = cfg.raw_bucket
        self._proc = cfg.processed_bucket
        self._base_prefix = getattr(cfg, "base_prefix", "")
        self._kms = cfg.s3.kms_key_id

    def _bucket_for(self, key: str) -> str:
        logical = key.replace("\\", "/").lstrip("/")
        # Bucket selection is based on the logical key, before base_prefix is applied.
        if self._base_prefix and logical.startswith(self._base_prefix.strip("/") + "/"):
            logical = logical[len(self._base_prefix.strip("/")) + 1:]
        return self._proc if logical.startswith("processed/") else self._raw

    def _key_for_storage(self, key: str) -> str:
        return _join_prefix(self._base_prefix, key)

    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        bucket = self._bucket_for(key)
        extra = {"ContentType": content_type}
        if self._kms:
            extra["ServerSideEncryption"] = "aws:kms"
            extra["SSEKMSKeyId"] = self._kms
        storage_key = self._key_for_storage(key)
        self._s3.put_object(Bucket=bucket, Key=storage_key, Body=data, **extra)
        log.info("[S3] uploaded s3://%s/%s (%d bytes)", bucket, storage_key, len(data))
        return f"s3://{bucket}/{storage_key}"

    async def download(self, key: str) -> bytes:
        bucket = self._bucket_for(key)
        resp = self._s3.get_object(Bucket=bucket, Key=self._key_for_storage(key))
        return resp["Body"].read()

    async def delete(self, key: str) -> None:
        bucket = self._bucket_for(key)
        self._s3.delete_object(Bucket=bucket, Key=self._key_for_storage(key))

    async def presigned_url(self, key: str, expires: int = 3600) -> str:
        bucket = self._bucket_for(key)
        return self._s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": self._key_for_storage(key)},
            ExpiresIn=expires,
        )

    async def exists(self, key: str) -> bool:
        import botocore
        bucket = self._bucket_for(key)
        try:
            self._s3.head_object(Bucket=bucket, Key=self._key_for_storage(key))
            return True
        except botocore.exceptions.ClientError:
            return False


# ── Azure Blob Storage ────────────────────────────────────────────────────────

class AzureProvider(StorageProvider):
    """Uses azure-storage-blob. pip install azure-storage-blob"""

    def __init__(self, cfg: StorageConfig):
        from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
        self._gen_sas = generate_blob_sas
        self._sas_perms = BlobSasPermissions
        account_key = os.environ.get(cfg.azure.account_key_secret, "")
        conn_str = (
            f"DefaultEndpointsProtocol=https;"
            f"AccountName={cfg.azure.account_name};"
            f"AccountKey={account_key};"
            f"EndpointSuffix=core.windows.net"
        )
        self._client = BlobServiceClient.from_connection_string(conn_str)
        self._raw_container = cfg.azure.container_raw
        self._proc_container = cfg.azure.container_processed
        self._account = cfg.azure.account_name
        self._key = account_key

    def _container(self, key: str) -> str:
        return self._proc_container if key.startswith("processed/") else self._raw_container

    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        container = self._container(key)
        blob = self._client.get_blob_client(container=container, blob=key)
        blob.upload_blob(data, overwrite=True, content_settings={"content_type": content_type})
        return f"https://{self._account}.blob.core.windows.net/{container}/{key}"

    async def download(self, key: str) -> bytes:
        container = self._container(key)
        blob = self._client.get_blob_client(container=container, blob=key)
        return blob.download_blob().readall()

    async def delete(self, key: str) -> None:
        container = self._container(key)
        self._client.get_blob_client(container=container, blob=key).delete_blob()

    async def presigned_url(self, key: str, expires: int = 3600) -> str:
        import datetime
        container = self._container(key)
        sas = self._gen_sas(
            account_name=self._account,
            container_name=container,
            blob_name=key,
            account_key=self._key,
            permission=self._sas_perms(write=True),
            expiry=datetime.datetime.utcnow() + datetime.timedelta(seconds=expires),
        )
        return f"https://{self._account}.blob.core.windows.net/{container}/{key}?{sas}"

    async def exists(self, key: str) -> bool:
        container = self._container(key)
        blob = self._client.get_blob_client(container=container, blob=key)
        return blob.exists()


# ── GCP Cloud Storage ─────────────────────────────────────────────────────────

class GCPProvider(StorageProvider):
    """Uses google-cloud-storage. pip install google-cloud-storage"""

    def __init__(self, cfg: StorageConfig):
        import json
        from google.cloud import storage
        from google.oauth2 import service_account
        creds_json = os.environ.get(cfg.gcp.credentials_secret, "{}")
        creds = service_account.Credentials.from_service_account_info(json.loads(creds_json))
        self._client = storage.Client(project=cfg.gcp.project_id, credentials=creds)
        self._raw_bucket = cfg.gcp.bucket_raw
        self._proc_bucket = cfg.gcp.bucket_processed

    def _bucket_name(self, key: str) -> str:
        return self._proc_bucket if key.startswith("processed/") else self._raw_bucket

    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        bucket = self._client.bucket(self._bucket_name(key))
        blob = bucket.blob(key)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{self._bucket_name(key)}/{key}"

    async def download(self, key: str) -> bytes:
        bucket = self._client.bucket(self._bucket_name(key))
        return bucket.blob(key).download_as_bytes()

    async def delete(self, key: str) -> None:
        bucket = self._client.bucket(self._bucket_name(key))
        bucket.blob(key).delete()

    async def presigned_url(self, key: str, expires: int = 3600) -> str:
        import datetime
        bucket = self._client.bucket(self._bucket_name(key))
        blob = bucket.blob(key)
        return blob.generate_signed_url(
            expiration=datetime.timedelta(seconds=expires),
            method="PUT",
        )

    async def exists(self, key: str) -> bool:
        bucket = self._client.bucket(self._bucket_name(key))
        return bucket.blob(key).exists()


# ── Local Filesystem (dev only) ───────────────────────────────────────────────

class LocalProvider(StorageProvider):
    """Stores files on local disk. No AWS/Azure/GCP needed for local dev."""

    def __init__(self, cfg: StorageConfig):
        self._base_prefix = getattr(cfg, "base_prefix", "")
        self._base = Path(cfg.local.base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        (self._base / "raw").mkdir(exist_ok=True)
        (self._base / "processed").mkdir(exist_ok=True)

    def _path(self, key: str) -> Path:
        logical = key.replace("\\", "/").lstrip("/")
        if self._base_prefix and logical.startswith(self._base_prefix.strip("/") + "/"):
            logical_for_bucket = logical[len(self._base_prefix.strip("/")) + 1:]
        else:
            logical_for_bucket = logical
        storage_key = _join_prefix(self._base_prefix, key)
        folder = self._base / ("processed" if logical_for_bucket.startswith("processed/") else "raw")
        full = folder / storage_key
        full.parent.mkdir(parents=True, exist_ok=True)
        return full

    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.write_bytes(data)
        return f"local://{path}"

    async def download(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    async def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    async def presigned_url(self, key: str, expires: int = 3600) -> str:
        # Local dev: return fake URL; actual upload done via API
        return f"http://localhost:8000/v1/dev/upload/{key}"

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()


# ── Factory ───────────────────────────────────────────────────────────────────

_provider_cache: dict[str, StorageProvider] = {}


def get_storage_provider(tenant_cfg: TenantConfig) -> StorageProvider:
    """
    Returns the right storage provider for this tenant.
    Cached per tenant_id so boto3/azure clients are reused.
    """
    tid = tenant_cfg.tenant_id
    if tid in _provider_cache:
        return _provider_cache[tid]

    cfg = tenant_cfg.storage
    provider_name = cfg.provider.lower()

    if provider_name == "s3":
        from config.settings import get_settings
        s = get_settings()
        p = S3Provider(cfg, s.aws_access_key_id, s.aws_secret_access_key, s.aws_region)
        # Override endpoint_url if set in global settings (LocalStack)
        if s.aws_endpoint_url and not cfg.s3.endpoint_url:
            cfg.s3.endpoint_url = s.aws_endpoint_url
        p = S3Provider(cfg, s.aws_access_key_id, s.aws_secret_access_key, s.aws_region)

    elif provider_name == "azure":
        p = AzureProvider(cfg)

    elif provider_name == "gcp":
        p = GCPProvider(cfg)

    elif provider_name == "local":
        raise ValueError("Local storage is disabled. DIS is S3-only for Source Library persistence. Use provider: s3 and DIS_S3_BUCKET.")

    else:
        raise ValueError(f"Unknown storage provider: '{provider_name}'. Use: s3")

    _provider_cache[tid] = p
    log.info("[Storage] tenant=%s provider=%s", tid, provider_name)
    return p
