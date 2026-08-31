"""Phase 8A: CAS tenant create forwards template to DIS provisioning."""

from __future__ import annotations

import pytest

from promptops_app.database import Project


def _create_payload(**overrides):
    payload = {
        "slug": "tpl001",
        "name": "Template Client",
        "client_name": "Template Client",
        "max_users": 10,
        "admin_username": "tpl001_admin",
        "admin_password": "secret12",
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def provision_calls(monkeypatch):
    calls: list[tuple[tuple, dict]] = []

    def _capture(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr("app.services.dis_provisioning.provision_dis_client", _capture)
    return calls


@pytest.mark.parametrize("template", ["minimal", "academic", "publishing"])
def test_valid_template_creates_tenant(client, db, auth_headers, provision_calls, template):
    slug = f"tpl-{template}"
    resp = client.post(
        "/api/v1/platform/tenants",
        json=_create_payload(slug=slug, admin_username=f"{slug}_admin", template=template),
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text

    project = db.query(Project).filter(Project.slug == slug).one()
    assert project.name == "Template Client"

    assert len(provision_calls) == 1
    args, kwargs = provision_calls[0]
    assert args == ("Template Client", "Template Client")
    assert kwargs == {"template": template}


def test_omitted_template_defaults_to_minimal(client, db, auth_headers, provision_calls):
    resp = client.post(
        "/api/v1/platform/tenants",
        json=_create_payload(slug="tpl-omit", admin_username="tpl_omit_admin"),
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text

    assert db.query(Project).filter(Project.slug == "tpl-omit").one()
    assert provision_calls[0][1] == {"template": "minimal"}


@pytest.mark.parametrize("template", ["foo", "../foo", "aviation"])
def test_invalid_template_returns_422_and_does_not_create_project(
    client, db, auth_headers, provision_calls, template,
):
    slug = f"bad-{template.replace('/', '-')}"
    before = db.query(Project).filter(Project.slug == slug).count()
    resp = client.post(
        "/api/v1/platform/tenants",
        json=_create_payload(
            slug=slug,
            admin_username=f"{slug}_admin",
            template=template,
        ),
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert db.query(Project).filter(Project.slug == slug).count() == before
    assert provision_calls == []
