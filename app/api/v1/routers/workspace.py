"""
Workspace router — persist and restore user session state.

In Streamlit, the selected project/course/CDD IDs and sidebar config
(model, audience, domain) were kept in ``st.session_state`` and saved
into the JWT cookie on every render.

In the React app, the client calls these endpoints to:
  1. Load the last workspace on startup (GET /workspace).
  2. Save workspace changes when the user switches project/course (PUT /workspace).
  3. Save sidebar config changes (PUT /workspace/config).

The workspace state is embedded in the JWT payload (``ws`` and ``cfg`` claims)
so it survives page refreshes without an extra database round-trip.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.core.dependencies import get_current_user
from app.core.security import create_access_token, decode_access_token
from app.schemas.auth import WorkspaceUpdateResponse
from app.schemas.workspace import (
    SidebarConfig,
    WorkspaceConfigUpdateRequest,
    WorkspaceRead,
    WorkspaceUpdateRequest,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_log = logging.getLogger(__name__)

router = APIRouter()
_bearer = HTTPBearer(auto_error=False)


@router.get(
    "",
    response_model=WorkspaceRead,
    summary="Load the user's last saved workspace",
    description=(
        "Decodes the JWT to return the user's last saved project/course selection "
        "and sidebar configuration. The React app calls this on startup."
    ),
)
def get_workspace(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    current_user=Depends(get_current_user),
) -> WorkspaceRead:
    """
    Return the workspace and sidebar config embedded in the current JWT.

    Replacing Streamlit's st.session_state restore from cookie on startup.
    """
    from app.core.security import decode_access_token

    payload = decode_access_token(credentials.credentials)
    ws = payload.get("ws", {})
    cfg = payload.get("cfg", {})

    return WorkspaceRead(
        selected_project_id=ws.get("selected_project_id"),
        selected_project_name=ws.get("selected_project_name"),
        selected_cluster_id=ws.get("selected_cluster_id"),
        selected_course_id=ws.get("selected_course_id"),
        active_cdd_id=ws.get("active_cdd_id"),
        active_blueprint_id=ws.get("active_blueprint_id"),
        nav_page=ws.get("nav_page"),
        config=SidebarConfig(**cfg) if cfg else None,
    )


@router.put(
    "",
    response_model=WorkspaceUpdateResponse,
    summary="Save workspace selection (project, course, active CDD/Blueprint)",
    description=(
        "Issues a new JWT with the updated workspace embedded. "
        "The client must replace its stored token with the returned one."
    ),
)
def update_workspace(
    request_body: WorkspaceUpdateRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    current_user=Depends(get_current_user),
) -> WorkspaceUpdateResponse:
    """
    Persist the user's current workspace selection in the JWT.

    Called whenever the user switches project, course, or nav page in React.
    Replicates the cookie re-issue that Streamlit did on every render.
    """
    # Carry forward existing cfg so it is not lost during a workspace update.
    payload = decode_access_token(credentials.credentials)
    existing_cfg = payload.get("cfg", {})

    new_token = create_access_token(
        current_user.username,
        current_user.role,
        workspace=request_body.model_dump(exclude_none=True),
        config=existing_cfg or None,
    )

    _log.info("workspace_updated  user=%s", current_user.username)
    return WorkspaceUpdateResponse(access_token=new_token)


@router.put(
    "/config",
    response_model=WorkspaceUpdateResponse,
    summary="Save sidebar configuration (model, audience, domain)",
    description=(
        "Issues a new JWT with the updated sidebar config embedded. "
        "Called when the user changes the model selector or audience fields."
    ),
)
def update_workspace_config(
    request_body: WorkspaceConfigUpdateRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    current_user=Depends(get_current_user),
) -> WorkspaceUpdateResponse:
    """
    Persist sidebar configuration in the JWT.

    Replicates ``save_course_target_config()`` from the Streamlit app.
    """
    payload = decode_access_token(credentials.credentials)
    existing_ws = payload.get("ws", {})

    new_token = create_access_token(
        current_user.username,
        current_user.role,
        workspace=existing_ws or None,
        config=request_body.model_dump(),
    )

    _log.info("workspace_config_updated  user=%s  model=%s", current_user.username, request_body.model_choice)
    return WorkspaceUpdateResponse(access_token=new_token)
