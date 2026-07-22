"""Unit tests for permanent course purge (repository + archive gate)."""

from __future__ import annotations

import pytest

from app.core.exceptions import ValidationError
from promptops_app.repositories import course_repository


@pytest.fixture()
def course_graph(db):
    from promptops_app.database import (
        Block,
        BlockVersion,
        Cluster,
        Course,
        CourseDesignDocument,
        CourseModule,
        CourseUserAssignment,
        Generation,
        Project,
    )

    project = Project(name="Purge Proj", is_active=True)
    db.add(project)
    db.flush()
    cluster = Cluster(name="Purge Cluster", project_id=project.id, is_active=True)
    db.add(cluster)
    db.flush()
    course = Course(
        name="Archived Course",
        project_id=project.id,
        cluster_id=cluster.id,
        is_active=False,
    )
    db.add(course)
    db.flush()

    module = CourseModule(course_id=course.id, title="Module 1", position=1)
    db.add(module)
    db.flush()

    cdd = CourseDesignDocument(
        title="CDD",
        course_title="Archived Course",
        course_id=course.id,
        project_id=project.id,
        created_by="tester",
    )
    db.add(cdd)
    db.flush()
    course.active_cdd_id = cdd.id

    gen = Generation(
        topic="Topic",
        prompt_name="import",
        prompt_version="import",
        block_type="lesson",
        output_text="body",
        project_id=project.id,
        course_id=course.id,
        cdd_id=cdd.id,
        created_by="tester",
    )
    db.add(gen)
    db.flush()

    block = Block(
        generation_id=gen.id,
        block_type="lesson",
        block_label="Lesson 1",
        content="Hello",
        workflow_state="draft",
        module_id=module.id,
        version_num=1,
    )
    db.add(block)
    db.flush()
    db.add(
        BlockVersion(
            block_id=block.id,
            version_num=1,
            content="Hello",
            change_source="import",
            created_by="tester",
        )
    )
    db.add(CourseUserAssignment(course_id=course.id, username="author1"))
    db.commit()
    db.refresh(course)
    return {
        "cluster": cluster,
        "course": course,
        "module": module,
        "cdd": cdd,
        "gen": gen,
        "block": block,
    }


def test_list_courses_include_archived(db, course_graph):
    cluster_id = course_graph["cluster"].id
    active_only = course_repository.list_courses_for_cluster(db, cluster_id)
    assert active_only == []

    with_archived = course_repository.list_courses_for_cluster(
        db, cluster_id, include_archived=True
    )
    assert len(with_archived) == 1
    assert with_archived[0].is_active is False


def test_purge_course_removes_owned_content(db, course_graph):
    from promptops_app.database import (
        Block,
        BlockVersion,
        Course,
        CourseDesignDocument,
        CourseModule,
        CourseUserAssignment,
        Generation,
    )

    course_id = course_graph["course"].id
    block_id = course_graph["block"].id
    gen_id = course_graph["gen"].id
    module_id = course_graph["module"].id
    cdd_id = course_graph["cdd"].id

    course_repository.purge_course(db, course_id)

    assert db.query(Course).filter_by(id=course_id).first() is None
    assert db.query(Generation).filter_by(id=gen_id).first() is None
    assert db.query(Block).filter_by(id=block_id).first() is None
    assert db.query(BlockVersion).filter_by(block_id=block_id).count() == 0
    assert db.query(CourseModule).filter_by(id=module_id).first() is None
    assert db.query(CourseDesignDocument).filter_by(id=cdd_id).first() is None
    assert db.query(CourseUserAssignment).filter_by(course_id=course_id).count() == 0


def test_permanent_delete_endpoint_rejects_active_course(db):
    """Active courses must be archived before hard-delete."""
    from types import SimpleNamespace

    from app.api.v1.routers.courses import permanently_delete_course
    from promptops_app.database import Cluster, Course, Project

    project = Project(name="Live Proj", is_active=True)
    db.add(project)
    db.flush()
    cluster = Cluster(name="C", project_id=project.id, is_active=True)
    db.add(cluster)
    db.flush()
    course = Course(
        name="Live",
        project_id=project.id,
        cluster_id=cluster.id,
        is_active=True,
    )
    db.add(course)
    db.commit()
    db.refresh(course)

    with pytest.raises(ValidationError, match="Archive the course"):
        permanently_delete_course(
            course.id, db, SimpleNamespace(username="tester")
        )
