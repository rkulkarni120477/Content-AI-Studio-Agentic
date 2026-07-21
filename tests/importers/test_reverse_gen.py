"""Tests for reverse-generation of Blueprint + CDD (Session 5).

The LLM is mocked (``mock_llm`` fixture) so these assert wiring + persistence:
the reverse modules must produce the SAME rows the forward pipeline produces
(ModuleBlueprint/BlueprintVersion, CourseDesignDocument/CDDVersion) and pin the
course actives — so the Blueprint/CDD tabs render and regenerate unchanged.
"""

from __future__ import annotations

import json

from promptops_app.database import (
    BlueprintVersion,
    CDDVersion,
    Course,
    CourseDesignDocument,
    ModuleBlueprint,
)
from promptops_app.importers import editor_builder, reverse_blueprint, reverse_cdd, reverse_common
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


def test_reverse_blueprint_creates_versions_and_pins_active(db, mock_llm):
    course = _course_with_content(db)
    modules = reverse_common.collect_course_modules(db, course.id)

    result = reverse_blueprint.build_blueprints(
        db, course=course, model_choice="GPT-5.4", user_name="u", modules=modules,
    )

    assert result.created == 2                      # one per module
    bps = db.query(ModuleBlueprint).filter_by(course_id=course.id).all()
    assert len(bps) == 2
    assert [b.module_number for b in bps] == [1, 2]
    for bp in bps:
        active = db.query(BlueprintVersion).filter_by(blueprint_id=bp.id, is_active=True).first()
        assert active is not None
        assert json.loads(active.sections)          # non-empty parsed sections

    db.refresh(course)
    assert course.active_blueprint_id == result.first_blueprint_id == bps[0].id

    # Each item-generation is linked to its module blueprint so the Editor groups
    # imported items by module instead of "Other / Unlinked".
    from promptops_app.database import Generation
    bp_ids = {b.id for b in bps}
    linked = db.query(Generation).filter_by(course_id=course.id).all()
    assert linked and all(g.blueprint_id in bp_ids for g in linked)


def test_reverse_cdd_creates_version_and_pins_active(db, mock_llm):
    course = _course_with_content(db)
    modules = reverse_common.collect_course_modules(db, course.id)

    result = reverse_cdd.build_cdd(
        db, course=course, model_choice="GPT-5.4", user_name="u", modules=modules,
    )

    assert result.cdd_id is not None
    cdd = db.query(CourseDesignDocument).filter_by(course_id=course.id).first()
    assert cdd is not None
    active = db.query(CDDVersion).filter_by(cdd_id=cdd.id, is_active=True).first()
    assert active is not None
    assert json.loads(active.sections)

    db.refresh(course)
    assert course.active_cdd_id == cdd.id


def test_reverse_gen_is_nonfatal_on_llm_error(db, monkeypatch):
    # A hard LLM error must degrade to warnings, never raise.
    from types import SimpleNamespace
    monkeypatch.setattr(
        "promptops_app.services.llm_service.generate_with_metadata",
        lambda *a, **k: SimpleNamespace(text="upstream down", status="error", error_type="provider"),
    )
    course = _course_with_content(db)
    modules = reverse_common.collect_course_modules(db, course.id)

    bp = reverse_blueprint.build_blueprints(db, course=course, model_choice="X", user_name="u", modules=modules)
    cdd = reverse_cdd.build_cdd(db, course=course, model_choice="X", user_name="u", modules=modules)

    assert bp.created == 0
    assert bp.warnings and cdd.warnings             # surfaced, not raised
    db.refresh(course)
    assert course.active_blueprint_id is None
    assert course.active_cdd_id is None
    assert db.query(CourseDesignDocument).filter_by(course_id=course.id).count() == 0
