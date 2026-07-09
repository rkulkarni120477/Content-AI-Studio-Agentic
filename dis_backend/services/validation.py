"""DIS – Admission Gate (Step 2.5) — runs before S3, rejects before pipeline."""
from __future__ import annotations
import hashlib, json, logging
from typing import Optional, Set, Dict, Tuple
from config.settings import ClientConfig, TenantConfig
from models.schemas import ValidationResult

log = logging.getLogger(__name__)
_dedup: Dict[str, Set[str]] = {}

_MIME_MAP = {
    "pdf": "application/pdf", "docx": "application/vnd.openxmlformats",
    "pptx": "application/vnd.openxmlformats", "xlsx": "application/vnd.openxmlformats",
    "txt": "text/", "csv": "text/", "json": "application/json",
    "jpg": "image/", "jpeg": "image/", "png": "image/",
}

class ValidationService:
    def __init__(self, tenant_cfg: TenantConfig, client_cfg: Optional[ClientConfig] = None):
        self.tenant = tenant_cfg
        self.client = client_cfg

    def fingerprint(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _dedup_key(self) -> str:
        # Keep duplicate scope client-wise, not only tenant-wise.
        # Same file in another client should not be treated as duplicate.
        client_id = self.client.client_id if self.client else "default"
        return f"{self.tenant.tenant_id}:{client_id}"

    def is_duplicate(self, content: bytes) -> Tuple[bool, str]:
        fp = self.fingerprint(content)
        if not self.tenant.ingestion.dedup_enabled:
            return False, fp
        seen = _dedup.setdefault(self._dedup_key(), set())
        return fp in seen, fp

    def mark_seen(self, content: bytes) -> str:
        fp = self.fingerprint(content)
        if self.tenant.ingestion.dedup_enabled:
            seen = _dedup.setdefault(self._dedup_key(), set())
            seen.add(fp)
        return fp

    async def validate(self, filename: str, content: bytes, declared_type: str = "") -> ValidationResult:
        errors, warnings = [], []
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        size_mb = len(content) / (1024 * 1024)
        allowed = self.client.allowed_file_types if self.client else ["pdf","docx","txt","csv","json","xlsx","pptx","jpg","png"]
        max_mb = self.client.max_file_size_mb if self.client else 100

        if ext not in allowed:
            errors.append(f"File type '.{ext}' not allowed. Allowed: {allowed}")
        if size_mb > max_mb:
            errors.append(f"File size {size_mb:.1f}MB exceeds limit {max_mb}MB")
        if ext == "json":
            try: json.loads(content)
            except Exception as e: errors.append(f"Invalid JSON: {e}")

        # Duplicate detection is intentionally NOT performed here.
        # Why: this validation gate runs before storage upload. If we add the hash here
        # and storage/pipeline fails, a later retry of the same file becomes a false duplicate.
        # Duplicate check/mark is done by the ingestion router only after raw upload succeeds.

        if errors and self.tenant.ingestion.quarantine_on_fail:
            log.warning("[Validation] REJECTED %s: %s", filename, errors)

        return ValidationResult(passed=len(errors) == 0, errors=errors, warnings=warnings,
                                file_size_bytes=len(content))
