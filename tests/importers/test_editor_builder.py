"""Tests for editor_builder + the import job (Session 3 — reconstruction).

Uses the shared in-memory SQLite ``db`` fixture (tests/conftest.py). Reconstruction
must produce the *same rows the scratch pipeline produces* — CourseModule +
Generation + Block — so the Editor renders imported courses with zero changes.
"""

from __future__ import annotations

import json
import tempfile

from sqlalchemy.orm import sessionmaker

from promptops_app.core.constants import ChangeSource
from promptops_app.database import (
    Block,
    BlockVersion,
    Course,
    CourseImport,
    CourseModule,
    Generation,
    GenerationJob,
)
from promptops_app.importers import editor_builder, provenance
from promptops_app.importers.imscc_importer import parse_package
from tests.importers.fixtures import build_content_imscc


def _new_course(db, name="Imported", project_id=1) -> Course:
    course = Course(name=name, project_id=project_id)
    db.add(course)
    db.commit()
    db.refresh(course)
    return course


def test_build_creates_modules_generations_blocks(db):
    course = _new_course(db)
    icourse = parse_package(build_content_imscc())

    result = editor_builder.build(
        db, icourse, course_id=course.id, project_id=1, import_id=42, user_name="u",
    )

    assert result.modules_created == 2
    assert result.blocks_created == 3          # Intro + Quiz 1 + Wrap Up

    mods = (
        db.query(CourseModule)
        .filter_by(course_id=course.id)
        .order_by(CourseModule.position)
        .all()
    )
    assert [m.title for m in mods] == ["Module A", "Module B"]

    gens = db.query(Generation).filter_by(course_id=course.id).all()
    assert len(gens) == 3                       # one generation per item
    assert all(g.prompt_name == "import" for g in gens)
    assert {g.topic for g in gens} == {"Intro", "Quiz 1", "Wrap Up"}

    blocks = db.query(Block).filter(Block.generation_id.in_([g.id for g in gens])).all()
    assert len(blocks) == 3
    assert all(b.workflow_state == "draft" for b in blocks)
    assert all(b.module_id is not None for b in blocks)
    assert all(b.block_type in {"lesson", "quiz", "assignment", "discussion"} for b in blocks)

    for block in blocks:
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


def test_block_types_content_and_order(db):
    course = _new_course(db)
    editor_builder.build(
        db, parse_package(build_content_imscc()),
        course_id=course.id, project_id=1, import_id=42, user_name="u",
    )

    mod_a = (
        db.query(CourseModule)
        .filter_by(course_id=course.id)
        .order_by(CourseModule.position)
        .first()
    )
    ordered = db.query(Block).filter_by(module_id=mod_a.id).order_by(Block.position).all()
    assert [b.block_label for b in ordered] == ["Intro", "Quiz 1"]
    assert [b.position for b in ordered] == [0, 1]

    intro, quiz = ordered
    assert intro.block_type == "lesson"
    assert "Welcome to the course" in (intro.content or "")
    assert intro.content_html                    # original body HTML kept as fidelity fallback
    assert quiz.block_type == "quiz"
    assert "**Correct Answer:** B" in (quiz.content or "")


def test_provenance_rows_map_canvas_ids_to_entities(db):
    course = _new_course(db)
    editor_builder.build(
        db, parse_package(build_content_imscc()),
        course_id=course.id, project_id=1, import_id=77, user_name="u",
    )

    rows = provenance.list_for_import(db, 77)
    assert sum(1 for r in rows if r.canvas_type == "module") == 2
    assert sum(1 for r in rows if r.canvas_type == "page") == 2
    assert any(r.canvas_type == "quiz" for r in rows)

    block_ids = {b.id for b in db.query(Block).all()}
    module_ids = {m.id for m in db.query(CourseModule).all()}
    for r in rows:
        if r.cas_entity_type == "block":
            assert r.cas_entity_id in block_ids
        else:
            assert r.cas_entity_id in module_ids


def test_block_identifier_map_backs_provenance_export(db):
    # The map {block_id: canvas_identifier} is what provenance-aware export reads.
    course = _new_course(db)
    editor_builder.build(
        db, parse_package(build_content_imscc()),
        course_id=course.id, project_id=1, import_id=88, user_name="u",
    )
    id_map = provenance.block_identifier_map(db, 88)

    block_ids = {b.id for b in db.query(Block).all()}
    assert id_map                                   # non-empty
    assert set(id_map) <= block_ids                 # keys are real block ids
    assert all(v for v in id_map.values())          # every mapped id is non-empty


def test_run_import_job_end_to_end(db, monkeypatch, mock_llm):
    # mock_llm keeps the reverse-gen stages (Blueprint/CDD) offline + deterministic.
    from promptops_app.jobs import import_jobs
    from promptops_app.repositories import job_repository

    # The job opens its own SessionLocal — point it at the test engine so it
    # writes to the same in-memory DB the fixture reads.
    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr("promptops_app.jobs.import_jobs.SessionLocal", factory)

    course = _new_course(db, name="ViaJob", project_id=2)
    course_id = course.id
    ci = CourseImport(course_id=course_id, project_id=2, status="queued", package_name="x.imscc")
    db.add(ci)
    db.commit()
    db.refresh(ci)
    import_id = ci.id

    fd, pkg_path = tempfile.mkstemp(prefix="test_imscc_", suffix=".imscc")
    import os
    with os.fdopen(fd, "wb") as handle:
        handle.write(build_content_imscc())

    job_id = job_repository.create_job(
        db,
        user_name="u",
        request_params={
            "import_id": import_id, "course_id": course_id, "project_id": 2,
            "user_name": "u", "package_path": pkg_path, "package_name": "x.imscc",
        },
        project_id=2, course_id=course_id, job_type="import",
    )

    import_jobs.run_import_job(job_id)

    db.expire_all()
    job = db.query(GenerationJob).filter_by(id=job_id).first()
    assert job.status == "completed"
    assert job.result_entity_id == course_id      # result entity is the course
    assert job.progress == 100

    course = db.query(Course).filter_by(id=course_id).first()
    assert course.source_type == "imscc"
    assert course.import_id == import_id

    record = db.query(CourseImport).filter_by(id=import_id).first()
    assert record.status == "completed"
    assert json.loads(record.structure_counts_json)["pages"] == 2

    gens = db.query(Generation).filter_by(course_id=course_id).all()
    blocks = db.query(Block).filter(Block.generation_id.in_([g.id for g in gens])).all()
    assert len(blocks) == 3
    assert not os.path.isfile(pkg_path)           # staged package cleaned up

    # Reverse-gen (stages 5–6) populated + pinned Blueprint and CDD.
    from promptops_app.database import CourseDesignDocument, ModuleBlueprint
    assert course.active_blueprint_id is not None
    assert course.active_cdd_id is not None
    assert db.query(ModuleBlueprint).filter_by(course_id=course_id).count() == 2
    assert db.query(CourseDesignDocument).filter_by(course_id=course_id).count() == 1
