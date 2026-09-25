"""Real OAuth/HTTP MCP protocol and tenant-boundary tests (no auth overrides)."""

import base64
import hashlib
import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlmodel import Session, select

from api.auth import create_browser_session
from api.authorization import ensure_personal_workspace
from api.config import get_settings
from api.models.entities import ApiKey, McpOAuthCode, McpOAuthToken, Project, User
from api.security import COOKIE_NAME
from api.utils.time import utcnow

ORIGIN = "http://localhost:5173"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "a" * 64
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest())
    .rstrip(b"=")
    .decode()
)


def register(client, **kwargs):
    result = client.post(
        "/oauth/register",
        json={"client_name": "Claude", "redirect_uris": [CALLBACK], **kwargs},
    )
    assert result.status_code == 201, result.text
    return result.json()


def login(client, engine, user):
    with Session(engine) as session:
        workspace = ensure_personal_workspace(session, user)
        _, token = create_browser_session(
            session, user=user, secret=get_settings().jwt_secret
        )
        workspace_id = workspace.id
    client.cookies.set(COOKIE_NAME, token)
    return workspace_id


def authorization(client, registered, **kwargs):
    params = {
        "client_id": registered["client_id"],
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "scope": "read write",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": "client-state",
        "resource": ORIGIN + "/mcp",
        **kwargs,
    }
    return client.get("/oauth/authorize", params=params, follow_redirects=False)


def consent(client, registered, workspace_id, **kwargs):
    response = authorization(client, registered, **kwargs)
    assert response.status_code == 200, response.text
    assert (
        "form-action 'self' https://claude.ai"
        in response.headers["content-security-policy"]
    )
    signed = html.unescape(
        re.search(r'name="consent" value="([^"]+)"', response.text)[1]
    )
    response = client.post(
        "/oauth/authorize",
        data={
            "consent": signed,
            "workspace_id": workspace_id,
            "decision": "allow",
            "access": kwargs.get("scope", "read write"),
        },
        headers={"origin": ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["state"] == ["client-state"]
    return query["code"][0]


def exchange(client, registered, code, **kwargs):
    return client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": registered["client_id"],
            "code": code,
            "code_verifier": VERIFIER,
            "redirect_uri": CALLBACK,
            "resource": ORIGIN + "/mcp",
            **kwargs,
        },
    )


def connect(client, engine, user, scope="read write"):
    workspace = login(client, engine, user)
    registered = register(client)
    code = consent(client, registered, workspace, scope=scope)
    response = exchange(client, registered, code)
    assert response.status_code == 200, response.text
    return registered, response.json(), workspace


def rpc(client, token, method, params=None):
    return client.post(
        "/mcp",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
            "host": "localhost:5173",
            "MCP-Protocol-Version": "2025-11-25",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


@pytest.fixture(autouse=True)
def hosted_env(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", ORIGIN)
    get_settings.cache_clear()


def test_discovery_and_no_ambient_cookie_or_api_key_auth(
    enforce_auth_client, engine, test_user, agent_headers
):
    c = enforce_auth_client
    login(c, engine, test_user)
    metadata = c.get("/.well-known/oauth-protected-resource/mcp").json()
    assert metadata["resource"] == ORIGIN + "/mcp"
    assert c.get("/.well-known/oauth-authorization-server").json()[
        "code_challenge_methods_supported"
    ] == ["S256"]
    assert c.post("/mcp").status_code == 401
    assert c.post("/mcp", headers=agent_headers).status_code == 401
    assert "resource_metadata=" in c.post("/mcp").headers["www-authenticate"]


def test_signed_out_authorization_preserves_login_destination(enforce_auth_client):
    c = enforce_auth_client
    registered = register(c)
    response = authorization(c, registered)
    assert response.status_code == 302
    target = response.headers["location"]
    assert target.startswith("/api/v1/auth/login?return_to=")
    assert "code_challenge" in target


def test_full_protocol_workspace_isolation_and_write(
    enforce_auth_client, engine, test_user
):
    c = enforce_auth_client
    _, tokens, workspace = connect(c, engine, test_user)
    access = tokens["access_token"]
    # OAuth credentials are audience-bound and cannot authenticate the REST API.
    assert (
        c.get(
            "/api/v1/projects", headers={"Authorization": "Bearer " + access}
        ).status_code
        == 401
    )
    result = rpc(
        c,
        access,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "Claude", "version": "test"},
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["result"]["serverInfo"]["name"] == "mouvadah"
    tools = rpc(c, access, "tools/list").json()["result"]["tools"]
    assert "get_all_projects" in {t["name"] for t in tools}
    assert not any(t["name"].startswith("delete_") for t in tools)
    response = rpc(
        c,
        access,
        "tools/call",
        {
            "name": "create_project",
            "arguments": {"name": "Mobile project", "description": "Test"},
        },
    )
    assert response.status_code == 200, response.text
    assert "Created project" in response.text, response.text
    with Session(engine) as session:
        project = session.exec(
            select(Project).where(Project.name == "Mobile project")
        ).one()
        assert project.workspace_id == workspace
        project_id = project.id
        other = User(google_id="other", email="other@example.com", name="Other")
        session.add(other)
        session.commit()
        session.refresh(other)
        other_workspace = ensure_personal_workspace(session, other)
        session.add(
            Project(name="Private other project", workspace_id=other_workspace.id)
        )
        session.commit()
    response = rpc(c, access, "tools/call", {"name": "get_all_projects"})
    assert (
        "Mobile project" in response.text
        and "Private other project" not in response.text
    )
    assert rpc(
        c,
        access,
        "tools/call",
        {"name": "delete_project", "arguments": {"project_id": project_id}},
    ).json()["result"]["isError"]


def test_pkce_redirect_resource_and_code_replay(enforce_auth_client, engine, test_user):
    c = enforce_auth_client
    workspace = login(c, engine, test_user)
    registered = register(c)
    assert (
        authorization(c, registered, redirect_uri="https://evil.invalid/").status_code
        == 400
    )
    assert (
        authorization(c, registered, code_challenge_method="plain").status_code == 400
    )
    assert (
        authorization(c, registered, resource="https://evil.invalid/mcp").status_code
        == 400
    )
    assert authorization(c, registered, scope="read delete").status_code == 400
    code = consent(c, registered, workspace)
    assert exchange(c, registered, code, code_verifier="b" * 64).status_code == 400
    assert (
        exchange(c, registered, code, redirect_uri="https://evil.invalid/").status_code
        == 400
    )
    assert (
        exchange(c, registered, code, resource="https://evil.invalid/mcp").status_code
        == 400
    )
    tokens = exchange(c, registered, code).json()
    assert exchange(c, registered, code).json()["error"] == "invalid_grant"
    assert rpc(c, tokens["access_token"], "tools/list").status_code == 401


def test_refresh_rotation_narrowing_and_reuse_revokes_family(
    enforce_auth_client, engine, test_user
):
    c = enforce_auth_client
    registered, tokens, _ = connect(c, engine, test_user)
    params = {
        "grant_type": "refresh_token",
        "client_id": registered["client_id"],
        "refresh_token": tokens["refresh_token"],
        "resource": ORIGIN + "/mcp",
    }
    assert (
        c.post(
            "/oauth/token", data={**params, "scope": "read write delete"}
        ).status_code
        == 400
    )
    refreshed = c.post("/oauth/token", data={**params, "scope": "read"}).json()
    assert refreshed["refresh_token"] != tokens["refresh_token"]
    tools = rpc(c, refreshed["access_token"], "tools/list").json()["result"]["tools"]
    assert tools and all(t["annotations"]["readOnlyHint"] for t in tools)
    assert rpc(
        c,
        refreshed["access_token"],
        "tools/call",
        {"name": "create_project", "arguments": {"name": "Forbidden"}},
    ).json()["result"]["isError"]
    assert c.post("/oauth/token", data=params).json()["error"] == "invalid_grant"
    assert rpc(c, refreshed["access_token"], "tools/list").status_code == 401


def test_consent_requires_browser_csrf_and_workspace_membership(
    enforce_auth_client, engine, test_user
):
    c = enforce_auth_client
    workspace = login(c, engine, test_user)
    registered = register(c)
    response = authorization(c, registered)
    signed = html.unescape(
        re.search(r'name="consent" value="([^"]+)"', response.text)[1]
    )
    data = {"consent": signed, "workspace_id": workspace, "decision": "allow"}
    assert c.post("/oauth/authorize", data=data).status_code == 403
    assert (
        c.post(
            "/oauth/authorize",
            data={**data, "consent": signed + "x"},
            headers={"origin": ORIGIN},
        ).status_code
        == 403
    )
    assert (
        c.post(
            "/oauth/authorize",
            data={**data, "workspace_id": 99999},
            headers={"origin": ORIGIN},
        ).status_code
        == 404
    )
    response = c.post(
        "/oauth/authorize",
        data={**data, "decision": "deny"},
        headers={"origin": ORIGIN},
        follow_redirects=False,
    )
    assert "error=access_denied" in response.headers["location"]


def test_revocation_and_expiry(enforce_auth_client, engine, test_user):
    c = enforce_auth_client
    registered, tokens, _ = connect(c, engine, test_user)
    assert (
        c.post(
            "/oauth/revoke",
            data={
                "client_id": registered["client_id"],
                "token": tokens["refresh_token"],
            },
        ).status_code
        == 200
    )
    assert rpc(c, tokens["access_token"], "tools/list").status_code == 401
    _, tokens, _ = connect(c, engine, test_user)
    with Session(engine) as session:
        for row in session.exec(select(McpOAuthToken)):
            row.expires_at = utcnow() - timedelta(seconds=1)
            session.add(row)
        session.commit()
    assert rpc(c, tokens["access_token"], "tools/list").status_code == 401


def test_browser_revocation_and_parallel_identities(
    enforce_auth_client, engine, test_user
):
    c = enforce_auth_client
    _, alice, _ = connect(c, engine, test_user)
    with Session(engine) as session:
        bob = User(google_id="bob", email="bob@example.com", name="Bob")
        session.add(bob)
        session.commit()
        session.refresh(bob)
    _, bob_tokens, _ = connect(c, engine, bob)

    def create(args):
        token, name = args
        return rpc(
            c,
            token,
            "tools/call",
            {
                "name": "create_project",
                "arguments": {"name": name, "description": "Parallel isolation test"},
            },
        )

    with ThreadPoolExecutor(2) as pool:
        responses = list(
            pool.map(
                create,
                [
                    (alice["access_token"], "Alice project"),
                    (bob_tokens["access_token"], "Bob project"),
                ],
            )
        )
    assert all("Created project" in r.text for r in responses), [
        r.text for r in responses
    ]
    assert (
        "Alice project"
        not in rpc(
            c, bob_tokens["access_token"], "tools/call", {"name": "get_all_projects"}
        ).text
    )
    keys = c.get("/api/v1/apikeys").json()
    key_id = next(k["id"] for k in keys if k["name"] == "MCP: Claude")
    assert (
        c.delete(f"/api/v1/apikeys/{key_id}", headers={"origin": ORIGIN}).status_code
        == 200
    )
    assert rpc(c, bob_tokens["access_token"], "tools/list").status_code == 401
    assert rpc(c, alice["access_token"], "tools/list").status_code == 200


def test_registration_validation_and_confidential_client(
    enforce_auth_client, engine, test_user
):
    c = enforce_auth_client
    assert (
        c.post(
            "/oauth/register", json={"redirect_uris": ["http://evil.invalid/callback"]}
        ).status_code
        == 400
    )
    workspace = login(c, engine, test_user)
    registered = register(c, token_endpoint_auth_method="client_secret_post")
    code = consent(c, registered, workspace)
    assert exchange(c, registered, code).status_code == 401
    assert (
        exchange(
            c, registered, code, client_secret=registered["client_secret"]
        ).status_code
        == 200
    )


def test_member_removal_and_workspace_deletion_stop_access(
    enforce_auth_client, engine, test_user
):
    from api.models.entities import Workspace, WorkspaceMembership
    from sqlalchemy import delete

    c = enforce_auth_client
    _, tokens, workspace_id = connect(c, engine, test_user)
    with Session(engine) as session:
        session.execute(
            delete(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id
            )
        )
        session.commit()
    assert rpc(c, tokens["access_token"], "tools/list").status_code == 401
    with Session(engine) as session:
        from api.models.enums import WorkspaceRole

        session.add(
            WorkspaceMembership(
                user_id=test_user.id,
                workspace_id=workspace_id,
                role=WorkspaceRole.OWNER,
            )
        )
        session.commit()
    _, tokens, workspace_id = connect(c, engine, test_user)
    with Session(engine) as session:
        workspace = session.get(Workspace, workspace_id)
        workspace.deletion_requested_at = utcnow()
        workspace.purge_after = utcnow() + timedelta(days=7)
        workspace.deletion_requested_by = test_user.id
        workspace.deletion_export_sha256 = "a" * 64
        session.add(workspace)
        session.commit()
    assert rpc(c, tokens["access_token"], "tools/list").status_code == 401


def test_viewer_can_choose_read_only_and_cannot_grant_write(
    enforce_auth_client, engine, test_user
):
    from api.models.entities import WorkspaceMembership
    from api.models.enums import WorkspaceRole

    c = enforce_auth_client
    workspace = login(c, engine, test_user)
    registered = register(c)
    with Session(engine) as session:
        membership = session.exec(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace
            )
        ).one()
        membership.role = WorkspaceRole.VIEWER
        session.add(membership)
        session.commit()
    response = authorization(c, registered)
    signed = html.unescape(
        re.search(r'name="consent" value="([^"]+)"', response.text)[1]
    )
    data = {
        "consent": signed,
        "workspace_id": workspace,
        "decision": "allow",
        "access": "read write",
    }
    assert (
        c.post("/oauth/authorize", data=data, headers={"origin": ORIGIN}).status_code
        == 404
    )
    response = c.post(
        "/oauth/authorize",
        data={**data, "access": "read"},
        headers={"origin": ORIGIN},
        follow_redirects=False,
    )
    code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
    tokens = exchange(c, registered, code).json()
    assert tokens["scope"] == "read"
    assert "get_all_projects" in rpc(c, tokens["access_token"], "tools/list").text


def test_token_storage_and_wrong_client_and_audience(
    enforce_auth_client, engine, test_user
):
    from api.api_keys import hash_api_key

    c = enforce_auth_client
    registered, tokens, _ = connect(c, engine, test_user)
    other = register(c)
    response = c.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": other["client_id"],
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert response.status_code == 400
    with Session(engine) as session:
        token = session.get(McpOAuthToken, hash_api_key(tokens["access_token"]))
        assert len(token.token_hash) == 64
        assert tokens["access_token"] not in str(token.model_dump())
        token.resource = "https://another-resource.invalid/mcp"
        session.add(token)
        session.commit()
    assert rpc(c, tokens["access_token"], "tools/list").status_code == 401


def test_oauth_login_return_is_restricted_and_consent_is_session_bound(
    enforce_auth_client, engine, test_user, monkeypatch
):
    c = enforce_auth_client
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-google-client")
    get_settings.cache_clear()
    c.get(
        "/api/v1/auth/login",
        params={"return_to": "/oauth/authorize?client_id=test"},
        follow_redirects=False,
    )
    assert "/oauth/authorize" in c.cookies.get("mcp_return_to")
    c.get(
        "/api/v1/auth/login",
        params={"return_to": "//evil.invalid/"},
        follow_redirects=False,
    )
    assert c.cookies.get("mcp_return_to") is None
    workspace = login(c, engine, test_user)
    registered = register(c)
    response = authorization(c, registered)
    signed = html.unescape(
        re.search(r'name="consent" value="([^"]+)"', response.text)[1]
    )
    # A new browser session for the same account cannot reuse the old form.
    login(c, engine, test_user)
    assert (
        c.post(
            "/oauth/authorize",
            data={"consent": signed, "workspace_id": workspace, "decision": "allow"},
            headers={"origin": ORIGIN},
        ).status_code
        == 403
    )


def test_mcp_rejects_untrusted_origin_and_host(enforce_auth_client, engine, test_user):
    c = enforce_auth_client
    _, tokens, _ = connect(c, engine, test_user)
    headers = {
        "authorization": "Bearer " + tokens["access_token"],
        "accept": "application/json, text/event-stream",
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert (
        c.post(
            "/mcp", headers={**headers, "host": "evil.invalid"}, json=body
        ).status_code
        == 421
    )
    assert (
        c.post(
            "/mcp",
            headers={
                **headers,
                "host": "localhost:5173",
                "origin": "https://evil.invalid",
            },
            json=body,
        ).status_code
        == 403
    )
