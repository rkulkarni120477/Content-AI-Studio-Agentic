"""Project and Course schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel, ConfigDict, Field


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

    model_config = ConfigDict(from_attributes=True)


class ProjectListItem(BaseModel):
    id: int
    name: str
    client_name: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Project user assignment
# ---------------------------------------------------------------------------

class ProjectUserAssignRequest(BaseModel):
    """Assign a user to a project (matches Streamlit checkbox assignment)."""

    username: str = Field(..., min_length=1, max_length=150)


class ProjectUserListItem(BaseModel):
    username: str
