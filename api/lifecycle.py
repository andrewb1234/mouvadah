"""Operator tooling for verified workspace purge after the recovery window."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from sqlalchemy import delete
from sqlmodel import Session, select

from api.database import engine
from api.migrations.runtime import (
    assert_database_current,
    assert_schema_matches_metadata,
)
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
    Subproject,
    Ticket,
    TicketDependency,
    Workspace,
    WorkspaceInvitation,
    WorkspaceLifecycleEvent,
    WorkspaceMembership,
)
from api.models.enums import WorkspaceLifecycleAction
from api.observability import configure_runtime, flush_telemetry, observe_job
from api.utils.time import utcnow


@dataclass(frozen=True)
class PurgeResult:
    workspace_id: int
    backup_evidence: str
    deleted_records: dict[str, int]


def _ids(values) -> list[int]:
    return [value for value in values if value is not None]


def _workspace_graph(
    session: Session,
    workspace_id: int,
) -> dict[str, list[int]]:
    project_ids = _ids(
        session.exec(
            select(Project.id).where(Project.workspace_id == workspace_id)
        ).all()
    )
    subproject_ids = (
        _ids(
            session.exec(
                select(Subproject.id).where(
                    Subproject.project_id.in_(project_ids)  # type: ignore[union-attr]
                )
            ).all()
        )
        if project_ids
        else []
    )
    ticket_ids = (
        _ids(
            session.exec(
                select(Ticket.id).where(
                    Ticket.subproject_id.in_(subproject_ids)  # type: ignore[union-attr]
                )
            ).all()
        )
        if subproject_ids
        else []
    )
    node_ids = (
        _ids(
            session.exec(
                select(KnowledgeNode.id).where(
                    KnowledgeNode.project_id.in_(project_ids)  # type: ignore[union-attr]
                )
            ).all()
        )
        if project_ids
        else []
    )
    api_key_ids = _ids(
        session.exec(select(ApiKey.id).where(ApiKey.workspace_id == workspace_id)).all()
    )
    return {
        "project": project_ids,
        "subproject": subproject_ids,
        "ticket": ticket_ids,
        "knowledgenode": node_ids,
        "apikey": api_key_ids,
    }


def purge_workspace(
    session: Session,
    workspace_id: int,
    *,
    backup_evidence: str,
) -> PurgeResult:
    """Permanently remove one expired workspace and verify the sweep."""
    evidence = backup_evidence.strip()
    if len(evidence) < 8:
        raise ValueError("Verified backup evidence is required before permanent purge.")
    workspace = session.exec(
        select(Workspace).where(Workspace.id == workspace_id).with_for_update()
    ).first()
    now = utcnow()
    if workspace is None:
        raise ValueError(f"Workspace {workspace_id} does not exist.")
    if workspace.purge_after is None or workspace.deletion_requested_at is None:
        raise ValueError(f"Workspace {workspace_id} is not scheduled for deletion.")
    if workspace.purge_after > now:
        raise ValueError(f"Workspace {workspace_id} recovery window has not expired.")

    graph = _workspace_graph(session, workspace_id)
    project_ids = graph["project"]
    subproject_ids = graph["subproject"]
    ticket_ids = graph["ticket"]
    node_ids = graph["knowledgenode"]
    api_key_ids = graph["apikey"]

    deleted_records = {
        "workspace": 1,
        "workspace_memberships": len(
            session.exec(
                select(WorkspaceMembership.id).where(
                    WorkspaceMembership.workspace_id == workspace_id
                )
            ).all()
        ),
        "workspace_invitations": len(
            session.exec(
                select(WorkspaceInvitation.id).where(
                    WorkspaceInvitation.workspace_id == workspace_id
                )
            ).all()
        ),
        "projects": len(project_ids),
        "subprojects": len(subproject_ids),
        "tickets": len(ticket_ids),
        "ticket_dependencies": 0,
        "comments": 0,
        "audit_logs": 0,
        "knowledge_nodes": len(node_ids),
        "knowledge_proposals": 0,
        "agent_sessions": 0,
        "api_keys": len(api_key_ids),
        "api_key_projects": 0,
    }
    if ticket_ids:
        deleted_records["ticket_dependencies"] = len(
            session.exec(
                select(TicketDependency).where(
                    TicketDependency.ticket_id.in_(ticket_ids)  # type: ignore[union-attr]
                    | TicketDependency.depends_on_ticket_id.in_(ticket_ids)  # type: ignore[union-attr]
                )
            ).all()
        )
        deleted_records["comments"] = len(
            session.exec(
                select(Comment.id).where(
                    Comment.ticket_id.in_(ticket_ids)  # type: ignore[union-attr]
                )
            ).all()
        )
        deleted_records["audit_logs"] = len(
            session.exec(
                select(AuditLog.id).where(
                    AuditLog.ticket_id.in_(ticket_ids)  # type: ignore[union-attr]
                )
            ).all()
        )
    if node_ids:
        deleted_records["knowledge_proposals"] = len(
            session.exec(
                select(KnowledgeProposal.id).where(
                    KnowledgeProposal.node_id.in_(node_ids)  # type: ignore[union-attr]
                )
            ).all()
        )
    if project_ids:
        deleted_records["agent_sessions"] = len(
            session.exec(
                select(AgentSession.id).where(
                    AgentSession.project_id.in_(project_ids)  # type: ignore[union-attr]
                )
            ).all()
        )
    if api_key_ids:
        deleted_records["api_key_projects"] = len(
            session.exec(
                select(ApiKeyProject).where(
                    ApiKeyProject.api_key_id.in_(api_key_ids)  # type: ignore[union-attr]
                )
            ).all()
        )

    # Explicit child-first deletes work the same with SQLite's test setup and
    # PostgreSQL's immediate foreign-key constraints.
    from api.utils.github_cleanup import purge_workspace_connections
    purge_workspace_connections(session, workspace_id, project_ids)
    if api_key_ids:
        session.exec(
            delete(ApiKeyProject).where(ApiKeyProject.api_key_id.in_(api_key_ids))
        )
    if ticket_ids:
        session.exec(
            delete(TicketDependency).where(
                TicketDependency.ticket_id.in_(ticket_ids)
                | TicketDependency.depends_on_ticket_id.in_(ticket_ids)
            )
        )
        session.exec(delete(Comment).where(Comment.ticket_id.in_(ticket_ids)))
        session.exec(delete(AuditLog).where(AuditLog.ticket_id.in_(ticket_ids)))
    if node_ids:
        session.exec(
            delete(KnowledgeProposal).where(KnowledgeProposal.node_id.in_(node_ids))
        )
    if project_ids:
        session.exec(
            delete(AgentSession).where(AgentSession.project_id.in_(project_ids))
        )
    if ticket_ids:
        session.exec(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
    if subproject_ids:
        session.exec(delete(Subproject).where(Subproject.id.in_(subproject_ids)))
    if node_ids:
        session.exec(delete(KnowledgeNode).where(KnowledgeNode.id.in_(node_ids)))
    if api_key_ids:
        session.exec(delete(ApiKey).where(ApiKey.id.in_(api_key_ids)))
    if project_ids:
        for model in (ProjectInvitation, ProjectMembership):
            count = len(
                session.exec(
                    select(model.id).where(model.project_id.in_(project_ids))
                ).all()
            )
            deleted_records[model.__tablename__] = count
            session.exec(delete(model).where(model.project_id.in_(project_ids)))
        session.exec(delete(Project).where(Project.id.in_(project_ids)))
    session.exec(
        delete(WorkspaceInvitation).where(
            WorkspaceInvitation.workspace_id == workspace_id
        )
    )
    session.exec(
        delete(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id
        )
    )
    session.exec(delete(Workspace).where(Workspace.id == workspace_id))
    session.add(
        WorkspaceLifecycleEvent(
            workspace_id=workspace_id,
            action=WorkspaceLifecycleAction.PURGED,
            actor_user_id=None,
            details={
                "backup_evidence": evidence[:500],
                "deleted_records": deleted_records,
                "purged_at": f"{now.isoformat()}Z",
            },
        )
    )
    session.flush()
    if session.get(Workspace, workspace_id) is not None:
        raise RuntimeError(f"Workspace {workspace_id} still exists after purge.")
    session.commit()
    return PurgeResult(
        workspace_id=workspace_id,
        backup_evidence=evidence,
        deleted_records=deleted_records,
    )


def purge_due_workspaces(
    session: Session,
    *,
    backup_evidence: str,
    workspace_id: int | None = None,
    dry_run: bool = False,
) -> list[PurgeResult | dict[str, object]]:
    now = utcnow()
    query = select(Workspace).where(
        Workspace.deletion_requested_at.is_not(None),
        Workspace.purge_after.is_not(None),
        Workspace.purge_after <= now,
    )
    if workspace_id is not None:
        query = query.where(Workspace.id == workspace_id)
    due = list(session.exec(query.order_by(Workspace.id)).all())
    if dry_run:
        return [
            {
                "workspace_id": workspace.id,
                "purge_after": (
                    f"{workspace.purge_after.isoformat()}Z"
                    if workspace.purge_after
                    else None
                ),
            }
            for workspace in due
        ]
    if len(backup_evidence.strip()) < 8:
        raise ValueError("Verified backup evidence is required before permanent purge.")
    return [
        purge_workspace(
            session,
            workspace.id,  # type: ignore[arg-type]
            backup_evidence=backup_evidence,
        )
        for workspace in due
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mouvadah workspace data-lifecycle operator controls."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    purge = subparsers.add_parser(
        "purge-due",
        help="purge workspaces whose recovery window has expired",
    )
    purge.add_argument("--backup-evidence", default="")
    purge.add_argument("--workspace-id", type=int)
    purge.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_runtime(get_settings())
    try:
        with observe_job("workspace_purge"):
            assert_database_current(engine)
            assert_schema_matches_metadata(engine)
            if args.command == "purge-due":
                with Session(engine) as session:
                    results = purge_due_workspaces(
                        session,
                        backup_evidence=args.backup_evidence,
                        workspace_id=args.workspace_id,
                        dry_run=args.dry_run,
                    )
                print(
                    json.dumps(
                        [
                            (
                                asdict(result)
                                if isinstance(result, PurgeResult)
                                else result
                            )
                            for result in results
                        ],
                        sort_keys=True,
                    )
                )
                return 0
    finally:
        flush_telemetry()
    raise RuntimeError(f"Unsupported lifecycle command {args.command!r}.")


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
