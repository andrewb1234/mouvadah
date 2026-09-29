"""Knowledge proposal endpoints — agent submits, human reviews."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from api.auth import CurrentUser
from api.authorization import (
    require_knowledge_node,
    require_project,
    require_proposal,
    workspace_id_for_project,
)
from api.dependencies import SessionDep
from api.events import Event, get_broadcaster
from api.models.entities import KnowledgeNode, KnowledgeProposal
from api.models.enums import SSEAction
from api.schemas import (
    KnowledgeProposalCreate,
    KnowledgeProposalRead,
    KnowledgeProposalReview,
)
from api.utils.time import utcnow

router = APIRouter(tags=["proposals"])


@router.post(
    "/knowledge/{node_id}/proposals",
    response_model=KnowledgeProposalRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_proposal(
    node_id: int,
    payload: KnowledgeProposalCreate,
    session: SessionDep,
    user: CurrentUser,
) -> KnowledgeProposal:
    """Agent submits a proposed change for human review."""
    node = require_knowledge_node(session, user, node_id, write=True)

    proposal = KnowledgeProposal(
        actor_user_id=user.id,
        actor_name=user.name,
        node_id=node_id,
        proposed_by="AGENT",
        proposed_changes=payload.proposed_changes,
        rationale=payload.rationale,
        status="PENDING",
    )
    session.add(proposal)
    session.commit()
    session.refresh(proposal)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.KNOWLEDGE_PROPOSAL_CREATED,
            entity="knowledge_proposal",
            entity_id=proposal.id,  # type: ignore[arg-type]
            parent_id=node.project_id,
            project_id=node.project_id,
            workspace_id=workspace_id_for_project(
                session,
                node.project_id,
            ),
        )
    )
    return proposal


@router.get(
    "/projects/{project_id}/knowledge/proposals",
    response_model=list[KnowledgeProposalRead],
)
def list_proposals(
    project_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> list[KnowledgeProposal]:
    """List all pending proposals for a project."""
    require_project(session, user, project_id)
    nodes = list(
        session.exec(
            select(KnowledgeNode).where(KnowledgeNode.project_id == project_id)
        ).all()
    )
    node_ids = [n.id for n in nodes]
    if not node_ids:
        return []
    return list(
        session.exec(
            select(KnowledgeProposal)
            .where(KnowledgeProposal.node_id.in_(node_ids))  # type: ignore[union-attr]
            .order_by(KnowledgeProposal.created_at)
        ).all()
    )


@router.get(
    "/knowledge/{node_id}/proposals",
    response_model=list[KnowledgeProposalRead],
)
def list_node_proposals(
    node_id: int,
    session: SessionDep,
    user: CurrentUser,
) -> list[KnowledgeProposal]:
    """List proposals for a specific node."""
    require_knowledge_node(session, user, node_id)
    return list(
        session.exec(
            select(KnowledgeProposal)
            .where(KnowledgeProposal.node_id == node_id)
            .order_by(KnowledgeProposal.created_at)
        ).all()
    )


@router.patch(
    "/knowledge/proposals/{proposal_id}",
    response_model=KnowledgeProposalRead,
)
async def review_proposal(
    proposal_id: int,
    payload: KnowledgeProposalReview,
    session: SessionDep,
    user: CurrentUser,
) -> KnowledgeProposal:
    """Human accepts or rejects a proposal. Accepting applies the patch."""
    if payload.action not in ("accept", "reject"):
        raise HTTPException(
            status_code=422, detail="action must be 'accept' or 'reject'."
        )

    proposal = require_proposal(session, user, proposal_id, write=True)
    proposal_node = require_knowledge_node(
        session,
        user,
        proposal.node_id,
        write=True,
    )
    if proposal.status != "PENDING":
        raise HTTPException(status_code=409, detail="Proposal is already reviewed.")

    proposal.status = "ACCEPTED" if payload.action == "accept" else "REJECTED"
    proposal.reviewed_by = user.name
    proposal.reviewed_at = utcnow()

    if payload.action == "accept":
        node = proposal_node
        allowed_fields = {
            "title",
            "node_type",
            "status",
            "content",
            "source_refs",
            "parent_id",
            "superseded_by",
        }
        for key, value in proposal.proposed_changes.items():
            if key in allowed_fields:
                if key == "parent_id" and value is not None:
                    from api.routes.knowledge import _validate_parent

                    _validate_parent(session, node.project_id, value, self_id=node.id)
                if key == "superseded_by" and value is not None:
                    superseding_node = session.exec(
                        select(KnowledgeNode).where(
                            KnowledgeNode.id == value,
                            KnowledgeNode.project_id == node.project_id,
                        )
                    ).first()
                    if superseding_node is None or superseding_node.id == node.id:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "superseded_by must reference another node "
                                "in this project."
                            ),
                        )
                setattr(node, key, value)
        from api.utils.time import utcnow as _utcnow

        node.actor_user_id = user.id
        node.actor_name = user.name
        node.updated_at = _utcnow()
        session.add(node)

    session.add(proposal)
    session.commit()
    session.refresh(proposal)

    await get_broadcaster().publish(
        Event(
            action=SSEAction.KNOWLEDGE_PROPOSAL_REVIEWED,
            entity="knowledge_proposal",
            entity_id=proposal_id,
            # Proposal create and review events are both project-scoped
            # invalidations. A node ID here forces consumers to retain a full
            # proposal index merely to determine relevance.
            parent_id=proposal_node.project_id,
            project_id=proposal_node.project_id,
            workspace_id=workspace_id_for_project(
                session,
                proposal_node.project_id,
            ),
        )
    )
    if payload.action == "accept":
        await get_broadcaster().publish(
            Event(
                action=SSEAction.KNOWLEDGE_NODE_UPDATED,
                entity="knowledge_node",
                entity_id=proposal.node_id,
                parent_id=node.project_id,
                project_id=node.project_id,
                workspace_id=workspace_id_for_project(
                    session,
                    node.project_id,
                ),
            )
        )
    return proposal
