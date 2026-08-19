"""GET /api/v1/generations/{id}/trace — the imported-lesson fallback.

Imported items (editor_builder.py, prompt_name/version="import") never have a
GenerationJob — their content came from the uploaded package, not an LLM
call — so the endpoint's normal join always misses for them and used to 404
outright ("Trace for generation with id N not found"), even though the import
DOES make one real, traced LLM call per module to reconstruct its Blueprint
(reverse_blueprint.py). These tests pin the fallback: an imported Generation
linked to a ModuleBlueprint (via Generation.blueprint_id, set once the
reverse-Blueprint stage runs) now surfaces that module-level trace instead,
tagged so the frontend can label it as module-level rather than per-lesson.
"""

from __future__ import annotations

import pytest

from app.core.security import create_access_token, hash_password


def _headers(db, *, username: str, project_id: int):
    from promptops_app.database import TenantMembership, User

    user = User(
        username=username, password_hash=hash_password("test_password"),
        role="author", is_active=True, is_platform_admin=False, project_id=project_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(TenantMembership(user_id=user.id, project_id=project_id, role="author", active=True))
    db.commit()
    token = create_access_token(user.username, "author", project_id=project_id, is_platform_admin=False)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _mock_phoenix(monkeypatch):
    from app.core import phoenix_client

    monkeypatch.setattr(
        phoenix_client, "get_trace_observations",
        lambda trace_id: [{
            "id": "span1", "context": {"trace_id": trace_id, "span_id": "span1"},
            "name": "llm_call:reverse_blueprint", "span_kind": "LLM", "status_code": "OK",
            "attributes": {"input": {"value": "module content"}, "output": {"value": "module blueprint"}},
        }],
    )


@pytest.fixture()
def imported_module_setup(db):
    from promptops_app.database import Course, Generation, LLMUsageLog, ModuleBlueprint, Project

    project = Project(name="Import Trace Project", is_active=True)
    db.add(project)
    db.commit()
    db.refresh(project)

    course = Course(name="Imported Course", project_id=project.id, created_by="tester")
    db.add(course)
    db.commit()
    db.refresh(course)

    blueprint = ModuleBlueprint(title="Module 1 Blueprint", module_title="Module 1",
                               module_number=1, project_id=project.id, course_id=course.id,
                               created_by="tester")
    db.add(blueprint)
    db.commit()
    db.refresh(blueprint)

    imported_item = Generation(
        prompt_name="import", prompt_version="import", block_type="lesson", topic="Imported Lesson",
        output_text="content parsed from the uploaded package", project_id=project.id, course_id=course.id,
        blueprint_id=blueprint.id, created_by="tester",
    )
    db.add(imported_item)
    db.commit()
    db.refresh(imported_item)

    db.add(LLMUsageLog(
        user_id="tester", project_id=project.id, course_id=course.id,
        entity_type="reverse_blueprint", entity_id=str(blueprint.module_number),
        model_name="gpt-4o", status="success", trace_id="module-1-blueprint-trace",
    ))
    db.commit()

    return {
        "imported_item": imported_item, "blueprint": blueprint,
        "headers": _headers(db, username="import_project_user", project_id=project.id),
    }


def test_imported_item_falls_back_to_its_modules_reverse_blueprint_trace(client, imported_module_setup):
    resp = client.get(
        f"/api/v1/generations/{imported_module_setup['imported_item'].id}/trace",
        headers=imported_module_setup["headers"],
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["trace_id"] == "module-1-blueprint-trace"
    assert data["scope"] == "module_reconstruction"
    assert data["observations"][0]["attributes"]["output"]["value"] == "module blueprint"


def test_normal_generation_still_reports_generation_scope(client, db):
    """The fallback must not change behavior for the existing, non-import path."""
    from promptops_app.database import Generation, GenerationJob, LLMUsageLog, Project

    project = Project(name="Normal Gen Project", is_active=True)
    db.add(project)
    db.commit()
    db.refresh(project)

    gen = Generation(prompt_name="p", prompt_version="v1", block_type="lesson", topic="normal",
                     output_text="output", project_id=project.id, created_by="tester")
    db.add(gen)
    db.commit()
    db.refresh(gen)

    job = GenerationJob(id="normal-gen-job", job_type="generation", status="completed",
                       progress=100, result_entity_id=gen.id)
    db.add(job)
    db.add(LLMUsageLog(user_id="tester", project_id=project.id, entity_type="generation",
                       entity_id="normal-gen-job", model_name="gpt-4o", status="success",
                       trace_id="normal-gen-trace"))
    db.commit()

    resp = client.get(f"/api/v1/generations/{gen.id}/trace", headers=_headers(db, username="normal_user", project_id=project.id))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["trace_id"] == "normal-gen-trace"
    assert data["scope"] == "generation"


def test_imported_item_with_no_blueprint_link_still_404s(client, db):
    """No blueprint_id yet (reverse-Blueprint hasn't run/failed for this module)
    — there is genuinely nothing to fall back to."""
    from promptops_app.database import Generation, Project

    project = Project(name="Unlinked Import Project", is_active=True)
    db.add(project)
    db.commit()
    db.refresh(project)

    imported_item = Generation(prompt_name="import", prompt_version="import", block_type="lesson",
                               topic="Unlinked Imported Lesson", output_text="content", project_id=project.id,
                               created_by="tester")
    db.add(imported_item)
    db.commit()
    db.refresh(imported_item)

    resp = client.get(
        f"/api/v1/generations/{imported_item.id}/trace",
        headers=_headers(db, username="unlinked_user", project_id=project.id),
    )
    assert resp.status_code == 404


def test_imported_item_linked_to_a_module_whose_reverse_blueprint_failed_still_404s(client, db):
    """blueprint_id is set but the reverse_blueprint LLM call for that module
    never produced a usage row (e.g. it errored before logging) — still no
    trace to show, must not crash."""
    from promptops_app.database import Course, Generation, ModuleBlueprint, Project

    project = Project(name="Failed Reverse Blueprint Project", is_active=True)
    db.add(project)
    db.commit()
    db.refresh(project)
    course = Course(name="Course", project_id=project.id, created_by="tester")
    db.add(course)
    db.commit()
    db.refresh(course)
    blueprint = ModuleBlueprint(title="Module 1 Blueprint", module_title="Module 1", module_number=1,
                               project_id=project.id, course_id=course.id, created_by="tester")
    db.add(blueprint)
    db.commit()
    db.refresh(blueprint)

    imported_item = Generation(prompt_name="import", prompt_version="import", block_type="lesson",
                               topic="Imported Lesson", output_text="content", project_id=project.id,
                               course_id=course.id, blueprint_id=blueprint.id, created_by="tester")
    db.add(imported_item)
    db.commit()
    db.refresh(imported_item)

    resp = client.get(
        f"/api/v1/generations/{imported_item.id}/trace",
        headers=_headers(db, username="failed_reverse_user", project_id=project.id),
    )
    assert resp.status_code == 404
