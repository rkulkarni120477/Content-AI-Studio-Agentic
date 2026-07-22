"""Editor builder accepts Cendoc-parsed ICourse (shared rebuild path)."""

from __future__ import annotations

import re

from promptops_app.core.constants import ChangeSource
from promptops_app.database import Block, BlockVersion, Course, CourseModule
from promptops_app.importers import editor_builder
from promptops_app.importers.imscc_importer import parse_package
from tests.importers.fixtures import build_sample_cendoc


def test_editor_builder_from_cendoc(db):
    course = Course(name="Cendoc Course", project_id=1, source_type="cendoc")
    db.add(course)
    db.commit()
    db.refresh(course)

    icourse = parse_package(build_sample_cendoc())
    result = editor_builder.build(
        db,
        icourse,
        course_id=course.id,
        project_id=1,
        import_id=99,
        user_name="tester",
    )
    assert result.modules_created >= 1
    assert result.blocks_created >= 2
    modules = (
        db.query(CourseModule)
        .filter(CourseModule.course_id == course.id)
        .order_by(CourseModule.position)
        .all()
    )
    assert modules[0].title == "Front Matter"
    overview = next(m for m in modules if m.title == "An Overview")
    blocks = db.query(Block).filter(Block.module_id == overview.id).all()
    assert any(b.block_type == "lesson" and b.content for b in blocks)

    for block in blocks:
        assert not re.match(r"^lesson_\d", block.block_type or "")
        versions = (
            db.query(BlockVersion)
            .filter(BlockVersion.block_id == block.id)
            .order_by(BlockVersion.version_num)
            .all()
        )
        assert len(versions) == 1
        assert versions[0].version_num == 1
        assert versions[0].change_source == ChangeSource.IMPORT
        assert versions[0].change_note == "Initial version"
        assert versions[0].content == (block.content or "")
