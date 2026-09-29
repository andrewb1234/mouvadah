"""Project-level endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import case, func, delete
from sqlmodel import select

from api.auth import CurrentUser
from api.authorization import (
    ensure_personal_workspace,
    accessible_projects_query,
    project_read,
    require_project,
    require_workspace,
)
from api.dependencies import SessionDep
from api.events import Event, get_broadcaster
from api.models.entities import (
    AgentSession,
    ApiKey,
    ApiKeyProject,
    KnowledgeNode,
    KnowledgeProposal,
    Project,
    ProjectMembership,
    ProjectInvitation,
    Subproject,
    Ticket,
    Workspace,
    WorkspaceMembership,
)
from api.models.enums import (
    KnowledgeNodeStatus,
    SSEAction,
    TicketAssignee,
    TicketStatus,
)
from api.security import get_api_key_authorization
from api.schemas import (
    AgentSessionRead,
    ControlRoomSubprojectCounts,
    ControlRoomSubprojectRead,
    ControlRoomSummary,
    ProjectCreate,
    ProjectRead,
    SubprojectCreate,
    SubprojectRead,
    TicketRef,
)
from api.utils.ticket_deps import delete_ticket_dependencies, resolve_ticket_refs

router = APIRouter(prefix="/projects", tags=["projects"])


# The dashboard is a triage surface, not a substitute for Kanban. Keep its
# focal lists bounded as projects accumulate historical tickets and sessions.
_CONTROL_ROOM_FOCAL_TICKET_LIMIT = 20
_CONTROL_ROOM_HANDOFF_LIMIT = 4
_CONTROL_ROOM_CONTEXT_PREVIEW_CHARS = 280


@router.get("", response_model=list[ProjectRead])
def list_projects(
    session: SessionDep,
    user: CurrentUser,
) -> list[Project]:
    return [
        project_read(session, user, project)
        for project in session.exec(accessible_projects_query(user)).all()
    ]


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    payload: ProjectCreate,
    session: SessionDep,
    user: CurrentUser,
) -> Project:
    api_key = get_api_key_authorization()
    if api_key is not None:
        if api_key.project_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Project-restricted API keys cannot create projects.",
            )
        if payload.workspace_id not in {None, api_key.workspace_id}:
            require_workspace(
                session,
                user,
                payload.workspace_id,
                write=True,
            )
        workspace, _ = require_workspace(
            session,
            user,
            api_key.workspace_id,
            write=True,
        )
    elif payload.workspace_id is None:
        workspace = ensure_personal_workspace(session, user)
        workspace, _ = require_workspace(
            session,
            user,
            workspace.id,  # type: ignore[arg-type]
            write=True,
        )
    else:
        workspace, _ = require_workspace(
            session,
            user,
            payload.workspace_id,
            write=True,
        )
    project = Project(
        workspace_id=workspace.id,
        name=payload.name,
        description=payload.description,
    )
    session.add(project)
    session.commit()
    session.refresh(project)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.PROJECT_CREATED,
            entity="project",
            entity_id=project.id,  # type: ignore[arg-type]
            project_id=project.id,
            workspace_id=workspace.id,
        )
    )
    return project_read(session, user, project)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> Project:
    return project_read(session, user, require_project(session, user, project_id))


def _control_room_ticket_refs(
    session,
    project_id: int,
    *,
    statuses: tuple[TicketStatus, ...],
    limit: int,
    blocked_first: bool = False,
) -> list[TicketRef]:
    """Read a bounded, compact ticket list for the Control Room."""
    ordering = [Ticket.id]
    if blocked_first:
        ordering.insert(
            0,
            case((Ticket.status == TicketStatus.BLOCKED, 0), else_=1),
        )
    rows = session.exec(
        select(Ticket, Subproject.name)
        .join(Subproject, Ticket.subproject_id == Subproject.id)  # type: ignore[arg-type]
        .where(
            Subproject.project_id == project_id,
            Ticket.status.in_(statuses),
        )
        .order_by(*ordering)
        .limit(limit)
    ).all()
    return [
        TicketRef(
            id=ticket.id,  # type: ignore[arg-type]
            title=ticket.title,
            status=ticket.status,
            assignee=ticket.assignee,
            subproject_id=ticket.subproject_id,
            subproject_name=subproject_name,
        )
        for ticket, subproject_name in rows
    ]


@router.get(
    "/{project_id}/control-room",
    response_model=ControlRoomSummary,
)
def get_control_room_summary(
    project_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> ControlRoomSummary:
    """Return the bounded read model needed by the project Control Room.

    The previous page composition loaded full knowledge content, proposal
    payloads, and session history to compute a few dashboard values. This
    endpoint keeps the authorized query scope in one server-side seam and
    returns only the compact operational data the dashboard renders.

    Like the established SSE invalidation flow, this is an eventually
    consistent read. A concurrent mutation can be reflected by the next
    project-scoped invalidation and summary refresh.
    """
    project = require_project(session, user, project_id)

    subproject_rows = list(
        session.exec(
            select(
                Subproject.id,
                Subproject.name,
                func.substr(
                    Subproject.context_brief,
                    1,
                    _CONTROL_ROOM_CONTEXT_PREVIEW_CHARS,
                ),
                Subproject.status,
            )
            .where(Subproject.project_id == project_id)
            .order_by(Subproject.id)
        ).all()
    )

    status_counts = {status: 0 for status in TicketStatus}
    for ticket_status, count in session.exec(
        select(Ticket.status, func.count(Ticket.id))
        .join(Subproject, Ticket.subproject_id == Subproject.id)  # type: ignore[arg-type]
        .where(Subproject.project_id == project_id)
        .group_by(Ticket.status)
    ).all():
        status_counts[ticket_status] = int(count)

    assignee_counts = {assignee: 0 for assignee in TicketAssignee}
    for assignee, count in session.exec(
        select(Ticket.assignee, func.count(Ticket.id))
        .join(Subproject, Ticket.subproject_id == Subproject.id)  # type: ignore[arg-type]
        .where(Subproject.project_id == project_id)
        .group_by(Ticket.assignee)
    ).all():
        assignee_counts[assignee] = int(count)

    moving_case = case(
        (Ticket.status == TicketStatus.IN_PROGRESS, 1),
        else_=0,
    )
    attention_case = case(
        (
            Ticket.status.in_((TicketStatus.BLOCKED, TicketStatus.REVIEW)),
            1,
        ),
        else_=0,
    )
    subproject_counts = [
        ControlRoomSubprojectCounts(
            subproject_id=subproject_id,
            total=int(total),
            moving=int(moving),
            attention=int(attention),
        )
        for subproject_id, total, moving, attention in session.exec(
            select(
                Ticket.subproject_id,
                func.count(Ticket.id),
                func.coalesce(func.sum(moving_case), 0),
                func.coalesce(func.sum(attention_case), 0),
            )
            .join(Subproject, Ticket.subproject_id == Subproject.id)  # type: ignore[arg-type]
            .where(Subproject.project_id == project_id)
            .group_by(Ticket.subproject_id)
        ).all()
    ]

    attention_filter = (TicketStatus.BLOCKED, TicketStatus.REVIEW)
    attention_total = int(
        session.exec(
            select(func.count(Ticket.id))
            .join(Subproject, Ticket.subproject_id == Subproject.id)  # type: ignore[arg-type]
            .where(
                Subproject.project_id == project_id,
                Ticket.status.in_(attention_filter),
            )
        ).one()
    )
    in_flight_total = int(
        session.exec(
            select(func.count(Ticket.id))
            .join(Subproject, Ticket.subproject_id == Subproject.id)  # type: ignore[arg-type]
            .where(
                Subproject.project_id == project_id,
                Ticket.status == TicketStatus.IN_PROGRESS,
            )
        ).one()
    )

    stale_knowledge_count = int(
        session.exec(
            select(func.count(KnowledgeNode.id)).where(
                KnowledgeNode.project_id == project_id,
                KnowledgeNode.status == KnowledgeNodeStatus.STALE,
            )
        ).one()
    )
    pending_proposal_count = int(
        session.exec(
            select(func.count(KnowledgeProposal.id))
            .join(KnowledgeNode, KnowledgeProposal.node_id == KnowledgeNode.id)  # type: ignore[arg-type]
            .where(
                KnowledgeNode.project_id == project_id,
                KnowledgeProposal.status == "PENDING",
            )
        ).one()
    )
    resumable_sessions = list(
        session.exec(
            select(AgentSession)
            .where(
                AgentSession.project_id == project_id,
                AgentSession.status != "ACTIVE",
                AgentSession.handoff_note.is_not(None),
                func.trim(AgentSession.handoff_note) != "",
            )
            .order_by(AgentSession.started_at.desc())  # type: ignore[union-attr]
            .limit(_CONTROL_ROOM_HANDOFF_LIMIT)
        ).all()
    )

    return ControlRoomSummary(
        project=project_read(session, user, project),
        subprojects=[
            ControlRoomSubprojectRead(
                id=subproject_id,
                name=name,
                context_preview=context_preview or "",
                status=subproject_status,
            )
            for (
                subproject_id,
                name,
                context_preview,
                subproject_status,
            ) in subproject_rows
        ],
        ticket_status_counts=status_counts,
        ticket_assignee_counts=assignee_counts,
        subproject_ticket_counts=subproject_counts,
        attention_tickets=_control_room_ticket_refs(
            session,
            project_id,
            statuses=attention_filter,
            limit=_CONTROL_ROOM_FOCAL_TICKET_LIMIT,
            blocked_first=True,
        ),
        attention_total=attention_total,
        in_flight_tickets=_control_room_ticket_refs(
            session,
            project_id,
            statuses=(TicketStatus.IN_PROGRESS,),
            limit=_CONTROL_ROOM_FOCAL_TICKET_LIMIT,
        ),
        in_flight_total=in_flight_total,
        stale_knowledge_count=stale_knowledge_count,
        pending_proposal_count=pending_proposal_count,
        resumable_sessions=[
            AgentSessionRead.model_validate(agent_session)
            for agent_session in resumable_sessions
        ],
    )


@router.get("/{project_id}/tickets", response_model=list[TicketRef])
def list_project_tickets(
    project_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> list[TicketRef]:
    """List compact ticket records for choosing in-project dependencies."""
    require_project(session, user, project_id)

    ticket_ids = list(
        session.exec(
            select(Ticket.id)
            .join(Subproject, Ticket.subproject_id == Subproject.id)  # type: ignore[arg-type]
            .where(Subproject.project_id == project_id)
            .order_by(Ticket.id)
        ).all()
    )
    refs = resolve_ticket_refs(session, ticket_ids)
    return [
        TicketRef(**refs[ticket_id]) for ticket_id in ticket_ids if ticket_id in refs
    ]


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_project(
    project_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> None:
    """Delete a project and cascade its subprojects, tickets, and knowledge nodes.

    The ORM relationships on ``Project`` have ``cascade="all, delete-orphan"``
    so a single ``session.delete`` sweeps the tree.
    """
    project = require_project(
        session,
        user,
        project_id,
        admin=True,
        write=True,
    )
    workspace_id = project.workspace_id
    ticket_ids = list(
        session.exec(
            select(Ticket.id)
            .join(Subproject, Ticket.subproject_id == Subproject.id)  # type: ignore[arg-type]
            .where(Subproject.project_id == project_id)
        ).all()
    )
    delete_ticket_dependencies(session, ticket_ids)

    # An empty ApiKeyProject set means unrestricted workspace access. If this
    # was the last allowed project for a restricted key, revoke the key instead
    # of accidentally broadening it when the project disappears.
    restrictions = session.exec(
        select(ApiKeyProject).where(ApiKeyProject.project_id == project_id)
    ).all()
    for restriction in restrictions:
        another_project = session.exec(
            select(ApiKeyProject.project_id).where(
                ApiKeyProject.api_key_id == restriction.api_key_id,
                ApiKeyProject.project_id != project_id,
            )
        ).first()
        if another_project is None:
            api_key = session.get(ApiKey, restriction.api_key_id)
            if api_key is not None:
                api_key.revoked = True
                session.add(api_key)
        session.delete(restriction)
    if restrictions:
        session.flush()

    guest_ids = list(
        session.exec(
            select(ProjectMembership.user_id).where(
                ProjectMembership.project_id == project_id
            )
        ).all()
    )
    session.exec(
        delete(ProjectInvitation).where(ProjectInvitation.project_id == project_id)
    )
    session.exec(
        delete(ProjectMembership).where(ProjectMembership.project_id == project_id)
    )
    session.delete(project)
    session.commit()

    for guest_id in guest_ids:
        await get_broadcaster().publish(
            Event(
                action=SSEAction.PROJECT_ACCESS_CHANGED,
                entity="project",
                entity_id=project_id,
                project_id=project_id,
                workspace_id=workspace_id,
                recipient_user_id=guest_id,
            )
        )

    await get_broadcaster().publish(
        Event(
            action=SSEAction.PROJECT_DELETED,
            entity="project",
            entity_id=project_id,
            project_id=project_id,
            workspace_id=workspace_id,
        )
    )
    return None


@router.get(
    "/{project_id}/subprojects",
    response_model=list[SubprojectRead],
)
def list_subprojects(
    project_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> list[Subproject]:
    require_project(session, user, project_id)
    return list(
        session.exec(
            select(Subproject)
            .where(Subproject.project_id == project_id)
            .order_by(Subproject.id)
        ).all()
    )


@router.post(
    "/{project_id}/subprojects",
    response_model=SubprojectRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_subproject(
    project_id: int,
    payload: SubprojectCreate,
    session: SessionDep,
    user: CurrentUser,
) -> Subproject:
    project = require_project(session, user, project_id, write=True)

    subproject = Subproject(
        project_id=project_id,
        name=payload.name,
        context_brief=payload.context_brief,
        status=payload.status,
    )
    session.add(subproject)
    session.commit()
    session.refresh(subproject)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.SUBPROJECT_CREATED,
            entity="subproject",
            entity_id=subproject.id,  # type: ignore[arg-type]
            parent_id=project_id,
            project_id=project_id,
            workspace_id=project.workspace_id,
        )
    )
    return subproject
