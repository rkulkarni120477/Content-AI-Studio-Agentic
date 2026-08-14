"""Archive / restore / purge for design documents (CDDs and Blueprints).

Why this exists
---------------
Neither CDDs nor Blueprints had a delete of any kind, so every generation stayed
in the picker forever — duplicates, failed runs, abandoned experiments and all.
One course reached 55 CDDs, nine of them from a single afternoon of retries.

Why archive rather than delete
------------------------------
``CourseDesignDocument.blueprints`` carries ``cascade="all, delete-orphan"``.
``db.delete(cdd)`` therefore deletes every blueprint derived from that CDD *and*
(through ``ModuleBlueprint.versions``) their entire version history — silently,
with no FK error to warn anyone. Half the CDDs in the database have derived
blueprints. So the everyday action is a reversible archive, and the irreversible
purge is gated behind a check that nothing points at the row at all.

Why one module for two document kinds
-------------------------------------
The rules are identical and the consequences of them drifting apart are data
loss. ``DocKind`` describes what differs (which table, which pin column, which
rows can reference it); everything else is written once.

Reference counting is batched
-----------------------------
``reference_counts()`` takes a list of ids and issues a fixed number of grouped
queries — five, regardless of whether it is asked about one document or two
hundred. The list endpoints call it once per page, so showing "safe to delete"
next to every row costs the same as showing it next to none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceInUseError, ValidationError
from promptops_app.database import (
    BlueprintVersion,
    CDDVersion,
    Course,
    CourseDesignDocument,
    FeedbackDocument,
    FeedbackItem,
    Generation,
    ModuleBlueprint,
)

# A bulk request names its ids explicitly — there is deliberately no
# predicate-driven mass delete — so this only bounds one request's work.
MAX_BULK_IDS = 500


# ---------------------------------------------------------------------------
# Document kinds
# ---------------------------------------------------------------------------

# eq=False so the two constants below compare by identity. The generated __eq__
# would compare InstrumentedAttribute fields, and ``Course.active_cdd_id ==
# Course.active_cdd_id`` builds a SQL expression rather than returning a bool.
@dataclass(frozen=True, eq=False)
class DocKind:
    """What differs between a CDD and a Blueprint, for archive purposes."""

    key: str                 # "cdd" | "blueprint" — audit action prefix
    label: str               # user-facing noun in error messages
    model: type              # CourseDesignDocument | ModuleBlueprint
    version_model: type      # CDDVersion | BlueprintVersion
    version_fk: Any          # the version table's FK column back to the document
    pin_column: Any          # Course.active_cdd_id | Course.active_blueprint_id
    generation_fk: Any       # Generation.cdd_id | Generation.blueprint_id


CDD = DocKind(
    key="cdd",
    label="CDD",
    model=CourseDesignDocument,
    version_model=CDDVersion,
    version_fk=CDDVersion.cdd_id,
    pin_column=Course.active_cdd_id,
    generation_fk=Generation.cdd_id,
)

BLUEPRINT = DocKind(
    key="blueprint",
    label="Blueprint",
    model=ModuleBlueprint,
    version_model=BlueprintVersion,
    version_fk=BlueprintVersion.blueprint_id,
    pin_column=Course.active_blueprint_id,
    generation_fk=Generation.blueprint_id,
)


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DocReferences:
    """Everything pointing at one design document.

    ``version_count`` is informational — versions are owned children and are
    deleted with the document. The other fields are what block a purge.
    """

    pinned_by: tuple[int, ...] = ()      # course ids with this doc set active
    blueprint_count: int = 0             # CDDs only: blueprints derived from it
    generation_count: int = 0            # generations that recorded it
    feedback_count: int = 0              # Blueprints only: feedback docs + items
    version_count: int = 0

    @property
    def is_pinned(self) -> bool:
        return bool(self.pinned_by)

    @property
    def blockers(self) -> tuple[str, ...]:
        """Human-readable reasons a permanent delete is refused, in severity order."""
        out: list[str] = []
        if self.pinned_by:
            courses = ", ".join(str(c) for c in self.pinned_by)
            out.append(f"pinned as active on course {courses}")
        if self.blueprint_count:
            out.append(
                f"{self.blueprint_count} blueprint(s) were derived from it "
                "(deleting it would delete them too)"
            )
        if self.generation_count:
            out.append(f"{self.generation_count} generation(s) record it as their source")
        if self.feedback_count:
            out.append(f"{self.feedback_count} reviewer-feedback record(s) are mapped to it")
        return tuple(out)

    @property
    def can_purge(self) -> bool:
        return not self.blockers


def _count_map(rows: Iterable[Sequence[Any]]) -> dict[int, int]:
    """``[(doc_id, n), ...]`` -> ``{doc_id: n}``, skipping NULL keys."""
    return {int(doc_id): int(n) for doc_id, n in rows if doc_id is not None}


def reference_counts(db: Session, kind: DocKind, doc_ids: Sequence[int]) -> dict[int, DocReferences]:
    """Map every id in *doc_ids* to what currently references it.

    Fixed query count regardless of how many ids are passed, so callers can ask
    about a whole page without an N+1. Ids with nothing pointing at them still
    get an entry (an all-zero ``DocReferences``), so callers never need to guard
    for a missing key.
    """
    ids = [int(i) for i in doc_ids if i is not None]
    if not ids:
        return {}

    pinned: dict[int, list[int]] = {}
    for course_id, doc_id in (
        db.query(Course.id, kind.pin_column)
        .filter(kind.pin_column.in_(ids))
        .all()
    ):
        pinned.setdefault(int(doc_id), []).append(int(course_id))

    generations = _count_map(
        db.query(kind.generation_fk, func.count())
        .filter(kind.generation_fk.in_(ids))
        .group_by(kind.generation_fk)
        .all()
    )
    versions = _count_map(
        db.query(kind.version_fk, func.count())
        .filter(kind.version_fk.in_(ids))
        .group_by(kind.version_fk)
        .all()
    )

    blueprints: dict[int, int] = {}
    feedback: dict[int, int] = {}
    if kind is CDD:
        # Derived blueprints are counted whether or not they are themselves
        # archived: an archived blueprint is restorable, and restoring one whose
        # CDD had been purged would leave it pointing at nothing.
        blueprints = _count_map(
            db.query(ModuleBlueprint.cdd_id, func.count())
            .filter(ModuleBlueprint.cdd_id.in_(ids))
            .group_by(ModuleBlueprint.cdd_id)
            .all()
        )
    else:
        # feedback_documents/items hold real FKs to module_blueprints with
        # ON DELETE SET NULL — a purge would not error, it would quietly unmap
        # reviewer feedback from its module. Treated as a blocker for that reason.
        for source in (FeedbackDocument.blueprint_id, FeedbackItem.blueprint_id):
            for doc_id, n in (
                db.query(source, func.count()).filter(source.in_(ids)).group_by(source).all()
            ):
                if doc_id is not None:
                    feedback[int(doc_id)] = feedback.get(int(doc_id), 0) + int(n)

    return {
        doc_id: DocReferences(
            pinned_by=tuple(sorted(pinned.get(doc_id, ()))),
            blueprint_count=blueprints.get(doc_id, 0),
            generation_count=generations.get(doc_id, 0),
            feedback_count=feedback.get(doc_id, 0),
            version_count=versions.get(doc_id, 0),
        )
        for doc_id in ids
    }


def references_for(db: Session, kind: DocKind, doc_id: int) -> DocReferences:
    """Single-document convenience wrapper around :func:`reference_counts`."""
    return reference_counts(db, kind, [doc_id]).get(int(doc_id), DocReferences())


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

def is_archived(doc: Any) -> bool:
    """True when *doc* has been archived.

    ``getattr`` with a default rather than a bare attribute read: code can be
    deployed before the migration adds the column (the deploy pipeline does not
    run alembic), and a live row is the safe answer in that window.
    """
    return getattr(doc, "deleted_at", None) is not None


def assert_live(doc: Any, kind: DocKind) -> None:
    """Guard operations that must not act on an archived document.

    Pinning and generating from an archive would quietly resurrect a document
    someone deliberately took out of circulation, so those paths call this.
    """
    if is_archived(doc):
        raise ValidationError(
            f"That {kind.label} is archived. Restore it before using it.",
            detail={"id": getattr(doc, "id", None), "archived": True},
        )


def _unpin_everywhere(db: Session, kind: DocKind, doc_id: int) -> list[int]:
    """Clear this document from every course that pinned it. Returns those course ids.

    The pin columns (``courses.active_cdd_id`` / ``active_blueprint_id``) are
    plain integers with no foreign key, so nothing at the database level would
    stop them pointing at an archived or deleted row. Clearing them here is what
    keeps that from happening — the same reason ``purge_course`` clears its soft
    pointers before deleting anything.
    """
    courses = db.query(Course).filter(kind.pin_column == doc_id).all()
    for course in courses:
        setattr(course, kind.pin_column.key, None)
    return [int(c.id) for c in courses]


@dataclass(frozen=True)
class ArchiveOutcome:
    """Result of one archive attempt, for both single and bulk callers."""

    doc_id: int
    ok: bool
    status: str                      # archived | already_archived | skipped
    reason: str = ""                 # why it was skipped, if it was
    unpinned_courses: tuple[int, ...] = ()


def archive(
    db: Session,
    kind: DocKind,
    doc: Any,
    *,
    actor: str,
    unpin: bool = False,
    commit: bool = True,
    refs: DocReferences | None = None,
) -> ArchiveOutcome:
    """Archive one document. Reversible; nothing is deleted.

    A pinned document is refused unless *unpin* is set. That is deliberate
    friction: archiving the active CDD silently would leave the course's
    generation flow falling back to "no CDD" with nothing on screen to explain
    why the next lesson came out contextless.

    *refs* lets a batch caller supply an already-resolved reference snapshot;
    without it this looks them up itself. ``bulk_archive`` passes them so a
    500-document batch costs one batched lookup rather than 500.
    """
    doc_id = int(doc.id)
    if is_archived(doc):
        return ArchiveOutcome(doc_id, ok=True, status="already_archived")

    pinned_courses: list[int] = []
    if refs is None:
        refs = references_for(db, kind, doc_id)
    if refs.is_pinned:
        if not unpin:
            return ArchiveOutcome(
                doc_id, ok=False, status="skipped",
                reason=(
                    f"This {kind.label} is pinned as active on course "
                    f"{', '.join(str(c) for c in refs.pinned_by)}. "
                    "Pin a different one first, or archive with unpin enabled."
                ),
            )
        pinned_courses = _unpin_everywhere(db, kind, doc_id)

    doc.deleted_at = datetime.utcnow()
    doc.deleted_by = actor
    if commit:
        db.commit()
    return ArchiveOutcome(
        doc_id, ok=True, status="archived", unpinned_courses=tuple(pinned_courses),
    )


def restore(db: Session, kind: DocKind, doc: Any, *, commit: bool = True) -> bool:
    """Bring an archived document back into the list. Returns False if it was never archived.

    Restoring deliberately does *not* re-pin: which document a course generates
    from is an explicit decision, and inferring it from an undo would silently
    change the next generation's context.
    """
    if not is_archived(doc):
        return False
    doc.deleted_at = None
    doc.deleted_by = None
    if commit:
        db.commit()
    return True


def purge(db: Session, kind: DocKind, doc: Any, *, commit: bool = True) -> DocReferences:
    """Permanently delete an archived document and its versions.

    Refuses unless the document is archived first (so a permanent delete is
    always a second, deliberate action) and unless nothing at all references it.
    Returns the reference snapshot the decision was made on, for the audit row.
    """
    doc_id = int(doc.id)
    if not is_archived(doc):
        raise ValidationError(
            f"Archive the {kind.label} before deleting it permanently.",
            detail={"id": doc_id, "archived": False},
        )

    refs = references_for(db, kind, doc_id)
    if not refs.can_purge:
        raise ResourceInUseError(
            f"This {kind.label} cannot be permanently deleted — "
            + "; ".join(refs.blockers)
            + ". It stays archived.",
            blockers=list(refs.blockers),
            detail={"id": doc_id},
        )

    db.delete(doc)
    if commit:
        db.commit()
    return refs


def bulk_archive(
    db: Session,
    kind: DocKind,
    doc_ids: Sequence[int],
    *,
    actor: str,
    unpin: bool = False,
    scope_course_id: int | None = None,
    scope_project_id: int | None = None,
) -> list[ArchiveOutcome]:
    """Archive many documents in one transaction, reporting each id's outcome.

    Takes explicit ids only — never a predicate. A "delete everything matching X"
    endpoint is one bad filter away from destroying a workspace, and the ids the
    UI offers came from a list the user just looked at.

    A skipped id (missing, out of scope, pinned) does not stop the others; the
    caller gets a row per id and can show exactly what was left behind. Nothing
    commits until every id has been decided, so the batch is all-or-nothing
    against a database error.
    """
    ids = list(dict.fromkeys(int(i) for i in doc_ids))  # de-dupe, keep order
    if not ids:
        return []
    if len(ids) > MAX_BULK_IDS:
        raise ValidationError(
            f"Too many documents in one request ({len(ids)}); the limit is {MAX_BULK_IDS}.",
            detail={"count": len(ids), "limit": MAX_BULK_IDS},
        )

    q = db.query(kind.model).filter(kind.model.id.in_(ids))
    # Scope filters make it impossible for a caller to reach outside the
    # workspace the ids were listed from, even with a hand-crafted id list.
    if scope_course_id is not None:
        q = q.filter(kind.model.course_id == scope_course_id)
    if scope_project_id is not None:
        # NULL project_id is accepted, matching how the list endpoints select
        # (``list_cdds_for_scope`` treats legacy rows with no project as
        # belonging to their course). Without this a legacy row would appear in
        # the list, be offered for archiving, and then come back "not in this
        # workspace" — a skip the user has no way to act on.
        q = q.filter(
            or_(
                kind.model.project_id == scope_project_id,
                kind.model.project_id.is_(None),
            )
        )
    found = {int(d.id): d for d in q.all()}

    # Resolved once for the whole batch. Letting each archive() look up its own
    # would make this the one path in the module that scales linearly in
    # queries — precisely the path that exists to handle many documents.
    refs = reference_counts(db, kind, list(found))

    outcomes: list[ArchiveOutcome] = []
    for doc_id in ids:
        doc = found.get(doc_id)
        if doc is None:
            outcomes.append(ArchiveOutcome(
                doc_id, ok=False, status="skipped",
                reason=f"No {kind.label} with that id in this workspace.",
            ))
            continue
        outcomes.append(archive(
            db, kind, doc, actor=actor, unpin=unpin, commit=False,
            refs=refs.get(doc_id),
        ))

    if any(o.ok and o.status == "archived" for o in outcomes):
        db.commit()
    return outcomes
