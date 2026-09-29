"""Explicit GitHub child cleanup, including SQLite without FK enforcement."""
from sqlalchemy import delete, update
from sqlmodel import select
from api.models.entities import (GitHubConnection, GitHubConnectState, GitHubDelivery,
                                 GitHubRepository, GitHubTicketLink)


def delete_ticket_links(session, ticket_ids):
    if ticket_ids:
        session.exec(delete(GitHubTicketLink).where(GitHubTicketLink.ticket_id.in_(ticket_ids)))


def delete_project_repositories(session, project_ids):
    repo_ids = session.exec(select(GitHubRepository.repository_id).where(GitHubRepository.project_id.in_(project_ids))).all()
    if repo_ids:
        session.exec(delete(GitHubTicketLink).where(GitHubTicketLink.repository_id.in_(repo_ids)))
        session.exec(delete(GitHubRepository).where(GitHubRepository.repository_id.in_(repo_ids)))


def disconnect_workspace(session, workspace_id):
    session.exec(update(GitHubConnection).where(GitHubConnection.workspace_id == workspace_id)
                 .values(status="disconnected", allowed_repositories=[]))
    session.exec(delete(GitHubConnectState).where(GitHubConnectState.workspace_id == workspace_id))
    session.exec(update(GitHubDelivery).where(GitHubDelivery.workspace_id == workspace_id,
                 GitHubDelivery.status == "pending").values(status="ignored"))


def purge_workspace_connections(session, workspace_id, project_ids):
    delete_project_repositories(session, project_ids)
    for model in [GitHubDelivery, GitHubConnectState, GitHubConnection]:
        session.exec(delete(model).where(model.workspace_id == workspace_id))
