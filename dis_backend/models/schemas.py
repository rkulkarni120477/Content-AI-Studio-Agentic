from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PENDING = "pending"; VALIDATING = "validating"; QUARANTINED = "quarantined"
    PROCESSING = "processing"; COMPLETED = "completed"; FAILED = "failed"; DUPLICATE = "duplicate"

class ContentClassification(str, Enum):
    PUBLIC = "public"; INTERNAL = "internal"; RESTRICTED = "restricted"; EXAM_SECRET = "exam_secret"


class UploadResponse(BaseModel):
    job_id: str; tenant_id: str; client_id: str; namespace: str
    s3_key: str; upload_url: str; status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)

class ValidationResult(BaseModel):
    passed: bool; errors: List[str] = []; warnings: List[str] = []
    file_size_bytes: int = 0; detected_type: Optional[str] = None

class JobStatusResponse(BaseModel):
    job_id: str; tenant_id: str; client_id: str; status: JobStatus
    progress_pct: int = 0; current_step: Optional[str] = None
    error_message: Optional[str] = None; created_at: datetime; updated_at: datetime
    completed_at: Optional[datetime] = None; metadata: Dict[str, Any] = {}

class TenantSummary(BaseModel):
    tenant_id: str; display_name: str; namespace: str
    is_active: bool; client_count: int; storage_provider: str

class TokenRequest(BaseModel):
    user_id: str
    secret: str
    # v9: client_id is required for client_admin/user.
    # super_admin can omit it and DIS will select the first active client for Swagger testing.
    client_id: str = ""

class TokenResponse(BaseModel):
    access_token: str; token_type: str = "bearer"; expires_in: int
    tenant_id: str; client_id: str; user_id: str; role: str
