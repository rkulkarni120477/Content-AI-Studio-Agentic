"""Unit tests for feedback module mapping and apply helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from promptops_app.repositories import feedback_repository
from promptops_app.services.feedback_service import (
    compile_feedback_instruction,
    resolve_apply_blueprint_id,
    apply_feedback_to_module,
)


@pytest.fixture()
def course_with_blueprint(db):
    from promptops_app.database import (
        Block,
        Cluster,
        Course,
        CourseDesignDocument,
        Generation,
        ModuleBlueprint,
        Project,
    )

    project = Project(name="FB Proj", is_active=True)
    db.add(project)
    db.flush()
    cluster = Cluster(name="FB Cluster", project_id=project.id, is_active=True)
    db.add(cluster)
    db.flush()
    course = Course(
        name="FB Course",
        project_id=project.id,
        cluster_id=cluster.id,
        is_active=True,
        config_model_choice="test-model",
    )
    db.add(course)
    db.flush()

    cdd = CourseDesignDocument(
        title="CDD",
        course_title="FB Course",
        course_id=course.id,
        project_id=project.id,
        created_by="tester",
    )
    db.add(cdd)
    db.flush()

    bp = ModuleBlueprint(
        cdd_id=cdd.id,
        title="Mod 1 Blueprint",
        module_title="Introduction",
        module_number=1,
        project_id=project.id,
        course_id=course.id,
        created_by="tester",
    )
    bp_other = ModuleBlueprint(
        cdd_id=cdd.id,
        title="Mod 2 Blueprint",
        module_title="Advanced",
        module_number=2,
        project_id=project.id,
        course_id=course.id,
        created_by="tester",
    )
    db.add_all([bp, bp_other])
    db.flush()

    gen = Generation(
        topic="Lesson A",
        prompt_name="lesson",
        prompt_version="v1",
        block_type="lesson",
        output_text="body",
        project_id=project.id,
        course_id=course.id,
        cdd_id=cdd.id,
        blueprint_id=bp.id,
        created_by="tester",
    )
    db.add(gen)
    db.flush()

    block = Block(
        generation_id=gen.id,
        block_type="lesson",
        block_label="Lesson A",
        content="Original content",
        workflow_state="draft",
        version_num=1,
    )
    db.add(block)
    db.commit()
    db.refresh(course)
    db.refresh(bp)
    db.refresh(bp_other)
    db.refresh(block)
    return {
        "project": project,
        "course": course,
        "bp": bp,
        "bp_other": bp_other,
        "block": block,
    }


class TestFeedbackRepositoryMapping:
    def test_create_document_and_items_inherit_blueprint(self, db, course_with_blueprint):
        course = course_with_blueprint["course"]
        bp = course_with_blueprint["bp"]

        doc = feedback_repository.create_document(
            db,
            project_id=course.project_id,
            course_id=course.id,
            blueprint_id=bp.id,
            filename="review.pptx",
            file_type="pptx",
            content="raw",
            model_used="test-model",
            created_by="tester",
        )
        items = feedback_repository.create_items(
            db,
            doc,
            [
                {
                    "feedback_text": "Add more examples",
                    "theme": "Content",
                    "sentiment": "suggestion",
                    "priority": "high",
                },
                {
                    "feedback_text": "Great intro",
                    "theme": "Tone",
                    "sentiment": "praise",
                    "priority": "low",
                },
            ],
        )
        db.commit()

        assert doc.blueprint_id == bp.id
        assert len(items) == 2
        assert all(i.blueprint_id == bp.id for i in items)
        assert doc.item_count == 2

    def test_create_document_course_wide_null_blueprint(self, db, course_with_blueprint):
        course = course_with_blueprint["course"]
        doc = feedback_repository.create_document(
            db,
            project_id=course.project_id,
            course_id=course.id,
            blueprint_id=None,
            filename="course-review.docx",
            file_type="docx",
            content="raw",
            model_used="test-model",
            created_by="tester",
        )
        items = feedback_repository.create_items(
            db, doc, [{"feedback_text": "Overall pacing", "sentiment": "concern", "priority": "medium"}],
        )
        db.commit()
        assert doc.blueprint_id is None
        assert items[0].blueprint_id is None

    def test_list_filter_by_blueprint_and_course_wide(self, db, course_with_blueprint):
        course = course_with_blueprint["course"]
        bp = course_with_blueprint["bp"]

        doc_mod = feedback_repository.create_document(
            db,
            project_id=course.project_id,
            course_id=course.id,
            blueprint_id=bp.id,
            filename="m.pptx",
            file_type="pptx",
            content="a",
            model_used="m",
            created_by="t",
        )
        feedback_repository.create_items(
            db, doc_mod, [{"feedback_text": "module item", "sentiment": "neutral", "priority": "low"}],
        )
        doc_course = feedback_repository.create_document(
            db,
            project_id=course.project_id,
            course_id=course.id,
            blueprint_id=None,
            filename="c.docx",
            file_type="docx",
            content="b",
            model_used="m",
            created_by="t",
        )
        feedback_repository.create_items(
            db, doc_course, [{"feedback_text": "course item", "sentiment": "neutral", "priority": "low"}],
        )
        db.commit()

        mod_items = feedback_repository.list_active_items(
            db, course_id=course.id, blueprint_id=bp.id,
        ).all()
        wide_items = feedback_repository.list_active_items(
            db, course_id=course.id, course_wide_only=True,
        ).all()

        assert len(mod_items) == 1
        assert mod_items[0].feedback_text == "module item"
        assert len(wide_items) == 1
        assert wide_items[0].feedback_text == "course item"

    def test_format_module_label(self, course_with_blueprint):
        bp = course_with_blueprint["bp"]
        assert feedback_repository.format_module_label(None) == "Entire course"
        assert feedback_repository.format_module_label(bp) == "Module 1 — Introduction"


class TestCompileAndResolve:
    def test_compile_feedback_instruction_orders_by_priority(self):
        items = [
            SimpleNamespace(
                id=1, priority="low", sentiment="praise", theme="Tone",
                feedback_text="Nice work",
            ),
            SimpleNamespace(
                id=2, priority="high", sentiment="concern", theme="Structure",
                feedback_text="Reorganize sections",
            ),
            SimpleNamespace(
                id=3, priority="medium", sentiment="suggestion", theme=None,
                feedback_text="Add a quiz",
            ),
        ]
        text = compile_feedback_instruction(items)
        assert text.startswith("Reviewer feedback to apply:")
        lines = text.splitlines()[1:]
        assert lines[0].startswith("[HIGH][concern] Structure:")
        assert "Reorganize sections" in lines[0]
        assert lines[1].startswith("[MEDIUM][suggestion]")
        assert lines[2].startswith("[LOW][praise] Tone:")

    def test_resolve_apply_blueprint_id_shared_module(self):
        items = [
            SimpleNamespace(blueprint_id=10),
            SimpleNamespace(blueprint_id=10),
        ]
        assert resolve_apply_blueprint_id(items, None) == 10

    def test_resolve_apply_blueprint_id_course_wide_needs_override(self):
        items = [SimpleNamespace(blueprint_id=None)]
        assert resolve_apply_blueprint_id(items, None) is None
        assert resolve_apply_blueprint_id(items, 42) == 42

    def test_resolve_apply_blueprint_id_mixed_needs_override(self):
        items = [
            SimpleNamespace(blueprint_id=1),
            SimpleNamespace(blueprint_id=2),
        ]
        assert resolve_apply_blueprint_id(items, None) is None
        assert resolve_apply_blueprint_id(items, 2) == 2


class TestApplyFeedbackToModule:
    def test_apply_regenerates_blocks_with_compiled_instruction(self, db, course_with_blueprint):
        course = course_with_blueprint["course"]
        bp = course_with_blueprint["bp"]
        block = course_with_blueprint["block"]

        items = [
            SimpleNamespace(
                id=1,
                priority="high",
                sentiment="suggestion",
                theme="Content",
                feedback_text="Add local examples",
                blueprint_id=bp.id,
            ),
        ]

        fake_result = MagicMock()
        fake_result.status = "ok"
        fake_result.is_error = False
        fake_result.text = "Improved content with local examples"

        with patch(
            "promptops_app.services.llm_service.generate_with_metadata",
            return_value=fake_result,
        ) as mock_llm:
            instruction, regenerated, skipped = apply_feedback_to_module(
                db,
                items=items,
                blueprint=bp,
                course=course,
                created_by="tester",
            )
            db.commit()

        assert "Add local examples" in instruction
        assert skipped == 0
        assert len(regenerated) == 1
        assert regenerated[0]["block_id"] == block.id
        db.refresh(block)
        assert block.content == "Improved content with local examples"
        assert mock_llm.called
        # Feedback instruction was passed into the IMPROVISE prompt
        user_prompt = mock_llm.call_args[0][2]
        assert "Add local examples" in user_prompt

    def test_apply_skips_on_llm_error(self, db, course_with_blueprint):
        course = course_with_blueprint["course"]
        bp = course_with_blueprint["bp"]
        block = course_with_blueprint["block"]
        original = block.content

        items = [
            SimpleNamespace(
                id=1, priority="high", sentiment="concern", theme=None,
                feedback_text="Fix this", blueprint_id=bp.id,
            ),
        ]
        fake_result = MagicMock()
        fake_result.status = "error"
        fake_result.is_error = True
        fake_result.error_type = "timeout"
        fake_result.text = ""

        with patch(
            "promptops_app.services.llm_service.generate_with_metadata",
            return_value=fake_result,
        ):
            _instr, regenerated, skipped = apply_feedback_to_module(
                db,
                items=items,
                blueprint=bp,
                course=course,
                created_by="tester",
            )
            db.commit()

        assert regenerated == []
        assert skipped == 1
        db.refresh(block)
        assert block.content == original
