"""Threaded comment endpoints."""

from __future__ import annotations

from fastapi import APIRouter, status, Request
from sqlmodel import select

from api.auth import CurrentUser
from api.authorization import require_ticket, workspace_id_for_ticket
from api.dependencies import SessionDep
from api.events import Event, get_broadcaster
from api.models.entities import Comment
from api.models.enums import SSEAction, ActorRole
from api.schemas import CommentCreate, CommentRead

router = APIRouter(prefix="/tickets", tags=["comments"])


@router.get("/{ticket_id}/comments", response_model=list[CommentRead])
def list_comments(
    ticket_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> list[Comment]:
    require_ticket(session, user, ticket_id)
    return list(
        session.exec(
            select(Comment)
            .where(Comment.ticket_id == ticket_id)
            .order_by(Comment.timestamp)
        ).all()
    )


@router.post(
    "/{ticket_id}/comments",
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_comment(
    ticket_id: int,
    payload: CommentCreate,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
) -> Comment:
    ticket = require_ticket(session, user, ticket_id, write=True)

    comment = Comment(
        actor_user_id=user.id,
        actor_name=user.name,
        ticket_id=ticket_id,
        author=(
            ActorRole.AGENT
            if request.state.auth_method == "api_key"
            else ActorRole.HUMAN
        ),
        content=payload.content,
    )
    session.add(comment)
    session.commit()
    session.refresh(comment)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.COMMENT_CREATED,
            entity="comment",
            entity_id=comment.id,  # type: ignore[arg-type]
            parent_id=ticket.id,
            workspace_id=workspace_id_for_ticket(
                session,
                ticket.id,  # type: ignore[arg-type]
            ),
        )
    )
    return comment
