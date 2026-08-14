"""Archive / restore / purge rules for design documents.

The dangerous case these tests exist for: ``CourseDesignDocument.blueprints``
carries ``cascade="all, delete-orphan"``, so ``db.delete(cdd)`` silently takes
every derived blueprint and its whole version history with it. There is no FK
error to catch it — only the guard in ``design_doc_archive.purge``. If
``test_purge_refuses_a_cdd_with_derived_blueprints`` ever goes green by way of
the delete succeeding, real content is being destroyed.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ResourceInUseError, ValidationError
from app.services import design_doc_archive as arch
from promptops_app.database import (
    Base,
    BlueprintVersion,
    CDDVersion,
    Course,
    CourseDesignDocument,
    FeedbackDocument,
    FeedbackItem,
    Generation,
    ModuleBlueprint,
    Project,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def course(db):
    project = Project(name="P")
    db.add(project)
    db.flush()
    c = Course(name="C", project_id=project.id)
    db.add(c)
    db.commit()
    return c


def _cdd(db, course, title="CDD"):
    doc = CourseDesignDocument(
        title=title, course_title="C", course_id=course.id, project_id=course.project_id,
    )
    db.add(doc)
    db.commit()
    return doc


def _blueprint(db, course, cdd_id=None, module_number=1):
    bp = ModuleBlueprint(
        title="BP", module_title="M", module_number=module_number, cdd_id=cdd_id,
        course_id=course.id, project_id=course.project_id,
    )
    db.add(bp)
    db.commit()
    return bp


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

def test_an_unreferenced_cdd_can_be_purged(db, course):
    doc = _cdd(db, course)
    refs = arch.references_for(db, arch.CDD, doc.id)
    assert refs.blockers == ()
    assert refs.can_purge is True


def test_references_reports_the_pinning_course(db, course):
    doc = _cdd(db, course)
    course.active_cdd_id = doc.id
    db.commit()

    refs = arch.references_for(db, arch.CDD, doc.id)
    assert refs.is_pinned is True
    assert refs.pinned_by == (course.id,)
    assert refs.can_purge is False
    assert "pinned as active" in refs.blockers[0]


def test_references_counts_derived_blueprints_and_generations(db, course):
    doc = _cdd(db, course)
    _blueprint(db, course, cdd_id=doc.id, module_number=1)
    _blueprint(db, course, cdd_id=doc.id, module_number=2)
    db.add(Generation(
        prompt_name="p", prompt_version="v1", block_type="b", topic="t",
        output_text="o", cdd_id=doc.id,
    ))
    db.add(CDDVersion(cdd_id=doc.id, version="v1", full_content="x"))
    db.commit()

    refs = arch.references_for(db, arch.CDD, doc.id)
    assert refs.blueprint_count == 2
    assert refs.generation_count == 1
    assert refs.version_count == 1
    assert refs.can_purge is False


def test_reference_counts_is_batched_over_many_ids(db, course):
    docs = [_cdd(db, course, title=f"CDD {i}") for i in range(5)]
    _blueprint(db, course, cdd_id=docs[2].id)

    refs = arch.reference_counts(db, arch.CDD, [d.id for d in docs])

    # Every id gets an entry, so callers never have to guard for a missing key.
    assert set(refs) == {d.id for d in docs}
    assert refs[docs[2].id].blueprint_count == 1
    assert all(refs[d.id].blueprint_count == 0 for d in docs if d.id != docs[2].id)


def test_reference_counts_of_nothing_is_empty_not_an_error(db):
    assert arch.reference_counts(db, arch.CDD, []) == {}


def test_an_archived_derived_blueprint_still_blocks_its_cdd(db, course):
    """A restorable blueprint must not be left pointing at a purged CDD."""
    doc = _cdd(db, course)
    bp = _blueprint(db, course, cdd_id=doc.id)
    arch.archive(db, arch.BLUEPRINT, bp, actor="u")

    refs = arch.references_for(db, arch.CDD, doc.id)
    assert refs.blueprint_count == 1
    assert refs.can_purge is False


def test_blueprint_references_count_mapped_feedback(db, course):
    """feedback FKs are ON DELETE SET NULL — a purge would unmap, not error."""
    bp = _blueprint(db, course)
    fd = FeedbackDocument(
        project_id=course.project_id, course_id=course.id,
        blueprint_id=bp.id, filename="f.docx",
    )
    db.add(fd)
    db.flush()
    db.add(FeedbackItem(
        document_id=fd.id, project_id=course.project_id, course_id=course.id,
        blueprint_id=bp.id, feedback_text="fix this",
    ))
    db.commit()

    refs = arch.references_for(db, arch.BLUEPRINT, bp.id)
    assert refs.feedback_count == 2          # the document and its item
    assert refs.can_purge is False
    assert "reviewer-feedback" in refs.blockers[0]


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

def test_archive_hides_the_row_and_records_who_did_it(db, course):
    doc = _cdd(db, course)
    outcome = arch.archive(db, arch.CDD, doc, actor="sami")

    assert outcome.ok and outcome.status == "archived"
    assert doc.deleted_at is not None
    assert doc.deleted_by == "sami"
    assert arch.is_archived(doc) is True


def test_archiving_twice_is_a_no_op_success(db, course):
    doc = _cdd(db, course)
    arch.archive(db, arch.CDD, doc, actor="u")
    first_stamp = doc.deleted_at

    outcome = arch.archive(db, arch.CDD, doc, actor="someone-else")

    assert outcome.ok and outcome.status == "already_archived"
    # The original archiver is not overwritten by a repeat call.
    assert doc.deleted_at == first_stamp
    assert doc.deleted_by == "u"


def test_archiving_a_pinned_cdd_is_refused_without_unpin(db, course):
    doc = _cdd(db, course)
    course.active_cdd_id = doc.id
    db.commit()

    outcome = arch.archive(db, arch.CDD, doc, actor="u")

    assert outcome.ok is False
    assert outcome.status == "skipped"
    assert "pinned as active" in outcome.reason
    assert doc.deleted_at is None
    # The pin survives a refused archive.
    assert course.active_cdd_id == doc.id


def test_unpin_clears_the_pointer_so_no_course_points_at_an_archive(db, course):
    doc = _cdd(db, course)
    course.active_cdd_id = doc.id
    db.commit()

    outcome = arch.archive(db, arch.CDD, doc, actor="u", unpin=True)

    assert outcome.ok and outcome.status == "archived"
    assert outcome.unpinned_courses == (course.id,)
    db.refresh(course)
    assert course.active_cdd_id is None


def test_archiving_a_blueprint_clears_its_own_pin_column(db, course):
    """The two kinds must not cross-clear each other's pointer."""
    bp = _blueprint(db, course)
    doc = _cdd(db, course)
    course.active_blueprint_id = bp.id
    course.active_cdd_id = doc.id
    db.commit()

    arch.archive(db, arch.BLUEPRINT, bp, actor="u", unpin=True)

    db.refresh(course)
    assert course.active_blueprint_id is None
    assert course.active_cdd_id == doc.id


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def test_restore_brings_it_back_but_does_not_re_pin(db, course):
    doc = _cdd(db, course)
    course.active_cdd_id = doc.id
    db.commit()
    arch.archive(db, arch.CDD, doc, actor="u", unpin=True)

    assert arch.restore(db, arch.CDD, doc) is True
    assert doc.deleted_at is None
    assert doc.deleted_by is None
    db.refresh(course)
    # Which document generation uses stays an explicit decision.
    assert course.active_cdd_id is None


def test_restoring_a_live_document_reports_no_change(db, course):
    doc = _cdd(db, course)
    assert arch.restore(db, arch.CDD, doc) is False


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------

def test_purge_requires_archiving_first(db, course):
    doc = _cdd(db, course)
    with pytest.raises(ValidationError):
        arch.purge(db, arch.CDD, doc)
    assert db.query(CourseDesignDocument).count() == 1


def test_purge_deletes_an_archived_unreferenced_cdd_and_its_versions(db, course):
    doc = _cdd(db, course)
    db.add(CDDVersion(cdd_id=doc.id, version="v1", full_content="x"))
    db.commit()
    arch.archive(db, arch.CDD, doc, actor="u")

    refs = arch.purge(db, arch.CDD, doc)

    assert refs.version_count == 1
    assert db.query(CourseDesignDocument).count() == 0
    assert db.query(CDDVersion).count() == 0


def test_purge_refuses_a_cdd_with_derived_blueprints(db, course):
    """The cascade guard. If this ever passes by deleting, blueprints are lost."""
    doc = _cdd(db, course)
    bp = _blueprint(db, course, cdd_id=doc.id)
    db.add(BlueprintVersion(blueprint_id=bp.id, version="v1", full_content="keep me"))
    db.commit()
    arch.archive(db, arch.CDD, doc, actor="u")

    with pytest.raises(ResourceInUseError) as exc:
        arch.purge(db, arch.CDD, doc)

    assert "would delete them too" in " ".join(exc.value.detail["blockers"])
    assert db.query(CourseDesignDocument).count() == 1
    assert db.query(ModuleBlueprint).count() == 1
    assert db.query(BlueprintVersion).count() == 1


def test_purge_refuses_a_cdd_a_generation_traces_to(db, course):
    doc = _cdd(db, course)
    db.add(Generation(
        prompt_name="p", prompt_version="v1", block_type="b", topic="t",
        output_text="o", cdd_id=doc.id,
    ))
    db.commit()
    arch.archive(db, arch.CDD, doc, actor="u")

    with pytest.raises(ResourceInUseError):
        arch.purge(db, arch.CDD, doc)
    assert db.query(CourseDesignDocument).count() == 1


def test_purge_refuses_a_still_pinned_document(db, course):
    """Reachable when a pin is re-created out of band, so it is checked at purge too."""
    doc = _cdd(db, course)
    arch.archive(db, arch.CDD, doc, actor="u")
    course.active_cdd_id = doc.id
    db.commit()

    with pytest.raises(ResourceInUseError):
        arch.purge(db, arch.CDD, doc)


# ---------------------------------------------------------------------------
# assert_live
# ---------------------------------------------------------------------------

def test_assert_live_blocks_pinning_an_archive(db, course):
    doc = _cdd(db, course)
    arch.archive(db, arch.CDD, doc, actor="u")
    with pytest.raises(ValidationError):
        arch.assert_live(doc, arch.CDD)


def test_assert_live_passes_a_live_document(db, course):
    arch.assert_live(_cdd(db, course), arch.CDD)


def test_is_archived_treats_a_missing_column_as_live(db, course):
    """Code can deploy before the migration adds the column; live is the safe read."""
    class Row:
        pass
    assert arch.is_archived(Row()) is False


# ---------------------------------------------------------------------------
# Bulk
# ---------------------------------------------------------------------------

def test_bulk_archive_reports_a_row_per_id(db, course):
    live = _cdd(db, course, "live")
    pinned = _cdd(db, course, "pinned")
    course.active_cdd_id = pinned.id
    db.commit()

    outcomes = arch.bulk_archive(
        db, arch.CDD, [live.id, pinned.id, 9999], actor="u",
    )

    by_id = {o.doc_id: o for o in outcomes}
    assert by_id[live.id].status == "archived"
    assert by_id[pinned.id].status == "skipped"        # pinned, unpin not asked for
    assert by_id[9999].status == "skipped"             # does not exist
    # A skipped id does not abandon the rest of the batch.
    db.refresh(live)
    assert live.deleted_at is not None
    db.refresh(pinned)
    assert pinned.deleted_at is None


def test_bulk_archive_collapses_duplicate_ids(db, course):
    doc = _cdd(db, course)
    outcomes = arch.bulk_archive(db, arch.CDD, [doc.id, doc.id, doc.id], actor="u")
    assert len(outcomes) == 1


def test_bulk_archive_skips_ids_outside_the_scope(db, course):
    """A stale id list must not reach another workspace."""
    other = Course(name="Other", project_id=course.project_id)
    db.add(other)
    db.commit()
    mine = _cdd(db, course)
    theirs = _cdd(db, other)

    outcomes = arch.bulk_archive(
        db, arch.CDD, [mine.id, theirs.id], actor="u", scope_course_id=course.id,
    )

    by_id = {o.doc_id: o for o in outcomes}
    assert by_id[mine.id].status == "archived"
    assert by_id[theirs.id].status == "skipped"
    db.refresh(theirs)
    assert theirs.deleted_at is None


def test_bulk_archive_does_not_query_per_document(db, course):
    """The batch path must not scale linearly in queries — it exists for scale.

    Counts SELECTs rather than asserting an exact number: the point is that
    doubling the documents does not double the queries.
    """
    from sqlalchemy import event

    few = [_cdd(db, course, f"a{i}") for i in range(2)]
    many = [_cdd(db, course, f"b{i}") for i in range(20)]

    def count_selects(ids):
        seen = []
        engine = db.get_bind()

        def before(conn, cursor, statement, *a, **kw):
            if statement.lstrip().upper().startswith("SELECT"):
                seen.append(statement)

        event.listen(engine, "before_cursor_execute", before)
        try:
            arch.bulk_archive(db, arch.CDD, ids, actor="u")
        finally:
            event.remove(engine, "before_cursor_execute", before)
        return len(seen)

    for_two = count_selects([d.id for d in few])
    for_twenty = count_selects([d.id for d in many])

    assert for_twenty <= for_two + 2, (
        f"query count grew with batch size: {for_two} for 2, {for_twenty} for 20"
    )


def test_bulk_archive_project_scope_keeps_legacy_rows_with_no_project(db, course):
    """A row the list offered must not come back "not in this workspace".

    ``list_cdds_for_scope`` treats a NULL project_id as belonging to its course,
    so such a row appears in the list and gets offered for bulk archiving. A
    strict equality scope here would skip it with a reason the user cannot act on.
    """
    legacy = CourseDesignDocument(
        title="legacy", course_title="C", course_id=course.id, project_id=None,
    )
    db.add(legacy)
    db.commit()

    outcomes = arch.bulk_archive(
        db, arch.CDD, [legacy.id], actor="u",
        scope_course_id=course.id, scope_project_id=course.project_id,
    )

    assert outcomes[0].status == "archived"


def test_bulk_archive_rejects_an_oversized_batch(db):
    with pytest.raises(ValidationError):
        arch.bulk_archive(db, arch.CDD, list(range(arch.MAX_BULK_IDS + 1)), actor="u")


def test_bulk_archive_of_nothing_is_empty_not_an_error(db):
    assert arch.bulk_archive(db, arch.CDD, [], actor="u") == []
