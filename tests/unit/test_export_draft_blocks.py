"""CAS AIM findings, Phase 6 (finding 14): an ID (author role) already has
export.course permission (test_permissions.py::test_author_can_export_course),
but the full-title export only ever included approved/published blocks
(build_blueprint_export_layout's exportable_only=True default) -- so a
title with nothing approved yet exported nothing at all, regardless of who
asked. exportable_only=False is the draft-package escape hatch: same
permission, same endpoint, no approval-workflow change.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def course(db):
    from promptops_app.database import Course, Project

    p = Project(name="Export Draft Project", created_by="tester")
    db.add(p)
    db.commit()
    db.refresh(p)

    c = Course(project_id=p.id, name="Export Draft Course", created_by="tester")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _make_block(db, course, *, workflow_state: str, topic: str = "Lesson 1"):
    from promptops_app.database import Block, Generation

    gen = Generation(
        prompt_name="", prompt_version="", block_type="Lesson", topic=topic,
        output_text="body", created_by="tester", course_id=course.id,
    )
    db.add(gen)
    db.commit()
    db.refresh(gen)

    block = Block(
        generation_id=gen.id, block_type="lesson", block_label=topic,
        content="body", workflow_state=workflow_state, position=0,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


class TestExportableOnlyFlag:
    def test_draft_blocks_are_excluded_by_default(self, db, course):
        from promptops_app.repositories.generation_repository import build_blueprint_export_layout

        _make_block(db, course, workflow_state="draft")

        ordered, modules, views = build_blueprint_export_layout(db, course.id)

        assert ordered == []

    def test_draft_blocks_are_included_when_exportable_only_is_false(self, db, course):
        from promptops_app.repositories.generation_repository import build_blueprint_export_layout

        block = _make_block(db, course, workflow_state="draft")

        ordered, modules, views = build_blueprint_export_layout(
            db, course.id, exportable_only=False,
        )

        assert [b.id for b in ordered] == [block.id]

    def test_a_mix_of_states_all_come_through_when_exportable_only_is_false(self, db, course):
        from promptops_app.repositories.generation_repository import build_blueprint_export_layout

        draft = _make_block(db, course, workflow_state="draft", topic="Draft Lesson")
        approved = _make_block(db, course, workflow_state="approved", topic="Approved Lesson")

        ordered, modules, views = build_blueprint_export_layout(
            db, course.id, exportable_only=False,
        )

        assert {b.id for b in ordered} == {draft.id, approved.id}

    def test_workflow_state_filter_still_applies_on_top_of_exportable_only_false(self, db, course):
        """exportable_only=False widens the pool; an explicit workflow_state
        filter (e.g. the published-only IMSCC export path) still narrows it."""
        from promptops_app.repositories.generation_repository import build_blueprint_export_layout

        _make_block(db, course, workflow_state="draft", topic="Draft Lesson")
        published = _make_block(db, course, workflow_state="published", topic="Published Lesson")

        ordered, modules, views = build_blueprint_export_layout(
            db, course.id, exportable_only=False, workflow_state="published",
        )

        assert [b.id for b in ordered] == [published.id]


class TestExportCourseEndpointExposesTheFlag:
    """The repository already supported exportable_only -- the actual gap was
    that GET /courses/{id}/export never let a caller ask for it, so a title
    with nothing approved yet always 400'd for everyone, ID included, even
    though export.course itself already allows the author/ID role."""

    def test_an_id_gets_nothing_for_an_unapproved_title_by_default(self, db, course, client, author_headers):
        _make_block(db, course, workflow_state="draft")

        resp = client.get(
            f"/api/v1/courses/{course.id}/export",
            params={"format": "md"},
            headers=author_headers,
        )

        assert resp.status_code == 409
        assert "approved or published" in resp.json()["error"]["message"].lower()

    def test_an_id_can_download_the_draft_package_instead(self, db, course, client, author_headers):
        _make_block(db, course, workflow_state="draft")

        resp = client.get(
            f"/api/v1/courses/{course.id}/export",
            params={"format": "md", "exportable_only": "false"},
            headers=author_headers,
        )

        assert resp.status_code == 200
        assert len(resp.content) > 0
