"""Canonical, tenant-scoped workspace export generation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlmodel import Session, select

from api.models.entities import (
    AgentSession,
    ApiKey,
    ApiKeyProject,
    AuditLog,
    Comment,
    KnowledgeNode,
    KnowledgeProposal,
    Project,
    ProjectMembership,
    ProjectInvitation,
    ProjectAccessEvent,
    Subproject,
    Ticket,
    TicketDependency,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceLifecycleEvent,
    WorkspaceMembership,
    WorkspaceMembershipEvent,
)
from api.utils.time import utcnow

EXPORT_FORMAT = "mouvadah.workspace-export.v1"


def _dump_rows(
    rows: list[Any],
    *,
    exclude: set[str] | None = None,
) -> list[dict[str, Any]]:
    return [
        row.model_dump(
            mode="json",
            exclude=exclude or set(),
            warnings=False,
        )
        for row in rows
    ]


def build_workspace_export(
    session: Session,
    workspace: Workspace,
) -> tuple[bytes, str, dict[str, int]]:
    """Return canonical JSON bytes, SHA-256, and table record counts."""
    workspace_id = workspace.id
    if workspace_id is None:  # pragma: no cover - persisted route invariant
        raise RuntimeError("Cannot export an unpersisted workspace.")

    memberships = list(
        session.exec(
            select(WorkspaceMembership)
            .where(WorkspaceMembership.workspace_id == workspace_id)
            .order_by(WorkspaceMembership.id)
        ).all()
    )
    user_ids = sorted({row.user_id for row in memberships})
    users = (
        list(
            session.exec(
                select(User)
                .where(User.id.in_(user_ids))  # type: ignore[union-attr]
                .order_by(User.id)
            ).all()
        )
        if user_ids
        else []
    )
    projects = list(
        session.exec(
            select(Project)
            .where(Project.workspace_id == workspace_id)
            .order_by(Project.id)
        ).all()
    )
    project_ids = [row.id for row in projects if row.id is not None]
    subprojects = (
        list(
            session.exec(
                select(Subproject)
                .where(Subproject.project_id.in_(project_ids))  # type: ignore[union-attr]
                .order_by(Subproject.id)
            ).all()
        )
        if project_ids
        else []
    )
    subproject_ids = [row.id for row in subprojects if row.id is not None]
    tickets = (
        list(
            session.exec(
                select(Ticket)
                .where(Ticket.subproject_id.in_(subproject_ids))  # type: ignore[union-attr]
                .order_by(Ticket.id)
            ).all()
        )
        if subproject_ids
        else []
    )
    ticket_ids = [row.id for row in tickets if row.id is not None]
    comments = (
        list(
            session.exec(
                select(Comment)
                .where(Comment.ticket_id.in_(ticket_ids))  # type: ignore[union-attr]
                .order_by(Comment.id)
            ).all()
        )
        if ticket_ids
        else []
    )
    audit_logs = (
        list(
            session.exec(
                select(AuditLog)
                .where(AuditLog.ticket_id.in_(ticket_ids))  # type: ignore[union-attr]
                .order_by(AuditLog.id)
            ).all()
        )
        if ticket_ids
        else []
    )
    dependencies = (
        list(
            session.exec(
                select(TicketDependency)
                .where(
                    TicketDependency.ticket_id.in_(ticket_ids),  # type: ignore[union-attr]
                    TicketDependency.depends_on_ticket_id.in_(ticket_ids),  # type: ignore[union-attr]
                )
                .order_by(
                    TicketDependency.ticket_id,
                    TicketDependency.depends_on_ticket_id,
                )
            ).all()
        )
        if ticket_ids
        else []
    )
    knowledge_nodes = (
        list(
            session.exec(
                select(KnowledgeNode)
                .where(KnowledgeNode.project_id.in_(project_ids))  # type: ignore[union-attr]
                .order_by(KnowledgeNode.id)
            ).all()
        )
        if project_ids
        else []
    )
    node_ids = [row.id for row in knowledge_nodes if row.id is not None]
    proposals = (
        list(
            session.exec(
                select(KnowledgeProposal)
                .where(KnowledgeProposal.node_id.in_(node_ids))  # type: ignore[union-attr]
                .order_by(KnowledgeProposal.id)
            ).all()
        )
        if node_ids
        else []
    )
    agent_sessions = (
        list(
            session.exec(
                select(AgentSession)
                .where(AgentSession.project_id.in_(project_ids))  # type: ignore[union-attr]
                .order_by(AgentSession.id)
            ).all()
        )
        if project_ids
        else []
    )
    api_keys = list(
        session.exec(
            select(ApiKey)
            .where(ApiKey.workspace_id == workspace_id)
            .order_by(ApiKey.id)
        ).all()
    )
    api_key_ids = [row.id for row in api_keys if row.id is not None]
    api_key_projects = (
        list(
            session.exec(
                select(ApiKeyProject)
                .where(ApiKeyProject.api_key_id.in_(api_key_ids))  # type: ignore[union-attr]
                .order_by(
                    ApiKeyProject.api_key_id,
                    ApiKeyProject.project_id,
                )
            ).all()
        )
        if api_key_ids
        else []
    )
    lifecycle_events = list(
        session.exec(
            select(WorkspaceLifecycleEvent)
            .where(WorkspaceLifecycleEvent.workspace_id == workspace_id)
            .order_by(WorkspaceLifecycleEvent.id)
        ).all()
    )
    invitations = list(
        session.exec(
            select(WorkspaceInvitation)
            .where(WorkspaceInvitation.workspace_id == workspace_id)
            .order_by(WorkspaceInvitation.id)
        ).all()
    )
    membership_events = list(
        session.exec(
            select(WorkspaceMembershipEvent)
            .where(WorkspaceMembershipEvent.workspace_id == workspace_id)
            .order_by(WorkspaceMembershipEvent.id)
        ).all()
    )

    tables: dict[str, list[dict[str, Any]]] = {
        "users": _dump_rows(users),
        "workspace_memberships": _dump_rows(memberships),
        # Invitation token hashes are bearer-credential material. Export the
        # lifecycle and intended access without making invitations portable.
        "workspace_invitations": _dump_rows(
            invitations,
            exclude={"token_hash"},
        ),
        "workspace_membership_events": _dump_rows(membership_events),
        "projects": _dump_rows(projects),
        "subprojects": _dump_rows(subprojects),
        "tickets": _dump_rows(tickets),
        "ticket_dependencies": _dump_rows(dependencies),
        "comments": _dump_rows(comments),
        "audit_logs": _dump_rows(audit_logs),
        "knowledge_nodes": _dump_rows(knowledge_nodes),
        "knowledge_proposals": _dump_rows(proposals),
        "agent_sessions": _dump_rows(agent_sessions),
        # API-key hashes are authentication credentials and are deliberately
        # excluded from portable tenant exports. Full encrypted DB backups
        # retain them for disaster recovery.
        "api_keys": _dump_rows(api_keys, exclude={"key_hash"}),
        "api_key_projects": _dump_rows(api_key_projects),
        "workspace_lifecycle_events": _dump_rows(lifecycle_events),
    }
    for model in (ProjectMembership, ProjectInvitation, ProjectAccessEvent):
        rows = list(
            session.exec(
                select(model)
                .where(model.project_id.in_(project_ids))
                .order_by(model.id)
            ).all()
        )
        tables[model.__tablename__] = _dump_rows(rows, exclude={"token_hash"})
    participant_ids = {row["user_id"] for row in tables["projectmembership"]}
    tables["project_participants"] = [
        {"id": person.id, "name": person.name}
        for person in session.exec(
            select(User).where(User.id.in_(participant_ids)).order_by(User.id)
        ).all()
    ]
    counts = {name: len(rows) for name, rows in tables.items()}
    payload = {
        "format": EXPORT_FORMAT,
        "exported_at": f"{utcnow().isoformat()}Z",
        "workspace": workspace.model_dump(mode="json"),
        "record_counts": counts,
        "tables": tables,
        "exclusions": [
            "API key and workspace invitation token hashes and full values",
            "browser sessions, which are account-scoped rather than workspace-scoped",
            "server configuration, OAuth secrets, database credentials, and backup keys",
        ],
    }
    content = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return content, hashlib.sha256(content).hexdigest(), counts
