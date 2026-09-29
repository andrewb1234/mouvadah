"""Workspace-admin connection controls and project-scoped GitHub evidence."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from api import github_app as provider
from api.auth import CurrentUser
from api.authorization import require_project, require_ticket, require_workspace
from api.dependencies import SessionDep
from api.github_sync import refresh_repository
from api.models.entities import (GitHubConnection, GitHubConnectState, GitHubDelivery,
                                 GitHubRepository, GitHubTicketLink, Subproject)
from api.security import get_api_key_authorization
from api.utils.time import utcnow

router = APIRouter(prefix="/github", tags=["github"])
webhook_router = APIRouter()


def admin(session, user, workspace_id, *, write=False):
    if get_api_key_authorization() is not None:
        raise HTTPException(403, "GitHub connection settings require an interactive browser session.")
    return require_workspace(session, user, workspace_id, admin=True, write=write)


def connection_for(session, workspace_id, installation_id, *, lock=False):
    query = select(GitHubConnection).where(GitHubConnection.installation_id == installation_id,
                                          GitHubConnection.workspace_id == workspace_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.exec(query).first()
    if row is None:
        raise HTTPException(404, "GitHub connection not found.")
    return row


@router.get("/workspaces/{workspace_id}")
def settings(workspace_id: int, session: SessionDep, user: CurrentUser):
    admin(session, user, workspace_id)
    connections = session.exec(select(GitHubConnection).where(GitHubConnection.workspace_id == workspace_id)).all()
    ids = [row.installation_id for row in connections]
    repos = session.exec(select(GitHubRepository).where(GitHubRepository.installation_id.in_(ids))).all() if ids else []
    s = provider.get_settings()
    return {"enabled": provider.configured(), "install_url": f"https://github.com/apps/{s.github_app_slug}/installations/new" if provider.configured() else None,
            "connections": connections, "repositories": repos}


class ConnectInput(BaseModel):
    installation_id: int = Field(gt=0)


@router.post("/workspaces/{workspace_id}/connect")
def connect(workspace_id: int, payload: ConnectInput, session: SessionDep, user: CurrentUser):
    provider.require_configured()
    admin(session, user, workspace_id, write=True)
    state = secrets.token_urlsafe(32)
    session.add(GitHubConnectState(state_hash=hashlib.sha256(state.encode()).hexdigest(),
        workspace_id=workspace_id, user_id=user.id, installation_id=payload.installation_id,
        expires_at=utcnow() + timedelta(minutes=10)))
    session.commit()
    return {"authorization_url": provider.authorization_url(state)}


def callback_result(outcome: str, workspace_id: int | None = None):
    params = {"github": outcome}
    if workspace_id is not None:
        params["github_workspace"] = str(workspace_id)
    return RedirectResponse(provider.get_settings().public_origin() + "/app?" + urlencode(params) + "#profile", status_code=303)


@router.get("/callback")
def callback(session: SessionDep, user: CurrentUser, code: str | None = None,
             state: str | None = None, error: str | None = None):
    if not provider.configured():
        return callback_result("unavailable")
    if not state or len(state) > 256 or (code and len(code) > 1024):
        return callback_result("expired")
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    row = session.get(GitHubConnectState, state_hash)
    if row is None or row.user_id != user.id or row.used or row.expires_at <= utcnow():
        return callback_result("expired")
    try:
        admin(session, user, row.workspace_id, write=True)
    except HTTPException:
        return callback_result("access_changed")
    changed = session.exec(update(GitHubConnectState).where(
        GitHubConnectState.state_hash == state_hash, GitHubConnectState.used.is_(False),
        GitHubConnectState.expires_at > utcnow()).values(used=True))
    if changed.rowcount != 1:
        return callback_result("expired")
    workspace_id, installation_id = row.workspace_id, row.installation_id
    session.commit()  # a failed exchange requires fresh consent, never replay
    if error or not code:
        return callback_result("cancelled", workspace_id)
    try:
        token = provider.user_token(code)
        installation, repos = provider.authorized_repositories(token, installation_id)
        del token
        # Check app identity with an app-authenticated request as well.
        canonical = provider.installation_info(installation_id)
        if canonical.get("id") != installation_id or canonical.get("account", {}).get("id") != installation.get("account", {}).get("id"):
            raise provider.GitHubFailure("installation_not_authorized")
    except provider.GitHubFailure:
        return callback_result("authorization_failed", workspace_id)
    try:
        admin(session, user, workspace_id, write=True)
    except HTTPException:
        return callback_result("access_changed")
    existing = session.exec(select(GitHubConnection).where(
        GitHubConnection.installation_id == installation_id).with_for_update()).first()
    if existing and existing.workspace_id != workspace_id:
        return callback_result("already_connected", workspace_id)
    connection = existing or GitHubConnection(installation_id=installation_id, workspace_id=workspace_id,
        account_login=installation["account"]["login"], account_id=installation["account"]["id"],
        allowed_repositories=repos, connected_by=user.id)
    connection.allowed_repositories = repos
    connection.status = "active"
    connection.last_error = None
    connection.next_sync_at = utcnow()
    connection.sync_cursor = 0
    connection.link_cursor = 0
    connection.cycle_started_at = None
    connection.connected_by = user.id
    session.add(connection)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return callback_result("already_connected", workspace_id)
    return callback_result("connected", workspace_id)


@router.delete("/workspaces/{workspace_id}/connections/{installation_id}", status_code=204)
def disconnect(workspace_id: int, installation_id: int, session: SessionDep, user: CurrentUser):
    admin(session, user, workspace_id, write=True)
    connection = connection_for(session, workspace_id, installation_id, lock=True)
    connection.status = "disconnected"
    connection.allowed_repositories = []
    connection.updated_at = utcnow()
    session.add(connection)
    session.exec(update(GitHubDelivery).where(GitHubDelivery.installation_id == installation_id,
                 GitHubDelivery.status == "pending").values(status="ignored", completed_at=utcnow()))
    session.exec(delete(GitHubConnectState).where(GitHubConnectState.workspace_id == workspace_id,
                 GitHubConnectState.installation_id == installation_id))
    session.commit()


@router.post("/workspaces/{workspace_id}/connections/{installation_id}/reconcile", status_code=202)
def queue_reconcile(workspace_id: int, installation_id: int, session: SessionDep, user: CurrentUser):
    provider.require_configured()
    admin(session, user, workspace_id, write=True)
    connection = connection_for(session, workspace_id, installation_id, lock=True)
    if connection.status == "disconnected":
        raise HTTPException(409, "Reconnect this installation first.")
    if connection.last_error and connection.next_sync_at > utcnow():
        raise HTTPException(429, "GitHub is in retry backoff. Try after the next scheduled sync.")
    connection.next_sync_at = utcnow()
    session.add(connection)
    session.commit()
    return {"status": "queued"}


class MapInput(BaseModel):
    installation_id: int = Field(gt=0)
    repository_id: int = Field(gt=0)
    project_id: int = Field(gt=0)


@router.post("/workspaces/{workspace_id}/repositories")
def map_repository(workspace_id: int, payload: MapInput, session: SessionDep, user: CurrentUser):
    provider.require_configured()
    admin(session, user, workspace_id, write=True)
    project = require_project(session, user, payload.project_id, admin=True)
    if project.workspace_id != workspace_id:
        raise HTTPException(404, "Project not found.")
    connection = connection_for(session, workspace_id, payload.installation_id, lock=True)
    if connection.status != "active":
        raise HTTPException(409, "Reconnect or reconcile this installation first.")
    row = session.get(GitHubRepository, payload.repository_id)
    if row and (row.installation_id != payload.installation_id or row.project_id != payload.project_id):
        raise HTTPException(409, "Repository is already mapped. Unmap it before reassignment.")
    row = row or GitHubRepository(repository_id=payload.repository_id, installation_id=payload.installation_id,
                                  project_id=payload.project_id, full_name="")
    try:
        refresh_repository(connection, row)
    except provider.GitHubFailure as exc:
        raise HTTPException(400, f"Repository cannot be mapped: {exc.code}.") from None
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Repository was mapped concurrently. Reload settings.") from None
    session.refresh(row)
    return row


@router.delete("/workspaces/{workspace_id}/repositories/{repository_id}", status_code=204)
def unmap_repository(workspace_id: int, repository_id: int, session: SessionDep, user: CurrentUser):
    admin(session, user, workspace_id, write=True)
    row = session.get(GitHubRepository, repository_id)
    if row is None:
        raise HTTPException(404, "Repository not found.")
    connection_for(session, workspace_id, row.installation_id, lock=True)
    session.exec(delete(GitHubTicketLink).where(GitHubTicketLink.repository_id == repository_id))
    session.delete(row)
    session.commit()


@router.get("/projects/{project_id}/repositories")
def project_repositories(project_id: int, session: SessionDep, user: CurrentUser):
    require_project(session, user, project_id)
    return session.exec(select(GitHubRepository).where(GitHubRepository.project_id == project_id)).all()


@router.get("/tickets/{ticket_id}/links")
def ticket_links(ticket_id: int, session: SessionDep, user: CurrentUser):
    require_ticket(session, user, ticket_id)
    rows = session.exec(select(GitHubTicketLink, GitHubRepository, GitHubConnection)
        .join(GitHubRepository, GitHubRepository.repository_id == GitHubTicketLink.repository_id)
        .join(GitHubConnection, GitHubConnection.installation_id == GitHubRepository.installation_id)
        .where(GitHubTicketLink.ticket_id == ticket_id)).all()
    return [{**link.model_dump(), "repository_name": repo.full_name, "connection_status": connection.status, "repository_status": repo.status}
            for link, repo, connection in rows]


class LinkInput(BaseModel):
    repository_id: int = Field(gt=0)
    kind: Literal["pull", "issue"]
    number: int = Field(gt=0)


@router.post("/tickets/{ticket_id}/links")
def link_ticket(ticket_id: int, payload: LinkInput, session: SessionDep, user: CurrentUser):
    provider.require_configured()
    ticket = require_ticket(session, user, ticket_id, write=True)
    subproject = session.get(Subproject, ticket.subproject_id)
    repo = session.get(GitHubRepository, payload.repository_id)
    if repo is None or repo.project_id != subproject.project_id:
        raise HTTPException(404, "Mapped repository not found for this project.")
    connection = session.exec(select(GitHubConnection).where(
        GitHubConnection.installation_id == repo.installation_id).with_for_update()).one()
    if connection.status != "active":
        raise HTTPException(409, "GitHub connection is unavailable. Ask an administrator to reconnect.")
    link = session.exec(select(GitHubTicketLink).where(
        GitHubTicketLink.ticket_id == ticket_id, GitHubTicketLink.repository_id == payload.repository_id,
        GitHubTicketLink.kind == payload.kind, GitHubTicketLink.number == payload.number)).first()
    if link:
        return link
    try:
        token = refresh_repository(connection, repo)
        snapshot = provider.object_info(token, repo.full_name, payload.kind, payload.number)
    except provider.GitHubFailure as exc:
        raise HTTPException(400, f"GitHub link could not be read: {exc.code}.") from None
    link = GitHubTicketLink(ticket_id=ticket_id, repository_id=repo.repository_id, kind=payload.kind,
                           number=payload.number, snapshot=snapshot, synced_at=utcnow())
    session.add(repo)
    session.add(link)
    session.commit()
    session.refresh(link)
    return link


@router.delete("/tickets/{ticket_id}/links/{link_id}", status_code=204)
def unlink_ticket(ticket_id: int, link_id: int, session: SessionDep, user: CurrentUser):
    require_ticket(session, user, ticket_id, write=True)
    link = session.get(GitHubTicketLink, link_id)
    if link is None or link.ticket_id != ticket_id:
        raise HTTPException(404, "GitHub link not found.")
    session.delete(link)
    session.commit()


@webhook_router.post("/integrations/github/webhook", status_code=202)
async def webhook(request: Request, session: SessionDep):
    settings = provider.require_configured()
    # Enforce a stream bound as well as the global Content-Length/body guard.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > settings.max_request_body_bytes:
            raise HTTPException(413, "Webhook body too large.")
    signature = request.headers.get("x-hub-signature-256", "")
    if not re.fullmatch(r"sha256=[0-9a-f]{64}", signature):
        raise HTTPException(401, "Invalid webhook signature.")
    valid = False
    for secret in settings.github_webhook_secrets:
        expected = "sha256=" + hmac.new(secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest()
        valid |= hmac.compare_digest(expected, signature)
    if not valid:
        raise HTTPException(401, "Invalid webhook signature.")
    delivery_id = request.headers.get("x-github-delivery", "")
    if not re.fullmatch(r"[a-zA-Z0-9-]{1,128}", delivery_id):
        raise HTTPException(400, "Invalid delivery ID.")
    event = request.headers.get("x-github-event", "")
    if event not in {"ping", "installation", "installation_repositories", "repository", "pull_request", "issues"}:
        return {"status": "ignored"}
    try:
        payload = json.loads(body)
        installation_id = int(payload.get("installation", {}).get("id", 0))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "Invalid webhook payload.") from None
    fingerprint = hashlib.sha256(body).hexdigest()
    existing = session.get(GitHubDelivery, delivery_id)
    if existing:
        if existing.payload_sha256 != fingerprint or existing.event != event:
            raise HTTPException(409, "Delivery ID reused with different content.")
        return {"status": "duplicate"}
    connection = session.exec(select(GitHubConnection).where(
        GitHubConnection.installation_id == installation_id).with_for_update()).first()
    active = connection is not None and connection.status != "disconnected"
    session.add(GitHubDelivery(delivery_id=delivery_id, installation_id=installation_id,
        workspace_id=connection.workspace_id if connection else None,
        event=event, payload_sha256=fingerprint, status="pending" if active else "ignored"))
    if active and not connection.last_error:
        connection.next_sync_at = utcnow()
        session.add(connection)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.get(GitHubDelivery, delivery_id)
        if not existing or existing.payload_sha256 != fingerprint or existing.event != event:
            raise HTTPException(409, "Delivery ID conflict.") from None
        return {"status": "duplicate"}
    return {"status": "queued" if active else "ignored"}
