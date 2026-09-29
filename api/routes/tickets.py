"""Ticket detail, mutation, and MR linking endpoints."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.orm import aliased
from sqlmodel import select

from api.auth import CurrentUser
from api.authorization import (
    project_id_for_subproject,
    require_knowledge_node,
    require_subproject,
    require_ticket,
    workspace_id_for_subproject,
    workspace_id_for_ticket,
)
from api.dependencies import SessionDep
from api.events import Event, get_broadcaster
from api.models.entities import AuditLog, Comment, Subproject, Ticket, TicketDependency
from api.models.enums import (
    ActorRole,
    AuditAction,
    BlockedByCategory,
    SSEAction,
    TicketStatus,
)
from api.schemas import (
    AuditLogRead,
    ClaimPayload,
    CommentRead,
    HeartbeatPayload,
    MRLinkPayload,
    TicketDetail,
    TicketRead,
    TicketRef,
    TicketUpdate,
)
from api.utils.ticket_deps import (
    build_ticket_read,
    delete_ticket_dependencies,
    resolve_ticket_refs,
    validate_and_set_deps,
)

router = APIRouter(prefix="/tickets", tags=["tickets"])


def _infer_actor(request: Request) -> ActorRole:
    """Detect whether the caller is the agent (API key) or the UI (cookie).

    Uses the auth_method set by get_current_user: 'api_key' = AGENT,
    'cookie' = HUMAN. Falls back to HUMAN if unset.
    """
    if getattr(request.state, "auth_method", None) == "api_key":
        return ActorRole.AGENT
    return ActorRole.HUMAN


@router.get("/{ticket_id}", response_model=TicketDetail)
def get_ticket(
    ticket_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> TicketDetail:
    ticket = require_ticket(session, user, ticket_id)
    comments = list(
        session.exec(
            select(Comment)
            .where(Comment.ticket_id == ticket_id)
            .order_by(Comment.timestamp)
        ).all()
    )
    audit_logs = list(
        session.exec(
            select(AuditLog)
            .where(AuditLog.ticket_id == ticket_id)
            .order_by(AuditLog.timestamp)
        ).all()
    )
    base = build_ticket_read(session, ticket)
    return TicketDetail(
        **base.model_dump(),
        comments=[CommentRead.model_validate(c) for c in comments],
        audit_logs=[AuditLogRead.model_validate(a) for a in audit_logs],
    )


@router.patch("/{ticket_id}", response_model=TicketRead)
async def update_ticket(
    ticket_id: int,
    payload: TicketUpdate,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
) -> Ticket:
    ticket = require_ticket(session, user, ticket_id, write=True)
    project_id = project_id_for_subproject(session, ticket.subproject_id)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update.")

    actor = _infer_actor(request)
    audit_events: list[AuditAction] = []

    if "status" in updates:
        try:
            updates["status"] = TicketStatus(updates["status"])
        except ValueError as exc:  # pragma: no cover - pydantic catches this
            raise HTTPException(status_code=400, detail="Invalid status.") from exc
        new_status = updates["status"]
        if (
            new_status == TicketStatus.BLOCKED
            and not updates.get("blocked_by")
            and not ticket.blocked_by
        ):
            raise HTTPException(
                status_code=422,
                detail="blocked_by is required when setting status to BLOCKED.",
            )
        if new_status != TicketStatus.BLOCKED:
            updates["blocked_by"] = None
            updates["blocked_reason"] = None
        if new_status != ticket.status:
            audit_events.append(AuditAction.STATUS_UPDATE)

    if any(k in updates for k in ("title", "description")):
        original = {"title": ticket.title, "description": ticket.description}
        if any(
            updates.get(k) != original[k]
            for k in ("title", "description")
            if k in updates
        ):
            audit_events.append(AuditAction.CONTENT_UPDATE)

    if (
        "mr_link" in updates
        and updates["mr_link"] != ticket.mr_link
        and updates["mr_link"]
    ):
        audit_events.append(AuditAction.MR_LINKED)

    if "depends_on" in updates:
        validate_and_set_deps(
            session, ticket.id, ticket.subproject_id, updates["depends_on"]  # type: ignore[arg-type]
        )
        # Remove from updates so the setattr loop doesn't try to set it on the model.
        del updates["depends_on"]

    for field_name, value in updates.items():
        setattr(ticket, field_name, value)

    session.add(ticket)
    for action in audit_events:
        session.add(
            AuditLog(
                actor_user_id=user.id,
                actor_name=user.name,
                ticket_id=ticket.id,
                action=action,
                actor=actor,
            )
        )
    session.commit()
    session.refresh(ticket)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.TICKET_UPDATED,
            entity="ticket",
            entity_id=ticket.id,  # type: ignore[arg-type]
            parent_id=ticket.subproject_id,
            project_id=project_id,
            workspace_id=workspace_id_for_ticket(
                session,
                ticket.id,  # type: ignore[arg-type]
            ),
        )
    )
    return build_ticket_read(session, ticket)


@router.delete(
    "/{ticket_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_ticket(
    ticket_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> None:
    """Delete a ticket and cascade its comments, audit logs, and dependency edges."""
    ticket = require_ticket(session, user, ticket_id, write=True)
    project_id = project_id_for_subproject(session, ticket.subproject_id)
    subproject_id = ticket.subproject_id
    workspace_id = workspace_id_for_ticket(session, ticket_id)
    delete_ticket_dependencies(session, [ticket_id])
    from api.utils.github_cleanup import delete_ticket_links
    delete_ticket_links(session, [ticket_id])
    session.delete(ticket)
    session.commit()

    await get_broadcaster().publish(
        Event(
            action=SSEAction.TICKET_DELETED,
            entity="ticket",
            entity_id=ticket_id,
            parent_id=subproject_id,
            project_id=project_id,
            workspace_id=workspace_id,
        )
    )
    return None


@router.post(
    "/{ticket_id}/mr",
    response_model=TicketRead,
    status_code=status.HTTP_200_OK,
)
async def attach_mr_link(
    ticket_id: int,
    payload: MRLinkPayload,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
) -> Ticket:
    """Attach (or replace) the Git MR link on a ticket.

    Branch generation is intentionally out-of-scope for MVP. If a ``GITHUB_PAT``
    is provided later, this route can grow a branch-creation side-effect.
    """
    ticket = require_ticket(session, user, ticket_id, write=True)
    project_id = project_id_for_subproject(session, ticket.subproject_id)
    actor = _infer_actor(request)

    ticket.mr_link = payload.url
    session.add(ticket)
    session.add(
        AuditLog(
            actor_user_id=user.id,
            actor_name=user.name,
            ticket_id=ticket.id,
            action=AuditAction.MR_LINKED,
            actor=actor,
        )
    )
    session.commit()
    session.refresh(ticket)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.MR_LINKED,
            entity="ticket",
            entity_id=ticket.id,  # type: ignore[arg-type]
            parent_id=ticket.subproject_id,
            project_id=project_id,
            workspace_id=workspace_id_for_ticket(
                session,
                ticket.id,  # type: ignore[arg-type]
            ),
        )
    )
    return build_ticket_read(session, ticket)


@router.get("/knowledge/{node_id}/tickets", response_model=list[TicketRef])
def get_tickets_for_node(
    node_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> list[TicketRef]:
    """Return all tickets whose source_refs include this knowledge node."""
    node = require_knowledge_node(session, user, node_id)
    ref_str = f"node:{node_id}"
    tickets = list(
        session.exec(
            select(Ticket)
            .join(
                Subproject,
                Ticket.subproject_id == Subproject.id,  # type: ignore[arg-type]
            )
            .where(Subproject.project_id == node.project_id)
        ).all()
    )
    matched = [t for t in tickets if ref_str in (t.source_refs or [])]
    ref_map = resolve_ticket_refs(session, [t.id for t in matched if t.id])  # type: ignore[misc]
    return [TicketRef(**ref_map[t.id]) for t in matched if t.id in ref_map]


# ---- Claim / heartbeat / requeue -----------------------------------------


_DEFAULT_LEASE_SECONDS = 600  # 10 minutes


def _claim_ticket_atomic(session, ticket_id: int, worker_id: str, now) -> bool:
    dependency = aliased(Ticket)
    has_unmet_dependency = (
        select(TicketDependency.ticket_id)
        .join(dependency, TicketDependency.depends_on_ticket_id == dependency.id)
        .where(TicketDependency.ticket_id == ticket_id)
        .where(dependency.status != TicketStatus.DONE)
        .exists()
    )
    claim = session.execute(
        update(Ticket)
        .where(Ticket.id == ticket_id)
        .where(Ticket.status == TicketStatus.TODO)
        .where(~has_unmet_dependency)
        .values(
            status=TicketStatus.IN_PROGRESS,
            claimed_by=worker_id,
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=_DEFAULT_LEASE_SECONDS),
        )
        .execution_options(synchronize_session=False)
    )
    return claim.rowcount == 1


@router.post("/{ticket_id}/claim", response_model=TicketRead)
async def claim_ticket(
    ticket_id: int,
    payload: ClaimPayload,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
) -> TicketRead:
    """Atomically transition a TODO ticket to IN_PROGRESS and set worker identity.

    Returns 200 on success, 409 if already claimed or not ready (unmet deps).
    """
    from api.utils.time import utcnow

    ticket = require_ticket(session, user, ticket_id, write=True)
    project_id = project_id_for_subproject(session, ticket.subproject_id)
    now = utcnow().replace(microsecond=0)
    if not _claim_ticket_atomic(session, ticket_id, payload.worker_id, now):
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Ticket is not available for claiming (not TODO or dependencies unmet).",
        )

    session.add(
        AuditLog(
            actor_user_id=user.id,
            actor_name=user.name,
            ticket_id=ticket_id,
            action=AuditAction.TICKET_CLAIMED,
            actor=_infer_actor(request),
        )
    )
    session.commit()
    session.expire_all()
    ticket = require_ticket(session, user, ticket_id)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.TICKET_CLAIMED,
            entity="ticket",
            entity_id=ticket.id,  # type: ignore[arg-type]
            parent_id=ticket.subproject_id,
            project_id=project_id,
            workspace_id=workspace_id_for_ticket(
                session,
                ticket.id,  # type: ignore[arg-type]
            ),
        )
    )
    return build_ticket_read(session, ticket)


@router.post("/{ticket_id}/heartbeat", response_model=TicketRead)
async def heartbeat_ticket(
    ticket_id: int,
    payload: HeartbeatPayload,
    session: SessionDep,
    user: CurrentUser,
) -> TicketRead:
    """Extend the lease on a claimed ticket.

    The ``worker_id`` must match ``claimed_by``. Returns 200 with the updated
    ticket, 409 if the worker doesn't own the ticket.
    """
    from api.utils.time import utcnow

    ticket = require_ticket(session, user, ticket_id, write=True)
    project_id = project_id_for_subproject(session, ticket.subproject_id)
    now = utcnow().replace(microsecond=0)
    heartbeat = session.execute(
        update(Ticket)
        .where(Ticket.id == ticket_id)
        .where(Ticket.status == TicketStatus.IN_PROGRESS)
        .where(Ticket.claimed_by == payload.worker_id)
        .where(Ticket.lease_expires_at >= now)
        .values(lease_expires_at=now + timedelta(seconds=payload.extend_seconds))
        .execution_options(synchronize_session=False)
    )
    if heartbeat.rowcount != 1:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Worker '{payload.worker_id}' does not own an active lease on ticket {ticket_id}.",
        )

    session.commit()
    session.expire_all()
    ticket = require_ticket(session, user, ticket_id)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.TICKET_UPDATED,
            entity="ticket",
            entity_id=ticket.id,  # type: ignore[arg-type]
            parent_id=ticket.subproject_id,
            project_id=project_id,
            workspace_id=workspace_id_for_ticket(
                session,
                ticket.id,  # type: ignore[arg-type]
            ),
        )
    )
    return build_ticket_read(session, ticket)


@router.post(
    "/subprojects/{subproject_id}/requeue-expired", response_model=list[TicketRead]
)
async def requeue_expired(
    subproject_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> list[TicketRead]:
    """Revert IN_PROGRESS tickets with expired leases back to TODO.

    Clears claim fields and writes a TICKET_REQUEUED audit log entry for each.
    """
    from api.utils.time import utcnow

    subproject = require_subproject(session, user, subproject_id, write=True)
    project_id = subproject.project_id
    workspace_id = workspace_id_for_subproject(session, subproject_id)
    now = utcnow()
    candidate_ids = list(
        session.exec(
            select(Ticket.id)
            .where(Ticket.subproject_id == subproject_id)
            .where(Ticket.status == TicketStatus.IN_PROGRESS)
            .where(Ticket.lease_expires_at.is_not(None))  # type: ignore[union-attr]
            .where(Ticket.lease_expires_at < now)  # type: ignore[operator]
        ).all()
    )
    requeued_ids: list[int] = []
    for candidate_id in candidate_ids:
        requeue = session.execute(
            update(Ticket)
            .where(Ticket.id == candidate_id)
            .where(Ticket.status == TicketStatus.IN_PROGRESS)
            .where(Ticket.lease_expires_at < now)
            .values(
                status=TicketStatus.TODO,
                claimed_by=None,
                claimed_at=None,
                lease_expires_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        if requeue.rowcount != 1:
            continue
        requeued_ids.append(candidate_id)
        session.add(
            AuditLog(
                actor_user_id=user.id,
                actor_name=user.name,
                ticket_id=candidate_id,
                action=AuditAction.TICKET_REQUEUED,
                actor=ActorRole.AGENT,
            )
        )
    session.commit()
    session.expire_all()
    tickets = [session.get(Ticket, ticket_id) for ticket_id in requeued_ids]
    result = [build_ticket_read(session, ticket) for ticket in tickets if ticket]

    for ticket_id in requeued_ids:
        await get_broadcaster().publish(
            Event(
                action=SSEAction.TICKET_REQUEUED,
                entity="ticket",
                entity_id=ticket_id,
                parent_id=subproject_id,
                project_id=project_id,
                workspace_id=workspace_id,
            )
        )
    return result
