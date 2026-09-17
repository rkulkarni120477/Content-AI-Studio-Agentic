"""Workspace and sidebar configuration schemas."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class WorkspaceRead(BaseModel):
    """The user's last saved workspace state, decoded from the JWT payload."""

    selected_project_id: Optional[int] = None
    selected_project_name: Optional[str] = None
    selected_cluster_id: Optional[int] = None
    selected_course_id: Optional[int] = None
    active_cdd_id: Optional[int] = None
    active_blueprint_id: Optional[int] = None
    nav_page: Optional[str] = None
    config: Optional["SidebarConfig"] = None


class SidebarConfig(BaseModel):
    """Sidebar model/audience configuration persisted between sessions."""

    model_choice: str = Field(default="GPT-5.6 Terra")
    expert_domain: str = Field(default="")
    target_audience: str = Field(default="")
    audience_category: str = Field(default="Professional/Corporate")


class WorkspaceUpdateRequest(BaseModel):
    """Body for PUT /api/v1/workspace — persists the user's current selection."""

    selected_project_id: Optional[int] = None
    selected_project_name: Optional[str] = None
    selected_cluster_id: Optional[int] = None
    selected_course_id: Optional[int] = None
    active_cdd_id: Optional[int] = None
    active_blueprint_id: Optional[int] = None
    nav_page: Optional[str] = None


class WorkspaceConfigUpdateRequest(BaseModel):
    """Body for PUT /api/v1/workspace/config — persists sidebar settings."""

    model_choice: str = Field(default="GPT-5.6 Terra")
    expert_domain: str = Field(default="", max_length=200)
    target_audience: str = Field(default="", max_length=200)
    audience_category: str = Field(default="Professional/Corporate")
