"""Authenticated, workspace-filtered Server-Sent Events stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse
from sqlmodel import Session, select

from api import database
from api.auth import StreamCurrentUser
from api.events import Event, ResyncSignal, get_broadcaster
from api.models.entities import WorkspaceMembership, Project, User, Workspace
from api.authorization import project_access
from api.observability import record_realtime_resync
from api.security import get_api_key_authorization

router = APIRouter(tags=["events"])

_HEARTBEAT_SECONDS = 15.0


@dataclass(frozen=True)
class StreamAuthorization:
    """Immutable authorization captured before the streaming response starts."""

    user_id: int
    api_key_workspace_id: int | None


def can_receive_event(
    session: Session,
    authorization: StreamAuthorization,
    event: Event,
) -> bool:
    """Return whether a caller currently belongs to an event's workspace."""
    if event.recipient_user_id is not None:
        return (
            authorization.api_key_workspace_id is None
            and authorization.user_id == event.recipient_user_id
        )
    if event.workspace_id is None:
        return False
    if (
        authorization.api_key_workspace_id is not None
        and authorization.api_key_workspace_id != event.workspace_id
    ):
        return False
    workspace = session.get(Workspace, event.workspace_id)
    if not workspace or workspace.deletion_requested_at is not None:
        return False
    if event.project_id is not None:
        project = session.get(Project, event.project_id)
        user = session.get(User, authorization.user_id)
        if project and user:
            return (
                project.workspace_id == event.workspace_id
                and project_access(session, user, project) is not None
            )
    return (
        session.exec(
            select(WorkspaceMembership.id).where(
                WorkspaceMembership.workspace_id == event.workspace_id,
                WorkspaceMembership.user_id == authorization.user_id,
            )
        ).first()
        is not None
    )


def _can_receive_with_short_session(
    authorization: StreamAuthorization,
    event: Event,
) -> bool:
    with Session(database.engine) as session:
        return can_receive_event(session, authorization, event)


@router.get("/events")
async def stream_events(
    request: Request,
    user: StreamCurrentUser,
) -> EventSourceResponse:
    """Stream content-free invalidations and explicit resync instructions."""
    user_id = user.id
    if user_id is None:  # pragma: no cover - persisted auth invariant
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    api_key = get_api_key_authorization()
    if api_key is not None and api_key.project_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Project-restricted API keys cannot subscribe to the "
                "workspace-wide event stream."
            ),
        )
    authorization = StreamAuthorization(
        user_id=user_id,
        api_key_workspace_id=(api_key.workspace_id if api_key is not None else None),
    )
    broadcaster = get_broadcaster()

    async def event_generator() -> AsyncIterator[dict]:
        async with broadcaster.subscribe() as queue:
            # Notifications are invalidation hints, not a replayable log.
            # Every initial connection and automatic EventSource reconnect
            # therefore instructs the UI to refetch all authorized live state.
            record_realtime_resync("connected")
            yield {
                "event": "ready",
                "data": ResyncSignal(reason="connected").to_json(),
            }
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(
                        queue.get(),
                        timeout=_HEARTBEAT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    yield {"comment": "heartbeat"}
                    continue
                if isinstance(item, ResyncSignal):
                    yield {"event": "resync", "data": item.to_json()}
                    continue
                # The request authentication session has already closed.
                # Re-check membership with a short transaction for every event
                # so revocation affects an already-open stream.
                allowed = await asyncio.to_thread(
                    _can_receive_with_short_session,
                    authorization,
                    item,
                )
                if not allowed:
                    continue
                yield {"event": "message", "data": item.to_json()}

    return EventSourceResponse(event_generator())
