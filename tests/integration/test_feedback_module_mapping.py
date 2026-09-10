"""API tests for feedback module remap + apply validation."""

from __future__ import annotations

import pytest

from app.core.security import create_access_token, hash_password


@pytest.fixture()
def platform_headers(db):
    """Auth headers for a platform admin (JWT with is_platform_admin=True)."""
    from promptops_app.database import User

    user = User(
        username="fb_platform_admin",
        password_hash=hash_password("test_password"),
        role="admin",
        is_active=True,
        is_platform_admin=True,
    )
    db.add(user)
    db.commit()
    token = create_access_token(
        user.username,
        user.role or "admin",
        is_platform_admin=True,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def feedback_graph(db):
    """Course + two blueprints + one feedback item (course-wide)."""
    from promptops_app.database import (
        Course,
        CourseDesignDocument,
        Cluster,
        FeedbackDocument,
        FeedbackItem,
        ModuleBlueprint,
        Project,
    )

    project = Project(name="API FB Proj", is_active=True)
    db.add(project)
    db.flush()
    cluster = Cluster(name="API FB Cluster", project_id=project.id, is_active=True)
    db.add(cluster)
    db.flush()
    course = Course(
        name="API FB Course",
        project_id=project.id,
        cluster_id=cluster.id,
        is_active=True,
    )
    db.add(course)
    db.flush()

    cdd = CourseDesignDocument(
        title="CDD",
        course_title=course.name,
        course_id=course.id,
        project_id=project.id,
        created_by="tester",
    )
    db.add(cdd)
    db.flush()

    bp = ModuleBlueprint(
        cdd_id=cdd.id,
        title="BP1",
        module_title="Intro",
        module_number=1,
        project_id=project.id,
        course_id=course.id,
        created_by="tester",
    )
    bp2 = ModuleBlueprint(
        cdd_id=cdd.id,
        title="BP2",
        module_title="Advanced",
        module_number=2,
        project_id=project.id,
        course_id=course.id,
        created_by="tester",
    )
    db.add_all([bp, bp2])
    db.flush()

    doc = FeedbackDocument(
        project_id=project.id,
        course_id=course.id,
        blueprint_id=None,
        filename="review.pptx",
        file_type="pptx",
        content="text",
        item_count=1,
        status="active",
        created_by="tester",
    )
    db.add(doc)
    db.flush()
    item = FeedbackItem(
        document_id=doc.id,
        project_id=project.id,
        course_id=course.id,
        blueprint_id=None,
        feedback_text="Need more examples",
        theme="Content",
        sentiment="suggestion",
        priority="high",
        status="active",
        created_by="tester",
    )
    db.add(item)
    db.commit()
    db.refresh(course)
    db.refresh(bp)
    db.refresh(bp2)
    db.refresh(item)
    return {"course": course, "bp": bp, "bp2": bp2, "item": item, "project": project}


class TestFeedbackRemapApi:
    def test_patch_remaps_item_to_module(self, client, platform_headers, feedback_graph):
        item = feedback_graph["item"]
        bp = feedback_graph["bp"]
        resp = client.patch(
            f"/api/v1/feedback/items/{item.id}",
            json={"blueprint_id": bp.id},
            headers=platform_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["blueprint_id"] == bp.id
        assert data["module_label"] == "Module 1 — Intro"

    def test_patch_rejects_blueprint_from_other_course(
        self, client, platform_headers, db, feedback_graph,
    ):
        from promptops_app.database import (
            Course,
            CourseDesignDocument,
            Cluster,
            ModuleBlueprint,
            Project,
        )

        item = feedback_graph["item"]
        other_proj = Project(name="Other", is_active=True)
        db.add(other_proj)
        db.flush()
        other_cluster = Cluster(name="OC", project_id=other_proj.id, is_active=True)
        db.add(other_cluster)
        db.flush()
        other_course = Course(
            name="Other Course",
            project_id=other_proj.id,
            cluster_id=other_cluster.id,
            is_active=True,
        )
        db.add(other_course)
        db.flush()
        other_cdd = CourseDesignDocument(
            title="X",
            course_title="Other",
            course_id=other_course.id,
            project_id=other_proj.id,
            created_by="t",
        )
        db.add(other_cdd)
        db.flush()
        foreign_bp = ModuleBlueprint(
            cdd_id=other_cdd.id,
            title="Foreign",
            module_title="Foreign",
            module_number=1,
            project_id=other_proj.id,
            course_id=other_course.id,
            created_by="t",
        )
        db.add(foreign_bp)
        db.commit()
        db.refresh(foreign_bp)

        resp = client.patch(
            f"/api/v1/feedback/items/{item.id}",
            json={"blueprint_id": foreign_bp.id},
            headers=platform_headers,
        )
        assert resp.status_code == 422, resp.text


class TestFeedbackApplyApi:
    def test_apply_requires_target_for_course_wide_items(
        self, client, platform_headers, feedback_graph,
    ):
        item = feedback_graph["item"]
        resp = client.post(
            "/api/v1/feedback/apply",
            json={"item_ids": [item.id]},
            headers=platform_headers,
        )
        assert resp.status_code == 422, resp.text
        body = str(resp.json()).lower()
        assert "module" in body

    def test_apply_with_target_queues_job(
        self, client, platform_headers, feedback_graph, monkeypatch, db,
    ):
        # Apply is now a background job (one LLM call per block). The endpoint
        # validates + enqueues and returns a job handle; stub the dispatch so this
        # asserts the enqueue contract without running the worker.
        submitted = {}

        def _fake_submit(fn, job_id):
            submitted["fn"] = fn
            submitted["job_id"] = job_id

        monkeypatch.setattr(
            "promptops_app.jobs.dispatch.submit", _fake_submit, raising=True,
        )

        item = feedback_graph["item"]
        bp = feedback_graph["bp"]
        resp = client.post(
            "/api/v1/feedback/apply",
            json={"item_ids": [item.id], "blueprint_id": bp.id},
            headers=platform_headers,
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert data["job_id"]
        assert data["status"] == "queued"
        assert data["status_url"].endswith(data["job_id"])
        # The worker was handed the job we returned.
        assert submitted["job_id"] == data["job_id"]

        # A queued apply_feedback job row exists, carrying the resolved target.
        from promptops_app.database import GenerationJob

        job = db.query(GenerationJob).filter(GenerationJob.id == data["job_id"]).first()
        assert job is not None
        assert job.job_type == "apply_feedback"
        import json as _json
        params = _json.loads(job.request_json)
        assert params["blueprint_id"] == bp.id
        assert params["item_ids"] == [item.id]

    def test_apply_result_returns_summary_for_completed_job(
        self, client, platform_headers, feedback_graph, db,
    ):
        # Seed a completed apply_feedback job as the worker would leave it, then
        # read its summary back through the result endpoint the frontend polls.
        import json as _json

        from promptops_app.database import GenerationJob

        bp = feedback_graph["bp"]
        job = GenerationJob(
            id="applyjob_test_0001",
            job_type="apply_feedback",
            status="completed",
            progress=100,
            current_step="Completed",
            created_by="fb_platform_admin",
            result_entity_id=bp.id,
            result_json=_json.dumps({
                "instruction": "Reviewer feedback to apply:\nNeed more examples",
                "regenerated": [{"block_id": 7, "block_label": "Intro - Body"}],
                "skipped": 1,
                "blueprint_id": bp.id,
                "module_label": "Module 1 — Intro",
            }),
        )
        db.add(job)
        db.commit()

        resp = client.get(
            f"/api/v1/feedback/apply-result/{job.id}",
            headers=platform_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["blueprint_id"] == bp.id
        assert data["module_label"] == "Module 1 — Intro"
        assert data["skipped"] == 1
        assert data["regenerated"] == [{"block_id": 7, "block_label": "Intro - Body"}]
        assert "Need more examples" in data["instruction"]

    def test_apply_result_unknown_job_is_404(self, client, platform_headers):
        resp = client.get(
            "/api/v1/feedback/apply-result/does_not_exist",
            headers=platform_headers,
        )
        assert resp.status_code == 404, resp.text
