"""Finding #9: two tenants whose client names normalize to the same DIS
client id must not both provision into the same DIS namespace.

tenant_service.create_tenant rejects the second tenant outright, before
anything commits — the alternative (letting dis_provisioning's best-effort
background task discover the collision later) has no request left to reject.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import ValidationError
from app.services import tenant_service


def _create(db, *, slug, name, client_name="", admin_username=None):
    return tenant_service.create_tenant(
        db,
        slug=slug,
        name=name,
        max_users=10,
        created_by="platform_admin",
        admin_username=admin_username or f"{slug}_admin",
        admin_password_hash="x",
        admin_display_name="Admin",
        client_name=client_name,
    )


def test_distinct_client_names_both_succeed(db):
    _create(db, slug="tenant-one", name="Tenant One", client_name="Alpha Corp")
    _create(db, slug="tenant-two", name="Tenant Two", client_name="Beta Corp")


def test_colliding_normalized_client_names_are_rejected(db):
    """"Cengage Learning" and "Cengage" both normalize to the same alias."""
    _create(db, slug="tenant-cengage-1", name="Tenant A", client_name="Cengage")
    with pytest.raises(ValidationError):
        _create(db, slug="tenant-cengage-2", name="Tenant B", client_name="Cengage Learning")


def test_collision_via_name_fallback_when_client_name_omitted(db):
    """No client_name → provisioning falls back to the tenant's own name
    (create_tenant.py's own fallback); the collision check must use the same
    fallback or it would miss exactly this case."""
    _create(db, slug="tenant-fallback-1", name="Nova Publishing", client_name="")
    with pytest.raises(ValidationError):
        _create(db, slug="tenant-fallback-2", name="Nova Publishing", client_name="")


def test_case_and_spacing_variants_collide(db):
    _create(db, slug="tenant-case-1", name="Tenant A", client_name="acme_corp")
    with pytest.raises(ValidationError):
        _create(db, slug="tenant-case-2", name="Tenant B", client_name="ACME Corp")


def test_an_inactive_tenants_client_name_does_not_block_reuse(db):
    from promptops_app.database import Project

    project, _ = _create(db, slug="tenant-retired", name="Retired Co", client_name="Retired Client")
    project.is_active = False
    db.commit()

    # Must not raise — a retired tenant's name is free to reuse.
    _create(db, slug="tenant-reuses-name", name="New Co", client_name="Retired Client")
