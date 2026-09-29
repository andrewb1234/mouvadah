"""Subproject detail + update + ticket-creation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import select

from api.auth import CurrentUser
from api.authorization import require_subproject, workspace_id_for_project
from api.dependencies import SessionDep
from api.events import Event, get_broadcaster
from api.models.entities import Subproject, Ticket
from api.models.enums import SSEAction, TicketStatus
from api.schemas import (
    SubprojectDetail,
    SubprojectRead,
    SubprojectUpdate,
    TicketCreate,
    TicketRead,
    TicketRef,
)
from api.utils.ticket_deps import (
    build_ticket_read,
    delete_ticket_dependencies,
    get_depends_on_map,
    is_ticket_ready,
    resolve_ticket_refs,
    validate_and_set_deps,
)

router = APIRouter(prefix="/subprojects", tags=["subprojects"])


@router.get("/{subproject_id}", response_model=SubprojectDetail)
def get_subproject(
    subproject_id: int,
    session: SessionDep,
    user: CurrentUser,
    ready: bool = Query(default=False),
) -> SubprojectDetail:
    subproject = require_subproject(session, user, subproject_id)
    tickets = list(
        session.exec(
            select(Ticket)
            .where(Ticket.subproject_id == subproject_id)
            .order_by(Ticket.id)
        ).all()
    )
    if ready:
        tickets = [t for t in tickets if is_ticket_ready(session, t)]
    dep_map = get_depends_on_map(session, [t.id for t in tickets if t.id])  # type: ignore[arg-type]
    all_dep_ids = sorted({dep_id for deps in dep_map.values() for dep_id in deps})
    ref_map = resolve_ticket_refs(session, all_dep_ids)
    ticket_reads = []
    for t in tickets:
        tr = TicketRead.model_validate(t)
        tr.project_id = subproject.project_id
        dep_ids = dep_map.get(t.id, [])  # type: ignore[arg-type]
        tr.depends_on = dep_ids
        tr.depends_on_refs = [TicketRef(**ref_map[d]) for d in dep_ids if d in ref_map]
        ticket_reads.append(tr)
    return SubprojectDetail(
        id=subproject.id,  # type: ignore[arg-type]
        project_id=subproject.project_id,
        name=subproject.name,
        context_brief=subproject.context_brief,
        status=subproject.status,
        tickets=ticket_reads,
    )


@router.patch("/{subproject_id}", response_model=SubprojectRead)
async def update_subproject(
    subproject_id: int,
    payload: SubprojectUpdate,
    session: SessionDep,
    user: CurrentUser,
) -> Subproject:
    subproject = require_subproject(session, user, subproject_id, write=True)

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update.")

    for field_name, value in updates.items():
        setattr(subproject, field_name, value)

    session.add(subproject)
    session.commit()
    session.refresh(subproject)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.SUBPROJECT_UPDATED,
            entity="subproject",
            entity_id=subproject.id,  # type: ignore[arg-type]
            parent_id=subproject.project_id,
            project_id=subproject.project_id,
            workspace_id=workspace_id_for_project(
                session,
                subproject.project_id,
            ),
        )
    )
    return subproject


@router.delete(
    "/{subproject_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_subproject(
    subproject_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> None:
    """Delete a subproject and cascade its tickets, comments, and audit logs."""
    subproject = require_subproject(
        session,
        user,
        subproject_id,
        admin=True,
        write=True,
    )
    project_id = subproject.project_id
    workspace_id = workspace_id_for_project(session, project_id)
    ticket_ids = list(
        session.exec(
            select(Ticket.id).where(Ticket.subproject_id == subproject_id)
        ).all()
    )
    delete_ticket_dependencies(session, ticket_ids)
    session.delete(subproject)
    session.commit()

    await get_broadcaster().publish(
        Event(
            action=SSEAction.SUBPROJECT_DELETED,
            entity="subproject",
            entity_id=subproject_id,
            parent_id=project_id,
            project_id=project_id,
            workspace_id=workspace_id,
        )
    )
    return None


@router.post(
    "/{subproject_id}/tickets",
    response_model=TicketRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_ticket(
    subproject_id: int,
    payload: TicketCreate,
    session: SessionDep,
    user: CurrentUser,
) -> TicketRead:
    subproject = require_subproject(
        session,
        user,
        subproject_id,
        write=True,
    )

    ticket = Ticket(
        subproject_id=subproject_id,
        title=payload.title,
        description=payload.description,
        status=payload.status,
        assignee=payload.assignee,
        source_refs=list(payload.source_refs),
    )
    session.add(ticket)
    session.flush()  # get the id without committing yet

    if payload.depends_on:
        validate_and_set_deps(session, ticket.id, subproject_id, payload.depends_on)  # type: ignore[arg-type]

    session.commit()
    session.refresh(ticket)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.TICKET_CREATED,
            entity="ticket",
            entity_id=ticket.id,  # type: ignore[arg-type]
            parent_id=subproject_id,
            project_id=subproject.project_id,
            workspace_id=workspace_id_for_project(
                session,
                subproject.project_id,
            ),
        )
    )
    return build_ticket_read(session, ticket)
