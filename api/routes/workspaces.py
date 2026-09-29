"""Workspace and membership endpoints."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func
from sqlmodel import select

from api.auth import CurrentUser
from api.config import get_settings
from api.authorization import require_workspace
from api.dependencies import SessionDep
from api.events import Event, get_broadcaster
from api.models.entities import (
    ApiKey,
    BrowserSession,
    User,
    Workspace,
    WorkspaceInvitation,
    ProjectInvitation,
    ProjectMembership,
    Project,
    WorkspaceLifecycleEvent,
    WorkspaceMembership,
    WorkspaceMembershipEvent,
)
from api.models.enums import (
    WorkspaceLifecycleAction,
    WorkspaceMembershipAction,
    WorkspaceRole,
    SSEAction,
)
from api.schemas import (
    WorkspaceCreate,
    WorkspaceDeletionCreate,
    WorkspaceDeletionRead,
    WorkspaceInvitationAccept,
    WorkspaceInvitationCreate,
    WorkspaceInvitationCreated,
    WorkspaceInvitationRead,
    WorkspaceLifecycleEventRead,
    WorkspaceMemberRead,
    WorkspaceMemberRoleUpdate,
    WorkspaceMembershipEventRead,
    WorkspaceMembershipMutationRead,
    WorkspaceOwnershipTransfer,
    WorkspaceRead,
)
from api.security import get_api_key_authorization
from api.utils.time import utcnow
from api.workspace_export import build_workspace_export

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

_MANAGED_MEMBER_ROLES = {
    WorkspaceRole.ADMIN,
    WorkspaceRole.MEMBER,
    WorkspaceRole.VIEWER,
}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:70] or "workspace"


def _available_slug(session, requested: str) -> str:
    slug = requested
    suffix = 2
    while session.exec(select(Workspace.id).where(Workspace.slug == slug)).first():
        slug = f"{requested[:70]}-{suffix}"
        suffix += 1
    return slug


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _hash_invitation_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _invitation_read(invitation: WorkspaceInvitation) -> WorkspaceInvitationRead:
    return WorkspaceInvitationRead(
        id=invitation.id,  # type: ignore[arg-type]
        workspace_id=invitation.workspace_id,
        email=invitation.email,
        role=invitation.role,
        created_by_user_id=invitation.created_by_user_id,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        accepted_by_user_id=invitation.accepted_by_user_id,
        revoked_at=invitation.revoked_at,
        created_at=invitation.created_at,
    )


def _require_interactive_browser() -> None:
    if get_api_key_authorization() is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace membership controls require a browser session.",
        )


@router.get("", response_model=list[WorkspaceRead])
def list_workspaces(
    session: SessionDep,
    user: CurrentUser,
) -> list[WorkspaceRead]:
    query = (
        select(Workspace, WorkspaceMembership)
        .join(
            WorkspaceMembership,
            WorkspaceMembership.workspace_id == Workspace.id,  # type: ignore[arg-type]
        )
        .where(WorkspaceMembership.user_id == user.id)
        .order_by(Workspace.id)
    )
    api_key = get_api_key_authorization()
    if api_key is not None:
        query = query.where(Workspace.id == api_key.workspace_id)
    rows = session.exec(query).all()
    return [
        WorkspaceRead(
            id=workspace.id,  # type: ignore[arg-type]
            name=workspace.name,
            slug=workspace.slug,
            role=membership.role,
            created_at=workspace.created_at,
            deletion_requested_at=workspace.deletion_requested_at,
            purge_after=workspace.purge_after,
            deletion_requested_by=workspace.deletion_requested_by,
            deletion_export_sha256=workspace.deletion_export_sha256,
        )
        for workspace, membership in rows
    ]


@router.post(
    "",
    response_model=WorkspaceRead,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace(
    payload: WorkspaceCreate,
    session: SessionDep,
    user: CurrentUser,
) -> WorkspaceRead:
    if get_api_key_authorization() is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace-bound API keys cannot create workspaces.",
        )
    requested_slug = payload.slug or _slugify(payload.name)
    workspace = Workspace(
        name=payload.name,
        slug=_available_slug(session, requested_slug),
    )
    session.add(workspace)
    session.flush()
    membership = WorkspaceMembership(
        workspace_id=workspace.id,  # type: ignore[arg-type]
        user_id=user.id,  # type: ignore[arg-type]
        role=WorkspaceRole.OWNER,
    )
    session.add(membership)
    session.commit()
    session.refresh(workspace)
    return WorkspaceRead(
        id=workspace.id,  # type: ignore[arg-type]
        name=workspace.name,
        slug=workspace.slug,
        role=membership.role,
        created_at=workspace.created_at,
        deletion_requested_at=workspace.deletion_requested_at,
        purge_after=workspace.purge_after,
        deletion_requested_by=workspace.deletion_requested_by,
        deletion_export_sha256=workspace.deletion_export_sha256,
    )


def _require_owner_browser(
    session: SessionDep,
    user: CurrentUser,
    workspace_id: int,
    *,
    include_deleted: bool = False,
    lock: bool = False,
) -> tuple[Workspace, WorkspaceMembership]:
    if get_api_key_authorization() is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Workspace export and deletion controls require an "
                "interactive owner session."
            ),
        )
    workspace, membership = require_workspace(
        session,
        user,
        workspace_id,
        admin=True,
        include_deleted=include_deleted,
        lock=lock,
    )
    if WorkspaceRole(membership.role) != WorkspaceRole.OWNER:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return workspace, membership


@router.post(
    "/{workspace_id}/invitations",
    response_model=WorkspaceInvitationCreated,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace_invitation(
    workspace_id: int,
    payload: WorkspaceInvitationCreate,
    session: SessionDep,
    user: CurrentUser,
) -> WorkspaceInvitationCreated:
    workspace, _ = _require_owner_browser(
        session,
        user,
        workspace_id,
        lock=True,
    )
    role = WorkspaceRole(payload.role)
    if role not in _MANAGED_MEMBER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invitations support ADMIN, MEMBER, or VIEWER roles.",
        )

    email = _normalize_email(payload.email)
    existing_member = session.exec(
        select(WorkspaceMembership.id)
        .join(User, WorkspaceMembership.user_id == User.id)  # type: ignore[arg-type]
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            func.lower(User.email) == email,
        )
    ).first()
    if existing_member is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email already belongs to a workspace member.",
        )

    now = utcnow()
    active_invitation = session.exec(
        select(WorkspaceInvitation.id).where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.email == email,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
            WorkspaceInvitation.expires_at > now,
        )
    ).first()
    if active_invitation is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active invitation already exists for that email.",
        )

    token = secrets.token_urlsafe(32)
    invitation = WorkspaceInvitation(
        workspace_id=workspace_id,
        email=email,
        role=role,
        token_hash=_hash_invitation_token(token),
        created_by_user_id=user.id,  # type: ignore[arg-type]
        created_at=now,
        expires_at=now + timedelta(days=payload.expires_in_days),
    )
    session.add(invitation)
    session.flush()
    session.add(
        WorkspaceMembershipEvent(
            workspace_id=workspace_id,
            action=WorkspaceMembershipAction.INVITATION_CREATED,
            actor_user_id=user.id,
            invitation_id=invitation.id,
            details={
                "role": role.value,
                "expires_at": f"{invitation.expires_at.isoformat()}Z",
            },
        )
    )
    session.commit()
    session.refresh(invitation)
    accept_url = f"{get_settings().public_origin()}/#invite={token}"
    return WorkspaceInvitationCreated(
        **_invitation_read(invitation).model_dump(),
        token=token,
        accept_url=accept_url,
    )


@router.get(
    "/{workspace_id}/invitations",
    response_model=list[WorkspaceInvitationRead],
)
def list_workspace_invitations(
    workspace_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> list[WorkspaceInvitationRead]:
    _require_owner_browser(session, user, workspace_id)
    invitations = list(
        session.exec(
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.workspace_id == workspace_id)
            .order_by(WorkspaceInvitation.id.desc())
        ).all()
    )
    return [_invitation_read(invitation) for invitation in invitations]


@router.delete(
    "/{workspace_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_workspace_invitation(
    workspace_id: int,
    invitation_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> Response:
    _require_owner_browser(
        session,
        user,
        workspace_id,
        lock=True,
    )
    invitation = session.exec(
        select(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.id == invitation_id,
            WorkspaceInvitation.workspace_id == workspace_id,
        )
        .with_for_update()
    ).first()
    if invitation is None:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    if (
        invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= utcnow()
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invitation is no longer active.",
        )
    invitation.revoked_at = utcnow()
    session.add(invitation)
    session.add(
        WorkspaceMembershipEvent(
            workspace_id=workspace_id,
            action=WorkspaceMembershipAction.INVITATION_REVOKED,
            actor_user_id=user.id,
            invitation_id=invitation.id,
            details={"role": WorkspaceRole(invitation.role).value},
        )
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/invitations/accept",
    response_model=WorkspaceRead,
)
def accept_workspace_invitation(
    payload: WorkspaceInvitationAccept,
    session: SessionDep,
    user: CurrentUser,
) -> WorkspaceRead:
    _require_interactive_browser()
    token_hash = _hash_invitation_token(payload.token)
    invitation = session.exec(
        select(WorkspaceInvitation).where(WorkspaceInvitation.token_hash == token_hash)
    ).first()
    if invitation is not None:
        session.exec(
            select(Workspace)
            .where(Workspace.id == invitation.workspace_id)
            .with_for_update()
        ).first()
        invitation = session.exec(
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.token_hash == token_hash)
            .with_for_update()
        ).first()
    now = utcnow()
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= now
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation is not available for this account.",
        )
    if invitation.email != _normalize_email(user.email):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation is not available for this account.",
        )

    workspace = session.exec(
        select(Workspace)
        .where(
            Workspace.id == invitation.workspace_id,
            Workspace.deletion_requested_at.is_(None),
        )
        .with_for_update()
    ).first()
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    existing = session.exec(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == invitation.workspace_id,
            WorkspaceMembership.user_id == user.id,
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already a member of this workspace.",
        )

    membership = WorkspaceMembership(
        workspace_id=invitation.workspace_id,
        user_id=user.id,  # type: ignore[arg-type]
        role=invitation.role,
    )
    invitation.accepted_at = now
    invitation.accepted_by_user_id = user.id
    session.add(membership)
    session.add(invitation)
    session.add(
        WorkspaceMembershipEvent(
            workspace_id=invitation.workspace_id,
            action=WorkspaceMembershipAction.INVITATION_ACCEPTED,
            actor_user_id=user.id,
            subject_user_id=user.id,
            invitation_id=invitation.id,
            details={"role": WorkspaceRole(invitation.role).value},
        )
    )
    session.commit()
    session.refresh(workspace)
    return WorkspaceRead(
        id=workspace.id,  # type: ignore[arg-type]
        name=workspace.name,
        slug=workspace.slug,
        role=membership.role,
        created_at=workspace.created_at,
        deletion_requested_at=workspace.deletion_requested_at,
        purge_after=workspace.purge_after,
        deletion_requested_by=workspace.deletion_requested_by,
        deletion_export_sha256=workspace.deletion_export_sha256,
    )


@router.patch(
    "/{workspace_id}/members/{member_user_id}",
    response_model=WorkspaceMembershipMutationRead,
)
async def update_workspace_member_role(
    workspace_id: int,
    member_user_id: int,
    payload: WorkspaceMemberRoleUpdate,
    session: SessionDep,
    user: CurrentUser,
) -> WorkspaceMembershipMutationRead:
    _require_owner_browser(
        session,
        user,
        workspace_id,
        lock=True,
    )
    new_role = WorkspaceRole(payload.role)
    if new_role not in _MANAGED_MEMBER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Use ownership transfer to assign the OWNER role.",
        )
    membership = session.exec(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == member_user_id,
        )
        .with_for_update()
    ).first()
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace member not found.")
    old_role = WorkspaceRole(membership.role)
    if old_role == WorkspaceRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Use ownership transfer to change the owner.",
        )
    revoked_api_keys = 0
    if new_role == WorkspaceRole.VIEWER and old_role != WorkspaceRole.VIEWER:
        keys = list(
            session.exec(
                select(ApiKey).where(
                    ApiKey.workspace_id == workspace_id,
                    ApiKey.user_id == member_user_id,
                    ApiKey.revoked.is_(False),
                )
            ).all()
        )
        for api_key in keys:
            if "write" in api_key.scopes:
                api_key.revoked = True
                revoked_api_keys += 1
                session.add(api_key)
    membership.role = new_role
    session.add(membership)
    session.add(
        WorkspaceMembershipEvent(
            workspace_id=workspace_id,
            action=WorkspaceMembershipAction.ROLE_CHANGED,
            actor_user_id=user.id,
            subject_user_id=member_user_id,
            details={
                "from_role": old_role.value,
                "to_role": new_role.value,
                "revoked_write_api_keys": revoked_api_keys,
            },
        )
    )
    session.commit()
    await get_broadcaster().publish(
        Event(
            action=SSEAction.SYNC_REQUIRED,
            entity="workspace",
            entity_id=workspace_id,
            workspace_id=workspace_id,
            recipient_user_id=member_user_id,
        )
    )
    return WorkspaceMembershipMutationRead(
        workspace_id=workspace_id,
        user_id=member_user_id,
        role=new_role,
        revoked_api_keys=revoked_api_keys,
    )


@router.delete(
    "/{workspace_id}/members/{member_user_id}",
    response_model=WorkspaceMembershipMutationRead,
)
async def remove_workspace_member(
    workspace_id: int,
    member_user_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> WorkspaceMembershipMutationRead:
    _require_owner_browser(
        session,
        user,
        workspace_id,
        lock=True,
    )
    membership = session.exec(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == member_user_id,
        )
        .with_for_update()
    ).first()
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace member not found.")
    old_role = WorkspaceRole(membership.role)
    if old_role == WorkspaceRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transfer ownership before removing the owner.",
        )

    now = utcnow()
    browser_sessions = list(
        session.exec(
            select(BrowserSession).where(
                BrowserSession.user_id == member_user_id,
                BrowserSession.revoked_at.is_(None),
            )
        ).all()
    )
    api_keys = list(
        session.exec(
            select(ApiKey).where(
                ApiKey.workspace_id == workspace_id,
                ApiKey.user_id == member_user_id,
                ApiKey.revoked.is_(False),
            )
        ).all()
    )
    for browser_session in browser_sessions:
        browser_session.revoked_at = now
        session.add(browser_session)
    for api_key in api_keys:
        api_key.revoked = True
        session.add(api_key)
    session.delete(membership)
    session.add(
        WorkspaceMembershipEvent(
            workspace_id=workspace_id,
            action=WorkspaceMembershipAction.MEMBER_REMOVED,
            actor_user_id=user.id,
            subject_user_id=member_user_id,
            details={
                "prior_role": old_role.value,
                "revoked_browser_sessions": len(browser_sessions),
                "revoked_api_keys": len(api_keys),
            },
        )
    )
    session.commit()
    await get_broadcaster().publish(
        Event(
            action=SSEAction.SYNC_REQUIRED,
            entity="workspace",
            entity_id=workspace_id,
            workspace_id=workspace_id,
            recipient_user_id=member_user_id,
        )
    )
    return WorkspaceMembershipMutationRead(
        workspace_id=workspace_id,
        user_id=member_user_id,
        revoked_browser_sessions=len(browser_sessions),
        revoked_api_keys=len(api_keys),
    )


@router.post(
    "/{workspace_id}/ownership-transfer",
    response_model=WorkspaceMembershipMutationRead,
)
def transfer_workspace_ownership(
    workspace_id: int,
    payload: WorkspaceOwnershipTransfer,
    session: SessionDep,
    user: CurrentUser,
) -> WorkspaceMembershipMutationRead:
    workspace, owner_membership = _require_owner_browser(
        session,
        user,
        workspace_id,
        lock=True,
    )
    if payload.confirmation != workspace.slug:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Type the exact workspace slug to transfer ownership.",
        )
    if payload.user_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select another workspace member.",
        )
    target = session.exec(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == payload.user_id,
        )
        .with_for_update()
    ).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Workspace member not found.")
    prior_target_role = WorkspaceRole(target.role)
    if prior_target_role == WorkspaceRole.SERVICE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A service membership cannot own a workspace.",
        )

    owner_membership.role = WorkspaceRole.ADMIN
    session.add(owner_membership)
    session.flush()
    target.role = WorkspaceRole.OWNER
    session.add(target)
    session.add(
        WorkspaceMembershipEvent(
            workspace_id=workspace_id,
            action=WorkspaceMembershipAction.OWNERSHIP_TRANSFERRED,
            actor_user_id=user.id,
            subject_user_id=payload.user_id,
            details={
                "previous_owner_user_id": user.id,
                "previous_target_role": prior_target_role.value,
                "previous_owner_new_role": WorkspaceRole.ADMIN.value,
            },
        )
    )
    session.commit()
    return WorkspaceMembershipMutationRead(
        workspace_id=workspace_id,
        user_id=payload.user_id,
        role=WorkspaceRole.OWNER,
    )


@router.get(
    "/{workspace_id}/membership-events",
    response_model=list[WorkspaceMembershipEventRead],
)
def list_workspace_membership_events(
    workspace_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> list[WorkspaceMembershipEvent]:
    _require_owner_browser(
        session,
        user,
        workspace_id,
        include_deleted=True,
    )
    return list(
        session.exec(
            select(WorkspaceMembershipEvent)
            .where(WorkspaceMembershipEvent.workspace_id == workspace_id)
            .order_by(WorkspaceMembershipEvent.id)
        ).all()
    )


@router.get("/{workspace_id}/export")
def export_workspace(
    workspace_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> Response:
    workspace, _ = _require_owner_browser(
        session,
        user,
        workspace_id,
        lock=True,
    )
    content, sha256, counts = build_workspace_export(session, workspace)
    event = WorkspaceLifecycleEvent(
        workspace_id=workspace_id,
        action=WorkspaceLifecycleAction.EXPORTED,
        actor_user_id=user.id,
        details={
            "sha256": sha256,
            "record_counts": counts,
            "format": "mouvadah.workspace-export.v1",
        },
    )
    session.add(event)
    session.commit()
    date_stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                f'attachment; filename="mouvadah-{workspace.slug}-'
                f'{date_stamp}.json"'
            ),
            "X-Mouvadah-Export-SHA256": sha256,
        },
    )


@router.post(
    "/{workspace_id}/deletion",
    response_model=WorkspaceDeletionRead,
)
async def schedule_workspace_deletion(
    workspace_id: int,
    payload: WorkspaceDeletionCreate,
    session: SessionDep,
    user: CurrentUser,
) -> WorkspaceDeletionRead:
    workspace, _ = _require_owner_browser(
        session,
        user,
        workspace_id,
        lock=True,
    )
    if payload.confirmation != workspace.slug:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Type the exact workspace slug to confirm deletion.",
        )

    recent_exports = list(
        session.exec(
            select(WorkspaceLifecycleEvent)
            .where(
                WorkspaceLifecycleEvent.workspace_id == workspace_id,
                WorkspaceLifecycleEvent.action == WorkspaceLifecycleAction.EXPORTED,
            )
            .order_by(WorkspaceLifecycleEvent.id.desc())
            .limit(20)
        ).all()
    )
    now = utcnow()
    matching_export = next(
        (
            event
            for event in recent_exports
            if event.details.get("sha256") == payload.export_sha256
            and event.occurred_at >= now - timedelta(hours=24)
        ),
        None,
    )
    if matching_export is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Create a fresh workspace export before scheduling deletion."),
        )

    purge_after = now + timedelta(days=get_settings().deletion_recovery_days)
    workspace.deletion_requested_at = now
    workspace.purge_after = purge_after
    workspace.deletion_requested_by = user.id
    workspace.deletion_export_sha256 = payload.export_sha256
    from api.utils.github_cleanup import disconnect_workspace
    disconnect_workspace(session, workspace_id)
    session.add(workspace)

    api_keys = list(
        session.exec(
            select(ApiKey).where(
                ApiKey.workspace_id == workspace_id,
                ApiKey.revoked.is_(False),
            )
        ).all()
    )
    for api_key in api_keys:
        api_key.revoked = True
        session.add(api_key)
    pending_invitations = list(
        session.exec(
            select(WorkspaceInvitation).where(
                WorkspaceInvitation.workspace_id == workspace_id,
                WorkspaceInvitation.accepted_at.is_(None),
                WorkspaceInvitation.revoked_at.is_(None),
            )
        ).all()
    )
    project_ids = select(Project.id).where(Project.workspace_id == workspace_id)
    for project_invitation in session.exec(
        select(ProjectInvitation).where(
            ProjectInvitation.project_id.in_(project_ids),
            ProjectInvitation.accepted_at.is_(None),
            ProjectInvitation.revoked_at.is_(None),
        )
    ).all():
        project_invitation.revoked_at = now
        session.add(project_invitation)
    for invitation in pending_invitations:
        invitation.revoked_at = now
        session.add(invitation)
        session.add(
            WorkspaceMembershipEvent(
                workspace_id=workspace_id,
                action=WorkspaceMembershipAction.INVITATION_REVOKED,
                actor_user_id=user.id,
                invitation_id=invitation.id,
                details={
                    "role": WorkspaceRole(invitation.role).value,
                    "reason": "workspace_deletion_scheduled",
                },
            )
        )

    session.add(
        WorkspaceLifecycleEvent(
            workspace_id=workspace_id,
            action=WorkspaceLifecycleAction.DELETION_SCHEDULED,
            actor_user_id=user.id,
            details={
                "export_sha256": payload.export_sha256,
                "purge_after": f"{purge_after.isoformat()}Z",
                "revoked_api_keys": len(api_keys),
                "revoked_invitations": len(pending_invitations),
            },
        )
    )
    recipients = set(
        session.exec(
            select(WorkspaceMembership.user_id).where(
                WorkspaceMembership.workspace_id == workspace_id
            )
        ).all()
    )
    recipients.update(
        session.exec(
            select(ProjectMembership.user_id)
            .join(Project)
            .where(Project.workspace_id == workspace_id)
        ).all()
    )
    session.commit()
    for recipient in recipients:
        await get_broadcaster().publish(
            Event(
                action=SSEAction.SYNC_REQUIRED,
                entity="workspace",
                entity_id=workspace_id,
                workspace_id=workspace_id,
                recipient_user_id=recipient,
            )
        )
    return WorkspaceDeletionRead(
        workspace_id=workspace_id,
        deletion_requested_at=now,
        purge_after=purge_after,
        deletion_export_sha256=payload.export_sha256,
        revoked_api_keys=len(api_keys),
        revoked_invitations=len(pending_invitations),
    )


@router.post(
    "/{workspace_id}/restore",
    response_model=WorkspaceRead,
)
def restore_workspace(
    workspace_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> WorkspaceRead:
    workspace, membership = _require_owner_browser(
        session,
        user,
        workspace_id,
        include_deleted=True,
        lock=True,
    )
    if workspace.deletion_requested_at is None or workspace.purge_after is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workspace is not scheduled for deletion.",
        )
    if workspace.purge_after <= utcnow():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The workspace recovery window has expired.",
        )
    prior_purge_after = workspace.purge_after
    workspace.deletion_requested_at = None
    workspace.purge_after = None
    workspace.deletion_requested_by = None
    workspace.deletion_export_sha256 = None
    session.add(workspace)
    session.add(
        WorkspaceLifecycleEvent(
            workspace_id=workspace_id,
            action=WorkspaceLifecycleAction.DELETION_RESTORED,
            actor_user_id=user.id,
            details={
                "prior_purge_after": f"{prior_purge_after.isoformat()}Z",
                "api_keys_remain_revoked": True,
            },
        )
    )
    session.commit()
    session.refresh(workspace)
    return WorkspaceRead(
        id=workspace_id,
        name=workspace.name,
        slug=workspace.slug,
        role=membership.role,
        created_at=workspace.created_at,
        deletion_requested_at=None,
        purge_after=None,
        deletion_requested_by=None,
        deletion_export_sha256=None,
    )


@router.get(
    "/{workspace_id}/lifecycle-events",
    response_model=list[WorkspaceLifecycleEventRead],
)
def list_workspace_lifecycle_events(
    workspace_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> list[WorkspaceLifecycleEvent]:
    _require_owner_browser(
        session,
        user,
        workspace_id,
        include_deleted=True,
    )
    return list(
        session.exec(
            select(WorkspaceLifecycleEvent)
            .where(WorkspaceLifecycleEvent.workspace_id == workspace_id)
            .order_by(WorkspaceLifecycleEvent.id)
        ).all()
    )


@router.get("/{workspace_id}/members", response_model=list[WorkspaceMemberRead])
def list_workspace_members(
    workspace_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> list[WorkspaceMemberRead]:
    _require_owner_browser(session, user, workspace_id)
    rows = session.exec(
        select(WorkspaceMembership, User)
        .join(User, WorkspaceMembership.user_id == User.id)  # type: ignore[arg-type]
        .where(WorkspaceMembership.workspace_id == workspace_id)
        .order_by(WorkspaceMembership.id)
    ).all()
    return [
        WorkspaceMemberRead(
            user_id=member.id,  # type: ignore[arg-type]
            email=member.email,
            name=member.name,
            role=membership.role,
            created_at=membership.created_at,
        )
        for membership, member in rows
    ]
