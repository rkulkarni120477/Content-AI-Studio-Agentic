"""Request/response shapes for archiving design documents (CDDs, Blueprints).

Shared rather than duplicated per document kind: the two archive flows are the
same flow, and the cost of them drifting apart is a delete that behaves one way
for CDDs and another for blueprints.

See ``app/services/design_doc_archive.py`` for the rules these shapes carry.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DocumentReferences(BaseModel):
    """What currently points at a design document.

    Returned alongside every list row so the UI can mark what is safe to delete
    without a request per row, and returned on a refused purge so it can say why.
    """

    is_pinned: bool = Field(
        default=False,
        description="True if any course has this document set as its active one.",
    )
    pinned_by: List[int] = Field(
        default_factory=list,
        description="Course ids with this document pinned as active.",
    )
    blueprint_count: int = Field(
        default=0,
        description=(
            "CDDs only. Blueprints derived from this CDD. Permanently deleting "
            "the CDD would cascade-delete these and their version history."
        ),
    )
    generation_count: int = Field(
        default=0,
        description="Generations that recorded this document as their source.",
    )
    feedback_count: int = Field(
        default=0,
        description="Blueprints only. Reviewer-feedback documents and items mapped to it.",
    )
    version_count: int = Field(
        default=0,
        description="Saved versions. Owned by the document — deleted with it, never a blocker.",
    )
    can_purge: bool = Field(
        default=True,
        description="True when nothing references it, so a permanent delete is allowed.",
    )
    blockers: List[str] = Field(
        default_factory=list,
        description="Human-readable reasons a permanent delete is refused. Empty when can_purge.",
    )

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_refs(cls, refs) -> "DocumentReferences":
        """Build from a ``design_doc_archive.DocReferences``."""
        return cls(
            is_pinned=refs.is_pinned,
            pinned_by=list(refs.pinned_by),
            blueprint_count=refs.blueprint_count,
            generation_count=refs.generation_count,
            feedback_count=refs.feedback_count,
            version_count=refs.version_count,
            can_purge=refs.can_purge,
            blockers=list(refs.blockers),
        )


class ArchiveRequest(BaseModel):
    """Body for archiving a single document."""

    unpin: bool = Field(
        default=False,
        description=(
            "Required to archive a document that a course has pinned as active. "
            "Clears the pin first. Without it, a pinned document is refused so "
            "nobody silently removes the document their next generation depends on."
        ),
    )


class ArchiveResponse(BaseModel):
    """Result of archiving or restoring one document."""

    id: int
    archived: bool = Field(description="The document's state after the call.")
    unpinned_courses: List[int] = Field(
        default_factory=list,
        description="Courses whose active-document pin was cleared to allow the archive.",
    )
    message: str = ""


class PurgeResponse(BaseModel):
    """Result of a permanent delete."""

    id: int
    deleted: bool = True
    message: str = ""


class BulkArchiveRequest(BaseModel):
    """Body for archiving many documents at once.

    Explicit ids only. There is deliberately no predicate form — a
    "delete everything matching X" endpoint is one bad filter away from
    emptying a workspace, and the ids the UI sends came from a list the user
    was just looking at.
    """

    ids: List[int] = Field(
        min_length=1, max_length=500,
        description="Document ids to archive. Duplicates are collapsed.",
    )
    unpin: bool = Field(
        default=False,
        description="Also archive documents that are pinned as active, clearing the pin.",
    )
    course_id: Optional[int] = Field(
        default=None,
        description=(
            "Restrict the operation to one course. Ids outside it are skipped "
            "rather than archived, so a stale id list cannot reach another workspace."
        ),
    )
    project_id: Optional[int] = Field(
        default=None,
        description="Restrict the operation to one project. Ids outside it are skipped.",
    )


class BulkArchiveItemResult(BaseModel):
    """One id's outcome within a bulk request."""

    id: int
    ok: bool
    status: str = Field(description="archived | already_archived | skipped")
    reason: str = Field(default="", description="Why it was skipped, when it was.")
    unpinned_courses: List[int] = Field(default_factory=list)


class BulkArchiveResponse(BaseModel):
    """Per-id outcomes plus the counts a toast needs.

    Every id gets a row: a partial success has to be legible, not rounded to
    "done" or "failed".
    """

    archived: int = 0
    skipped: int = 0
    already_archived: int = 0
    results: List[BulkArchiveItemResult] = Field(default_factory=list)

    @classmethod
    def from_outcomes(cls, outcomes) -> "BulkArchiveResponse":
        """Build from a list of ``design_doc_archive.ArchiveOutcome``."""
        results = [
            BulkArchiveItemResult(
                id=o.doc_id, ok=o.ok, status=o.status, reason=o.reason,
                unpinned_courses=list(o.unpinned_courses),
            )
            for o in outcomes
        ]
        return cls(
            archived=sum(1 for o in outcomes if o.status == "archived"),
            skipped=sum(1 for o in outcomes if o.status == "skipped"),
            already_archived=sum(1 for o in outcomes if o.status == "already_archived"),
            results=results,
        )
