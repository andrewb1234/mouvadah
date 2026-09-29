"""Durable webhook reconciliation. Run periodically: python -m api.github_sync.

Provider state is read under a connection lock, not copied from event payloads.
Bounded batches resume with a cursor; receipt completion and changes commit
together. Unexpected failure rolls back the batch for the next worker.
"""
from __future__ import annotations

import argparse
from datetime import timedelta

from sqlalchemy import delete, update
from sqlmodel import Session, select

from api import github_app as provider
from api.models.entities import (GitHubConnection, GitHubConnectState, GitHubDelivery,
                                 GitHubRepository, GitHubTicketLink, Workspace)
from api.utils.time import utcnow


def refresh_repository(connection: GitHubConnection, repo: GitHubRepository) -> str:
    if repo.repository_id not in {r["id"] for r in connection.allowed_repositories}:
        raise provider.GitHubFailure("repository_not_authorized")
    token = provider.installation_token(connection.installation_id, repo.repository_id)
    data = provider.repository_info(token, repo.repository_id)
    # Transfers across owners require a fresh human connection, even when an
    # installation token unexpectedly retains access to the numeric repo ID.
    if data.get("owner", {}).get("id") != connection.account_id:
        raise provider.GitHubFailure("repository_transferred")
    repo.full_name = data["full_name"]
    repo.status = "active"
    return token


def reconcile(session: Session, installation_id: int, *, batch_size: int = 1) -> bool:
    connection = session.exec(select(GitHubConnection).where(
        GitHubConnection.installation_id == installation_id
    ).with_for_update(skip_locked=True).execution_options(populate_existing=True)).first()
    if connection is None or connection.status == "disconnected":
        return False
    workspace = session.get(Workspace, connection.workspace_id)
    if workspace is None or workspace.deletion_requested_at:
        return False
    now = utcnow()
    if connection.next_sync_at > now:
        return False
    try:
        info = provider.installation_info(installation_id)
        if info.get("id") != installation_id or info.get("account", {}).get("id") != connection.account_id:
            raise provider.GitHubFailure("installation_not_authorized")
        if info.get("suspended_at"):
            raise provider.GitHubFailure("installation_suspended", 300)
        if connection.cycle_started_at is None:
            connection.cycle_started_at = now
        repos = list(session.exec(select(GitHubRepository).where(
            GitHubRepository.installation_id == installation_id,
            GitHubRepository.repository_id > connection.sync_cursor,
        ).order_by(GitHubRepository.repository_id).limit(batch_size)).all())
        for repo in repos:
            try:
                token = refresh_repository(connection, repo)
                links = list(session.exec(select(GitHubTicketLink).where(
                    GitHubTicketLink.repository_id == repo.repository_id,
                    GitHubTicketLink.id > connection.link_cursor,
                ).order_by(GitHubTicketLink.id).limit(10)).all())
                for link in links:
                    try:
                        link.snapshot = provider.object_info(token, repo.full_name, link.kind, link.number)
                    except provider.GitHubFailure as exc:
                        if exc.code != "access_revoked":
                            raise
                        link.snapshot = {**link.snapshot, "state": "unavailable"}
                    link.synced_at = now
                    session.add(link)
                    connection.link_cursor = link.id
                if len(links) < 10:
                    connection.link_cursor = 0
            except provider.GitHubFailure as exc:
                if exc.code not in {"access_revoked", "repository_not_authorized", "repository_transferred"}:
                    raise
                repo.status = exc.code
                connection.link_cursor = 0
                # Old evidence remains visible but explicitly unavailable.
                for link in session.exec(select(GitHubTicketLink).where(GitHubTicketLink.repository_id == repo.repository_id)).all():
                    link.snapshot = {**link.snapshot, "state": "unavailable"}
                    session.add(link)
            session.add(repo)
            if connection.link_cursor:
                break
            connection.sync_cursor = repo.repository_id
        connection.status = "active"
        connection.last_error = None
        connection.updated_at = now
        remaining = session.exec(select(GitHubRepository.repository_id).where(
            GitHubRepository.installation_id == installation_id,
            GitHubRepository.repository_id > connection.sync_cursor)).first()
        if remaining is None and connection.link_cursor == 0:
            session.exec(update(GitHubDelivery).where(
                GitHubDelivery.installation_id == installation_id,
                GitHubDelivery.workspace_id == connection.workspace_id,
                GitHubDelivery.status == "pending",
                GitHubDelivery.received_at <= connection.cycle_started_at,
            ).values(status="processed", completed_at=now))
            connection.sync_cursor = 0
            connection.cycle_started_at = None
            pending = session.exec(select(GitHubDelivery.delivery_id).where(
                GitHubDelivery.installation_id == installation_id,
                GitHubDelivery.status == "pending")).first()
            connection.next_sync_at = now if pending else now + timedelta(minutes=5)
        else:
            connection.next_sync_at = now
        session.add(connection)
        session.commit()
        return True
    except provider.GitHubFailure as exc:
        # Revert partial snapshots; keep durable receipts pending for retry.
        session.rollback()
        connection = session.exec(select(GitHubConnection).where(
            GitHubConnection.installation_id == installation_id
        ).with_for_update().execution_options(populate_existing=True)).first()
        if connection is None or connection.status == "disconnected":
            return False
        connection.last_error = exc.code
        connection.status = "revoked" if exc.code in {"access_revoked", "installation_suspended", "installation_not_authorized"} else "error"
        connection.next_sync_at = now + timedelta(seconds=exc.retry_after)
        connection.updated_at = now
        session.add(connection)
        session.commit()
        return False


def run_once(engine, limit: int = 10) -> dict:
    provider.require_configured()
    with Session(engine) as session:
        ids = session.exec(select(GitHubConnection.installation_id).join(
            Workspace, Workspace.id == GitHubConnection.workspace_id
        ).where(GitHubConnection.status != "disconnected",
                GitHubConnection.next_sync_at <= utcnow(),
                Workspace.deletion_requested_at.is_(None))
          .order_by(GitHubConnection.next_sync_at).limit(limit)).all()
    completed = 0
    for installation_id in ids:
        with Session(engine) as session:
            completed += int(reconcile(session, installation_id))
    with Session(engine) as session:
        session.exec(delete(GitHubConnectState).where(GitHubConnectState.expires_at < utcnow()))
        # Pending failed deliveries are retained until repaired/disconnected.
        session.exec(delete(GitHubDelivery).where(GitHubDelivery.status != "pending",
                     GitHubDelivery.received_at < utcnow() - timedelta(days=30)))
        session.commit()
    return {"attempted": len(ids), "completed_batches": completed}


if __name__ == "__main__":
    from api.database import engine
    from api.migrations.runtime import assert_database_current
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 101))
    args = parser.parse_args()
    assert_database_current(engine)
    print(run_once(engine, args.limit))
