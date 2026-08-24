"""Course schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

_DIGEST_FLAG_DESCRIPTION = (
    "Whether the block-wide digest pipeline (CDD / Block Blueprint) can run against "
    "THIS course — i.e. whether the course's project client is enabled for it. The "
    "generate-block endpoints gate on exactly this, so the UI must gate the panel on "
    "it too rather than on the caller's own client, which is a different question and "
    "produced a 400 on 2 of every 3 courses. Computed, not stored: see "
    "app.core.dis_access.digest_pipeline_enabled_for_course."
)


class CourseCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300, examples=["Module 1 — Fundamentals"])
    cluster_id: Optional[int] = Field(default=None, description="Optional cluster grouping.")
    description: Optional[str] = Field(default=None, max_length=2000)


class CourseUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=2000)
    cluster_id: Optional[int] = None


class CourseUserAssignRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)


class CourseUserListItem(BaseModel):
    username: str


class CourseRead(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    project_id: Optional[int] = None
    cluster_id: Optional[int] = None
    active_cdd_id: Optional[int] = Field(
        default=None,
        description="The CDD currently pinned for generation on this course.",
    )
    active_blueprint_id: Optional[int] = Field(
        default=None,
        description="The Blueprint currently pinned for generation on this course.",
    )
    source_type: Optional[str] = Field(
        default=None,
        description="Origin: None/'scratch' for authored courses, 'imscc' for imports. Display-only.",
    )
    import_id: Optional[int] = Field(
        default=None,
        description="course_imports.id when this course was imported (for warnings surfacing).",
    )
    created_at: Optional[datetime] = None
    digest_pipeline_enabled: bool = Field(
        default=False,
        description=_DIGEST_FLAG_DESCRIPTION,
    )

    model_config = ConfigDict(from_attributes=True)


class CourseListItem(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    cluster_id: Optional[int] = None
    active_cdd_id: Optional[int] = None
    active_blueprint_id: Optional[int] = None
    source_type: Optional[str] = None          # display-only origin badge
    is_active: bool = True                     # False = archived (soft-deleted)
    created_at: Optional[datetime] = None
    digest_pipeline_enabled: bool = Field(
        default=False,
        description=_DIGEST_FLAG_DESCRIPTION,
    )

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Builders — the ONLY way these two payloads are constructed
# --------------------------------------------------------------------------- #
# ``digest_pipeline_enabled`` is not a column: it is computed from settings plus the
# course's project client, so ``model_validate(course)`` cannot produce it and would
# silently emit the schema default instead. A capability flag defaulting to False is
# indistinguishable from a definitive "no" — that exact shape hid the block-wide panel
# for every freshly-logged-in user until 2026-08-13 (see auth.build_user_profile).
#
# Both builders therefore take it as a REQUIRED KEYWORD. Forgetting it is a TypeError
# at the call site, not a wrong answer served to a browser.

def build_course_read(course, *, digest_pipeline_enabled: bool) -> "CourseRead":
    """Build a CourseRead. Do not construct CourseRead anywhere else."""
    return CourseRead.model_validate(course).model_copy(
        update={"digest_pipeline_enabled": digest_pipeline_enabled})


def build_course_list_item(course, *, digest_pipeline_enabled: bool) -> "CourseListItem":
    """Build a CourseListItem. Do not construct CourseListItem anywhere else.

    Callers listing a whole project should resolve the flag ONCE and pass the same
    value for every row — it is a property of the project's client, so recomputing it
    per course is a needless query per row.
    """
    return CourseListItem.model_validate(course).model_copy(
        update={"digest_pipeline_enabled": digest_pipeline_enabled})
