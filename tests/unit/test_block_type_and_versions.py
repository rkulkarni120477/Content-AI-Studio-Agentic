"""Unit tests for canonical_block_type and save_block_version(commit=False)."""

from __future__ import annotations

from promptops_app.core.constants import ChangeSource, canonical_block_type
from promptops_app.database import Block, BlockVersion, Generation
from promptops_app.repositories.block_repo import save_block_version


def test_canonical_block_type_lessons():
    assert canonical_block_type("lesson") == "lesson"
    assert canonical_block_type("lesson_1") == "lesson"
    assert canonical_block_type("lesson_2") == "lesson"
    assert canonical_block_type("lesson_5") == "lesson"
    assert canonical_block_type("lesson_1_1") == "lesson"
    assert canonical_block_type("LESSON_3") == "lesson"
    assert canonical_block_type(None) == "lesson"
    assert canonical_block_type("") == "lesson"


def test_canonical_block_type_assessments_and_others():
    assert canonical_block_type("module_assessment") == "quiz"
    assert canonical_block_type("quiz") == "quiz"
    assert canonical_block_type("assessment") == "quiz"
    assert canonical_block_type("assignment") == "assignment"
    assert canonical_block_type("assignment_1") == "assignment"
    assert canonical_block_type("discussion") == "discussion"
    assert canonical_block_type("discussion_2") == "discussion"


def test_save_block_version_commit_false_batches(db):
    gen = Generation(
        topic="t",
        prompt_name="import",
        prompt_version="import",
        block_type="lesson",
        output_text="body",
        project_id=1,
        course_id=1,
        created_by="tester",
    )
    db.add(gen)
    db.flush()
    block = Block(
        generation_id=gen.id,
        block_type="lesson",
        block_label="Lesson",
        content="Hello world",
        workflow_state="draft",
        version_num=1,
    )
    db.add(block)
    db.flush()

    ver = save_block_version(
        db,
        block,
        change_source=ChangeSource.IMPORT,
        change_note="Initial version",
        created_by="tester",
        commit=False,
    )
    assert ver.version_num == 1
    assert ver.id is not None

    # Not committed yet from the helper — still visible in this session.
    assert db.query(BlockVersion).filter_by(block_id=block.id).count() == 1

    db.commit()
    assert db.query(BlockVersion).filter_by(block_id=block.id).count() == 1
    stored = db.query(BlockVersion).filter_by(block_id=block.id).one()
    assert stored.change_source == ChangeSource.IMPORT
    assert stored.content == "Hello world"
    assert block.version_num == 1
