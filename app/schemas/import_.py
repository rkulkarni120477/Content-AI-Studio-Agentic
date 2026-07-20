"""Import (reverse pipeline) schemas.

Mirrors the pydantic patterns in ``app/schemas/course.py``. Only the health DTO
exists in Session 0 (scaffolding); validate/start/status DTOs are added in later
sessions as their endpoints land. See reverse_cas.md.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImportHealthResponse(BaseModel):
    """Capability probe for the reverse-pipeline feature."""
    enabled: bool = Field(..., description="True when IMPORT_COURSES_ENABLED is on.")


class StructureCounts(BaseModel):
    """Pre-flight counts of what the parser found in the package."""
    modules: int = 0
    pages: int = 0
    quizzes: int = 0
    assignments: int = 0
    discussions: int = 0
    resources: int = 0


class ImportValidateResponse(BaseModel):
    """Result of POST /imports/validate — structure preview, no DB writes."""
    package_name: str = Field(..., description="Original uploaded filename.")
    course_title: str = Field(..., description="Course title read from the manifest.")
    structure_counts: StructureCounts
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal items flagged for review (e.g. external LTI).",
    )


class ImportStartResponse(BaseModel):
    """Result of POST /projects/{projectId}/imports — course shell + enqueued job."""
    course_id: int = Field(..., description="Newly created course shell id.")
    import_id: int = Field(..., description="course_imports record id.")
    job_id: str = Field(..., description="GenerationJob id — poll GET /jobs/{jobId} for progress.")


class ImportRecordResponse(BaseModel):
    """An import record + latest status (GET /imports/{importId})."""
    id: int
    course_id: int | None = None
    project_id: int | None = None
    package_name: str | None = None
    status: str
    provenance_ready: bool = False
    structure_counts: StructureCounts | None = None
    warnings: list[str] = Field(default_factory=list)
