"""TenantCreateRequest template validation (Phase 8A)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.tenant import TenantCreateRequest


def _base_payload(**overrides):
    payload = {
        "slug": "client001",
        "name": "Client One",
        "client_name": "Client One",
        "admin_username": "client001_admin",
        "admin_password": "secret12",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("template", ["minimal", "academic", "publishing"])
def test_valid_template_values_are_accepted(template):
    req = TenantCreateRequest(**_base_payload(template=template))
    assert req.template == template


def test_omitted_template_defaults_to_minimal():
    req = TenantCreateRequest(**_base_payload())
    assert req.template == "minimal"


@pytest.mark.parametrize("template", ["foo", "../foo", "aviation", ""])
def test_invalid_template_values_are_rejected(template):
    with pytest.raises(ValidationError):
        TenantCreateRequest(**_base_payload(template=template))
