"""GitHub tenancy, consent, delivery replay, reconciliation and lifecycle."""
import hashlib
import hmac
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlmodel import Session, select

from api import github_app as provider
from api.config import get_settings
from api.github_sync import reconcile
from api.models.entities import (GitHubConnection, GitHubConnectState, GitHubDelivery,
                                 GitHubRepository, GitHubTicketLink, Ticket)
from api.utils.time import utcnow

REAL_INSTALLATION_TOKEN = provider.installation_token


@pytest.fixture
def github(monkeypatch):
    for key, value in {
        "GITHUB_APP_ENABLED": "true", "GITHUB_APP_ID": "123", "GITHUB_APP_SLUG": "test-app",
        "GITHUB_APP_PRIVATE_KEY": "fake-private-key", "GITHUB_APP_CLIENT_ID": "client",
        "GITHUB_APP_CLIENT_SECRET": "secret", "GITHUB_WEBHOOK_SECRETS": json.dumps(["new-hook-secret" * 3, "old-hook-secret" * 3]),
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    installation = {"id": 11, "account": {"id": 22, "login": "example"}, "suspended_at": None}
    monkeypatch.setattr(provider, "user_token", lambda code: "ephemeral-user-token")
    monkeypatch.setattr(provider, "authorized_repositories", lambda token, iid: (installation, [{"id": 33, "full_name": "example/repo"}]))
    monkeypatch.setattr(provider, "installation_info", lambda iid: installation)
    monkeypatch.setattr(provider, "installation_token", lambda iid, rid: "ephemeral-install-token")
    monkeypatch.setattr(provider, "repository_info", lambda token, rid: {"id": rid, "full_name": "example/repo", "owner": {"id": 22}})
    snapshot = {"title": "Change", "state": "open", "url": "https://github.com/example/repo/pull/1", "head_sha": "a" * 40}
    monkeypatch.setattr(provider, "object_info", lambda *args: dict(snapshot))
    return snapshot


def graph(client, headers=None):
    p = client.post("/api/v1/projects", json={"name": "GitHub evidence"}, headers=headers).json()
    s = client.post(f"/api/v1/projects/{p['id']}/subprojects", json={"name": "Delivery"}, headers=headers).json()
    t = client.post(f"/api/v1/subprojects/{s['id']}/tickets", json={"title": "Change"}, headers=headers).json()
    return p, t


def connect(client, workspace_id, headers=None):
    response = client.post(f"/api/v1/github/workspaces/{workspace_id}/connect", json={"installation_id": 11}, headers=headers)
    assert response.status_code == 200, response.text
    state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
    response = client.get("/api/v1/github/callback", params={"code": "auth-code", "state": state}, headers=headers, follow_redirects=False)
    assert response.status_code == 303, response.text
    return state


def mapped(client, github, headers=None):
    p, t = graph(client, headers)
    connect(client, p["workspace_id"], headers)
    response = client.post(f"/api/v1/github/workspaces/{p['workspace_id']}/repositories", headers=headers,
        json={"installation_id": 11, "repository_id": 33, "project_id": p["id"]})
    assert response.status_code == 200, response.text
    response = client.post(f"/api/v1/github/tickets/{t['id']}/links", headers=headers,
        json={"repository_id": 33, "kind": "pull", "number": 1})
    assert response.status_code == 200, response.text
    return p, t, response.json()


def hook(client, *, delivery="delivery-1", secret="new-hook-secret" * 3, payload=None, event="pull_request"):
    body = json.dumps(payload or {"installation": {"id": 11}, "action": "opened"}).encode()
    return client.post("/integrations/github/webhook", content=body, headers={
        "x-github-delivery": delivery, "x-github-event": event,
        "x-hub-signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
        "content-type": "application/json",
    })


def test_disabled_and_oauth_single_use(client, engine, github, monkeypatch):
    p, _ = graph(client)
    monkeypatch.setenv("GITHUB_APP_ENABLED", "false"); get_settings.cache_clear()
    assert client.get(f"/api/v1/github/workspaces/{p['workspace_id']}").json()["enabled"] is False
    assert client.post(f"/api/v1/github/workspaces/{p['workspace_id']}/connect", json={"installation_id": 11}).status_code == 503
    monkeypatch.setenv("GITHUB_APP_ENABLED", "true"); get_settings.cache_clear()
    state = connect(client, p["workspace_id"])
    replay = client.get("/api/v1/github/callback", params={"code": "auth-code", "state": state}, follow_redirects=False)
    assert replay.status_code == 303 and "github=expired" in replay.headers["location"]
    with Session(engine) as session:
        rows = session.exec(select(GitHubConnectState)).all()
        assert len(rows) == 1 and rows[0].state_hash != state and rows[0].used
        exported = client.get(f"/api/v1/workspaces/{p['workspace_id']}/export").text
        assert "ephemeral" not in exported and state not in exported


def test_cross_workspace_and_scoped_links(multi_user_client, github):
    client, _ = multi_user_client
    p, t, link = mapped(client, github)
    bob = {"x-test-user": "bob"}
    for path in [f"workspaces/{p['workspace_id']}", f"projects/{p['id']}/repositories", f"tickets/{t['id']}/links"]:
        assert client.get(f"/api/v1/github/{path}", headers=bob).status_code == 404
    other, other_ticket = graph(client, bob)
    assert client.post(f"/api/v1/github/tickets/{other_ticket['id']}/links", headers=bob,
        json={"repository_id": 33, "kind": "pull", "number": 1}).status_code == 404
    response = client.post(f"/api/v1/github/workspaces/{other['workspace_id']}/connect", headers=bob, json={"installation_id": 11})
    state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
    conflict = client.get("/api/v1/github/callback", headers=bob, params={"code": "valid", "state": state}, follow_redirects=False)
    assert conflict.status_code == 303 and "github=already_connected" in conflict.headers["location"]


def test_raw_signature_rotation_and_duplicate_receipt(client, github, engine):
    p, t, _ = mapped(client, github)
    assert hook(client, secret="wrong").status_code == 401
    assert hook(client, secret="old-hook-secret" * 3).json() == {"status": "queued"}
    assert hook(client).json() == {"status": "duplicate"}
    assert hook(client, payload={"installation": {"id": 11}, "action": "closed"}).status_code == 409
    with Session(engine) as session:
        rows = session.exec(select(GitHubDelivery)).all()
        assert len(rows) == 1 and rows[0].workspace_id == p["workspace_id"]
        assert rows[0].status == "pending"
        assert session.get(Ticket, t["id"]).status.value == "TODO"


def test_reconciliation_reads_current_state_not_old_event(client, github, engine):
    p, t, link = mapped(client, github)
    github["state"] = "merged"; github["head_sha"] = "b" * 40
    hook(client, payload={"installation": {"id": 11}, "action": "opened", "pull_request": {"state": "open"}})
    with Session(engine) as session:
        assert reconcile(session, 11)
        row = session.get(GitHubTicketLink, link["id"])
        assert row.snapshot["state"] == "merged" and row.snapshot["head_sha"] == "b" * 40
        assert session.get(Ticket, t["id"]).status.value == "TODO"
        assert session.get(GitHubDelivery, "delivery-1").status == "processed"
    hook(client, delivery="late-old-event")
    with Session(engine) as session:
        assert reconcile(session, 11)
        assert session.get(GitHubTicketLink, link["id"]).snapshot["state"] == "merged"


def test_provider_failure_is_retryable_without_partial_updates(client, github, engine, monkeypatch):
    p, t, link = mapped(client, github)
    hook(client)
    def fail(*args):
        raise provider.GitHubFailure("provider_rate_or_permission_limit", 900)
    monkeypatch.setattr(provider, "object_info", fail)
    with Session(engine) as session:
        assert not reconcile(session, 11)
        connection = session.get(GitHubConnection, 11)
        assert connection.last_error == "provider_rate_or_permission_limit"
        assert connection.next_sync_at > utcnow() + timedelta(minutes=10)
        assert session.get(GitHubDelivery, "delivery-1").status == "pending"
        assert session.get(GitHubTicketLink, link["id"]).snapshot["state"] == "open"
    hook(client, delivery="retry-storm")
    with Session(engine) as session:
        assert not reconcile(session, 11)  # hook cannot erase the backoff
        connection = session.get(GitHubConnection, 11)
        connection.next_sync_at = utcnow(); session.add(connection); session.commit()
    monkeypatch.setattr(provider, "object_info", lambda *args: {**github, "state": "closed"})
    with Session(engine) as session:
        assert reconcile(session, 11)
        assert session.get(GitHubDelivery, "delivery-1").status == "processed"


def test_revocation_disconnect_and_repository_transfer(client, github, engine, monkeypatch):
    p, t, link = mapped(client, github)
    def revoked(*args):
        raise provider.GitHubFailure("access_revoked")
    monkeypatch.setattr(provider, "installation_info", revoked)
    with Session(engine) as session:
        assert not reconcile(session, 11)
        assert session.get(GitHubConnection, 11).status == "revoked"
    assert client.post(f"/api/v1/github/tickets/{t['id']}/links", json={"repository_id": 33, "kind": "pull", "number": 2}).status_code == 409
    assert client.delete(f"/api/v1/github/workspaces/{p['workspace_id']}/connections/11").status_code == 204
    assert hook(client).json()["status"] == "ignored"
    with Session(engine) as session:
        assert not reconcile(session, 11)


def test_repo_transfer_fails_closed(client, github, engine, monkeypatch):
    p, t, link = mapped(client, github)
    monkeypatch.setattr(provider, "repository_info", lambda *args: {"id": 33, "full_name": "other/repo", "owner": {"id": 99}})
    with Session(engine) as session:
        assert reconcile(session, 11)
        assert session.get(GitHubRepository, 33).status == "repository_transferred"
        assert session.get(GitHubTicketLink, link["id"]).snapshot["state"] == "unavailable"


@pytest.mark.parametrize("target", ["ticket", "project"])
def test_deletion_cleans_links_on_sqlite(client, github, engine, target):
    p, t, link = mapped(client, github)
    path = f"/api/v1/tickets/{t['id']}" if target == "ticket" else f"/api/v1/projects/{p['id']}"
    assert client.delete(path).status_code == 204
    with Session(engine) as session:
        assert session.exec(select(GitHubTicketLink)).all() == []
        if target == "project":
            assert session.exec(select(GitHubRepository)).all() == []


def test_non_admin_repository_is_not_delegated(monkeypatch):
    def pages(path, token, key):
        if key == "installations":
            return [{"id": 11}]
        return [{"id": 33, "full_name": "example/repo", "permissions": {"pull": True, "admin": False}}]
    monkeypatch.setattr(provider, "paginated", pages)
    with pytest.raises(provider.GitHubFailure, match="repository_admin_required"):
        provider.authorized_repositories("user-token", 11)


def test_cancelled_authorization_returns_workspace_context(client, github):
    p, _ = graph(client)
    response = client.post(f"/api/v1/github/workspaces/{p['workspace_id']}/connect", json={"installation_id": 11})
    state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
    response = client.get("/api/v1/github/callback", params={"error": "access_denied", "state": state}, follow_redirects=False)
    assert response.status_code == 303
    assert f"github=cancelled&github_workspace={p['workspace_id']}" in response.headers["location"]
    assert state not in response.headers["location"]


def test_expired_state_never_calls_github(client, github, engine, monkeypatch):
    p, _ = graph(client)
    response = client.post(f"/api/v1/github/workspaces/{p['workspace_id']}/connect", json={"installation_id": 11})
    state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
    with Session(engine) as session:
        row = session.get(GitHubConnectState, hashlib.sha256(state.encode()).hexdigest())
        row.expires_at = utcnow() - timedelta(seconds=1); session.add(row); session.commit()
    monkeypatch.setattr(provider, "user_token", lambda _: pytest.fail("Expired consent must not exchange a code"))
    response = client.get("/api/v1/github/callback", params={"code": "code", "state": state}, follow_redirects=False)
    assert "github=expired" in response.headers["location"]


def test_api_key_cannot_configure_connection(enforce_auth_client, agent_headers, engine, github):
    client = enforce_auth_client
    from api.models.entities import ApiKey
    with Session(engine) as session:
        wid = session.exec(select(ApiKey.workspace_id)).first()
    assert client.post(f"/api/v1/github/workspaces/{wid}/connect", headers=agent_headers,
                       json={"installation_id": 11}).status_code == 403


def test_bounded_batches_resume_without_losing_receipts(client, github, engine):
    p, t, link = mapped(client, github)
    with Session(engine) as session:
        for number in range(2, 14):
            session.add(GitHubTicketLink(ticket_id=t["id"], repository_id=33, kind="pull", number=number))
        session.commit()
    hook(client)
    with Session(engine) as session:
        assert reconcile(session, 11)
        assert session.get(GitHubDelivery, "delivery-1").status == "pending"
        assert session.get(GitHubConnection, 11).link_cursor > 0
    with Session(engine) as session:
        assert reconcile(session, 11)
        assert session.get(GitHubDelivery, "delivery-1").status == "processed"
        links = session.exec(select(GitHubTicketLink)).all()
        assert len(links) == 13 and all(l.synced_at for l in links)


def test_workspace_deletion_disconnects_and_export_contains_evidence(client, github, engine):
    p, t, link = mapped(client, github)
    hook(client)
    export = client.get(f"/api/v1/workspaces/{p['workspace_id']}/export")
    tables = export.json()["tables"]
    assert len(tables["github_connections"]) == len(tables["github_ticket_links"]) == 1
    assert "githubconnectstate" not in export.text
    from api.models.entities import Workspace
    from api.lifecycle import purge_workspace
    with Session(engine) as session:
        w = session.get(Workspace, p["workspace_id"])
        slug = w.slug
    response = client.post(f"/api/v1/workspaces/{p['workspace_id']}/deletion", json={
        "confirmation": slug, "export_sha256": export.headers["x-mouvadah-export-sha256"]})
    assert response.status_code == 200, response.text
    with Session(engine) as session:
        assert session.get(GitHubConnection, 11).status == "disconnected"
        w = session.get(Workspace, p["workspace_id"])
        w.deletion_requested_at = utcnow() - timedelta(days=40)
        w.purge_after = utcnow() - timedelta(days=1)
        session.add(w); session.commit()
        purge_workspace(session, w.id, backup_evidence="local-test-backup")
        for model in [GitHubConnection, GitHubRepository, GitHubTicketLink, GitHubDelivery, GitHubConnectState]:
            assert session.exec(select(model)).all() == []


def test_http_tokens_are_repository_scoped_and_redirects_rejected(monkeypatch, github):
    import httpx
    seen = []
    original_client = httpx.Client
    def respond(request):
        seen.append(request)
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "short-lived"})
        return httpx.Response(302, headers={"location": "https://untrusted.invalid/steal"})
    monkeypatch.setattr(provider, "app_token", lambda: "app-jwt")
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    # Restore the real helper, which the github fixture replaces.
    assert REAL_INSTALLATION_TOKEN(11, 33) == "short-lived"
    payload = json.loads(seen[0].content)
    assert payload["repository_ids"] == [33]
    assert set(payload["permissions"].values()) == {"read"}
    with pytest.raises(provider.GitHubFailure, match="provider_request_failed"):
        provider._json("GET", "/repositories/33", "short-lived")
    assert len(seen) == 2 and all(r.url.host == "api.github.com" for r in seen)
