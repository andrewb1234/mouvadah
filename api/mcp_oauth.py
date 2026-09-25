"""OAuth 2.1 authorization for the hosted MCP resource.

Google is used only to sign in to Mouvadah. MCP clients receive independent,
workspace-scoped grants, never Google tokens or a server owner's credentials.
"""

from __future__ import annotations

import base64
import hashlib
import html
import re
import secrets
from datetime import timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlmodel import Session, select

from api import database
from api.api_keys import hash_api_key, issue_api_key
from api.auth import get_current_user
from api.authorization import require_workspace
from api.config import get_settings
from api.dependencies import SessionDep, SettingsDep
from api.models.entities import (
    ApiKey,
    McpOAuthClient,
    McpOAuthCode,
    McpOAuthToken,
    Workspace,
    WorkspaceMembership,
)
from api.security import rate_limiter
from api.utils.time import utcnow

router = APIRouter()
SCOPES = {"read", "write"}
TOKEN_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def resource_url() -> str:
    return get_settings().public_origin() + "/mcp"


def error(code: str, description: str, status: int = 400):
    return JSONResponse(
        {"error": code, "error_description": description},
        status_code=status,
        headers=TOKEN_HEADERS,
    )


def limit(request: Request, category: str, count: int = 60):
    identity = request.client.host if request.client else "unknown"
    allowed, retry = rate_limiter.allow(
        f"mcp-oauth:{category}:{identity}", limit=count, window_seconds=60
    )
    if not allowed:
        raise HTTPException(
            429, "Too many requests", headers={"Retry-After": str(retry)}
        )


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource():
    return {
        "resource": resource_url(),
        "authorization_servers": [get_settings().public_origin()],
        "scopes_supported": ["read", "write"],
        "bearer_methods_supported": ["header"],
        "resource_name": "Mouvadah",
    }


@router.get("/.well-known/oauth-authorization-server")
def authorization_metadata():
    origin = get_settings().public_origin()
    return {
        "issuer": origin,
        "authorization_endpoint": origin + "/oauth/authorize",
        "token_endpoint": origin + "/oauth/token",
        "registration_endpoint": origin + "/oauth/register",
        "revocation_endpoint": origin + "/oauth/revoke",
        "scopes_supported": ["read", "write"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "none",
            "client_secret_post",
            "client_secret_basic",
        ],
    }


class Registration(BaseModel):
    client_name: str = Field(default="MCP client", min_length=1, max_length=100)
    redirect_uris: list[str] = Field(min_length=1, max_length=10)
    token_endpoint_auth_method: str = "none"
    grant_types: list[str] = Field(
        default_factory=lambda: ["authorization_code", "refresh_token"]
    )
    response_types: list[str] = Field(default_factory=lambda: ["code"])
    scope: str = "read write"


@router.post("/oauth/register")
def register(payload: Registration, request: Request, session: SessionDep):
    limit(request, "register", 10)
    if (
        payload.token_endpoint_auth_method
        not in {"none", "client_secret_post", "client_secret_basic"}
        or not set(payload.grant_types).issubset(
            {"authorization_code", "refresh_token"}
        )
        or "authorization_code" not in payload.grant_types
        or payload.response_types != ["code"]
        or not set(payload.scope.split()).issubset(SCOPES)
    ):
        return error(
            "invalid_client_metadata",
            "Unsupported client authentication, grant, response type, or scope.",
        )
    for uri in payload.redirect_uris:
        try:
            parsed = urlsplit(uri)
            valid = (
                len(uri) <= 2048
                and bool(parsed.hostname)
                and re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", parsed.netloc) is not None
                and "\\" not in uri
                and not parsed.fragment
                and not parsed.username
                and not parsed.password
                and (
                    parsed.scheme == "https"
                    or (
                        parsed.scheme == "http"
                        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                    )
                )
                and not any(c.isspace() for c in uri)
            )
        except ValueError:
            valid = False
        if not valid:
            return error(
                "invalid_redirect_uri",
                "Use an HTTPS callback or a loopback HTTP callback without a fragment.",
            )
    client_id = secrets.token_urlsafe(32)
    secret = (
        secrets.token_urlsafe(32)
        if payload.token_endpoint_auth_method != "none"
        else None
    )
    session.add(
        McpOAuthClient(
            id=client_id,
            secret_hash=hash_api_key(secret) if secret else None,
            metadata_json=payload.model_dump(),
        )
    )
    session.commit()
    result = {
        **payload.model_dump(),
        "client_id": client_id,
        "client_id_issued_at": int(utcnow().replace(tzinfo=timezone.utc).timestamp()),
    }
    if secret:
        result.update(client_secret=secret, client_secret_expires_at=0)
    return JSONResponse(result, status_code=201, headers=TOKEN_HEADERS)


def callback(uri: str, **params: str):
    parts = urlsplit(uri)
    query = urlencode([*parse_qsl(parts.query), *params.items()])
    return RedirectResponse(
        urlunsplit(parts._replace(query=query)), status_code=303, headers=TOKEN_HEADERS
    )


def page(title: str, body: str, callback_origin: str = ""):
    # Browsers also apply form-action to the authorization POST redirect.
    policy = (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'self' "
        + callback_origin
    )
    return HTMLResponse(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} · Mouvadah</title>
<style>body{{font:17px/1.6 system-ui,sans-serif;background:#f7f4ed;color:#272824;margin:0;padding:24px}}main{{max-width:560px;margin:8vh auto}}h1{{line-height:1.15}}label{{display:block;margin-top:20px}}select,button{{font:inherit;padding:12px;max-width:100%;border:1px solid #666;border-radius:4px}}select{{width:100%}}button{{margin:24px 8px 0 0;cursor:pointer}}button[value=allow]{{background:#272824;color:white}}code{{overflow-wrap:anywhere}}.muted{{color:#565952}}</style></head><body><main><p>MOUVADAH</p><h1>{html.escape(title)}</h1>{body}</main></body></html>""",
        headers={
            **TOKEN_HEADERS,
            "Referrer-Policy": "same-origin",
            "Content-Security-Policy": policy,
        },
    )


@router.get("/oauth/authorize")
async def authorize(request: Request, session: SessionDep, settings: SettingsDep):
    limit(request, "authorize")
    q = request.query_params
    client = session.get(McpOAuthClient, q.get("client_id", ""))
    if not client or q.get("redirect_uri") not in client.metadata_json["redirect_uris"]:
        return error("invalid_request", "Unknown client or unregistered redirect URI.")
    scopes = set(q.get("scope", "read write").split())
    if (
        q.get("response_type") != "code"
        or q.get("code_challenge_method") != "S256"
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", q.get("code_challenge", ""))
    ):
        return error(
            "invalid_request", "Authorization code flow with S256 PKCE is required."
        )
    if (
        "read" not in scopes
        or not scopes.issubset(SCOPES)
        or not scopes.issubset(set(client.metadata_json["scope"].split()))
    ):
        return error(
            "invalid_scope", "Only registered read and write scopes are supported."
        )
    if q.get("resource", resource_url()) != resource_url():
        return error("invalid_target", "The requested resource is not this MCP server.")
    try:
        user = await get_current_user(request, session, settings)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        return_to = "/oauth/authorize?" + str(request.query_params)
        return RedirectResponse(
            "/api/v1/auth/login?" + urlencode({"return_to": return_to}),
            status_code=302,
            headers=TOKEN_HEADERS,
        )
    if request.state.auth_method != "cookie":
        return error(
            "access_denied", "Sign in with your browser to connect a client.", 403
        )
    workspaces = session.exec(
        select(Workspace)
        .join(WorkspaceMembership)
        .where(
            WorkspaceMembership.user_id == user.id,
            Workspace.deletion_requested_at.is_(None),
        )
    ).all()
    if not workspaces:
        return page(
            "Join a workspace first",
            '<p>Open Mouvadah and create a workspace or accept your team’s invitation, then connect again.</p><a href="/app">Open Mouvadah</a>',
        )
    consent = jwt.encode(
        {
            "aud": "mcp-consent",
            "sub": str(user.id),
            "sid": request.state.browser_session_id,
            "exp": utcnow() + timedelta(minutes=10),
            "jti": secrets.token_urlsafe(32),
            "client_id": client.id,
            "redirect_uri": q["redirect_uri"],
            "state": q.get("state", ""),
            "challenge": q["code_challenge"],
            "scopes": sorted(scopes),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    access_choice = (
        '<label for="access">Access</label><select id="access" name="access"><option value="read write">Read and update</option><option value="read">Read only</option></select>'
        if "write" in scopes
        else '<input type="hidden" name="access" value="read">'
    )
    options = "".join(
        f'<option value="{w.id}">{html.escape(w.name)}</option>' for w in workspaces
    )
    name = html.escape(client.metadata_json["client_name"])
    host = html.escape(urlsplit(q["redirect_uri"]).netloc)
    access = (
        "Read and update projects, tickets, comments, and knowledge"
        if "write" in scopes
        else "Read projects, tickets, comments, and knowledge"
    )
    return page(
        f"Connect {client.metadata_json['client_name']}?",
        f"""<p>Signed in as <strong>{html.escape(user.email)}</strong>.</p>
<p><strong>{name}</strong> will be able to {access.lower()} in the workspace you choose. It cannot delete data or manage your account.</p>
<p class="muted">You can revoke this connection at any time in Settings → Agent credentials. Access expires after 30 days; reconnect to renew it.</p>
<form method="post" action="/oauth/authorize"><input type="hidden" name="consent" value="{html.escape(consent)}">
<label for="workspace">Workspace</label><select id="workspace" name="workspace_id" required>{options}</select>{access_choice}
<p class="muted">After approval, return to <code>{host}</code>. Only approve a client you intended to connect.</p>
<button name="decision" value="allow">Allow connection</button><button name="decision" value="deny">Cancel</button></form>""",
        callback_origin=urlunsplit((*urlsplit(q["redirect_uri"])[:2], "", "", "")),
    )


@router.post("/oauth/authorize")
async def approve(request: Request, session: SessionDep, settings: SettingsDep):
    limit(request, "approve")
    if request.headers.get("origin") != settings.public_origin():
        return error("access_denied", "Untrusted form origin.", 403)
    user = await get_current_user(request, session, settings)
    if request.state.auth_method != "cookie":
        return error("access_denied", "A browser session is required.", 403)
    form = await request.form()
    try:
        consent = jwt.decode(
            str(form.get("consent", "")),
            settings.jwt_secret,
            algorithms=["HS256"],
            audience="mcp-consent",
        )
        assert (
            consent["sub"] == str(user.id)
            and consent["sid"] == request.state.browser_session_id
        )
    except (jwt.PyJWTError, AssertionError, KeyError):
        return error(
            "access_denied",
            "Consent expired or belongs to a different browser session.",
            403,
        )
    client = session.get(McpOAuthClient, consent["client_id"])
    if (
        not client
        or consent["redirect_uri"] not in client.metadata_json["redirect_uris"]
    ):
        return error("invalid_request", "Client registration is no longer valid.")
    if form.get("decision") != "allow":
        return callback(
            consent["redirect_uri"], error="access_denied", state=consent["state"]
        )
    try:
        workspace_id = int(str(form.get("workspace_id", "")))
    except ValueError:
        return error("invalid_request", "Select a workspace.")
    granted_scopes = sorted(set(str(form.get("access", "read")).split()))
    if "read" not in granted_scopes or not set(granted_scopes).issubset(
        set(consent["scopes"])
    ):
        return error(
            "invalid_scope", "Access must be within the requested permissions."
        )
    require_workspace(
        session, user, workspace_id, write="write" in granted_scopes, lock=True
    )
    key, _ = issue_api_key(
        session,
        user_id=user.id,
        workspace_id=workspace_id,
        name=f"MCP: {client.metadata_json['client_name']}"[:100],
        scopes=granted_scopes,
        expires_in_days=30,
    )
    code = secrets.token_urlsafe(32)
    session.add(
        McpOAuthCode(
            code_hash=hash_api_key(code),
            client_id=client.id,
            api_key_id=key.id,
            redirect_uri=consent["redirect_uri"],
            challenge=consent["challenge"],
            resource=resource_url(),
            expires_at=utcnow() + timedelta(minutes=5),
        )
    )
    session.commit()
    return callback(consent["redirect_uri"], code=code, state=consent["state"])


async def client_form(request: Request, session: Session):
    form = dict(await request.form())
    client_id = str(form.get("client_id", ""))
    secret = str(form.get("client_secret", ""))
    method = "client_secret_post" if secret else "none"
    authorization = request.headers.get("authorization", "")
    if authorization:
        try:
            scheme, encoded = authorization.split(" ", 1)
            if scheme.lower() != "basic":
                raise ValueError()
            client_id, secret = (
                base64.b64decode(encoded, validate=True).decode().split(":", 1)
            )
            method = "client_secret_basic"
        except (ValueError, UnicodeError):
            return None, form
    client = session.get(McpOAuthClient, client_id)
    if not client or method != client.metadata_json["token_endpoint_auth_method"]:
        return None, form
    if client.secret_hash and not secrets.compare_digest(
        hash_api_key(secret), client.secret_hash
    ):
        return None, form
    return client, form


def active_key(session: Session, key_id: int):
    key = session.get(ApiKey, key_id)
    if not key or key.revoked or (key.expires_at and key.expires_at <= utcnow()):
        return None
    membership = session.exec(
        select(WorkspaceMembership).where(
            WorkspaceMembership.user_id == key.user_id,
            WorkspaceMembership.workspace_id == key.workspace_id,
        )
    ).first()
    workspace = session.get(Workspace, key.workspace_id)
    if not membership or not workspace or workspace.deletion_requested_at is not None:
        return None
    return key


def mint(
    session: Session, key: ApiKey, client_id: str, scopes: list[str], resource: str
):
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = utcnow()
    for raw, kind, expiry in [
        (access, "access", now + timedelta(hours=1)),
        (refresh, "refresh", key.expires_at),
    ]:
        session.add(
            McpOAuthToken(
                token_hash=hash_api_key(raw),
                client_id=client_id,
                api_key_id=key.id,
                kind=kind,
                resource=resource,
                scopes=scopes,
                expires_at=min(expiry, key.expires_at),
            )
        )
    session.commit()
    return JSONResponse(
        {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": max(
                0, min(3600, int((key.expires_at - now).total_seconds()))
            ),
            "scope": " ".join(scopes),
        },
        headers=TOKEN_HEADERS,
    )


@router.post("/oauth/token")
async def token(request: Request, session: SessionDep):
    limit(request, "token")
    client, form = await client_form(request, session)
    if not client:
        return error("invalid_client", "Client authentication failed.", 401)
    if form.get("resource", resource_url()) != resource_url():
        return error("invalid_target", "Incorrect MCP resource.")
    grant_type = form.get("grant_type")
    if grant_type not in client.metadata_json["grant_types"]:
        return error("unauthorized_client", "Grant type is not registered.")
    if grant_type == "authorization_code":
        row = session.get(McpOAuthCode, hash_api_key(str(form.get("code", ""))))
        if (
            not row
            or row.client_id != client.id
            or row.expires_at <= utcnow()
            or row.resource != resource_url()
        ):
            return error("invalid_grant", "Invalid or expired authorization code.")
        verifier = str(form.get("code_verifier", ""))
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if (
            not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
            or not secrets.compare_digest(challenge, row.challenge)
            or form.get("redirect_uri") != row.redirect_uri
        ):
            return error("invalid_grant", "PKCE or redirect URI does not match.")
        key = active_key(session, row.api_key_id)
        consumed = session.execute(
            update(McpOAuthCode)
            .where(McpOAuthCode.code_hash == row.code_hash, McpOAuthCode.used == False)
            .values(used=True)
        ).rowcount
        scopes = key.scopes if key else []
    elif grant_type == "refresh_token":
        row = session.get(
            McpOAuthToken, hash_api_key(str(form.get("refresh_token", "")))
        )
        if (
            not row
            or row.kind != "refresh"
            or row.client_id != client.id
            or row.expires_at <= utcnow()
            or row.resource != resource_url()
        ):
            return error("invalid_grant", "Invalid or expired refresh token.")
        scopes = sorted(set(str(form.get("scope", " ".join(row.scopes))).split()))
        if not scopes or not set(scopes).issubset(set(row.scopes)):
            return error("invalid_scope", "Refresh cannot increase permissions.")
        key = active_key(session, row.api_key_id)
        consumed = session.execute(
            update(McpOAuthToken)
            .where(
                McpOAuthToken.token_hash == row.token_hash, McpOAuthToken.used == False
            )
            .values(used=True)
        ).rowcount
    else:
        return error(
            "unsupported_grant_type", "Use authorization_code or refresh_token."
        )
    if not key or not consumed:
        if key:
            key.revoked = True  # Reuse revokes the whole grant, including descendants.
            session.add(key)
        session.commit()
        return error(
            "invalid_grant",
            "Grant revoked or token already used. Reconnect the client.",
        )
    return mint(session, key, client.id, scopes, row.resource)


def resolve_access(raw: str):
    with Session(database.engine) as session:
        token = session.get(McpOAuthToken, hash_api_key(raw))
        if (
            not token
            or token.kind != "access"
            or token.used
            or token.expires_at <= utcnow()
            or token.resource != resource_url()
        ):
            return None
        key = active_key(session, token.api_key_id)
        if not key:
            return None
        return key.id, frozenset(token.scopes)


@router.post("/oauth/revoke")
async def revoke(request: Request, session: SessionDep):
    limit(request, "revoke")
    client, form = await client_form(request, session)
    if not client:
        return error("invalid_client", "Client authentication failed.", 401)
    row = session.get(McpOAuthToken, hash_api_key(str(form.get("token", ""))))
    if row and row.client_id == client.id:
        key = session.get(ApiKey, row.api_key_id)
        if key:
            key.revoked = True
            session.add(key)
            session.commit()
    return JSONResponse({}, headers=TOKEN_HEADERS)
