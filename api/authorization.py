"""Workspace-scoped authorization helpers.

Every route that accepts an object ID must resolve it through this module (or
first resolve an authorized parent). Authentication identifies a caller;
these helpers decide whether that caller can access the requested object.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import HTTPException
from sqlmodel import Session, select

from api.config import get_settings
from api.models.entities import (
    AgentSession,
    Comment,
    KnowledgeNode,
    KnowledgeProposal,
    Project,
    ProjectMembership,
    Subproject,
    Ticket,
    User,
    Workspace,
    WorkspaceMembership,
)
from api.models.enums import WorkspaceRole
from api.security import get_api_key_authorization

_WRITE_ROLES = {
    WorkspaceRole.OWNER,
    WorkspaceRole.ADMIN,
    WorkspaceRole.MEMBER,
    WorkspaceRole.SERVICE,
}
_ADMIN_ROLES = {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}


def _not_found(label: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{label} not found.")


def _coerce_role(role: WorkspaceRole | str) -> WorkspaceRole:
    return role if isinstance(role, WorkspaceRole) else WorkspaceRole(role)


def _check_role(
    membership: WorkspaceMembership | None,
    *,
    write: bool = False,
    admin: bool = False,
    label: str,
) -> WorkspaceMembership:
    if membership is None:
        raise _not_found(label)
    role = _coerce_role(membership.role)
    if admin and role not in _ADMIN_ROLES:
        raise _not_found(label)
    if write and role not in _WRITE_ROLES:
        raise _not_found(label)
    return membership


def get_membership(
    session: Session,
    user: User,
    workspace_id: int,
) -> WorkspaceMembership | None:
    return session.exec(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user.id,
        )
        .execution_options(populate_existing=True)
    ).first()


def require_workspace(
    session: Session,
    user: User,
    workspace_id: int,
    *,
    write: bool = False,
    admin: bool = False,
    include_deleted: bool = False,
    lock: bool = False,
) -> tuple[Workspace, WorkspaceMembership]:
    api_key = get_api_key_authorization()
    if api_key is not None and api_key.workspace_id != workspace_id:
        raise _not_found("Workspace")
    # Reject callers outside the tenant before taking its serialization lock.
    # The role is checked again after the lock because it may change while a
    # request is queued behind an ownership transfer, downgrade, or removal.
    preflight_membership = get_membership(session, user, workspace_id)
    _check_role(
        preflight_membership,
        write=write,
        admin=admin,
        label="Workspace",
    )
    workspace_query = select(Workspace).where(Workspace.id == workspace_id)
    if not include_deleted:
        workspace_query = workspace_query.where(
            Workspace.deletion_requested_at.is_(None)
        )
    if lock or write:
        workspace_query = workspace_query.with_for_update()
    workspace_query = workspace_query.execution_options(populate_existing=True)
    workspace = session.exec(workspace_query).first()
    if workspace is None:
        raise _not_found("Workspace")
    # Membership must be read after the tenant lock. Otherwise a request can
    # snapshot OWNER/MEMBER, wait behind a transfer/removal transaction, and
    # then mutate with privileges that no longer exist.
    membership = get_membership(session, user, workspace_id)
    _check_role(membership, write=write, admin=admin, label="Workspace")
    return workspace, membership  # type: ignore[return-value]


def _personal_slug(user: User) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", user.email.lower()).strip("-")
    return f"{base[:55]}-{user.id}"


def ensure_personal_workspace(session: Session, user: User) -> Workspace:
    """Return the user's first owned workspace, creating one if necessary."""
    workspace = session.exec(
        select(Workspace)
        .join(
            WorkspaceMembership,
            WorkspaceMembership.workspace_id == Workspace.id,  # type: ignore[arg-type]
        )
        .where(
            WorkspaceMembership.user_id == user.id,
            WorkspaceMembership.role == WorkspaceRole.OWNER,
            Workspace.deletion_requested_at.is_(None),
        )
        .order_by(Workspace.id)
    ).first()
    if workspace is None:
        workspace = Workspace(
            name=f"{user.name}'s Workspace",
            slug=_personal_slug(user),
        )
        session.add(workspace)
        session.flush()
        session.add(
            WorkspaceMembership(
                workspace_id=workspace.id,  # type: ignore[arg-type]
                user_id=user.id,  # type: ignore[arg-type]
                role=WorkspaceRole.OWNER,
            )
        )
        session.flush()

    # Safe legacy adoption: explicit production owner, or exactly one local
    # user. Unowned projects otherwise remain inaccessible instead of leaking.
    settings = get_settings()
    users = list(session.exec(select(User.id)).all())
    may_adopt = settings.legacy_owner_email == user.email or (
        not settings.is_production() and len(users) == 1 and users[0] == user.id
    )
    if may_adopt:
        legacy = list(
            session.exec(select(Project).where(Project.workspace_id.is_(None))).all()
        )
        for project in legacy:
            project.workspace_id = workspace.id
            session.add(project)
    session.commit()
    session.refresh(workspace)
    return workspace


@dataclass(frozen=True)
class ProjectAccess:
    can_edit: bool
    can_manage_access: bool
    can_delete_project: bool
    can_leave: bool
    access_source: str
    effective_role: str


def project_access(
    session: Session, user: User, project: Project
) -> ProjectAccess | None:
    inherited = get_membership(session, user, project.workspace_id)
    direct = session.exec(
        select(ProjectMembership)
        .where(
            ProjectMembership.project_id == project.id,
            ProjectMembership.user_id == user.id,
        )
        .execution_options(populate_existing=True)
    ).first()
    if inherited is None and direct is None:
        return None
    role = _coerce_role(inherited.role) if inherited else None
    admin = role in _ADMIN_ROLES
    edit = role in _WRITE_ROLES or (direct is not None and direct.role == "EDITOR")
    return ProjectAccess(
        can_edit=edit,
        can_manage_access=admin,
        can_delete_project=admin,
        can_leave=direct is not None,
        access_source="workspace" if inherited else "project",
        effective_role="ADMIN" if admin else "EDITOR" if edit else "VIEWER",
    )


def accessible_projects_query(user: User):
    inherited = (
        select(WorkspaceMembership.id)
        .where(
            WorkspaceMembership.workspace_id == Project.workspace_id,
            WorkspaceMembership.user_id == user.id,
        )
        .exists()
    )
    direct = (
        select(ProjectMembership.id)
        .where(
            ProjectMembership.project_id == Project.id,
            ProjectMembership.user_id == user.id,
        )
        .exists()
    )
    query = (
        select(Project)
        .join(Workspace)
        .where(
            Workspace.deletion_requested_at.is_(None),
            inherited | direct,
        )
        .order_by(Project.created_at)
    )
    key = get_api_key_authorization()
    if key is not None:
        query = query.where(Project.workspace_id == key.workspace_id)
        if key.resource_mode == "PROJECTS" or key.project_ids:
            query = query.where(Project.id.in_(key.project_ids))
    return query


def project_read(session: Session, user: User, project: Project):
    from api.schemas import ProjectRead

    access = project_access(session, user, project)
    if access is None:
        raise _not_found("Project")
    workspace = session.get(Workspace, project.workspace_id)
    return ProjectRead(
        **project.model_dump(),
        **access.__dict__,
        workspace_name=workspace.name,
    )


def require_project(
    session: Session,
    user: User,
    project_id: int,
    *,
    write: bool = False,
    admin: bool = False,
    lock: bool = False,
) -> Project:
    project = session.exec(
        select(Project)
        .join(Workspace)
        .where(
            Project.id == project_id,
            Workspace.deletion_requested_at.is_(None),
        )
        .execution_options(populate_existing=True)
    ).first()
    if project is None or project.workspace_id is None:
        raise _not_found("Project")

    def check():
        access = project_access(session, user, project)
        if (
            access is None
            or (write and not access.can_edit)
            or (admin and not access.can_manage_access)
        ):
            raise _not_found("Project")
        key = get_api_key_authorization()
        if key is not None and (
            key.workspace_id != project.workspace_id
            or (
                (key.resource_mode == "PROJECTS" or key.project_ids)
                and project.id not in key.project_ids
            )
        ):
            raise _not_found("Project")

    check()
    if write or admin or lock:
        workspace = session.exec(
            select(Workspace)
            .where(
                Workspace.id == project.workspace_id,
                Workspace.deletion_requested_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if workspace is None:
            raise _not_found("Project")
        project = session.exec(
            select(Project)
            .where(Project.id == project_id)
            .execution_options(populate_existing=True)
        ).first()
        if project is None:
            raise _not_found("Project")
        check()
    return project


def require_subproject(
    session: Session,
    user: User,
    subproject_id: int,
    *,
    write: bool = False,
    admin: bool = False,
) -> Subproject:
    subproject = session.get(Subproject, subproject_id)
    if subproject is None:
        raise _not_found("Subproject")
    require_project(
        session,
        user,
        subproject.project_id,
        write=write,
        admin=admin,
    )
    return subproject


def require_ticket(
    session: Session,
    user: User,
    ticket_id: int,
    *,
    write: bool = False,
    admin: bool = False,
) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise _not_found("Ticket")
    require_subproject(
        session,
        user,
        ticket.subproject_id,
        write=write,
        admin=admin,
    )
    return ticket


def require_comment(
    session: Session,
    user: User,
    comment_id: int,
    *,
    write: bool = False,
) -> Comment:
    comment = session.get(Comment, comment_id)
    if comment is None:
        raise _not_found("Comment")
    require_ticket(session, user, comment.ticket_id, write=write)
    return comment


def require_knowledge_node(
    session: Session,
    user: User,
    node_id: int,
    *,
    write: bool = False,
    admin: bool = False,
) -> KnowledgeNode:
    node = session.get(KnowledgeNode, node_id)
    if node is None:
        raise _not_found("Knowledge node")
    require_project(
        session,
        user,
        node.project_id,
        write=write,
        admin=admin,
    )
    return node


def require_proposal(
    session: Session,
    user: User,
    proposal_id: int,
    *,
    write: bool = False,
) -> KnowledgeProposal:
    proposal = session.get(KnowledgeProposal, proposal_id)
    if proposal is None:
        raise _not_found("Proposal")
    require_knowledge_node(session, user, proposal.node_id, write=write)
    return proposal


def require_agent_session(
    session: Session,
    user: User,
    agent_session_id: int,
    *,
    write: bool = False,
) -> AgentSession:
    agent_session = session.get(AgentSession, agent_session_id)
    if agent_session is None:
        raise _not_found("Session")
    require_project(
        session,
        user,
        agent_session.project_id,
        write=write,
    )
    return agent_session


def workspace_id_for_project(session: Session, project_id: int) -> int:
    """Resolve the workspace for an already-authorized project."""
    workspace_id = session.exec(
        select(Project.workspace_id).where(Project.id == project_id)
    ).first()
    if workspace_id is None:
        raise RuntimeError(f"Project {project_id} has no workspace ownership.")
    return workspace_id


def workspace_id_for_subproject(session: Session, subproject_id: int) -> int:
    """Resolve the workspace for an already-authorized subproject."""
    workspace_id = session.exec(
        select(Project.workspace_id)
        .join(
            Subproject,
            Subproject.project_id == Project.id,  # type: ignore[arg-type]
        )
        .where(Subproject.id == subproject_id)
    ).first()
    if workspace_id is None:
        raise RuntimeError(f"Subproject {subproject_id} has no workspace ownership.")
    return workspace_id


def workspace_id_for_ticket(session: Session, ticket_id: int) -> int:
    """Resolve the workspace for an already-authorized ticket."""
    workspace_id = session.exec(
        select(Project.workspace_id)
        .join(
            Subproject,
            Subproject.project_id == Project.id,  # type: ignore[arg-type]
        )
        .join(Ticket, Ticket.subproject_id == Subproject.id)  # type: ignore[arg-type]
        .where(Ticket.id == ticket_id)
    ).first()
    if workspace_id is None:
        raise RuntimeError(f"Ticket {ticket_id} has no workspace ownership.")
    return workspace_id
