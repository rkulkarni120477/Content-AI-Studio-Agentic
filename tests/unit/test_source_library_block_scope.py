"""The block tag must be a fact about the upload, not a guess about the filename.

DIS derives ``block`` by regex over a document's path, filename and first 1,500
characters. A folder named for its subject rather than its block matches nothing,
so the document is stored with ``block: ""`` and is then invisible to every
block-scoped retrieval — while the Source Library still lists it as "Processed".

Measured on the AIM corpus 2026-08-27, before this fix: 148 documents lost across
six courses (59-79% of each). Block 9's CDD reported "Total Projects: 0" with
eleven landing-gear project files sitting in the store under
``Landing Gear Projects/``; Blocks 8 and 10 had no reachable teaching content at
all. Nothing anywhere reported a problem.

CAS knows the answer at upload time — the user picked a course, and the course is
named for its block — so these tests pin that it sends it.
"""
from unittest.mock import MagicMock

from app.api.v1.routers.source_library import _GLOBAL_COURSE_ID, _block_from_course


def _db_returning(course):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = course
    return db


def _course(name):
    c = MagicMock()
    c.name = name
    return c


def test_derives_the_block_from_the_courses_name():
    assert _block_from_course(_db_returning(_course("Block 9")), 101) == "Block 9"


def test_normalises_a_zero_padded_course_name():
    # The corpus carries the same block as "Block 9", "Block 09" and "9". New rows
    # must be written one way or the split keeps widening.
    assert _block_from_course(_db_returning(_course("Block 09")), 101) == "Block 9"


def test_reads_the_block_out_of_a_longer_course_title():
    assert _block_from_course(
        _db_returning(_course("Block 9 Aircraft Systems-II")), 101) == "Block 9"


def test_a_global_upload_is_never_stamped_with_a_block():
    # "Upload as Global" means visible in every course. A handbook that serves the
    # whole programme must not be filed under whichever course was on screen.
    assert _block_from_course(_db_returning(_course("Block 9")), _GLOBAL_COURSE_ID) == ""


def test_no_course_scope_means_no_block():
    assert _block_from_course(_db_returning(_course("Block 9")), None) == ""


def test_a_course_that_is_not_a_block_is_left_alone():
    # Non-AIM tenants have courses, not blocks. Guessing here would invent a tag.
    assert _block_from_course(_db_returning(_course("Principles of Marketing")), 56) == ""


def test_a_missing_course_returns_empty_rather_than_guessing():
    assert _block_from_course(_db_returning(None), 999) == ""


def test_a_broken_lookup_never_breaks_the_upload():
    db = MagicMock()
    db.query.side_effect = RuntimeError("database is on fire")
    assert _block_from_course(db, 101) == ""
