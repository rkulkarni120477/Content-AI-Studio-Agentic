"""
Editor asset storage — uploads inline editor images to S3.

Reuses the DIS S3 bucket and the shared AWS credentials/region from settings
(see ``app.core.config``). Objects are written public-read under a
``cas-assets/`` prefix so they render directly in ``<img>`` tags without an
auth header, and the returned URL stays valid indefinitely.

This is intentionally small and self-contained: CAS talks to S3 directly here
only for editor images. All other document storage still flows through DIS.
"""

from __future__ import annotations

import logging
import uuid

from app.core.config import settings
from app.core.exceptions import ExportError, ValidationError

_log = logging.getLogger(__name__)

# content-type -> file extension for the allowlisted image formats.
ALLOWED_IMAGE_TYPES: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}

_ASSET_PREFIX = "cas-assets"


def _join_prefix(base_prefix: str, key: str) -> str:
    """Join the optional S3 base prefix with a key (mirrors DIS's _join_prefix)."""
    p = (base_prefix or "").strip().strip("/")
    k = (key or "").replace("\\", "/").lstrip("/")
    if not p:
        return k
    if k == p or k.startswith(p + "/"):
        return k
    return f"{p}/{k}"


def _asset_region(settings) -> str:
    """S3 region for editor assets — dedicated value, else the main AWS region."""
    return settings.assets_s3_region or settings.aws_region


def _asset_credentials(settings):
    """(access_key, secret_key) for asset S3 — dedicated values, else the main ones."""
    access_key = (
        settings.assets_s3_access_key_id.get_secret_value()
        or settings.aws_access_key_id.get_secret_value()
    )
    secret_key = (
        settings.assets_s3_secret_access_key.get_secret_value()
        or settings.aws_secret_access_key.get_secret_value()
    )
    return access_key, secret_key


def _build_client(settings):
    import boto3

    kwargs = {"region_name": _asset_region(settings)}
    if settings.aws_endpoint_url:
        kwargs["endpoint_url"] = settings.aws_endpoint_url
    access_key, secret_key = _asset_credentials(settings)
    if access_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client("s3", **kwargs)


def _public_url(settings, bucket: str, storage_key: str) -> str:
    """Construct the browser-facing URL for a public-read object."""
    if settings.aws_endpoint_url:
        # LocalStack / custom endpoint: path-style URL.
        return f"{settings.aws_endpoint_url.rstrip('/')}/{bucket}/{storage_key}"
    return f"https://{bucket}.s3.{_asset_region(settings)}.amazonaws.com/{storage_key}"


def upload_image(data: bytes, content_type: str) -> str:
    """
    Upload image bytes to S3 (public-read) and return a browser-facing URL.

    Raises AppError if storage is not configured or the upload fails.
    """
    bucket = settings.assets_s3_bucket
    if not bucket:
        raise ExportError("Image storage is not configured. Set DIS_S3_BUCKET to enable uploads.")

    ext = ALLOWED_IMAGE_TYPES.get(content_type)
    if not ext:
        raise ValidationError(f"Unsupported image type: {content_type}")

    logical_key = f"{_ASSET_PREFIX}/{uuid.uuid4().hex}.{ext}"
    storage_key = _join_prefix(settings.assets_s3_base_prefix, logical_key)

    put_args = dict(
        Bucket=bucket,
        Key=storage_key,
        Body=data,
        ContentType=content_type,
        CacheControl="public, max-age=31536000, immutable",
    )

    try:
        import botocore.exceptions

        client = _build_client(settings)
        if settings.assets_s3_public_acl:
            try:
                # Per-object public-read ACL (only when the bucket supports ACLs).
                client.put_object(ACL="public-read", **put_args)
            except botocore.exceptions.ClientError as acl_exc:
                code = acl_exc.response.get("Error", {}).get("Code", "")
                if code not in ("AccessControlListNotSupported", "InvalidRequest", "AccessDenied"):
                    raise
                _log.info("asset_upload_no_acl  bucket rejected ACL (%s); storing without ACL", code)
                client.put_object(**put_args)
        else:
            # Default: no ACL. Public read comes from a bucket policy on the
            # cas-assets/ prefix (the shared DIS bucket has ACLs disabled).
            client.put_object(**put_args)
    except Exception as exc:  # noqa: BLE001 — surface any boto/client failure uniformly
        _log.exception("asset_upload_failed  key=%s", storage_key)
        raise ExportError("Image upload failed. Please try again.") from exc

    url = _public_url(settings, bucket, storage_key)
    _log.info("asset_uploaded  key=%s  bytes=%d", storage_key, len(data))
    return url


def delete_asset(key: str) -> bool:
    """
    Best-effort delete of an uploaded asset by its S3 key. Never raises — returns
    True on success, False otherwise. No-op if storage is not configured.
    """
    if not key or not settings.assets_s3_bucket:
        return False
    try:
        _build_client(settings).delete_object(Bucket=settings.assets_s3_bucket, Key=key)
        _log.info("asset_deleted  key=%s", key)
        return True
    except Exception:  # noqa: BLE001 — cleanup is best-effort, must not break callers
        _log.exception("asset_delete_failed  key=%s", key)
        return False
