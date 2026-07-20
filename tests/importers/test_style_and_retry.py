"""Tests for reverse Style detection + reverse-gen retry (Session 6).

LLM mocked via ``mock_llm``. Asserts the Style is created + pinned like a forward
style, and that retry re-runs reverse-gen ONLY (the reconstructed blocks are
never rebuilt).
"""

from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from promptops_app.database import (
    Block,
    Course,
    CourseImport,
    GenerationJob,
    Generation,
    ModuleBlueprint,
    Style,
    StyleVersion,
)
from promptops_app.importers import editor_builder, reverse_common, style_analyzer
from promptops_app.importers.imscc_importer import parse_package
from tests.importers.fixtures import build_content_imscc


def _course_with_content(db) -> Course:
    course = Course(name="Sample Course", project_id=1)
    db.add(course)
    db.commit()
    db.refresh(course)
    editor_builder.build(
        db, parse_package(build_content_imscc()),
        course_id=course.id, project_id=1, import_id=1, user_name="u",
    )
    return course


def test_style_detected_and_pinned(db, mock_llm):
    course = _course_with_content(db)
    modules = reverse_common.collect_course_modules(db, course.id)

    result = style_analyzer.build_style(
        db, course=course, model_choice="GPT-5.4", user_name="u", modules=modules,
    )

    assert result.style_id is not None
    style = db.query(Style).filter_by(id=result.style_id).first()
    assert style is not None
    assert style.generated_summary                     # backward-compat mirror synced
    active = db.query(StyleVersion).filter_by(style_id=style.id, is_active=True).first()
    assert active is not None
    assert active.understanding_content

    db.refresh(course)
    assert course.active_style_id == style.id          # pinned at course scope


def test_retry_regenerates_artifacts_without_touching_blocks(db, monkeypatch, mock_llm):
    from promptops_app.jobs import import_jobs
    from promptops_app.repositories import job_repository

    factory = sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr("promptops_app.jobs.import_jobs.SessionLocal", factory)

    course = _course_with_content(db)
    course_id = course.id
    ci = CourseImport(course_id=course_id, project_id=1, status="reconstructing")
    db.add(ci)
    db.commit()
    db.refresh(ci)

    # Snapshot the reconstructed blocks (retry must not rebuild these).
    gens = db.query(Generation).filter_by(course_id=course_id).all()
    blocks_before = sorted(b.id for b in db.query(Block).filter(Block.generation_id.in_([g.id for g in gens])).all())
    bp_before = db.query(ModuleBlueprint).filter_by(course_id=course_id).count()

    job_id = job_repository.create_job(
        db,
        user_name="u",
        request_params={"import_id": ci.id, "course_id": course_id, "project_id": 1, "user_name": "u"},
        project_id=1, course_id=course_id, job_type="import_reverse",
    )
    import_jobs.run_reverse_gen_job(job_id)

    db.expire_all()
    job = db.query(GenerationJob).filter_by(id=job_id).first()
    assert job.status == "completed"

    gens_after = db.query(Generation).filter_by(course_id=course_id).all()
    blocks_after = sorted(b.id for b in db.query(Block).filter(Block.generation_id.in_([g.id for g in gens_after])).all())
    assert blocks_after == blocks_before               # blocks untouched by retry

    # Reverse-gen artifacts were produced + pinned.
    assert db.query(ModuleBlueprint).filter_by(course_id=course_id).count() > bp_before
    course = db.query(Course).filter_by(id=course_id).first()
    assert course.active_blueprint_id is not None
    assert course.active_cdd_id is not None
    assert course.active_style_id is not None

    record = db.query(CourseImport).filter_by(id=ci.id).first()
    assert record.status == "completed"
    assert record.provenance_ready is True
