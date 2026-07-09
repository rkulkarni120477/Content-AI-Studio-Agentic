"""Project and Course schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=300, examples=["Nursing Foundations"])
    client_name: Optional[str] = Field(default=None, max_length=300)
    description: Optional[str] = Field(default=None, max_length=2000)

    # Optional — when provided, the platform admin creating this project
    # (tenant) also gets its first user created and assigned in one step.
    admin_username: Optional[str] = Field(default=None, min_length=1, max_length=150)
    admin_password: Optional[str] = Field(default=None, min_length=6, max_length=256)
    admin_display_name: Optional[str] = Field(default=None, max_length=200)
    admin_role: str = Field(default="admin", description="DB role for the initial user: admin | reviewer | author")


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=300)
    client_name: Optional[str] = Field(default=None, max_length=300)
    description: Optional[str] = Field(default=None, max_length=2000)


class ProjectRead(BaseModel):
    id: int
    name: str
    client_name: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ProjectListItem(BaseModel):
    id: int
    name: str
    client_name: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ProjectInitialAdmin(BaseModel):
    id: int
    username: str
    role: str
    display_name: Optional[str] = None


class ProjectCreateResponse(ProjectRead):
    initial_admin: Optional[ProjectInitialAdmin] = None


# ---------------------------------------------------------------------------
# Project user assignment
# ---------------------------------------------------------------------------

class ProjectUserAssignRequest(BaseModel):
    """Assign a user to a project (matches Streamlit checkbox assignment)."""

    username: str = Field(..., min_length=1, max_length=150)


class ProjectUserListItem(BaseModel):
    username: str
