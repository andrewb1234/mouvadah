"""API key management routes.

Users can create, list, and revoke per-user API keys for agent/MCP access.
The full key is returned only once on creation — thereafter only the prefix
is shown for identification.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlmodel import select

from api.api_keys import issue_api_key
from api.auth import CurrentUser
from api.authorization import require_workspace, require_project
from api.dependencies import SessionDep
from api.models.entities import (
    ApiKey,
    ApiKeyProject,
    Project,
    Workspace,
    WorkspaceMembership,
)
from api.security import (
    DELETE_SCOPE,
    READ_SCOPE,
    VALID_API_KEY_SCOPES,
    WRITE_SCOPE,
)

router = APIRouter(prefix="/apikeys", tags=["apikeys"])


class CreateApiKeyRequest(BaseModel):
    name: str = Field(default="Default", min_length=1, max_length=100)
    workspace_id: Optional[int] = None
    scopes: list[Literal["read", "write", "delete"]] = Field(
        default_factory=lambda: [READ_SCOPE, WRITE_SCOPE]
    )
    project_ids: list[int] = Field(default_factory=list, max_length=100)
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=365)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        scopes = sorted(set(value))
        if not scopes or not set(scopes).issubset(VALID_API_KEY_SCOPES):
            raise ValueError("At least one supported API-key scope is required.")
        return scopes

    @field_validator("project_ids")
    @classmethod
    def unique_project_ids(cls, value: list[int]) -> list[int]:
        return sorted(set(value))


class ApiKeyOut(BaseModel):
    id: int
    workspace_id: Optional[int]
    name: str
    key_prefix: str
    scopes: list[str]
    project_ids: list[int]
    expires_at: Optional[datetime]
    last_used_at: Optional[datetime]
    created_at: datetime
    revoked: bool
    resource_mode: str = "WORKSPACE"


class ApiKeyCreated(ApiKeyOut):
    key: str  # full key, shown once


def _require_browser_session(request: Request) -> None:
    if getattr(request.state, "auth_method", None) != "cookie":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys can only be managed from a browser session.",
        )


def _project_ids(session: SessionDep, api_key_id: int) -> list[int]:
    return list(
        session.exec(
            select(ApiKeyProject.project_id)
            .where(ApiKeyProject.api_key_id == api_key_id)
            .order_by(ApiKeyProject.project_id)
        ).all()
    )


def _to_out(session: SessionDep, api_key: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=api_key.id,
        workspace_id=api_key.workspace_id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        scopes=list(api_key.scopes),
        project_ids=_project_ids(session, api_key.id),  # type: ignore[arg-type]
        expires_at=api_key.expires_at,
        last_used_at=api_key.last_used_at,
        created_at=api_key.created_at,
        revoked=api_key.revoked,
        resource_mode=api_key.resource_mode,
    )


@router.get("")
async def list_api_keys(
    request: Request,
    session: SessionDep,
    user: CurrentUser,
) -> list[ApiKeyOut]:
    """List all API keys for the current user (non-revoked first)."""
    _require_browser_session(request)
    keys = session.exec(
        select(ApiKey)
        .where(ApiKey.user_id == user.id)
        .order_by(ApiKey.revoked, ApiKey.created_at.desc())
    ).all()
    return [_to_out(session, key) for key in keys]


@router.post("")
async def create_api_key(
    payload: CreateApiKeyRequest,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
) -> ApiKeyCreated:
    """Create a new API key. The full key is returned only once."""
    _require_browser_session(request)
    workspace_id = payload.workspace_id
    if payload.project_ids:
        projects = [
            require_project(
                session,
                user,
                project_id,
                write=bool({WRITE_SCOPE, DELETE_SCOPE} & set(payload.scopes)),
            )
            for project_id in payload.project_ids
        ]
        workspace_id = (
            workspace_id if workspace_id is not None else projects[0].workspace_id
        )
        if any(project.workspace_id != workspace_id for project in projects):
            raise HTTPException(
                422, "Every project restriction must belong to the key workspace."
            )
    else:
        if workspace_id is None:
            workspace_id = session.exec(
                select(WorkspaceMembership.workspace_id)
                .join(Workspace)
                .where(
                    WorkspaceMembership.user_id == user.id,
                    Workspace.deletion_requested_at.is_(None),
                )
                .order_by(WorkspaceMembership.id)
            ).first()
        if workspace_id is None:
            raise HTTPException(409, "Create a workspace before issuing an API key.")
        require_workspace(
            session,
            user,
            workspace_id,
            write=bool({WRITE_SCOPE, DELETE_SCOPE} & set(payload.scopes)),
            lock=True,
        )

    api_key, raw_key = issue_api_key(
        session,
        user_id=user.id,
        workspace_id=workspace_id,
        name=payload.name,
        scopes=payload.scopes,
        project_ids=payload.project_ids,
        expires_in_days=payload.expires_in_days,
    )

    return ApiKeyCreated(
        **_to_out(session, api_key).model_dump(),
        key=raw_key,
    )


@router.delete("/{key_id}")
async def revoke_api_key(
    key_id: int,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
) -> dict:
    """Revoke an API key by ID. Only the key owner can revoke."""
    _require_browser_session(request)
    api_key = session.get(ApiKey, key_id)
    if api_key is None or api_key.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )
    if api_key.workspace_id is not None:
        # Serialize key metadata changes with tenant export, deletion, and
        # purge without changing the existing owner-only revocation boundary.
        session.exec(
            select(Workspace)
            .where(Workspace.id == api_key.workspace_id)
            .with_for_update()
        ).first()
    api_key.revoked = True
    session.add(api_key)
    session.commit()
    return {"status": "revoked"}
