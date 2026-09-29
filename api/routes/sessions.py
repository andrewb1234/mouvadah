"""Agent session lifecycle endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from api.auth import CurrentUser
from api.authorization import (
    require_agent_session,
    require_project,
    workspace_id_for_project,
)
from api.dependencies import SessionDep
from api.events import Event, get_broadcaster
from api.models.entities import AgentSession, KnowledgeNode
from api.models.enums import SSEAction
from api.schemas import AgentSessionCreate, AgentSessionRead, AgentSessionUpdate
from api.utils.time import utcnow

router = APIRouter(tags=["sessions"])


def _validate_loaded_nodes(
    session,
    project_id: int,
    node_ids: list[int],
) -> None:
    ids = sorted(set(node_ids))
    if not ids:
        return
    valid_ids = set(
        session.exec(
            select(KnowledgeNode.id).where(
                KnowledgeNode.id.in_(ids),
                KnowledgeNode.project_id == project_id,
            )
        ).all()
    )
    if valid_ids != set(ids):
        raise HTTPException(
            status_code=422,
            detail="loaded_node_ids contains an unknown project node.",
        )


@router.post(
    "/projects/{project_id}/sessions",
    response_model=AgentSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def start_session(
    project_id: int,
    payload: AgentSessionCreate,
    session: SessionDep,
    user: CurrentUser,
) -> AgentSession:
    """Start a new agent session, recording intent and initial loaded nodes."""
    require_project(session, user, project_id, write=True)
    _validate_loaded_nodes(session, project_id, payload.loaded_node_ids)

    agent_session = AgentSession(
        actor_user_id=user.id,
        actor_name=user.name,
        project_id=project_id,
        intent=payload.intent,
        loaded_node_ids=list(payload.loaded_node_ids),
        status="ACTIVE",
    )
    session.add(agent_session)
    session.commit()
    session.refresh(agent_session)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.SESSION_STARTED,
            entity="agent_session",
            entity_id=agent_session.id,  # type: ignore[arg-type]
            parent_id=project_id,
            project_id=project_id,
            workspace_id=workspace_id_for_project(session, project_id),
        )
    )
    return agent_session


@router.get(
    "/projects/{project_id}/sessions",
    response_model=list[AgentSessionRead],
)
def list_sessions(
    project_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> list[AgentSession]:
    """Return all sessions for a project, most recent first."""
    require_project(session, user, project_id)
    return list(
        session.exec(
            select(AgentSession)
            .where(AgentSession.project_id == project_id)
            .order_by(AgentSession.started_at.desc())  # type: ignore[union-attr]
        ).all()
    )


@router.patch(
    "/agent/sessions/{session_id}",
    response_model=AgentSessionRead,
)
async def update_session(
    session_id: int,
    payload: AgentSessionUpdate,
    session: SessionDep,
    user: CurrentUser,
) -> AgentSession:
    """Checkpoint or close an agent session."""
    agent_session = require_agent_session(
        session,
        user,
        session_id,
        write=True,
    )
    updates = payload.model_dump(exclude_unset=True)
    if "loaded_node_ids" in updates:
        _validate_loaded_nodes(
            session,
            agent_session.project_id,
            updates["loaded_node_ids"],
        )

    for key, value in updates.items():
        setattr(agent_session, key, value)

    if payload.status in ("COMPLETE", "INTERRUPTED") and agent_session.ended_at is None:
        agent_session.ended_at = utcnow()

    session.add(agent_session)
    session.commit()
    session.refresh(agent_session)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.SESSION_ENDED,
            entity="agent_session",
            entity_id=session_id,
            parent_id=agent_session.project_id,
            project_id=agent_session.project_id,
            workspace_id=workspace_id_for_project(
                session,
                agent_session.project_id,
            ),
        )
    )
    return agent_session


@router.get(
    "/agent/sessions/{session_id}",
    response_model=AgentSessionRead,
)
def get_session(
    session_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> AgentSession:
    return require_agent_session(session, user, session_id)
