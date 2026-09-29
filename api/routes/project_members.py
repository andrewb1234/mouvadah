"""Interactive project sharing without workspace membership."""

from datetime import timedelta
import secrets

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from typing import Literal
from sqlmodel import select

from api.auth import CurrentUser
from api.api_keys import hash_api_key
from api.authorization import require_project, project_access, project_read
from api.config import get_settings
from api.dependencies import SessionDep
from api.events import Event, get_broadcaster
from api.models.entities import (
    ApiKey,
    ApiKeyProject,
    Project,
    ProjectMembership,
    ProjectInvitation,
    ProjectAccessEvent,
    User,
    Workspace,
    WorkspaceMembership,
)
from api.models.enums import SSEAction
from api.security import get_api_key_authorization
from api.utils.time import utcnow

router = APIRouter(tags=["project sharing"])


class InviteCreate(BaseModel):
    email: str = Field(
        min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    role: Literal["VIEWER", "EDITOR"] = "EDITOR"
    expires_in_days: int = Field(default=7, ge=1, le=30)


class InviteToken(BaseModel):
    token: str = Field(min_length=32, max_length=200)


class RoleUpdate(BaseModel):
    role: Literal["VIEWER", "EDITOR"]


def browser_only():
    if get_api_key_authorization() is not None:
        raise HTTPException(403, "Project sharing controls require a browser session.")


def ledger(session, project, user, action, subject=None):
    session.add(
        ProjectAccessEvent(
            project_id=project.id,
            workspace_id=project.workspace_id,
            actor_user_id=user.id,
            subject_user_id=subject,
            action=action,
        )
    )


def invitation_read(invitation):
    return invitation.model_dump(exclude={"token_hash"})


async def changed(project, user_id):
    await get_broadcaster().publish(
        Event(
            action=SSEAction.PROJECT_ACCESS_CHANGED,
            entity="project",
            entity_id=project.id,
            workspace_id=project.workspace_id,
            project_id=project.id,
            recipient_user_id=user_id,
        )
    )


def revoke_project_grants(session, project_id, user_id):
    # Revocation is scoped to grants explicitly containing this project, never
    # browser sessions or unrelated workspace/project credentials.
    for key in session.exec(
        select(ApiKey)
        .join(ApiKeyProject)
        .where(
            ApiKeyProject.project_id == project_id,
            ApiKey.user_id == user_id,
            ApiKey.revoked.is_(False),
        )
    ).all():
        key.revoked = True
        session.add(key)


@router.get("/projects/{project_id}/members")
def members(project_id: int, session: SessionDep, user: CurrentUser):
    browser_only()
    project = require_project(session, user, project_id)
    manager = project_access(session, user, project).can_manage_access
    inherited = session.exec(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == project.workspace_id
        )
    ).all()
    direct = session.exec(
        select(ProjectMembership).where(ProjectMembership.project_id == project_id)
    ).all()
    direct_by_user = {row.user_id: row for row in direct}
    ids = {row.user_id for row in inherited} | set(direct_by_user)
    result = []
    for person in session.exec(
        select(User).where(User.id.in_(ids)).order_by(User.name)
    ).all():
        access = project_access(session, person, project)
        grant = direct_by_user.get(person.id)
        result.append(
            dict(
                user_id=person.id,
                name=person.name,
                avatar_url=person.avatar_url,
                email=person.email if manager else None,
                effective_role=access.effective_role,
                access_source=access.access_source,
                direct_role=grant.role if grant else None,
            )
        )
    return result


@router.get("/projects/{project_id}/invitations")
def invitations(project_id: int, session: SessionDep, user: CurrentUser):
    browser_only()
    require_project(session, user, project_id, admin=True)
    return [
        invitation_read(row)
        for row in session.exec(
            select(ProjectInvitation)
            .where(
                ProjectInvitation.project_id == project_id,
                ProjectInvitation.accepted_at.is_(None),
                ProjectInvitation.revoked_at.is_(None),
                ProjectInvitation.expires_at > utcnow(),
            )
            .order_by(ProjectInvitation.id)
        ).all()
    ]


@router.post("/projects/{project_id}/invitations", status_code=201)
def invite(
    project_id: int, payload: InviteCreate, session: SessionDep, user: CurrentUser
):
    browser_only()
    project = require_project(session, user, project_id, admin=True)
    email = payload.email.strip().lower()
    person = session.exec(select(User).where(User.email == email)).first()
    if person and project_access(session, person, project):
        raise HTTPException(
            409,
            "This person already has project access. Manage their existing access instead.",
        )
    pending = session.exec(
        select(ProjectInvitation).where(
            ProjectInvitation.project_id == project_id,
            ProjectInvitation.email == email,
            ProjectInvitation.accepted_at.is_(None),
            ProjectInvitation.revoked_at.is_(None),
            ProjectInvitation.expires_at > utcnow(),
        )
    ).first()
    if pending:
        raise HTTPException(
            409,
            "An invitation is already pending. Revoke it before creating a replacement.",
        )
    raw = secrets.token_urlsafe(32)
    invitation = ProjectInvitation(
        project_id=project_id,
        email=email,
        role=payload.role,
        token_hash=hash_api_key(raw),
        created_by_user_id=user.id,
        expires_at=utcnow() + timedelta(days=payload.expires_in_days),
    )
    session.add(invitation)
    ledger(session, project, user, "INVITATION_CREATED")
    session.commit()
    session.refresh(invitation)
    return {
        **invitation_read(invitation),
        "token": raw,
        "accept_url": f"{get_settings().public_origin()}/app#project_invite={raw}",
    }


@router.delete("/projects/{project_id}/invitations/{invitation_id}", status_code=204)
def revoke_invite(
    project_id: int, invitation_id: int, session: SessionDep, user: CurrentUser
):
    browser_only()
    project = require_project(session, user, project_id, admin=True)
    row = session.exec(
        select(ProjectInvitation)
        .where(
            ProjectInvitation.id == invitation_id,
            ProjectInvitation.project_id == project_id,
        )
        .with_for_update()
    ).first()
    if not row or row.accepted_at or row.revoked_at:
        raise HTTPException(404, "Invitation not found.")
    row.revoked_at = utcnow()
    session.add(row)
    ledger(session, project, user, "INVITATION_REVOKED")
    session.commit()
    return Response(status_code=204)


def resolve_invitation(session, user, token, *, lock=False):
    row = session.exec(
        select(ProjectInvitation).where(
            ProjectInvitation.token_hash == hash_api_key(token)
        )
    ).first()
    if row is None or row.email != user.email.strip().lower():
        raise HTTPException(404, "Invitation is not available for this account.")
    project = session.get(Project, row.project_id)
    if not project:
        raise HTTPException(404, "Invitation is not available for this account.")
    query = select(Workspace).where(
        Workspace.id == project.workspace_id, Workspace.deletion_requested_at.is_(None)
    )
    if lock:
        query = query.with_for_update()
    if session.exec(query.execution_options(populate_existing=True)).first() is None:
        raise HTTPException(404, "Invitation is not available for this account.")
    row = session.exec(
        select(ProjectInvitation)
        .where(ProjectInvitation.id == row.id)
        .execution_options(populate_existing=True)
    ).first()
    if not row or row.accepted_at or row.revoked_at or row.expires_at <= utcnow():
        raise HTTPException(404, "Invitation is not available for this account.")
    return project, row


@router.post("/project-invitations/preview")
def preview(payload: InviteToken, session: SessionDep, user: CurrentUser):
    browser_only()
    project, row = resolve_invitation(session, user, payload.token)
    inviter = session.get(User, row.created_by_user_id)
    workspace = session.get(Workspace, project.workspace_id)
    return dict(
        project_id=project.id,
        project_name=project.name,
        workspace_name=workspace.name,
        inviter_name=inviter.name if inviter else "A project administrator",
        role=row.role,
        expires_at=row.expires_at,
    )


@router.post("/project-invitations/accept")
async def accept(payload: InviteToken, session: SessionDep, user: CurrentUser):
    browser_only()
    project, row = resolve_invitation(session, user, payload.token, lock=True)
    # Do not create a hidden second grant if workspace access was acquired
    # after issuance, and do not unexpectedly elevate an existing direct grant.
    if project_access(session, user, project) is None:
        session.add(
            ProjectMembership(
                project_id=project.id,
                user_id=user.id,
                role=row.role,
                created_by_user_id=row.created_by_user_id,
            )
        )
    row.accepted_at = utcnow()
    row.accepted_by_user_id = user.id
    session.add(row)
    ledger(session, project, user, "INVITATION_ACCEPTED", user.id)
    session.commit()
    await changed(project, user.id)
    return project_read(session, user, project)


@router.patch("/projects/{project_id}/members/{user_id}")
async def role(
    project_id: int,
    user_id: int,
    payload: RoleUpdate,
    session: SessionDep,
    user: CurrentUser,
):
    browser_only()
    project = require_project(session, user, project_id, admin=True)
    row = session.exec(
        select(ProjectMembership)
        .where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    ).first()
    if row is None:
        raise HTTPException(404, "Direct project membership not found.")
    old_role = row.role
    row.role = payload.role
    session.add(row)
    if old_role == "EDITOR" and payload.role == "VIEWER":
        revoke_project_grants(session, project_id, user_id)
    ledger(session, project, user, "ROLE_CHANGED", user_id)
    session.commit()
    await changed(project, user_id)
    return {"role": row.role}


async def remove_direct(project_id, user_id, session, user, *, leaving=False):
    browser_only()
    project = require_project(session, user, project_id, admin=not leaving)
    if leaving:
        # Even read-only members serialize leave with all access mutations.
        session.exec(
            select(Workspace)
            .where(Workspace.id == project.workspace_id)
            .with_for_update()
        ).first()
    row = session.exec(
        select(ProjectMembership)
        .where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    ).first()
    if row is None:
        raise HTTPException(404, "Direct project membership not found.")
    session.delete(row)
    revoke_project_grants(session, project_id, user_id)
    ledger(
        session, project, user, "MEMBER_LEFT" if leaving else "MEMBER_REMOVED", user_id
    )
    session.commit()
    await changed(project, user_id)
    return Response(status_code=204)


@router.delete("/projects/{project_id}/members/{user_id}", status_code=204)
async def remove(project_id: int, user_id: int, session: SessionDep, user: CurrentUser):
    return await remove_direct(project_id, user_id, session, user)


@router.delete("/projects/{project_id}/membership", status_code=204)
async def leave(project_id: int, session: SessionDep, user: CurrentUser):
    return await remove_direct(project_id, user.id, session, user, leaving=True)
