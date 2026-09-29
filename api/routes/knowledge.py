"""Knowledge-tree endpoints.

The knowledge tree sits *upstream* of subprojects and tickets: it is where
agents persist raw research, compressed summaries, and drafted PRD/TDD
artifacts before breaking work down into actionable tickets.

Every route is authenticated and resolved through a workspace-authorized
project or knowledge node. Every mutation emits an SSE event so the React tree
view can reconcile live.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlmodel import select

from api.auth import CurrentUser
from api.authorization import (
    require_knowledge_node,
    require_project,
    workspace_id_for_project,
)
from api.dependencies import SessionDep
from api.events import Event, get_broadcaster
from api.models.entities import KnowledgeNode
from api.models.enums import ActorRole, KnowledgeNodeStatus, SSEAction
from api.schemas import (
    KnowledgeNodeCreate,
    KnowledgeNodeRead,
    KnowledgeNodeUpdate,
    ContextTrailRead,
)
from api.utils.context_trails import build_context_trail
from api.utils.time import utcnow

router = APIRouter(tags=["knowledge"])


def _infer_actor(request: Request) -> ActorRole:
    """Detect whether the caller is the agent (API key) or the UI (cookie).

    Uses the auth_method set by get_current_user: 'api_key' = AGENT,
    'cookie' = HUMAN. Falls back to HUMAN if unset.
    """
    if getattr(request.state, "auth_method", None) == "api_key":
        return ActorRole.AGENT
    return ActorRole.HUMAN


def _validate_parent(
    session,
    project_id: int,
    parent_id: int | None,
    *,
    self_id: int | None = None,
) -> None:
    """Reject parent references that cross projects or form a cycle."""
    if parent_id is None:
        return
    parent = session.exec(
        select(KnowledgeNode).where(
            KnowledgeNode.id == parent_id,
            KnowledgeNode.project_id == project_id,
        )
    ).first()
    if parent is None:
        raise HTTPException(status_code=400, detail="Parent node does not exist.")
    # Cycle guard: walk up the ancestor chain, refusing if we hit ``self_id``.
    if self_id is not None:
        cursor = parent
        while cursor is not None:
            if cursor.id == self_id:
                raise HTTPException(
                    status_code=400,
                    detail="parent_id would create a cycle in the knowledge tree.",
                )
            cursor = (
                session.get(KnowledgeNode, cursor.parent_id)
                if cursor.parent_id is not None
                else None
            )


# ---- UI-side CRUD --------------------------------------------------------


@router.get(
    "/projects/{project_id}/knowledge",
    response_model=list[KnowledgeNodeRead],
)
def list_knowledge_nodes(
    project_id: int,
    session: SessionDep,
    user: CurrentUser,
    include_stale: bool = Query(default=False),
) -> list[KnowledgeNode]:
    """Return knowledge nodes for a project.

    By default only ``CURRENT`` nodes are returned. Pass ``?include_stale=true``
    to include ``STALE`` and ``ARCHIVED`` nodes (for full history).
    The shape is intentionally flat; the client reconstructs the tree
    locally using ``parent_id``. This keeps the endpoint cheap (one query)
    and SSE-friendly (a single action invalidates the whole panel).
    """
    require_project(session, user, project_id)
    query = (
        select(KnowledgeNode)
        .where(KnowledgeNode.project_id == project_id)
        .order_by(KnowledgeNode.created_at, KnowledgeNode.id)
    )
    if not include_stale:
        query = query.where(KnowledgeNode.status == KnowledgeNodeStatus.CURRENT)  # type: ignore[union-attr]
    return list(session.exec(query).all())


@router.get(
    "/projects/{project_id}/knowledge/context-trail",
    response_model=ContextTrailRead,
)
def get_context_trail(
    project_id: int,
    session: SessionDep,
    user: CurrentUser,
    query: str = Query(default="", max_length=200),
    limit: int = Query(default=6, ge=1, le=12),
    include_stale: bool = Query(default=False),
) -> ContextTrailRead:
    """Find the most relevant knowledge branches for a task-intent query."""
    project = require_project(session, user, project_id)
    stmt = (
        select(KnowledgeNode)
        .where(KnowledgeNode.project_id == project_id)
        .order_by(KnowledgeNode.created_at, KnowledgeNode.id)
    )
    if not include_stale:
        stmt = stmt.where(KnowledgeNode.status == KnowledgeNodeStatus.CURRENT)
    nodes = list(session.exec(stmt).all())
    return build_context_trail(project, nodes, query, limit=limit)


@router.post(
    "/projects/{project_id}/knowledge",
    response_model=KnowledgeNodeRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_node(
    project_id: int,
    payload: KnowledgeNodeCreate,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
) -> KnowledgeNode:
    require_project(session, user, project_id, write=True)
    _validate_parent(session, project_id, payload.parent_id)

    actor = _infer_actor(request)
    node = KnowledgeNode(
        actor_user_id=user.id,
        actor_name=user.name,
        project_id=project_id,
        parent_id=payload.parent_id,
        title=payload.title,
        node_type=payload.node_type,
        content=payload.content,
        source_refs=list(payload.source_refs),
        created_by=actor,
    )
    session.add(node)
    session.commit()
    session.refresh(node)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.KNOWLEDGE_NODE_CREATED,
            entity="knowledge_node",
            entity_id=node.id,  # type: ignore[arg-type]
            parent_id=project_id,
            workspace_id=workspace_id_for_project(session, project_id),
        )
    )
    return node


@router.get("/knowledge/{node_id}", response_model=KnowledgeNodeRead)
def get_knowledge_node(
    node_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> KnowledgeNode:
    return require_knowledge_node(session, user, node_id)


@router.patch("/knowledge/{node_id}", response_model=KnowledgeNodeRead)
async def update_knowledge_node(
    node_id: int,
    payload: KnowledgeNodeUpdate,
    session: SessionDep,
    user: CurrentUser,
) -> KnowledgeNode:
    node = require_knowledge_node(session, user, node_id, write=True)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update.")

    if "parent_id" in updates:
        _validate_parent(
            session,
            node.project_id,
            updates["parent_id"],
            self_id=node.id,
        )
    if updates.get("superseded_by") is not None:
        superseding_node = session.exec(
            select(KnowledgeNode).where(
                KnowledgeNode.id == updates["superseded_by"],
                KnowledgeNode.project_id == node.project_id,
            )
        ).first()
        if superseding_node is None or superseding_node.id == node.id:
            raise HTTPException(
                status_code=400,
                detail="superseded_by must reference another node in this project.",
            )

    for key, value in updates.items():
        setattr(node, key, value)
    node.actor_user_id = user.id
    node.actor_name = user.name
    node.updated_at = utcnow()

    session.add(node)
    session.commit()
    session.refresh(node)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.KNOWLEDGE_NODE_UPDATED,
            entity="knowledge_node",
            entity_id=node.id,  # type: ignore[arg-type]
            parent_id=node.project_id,
            workspace_id=workspace_id_for_project(
                session,
                node.project_id,
            ),
        )
    )
    return node


@router.delete(
    "/knowledge/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_knowledge_node(
    node_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> None:
    node = require_knowledge_node(session, user, node_id, write=True)
    project_id = node.project_id
    session.delete(node)
    session.commit()

    await get_broadcaster().publish(
        Event(
            action=SSEAction.KNOWLEDGE_NODE_DELETED,
            entity="knowledge_node",
            entity_id=node_id,
            parent_id=project_id,
            workspace_id=workspace_id_for_project(session, project_id),
        )
    )
    return None
