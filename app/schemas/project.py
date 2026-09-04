"""Project and Course schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.ui_labels import parse_ui_labels


CLIENT_OPTIONS = ('cengage', 'aim', 'academian', 'demo')

def normalize_client_id(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower().replace(' ', '_')
    aliases = {
        'cengage': 'cengage',
        'cengage_learning': 'cengage',
        'aim': 'aim',
        'academian': 'academian',
        'demo': 'demo',
    }
    return aliases.get(v, v)

# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300, examples=["Nursing Foundations"])
    client_name: Optional[str] = Field(default=None, max_length=300, description="Client id selected at project creation: cengage | aim | academian | demo")
    description: Optional[str] = Field(default=None, max_length=2000)


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=300)
    client_name: Optional[str] = Field(default=None, max_length=300, description="Client id selected at project creation: cengage | aim | academian | demo")
    description: Optional[str] = Field(default=None, max_length=2000)


class ProjectRead(BaseModel):
    id: int
    name: str
    client_name: Optional[str] = None
    @property
    def client_id(self) -> Optional[str]:
        return normalize_client_id(self.client_name)
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    # A project IS the tenant, so its display-label overrides ride along here.
    # This is how every member (not just platform admins) receives them: the
    # frontend already fetches this project on workspace load.
    ui_labels: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)

    @field_validator("ui_labels", mode="before")
    @classmethod
    def _parse_ui_labels(cls, value):
        return parse_ui_labels(value)


class ProjectListItem(BaseModel):
    id: int
    name: str
    client_name: Optional[str] = None
    created_at: Optional[datetime] = None
    ui_labels: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)

    @field_validator("ui_labels", mode="before")
    @classmethod
    def _parse_ui_labels(cls, value):
        return parse_ui_labels(value)


# ---------------------------------------------------------------------------
# Project user assignment
# ---------------------------------------------------------------------------

class ProjectUserAssignRequest(BaseModel):
    """Assign a user to a project (matches Streamlit checkbox assignment)."""

    username: str = Field(..., min_length=1, max_length=150)


class ProjectUserListItem(BaseModel):
    username: str
