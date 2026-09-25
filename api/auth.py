"""Revocable browser-session and scoped API-key authentication.

Browser cookies contain a signed JWT whose random ``sid`` must also resolve to
an active server-side ``BrowserSession`` row. Logging out or an administrative
revocation therefore takes effect immediately rather than waiting for JWT
expiry.

Per-user API keys are bound to one workspace, carry explicit read/write scopes,
and may carry a project allow-list. The full key is hashed (SHA-256) for lookup.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session, select

from api.api_keys import hash_api_key
from api.dependencies import FunctionSessionDep, SessionDep, SettingsDep
from api.models.entities import (
    ApiKey,
    ApiKeyProject,
    BrowserSession,
    User,
    WorkspaceMembership,
)
from api.security import (
    COOKIE_NAME,
    DELETE_SCOPE,
    READ_SCOPE,
    SAFE_METHODS,
    WRITE_SCOPE,
    ApiKeyAuthorization,
    parse_bearer_token,
    set_api_key_authorization,
)
from api.utils.time import utcnow

TOKEN_EXPIRY_DAYS = 30


def create_jwt(
    user_id: int,
    email: str,
    secret: str,
    session_id: str,
) -> str:
    """Sign a short browser token bound to a revocable session row."""
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "email": email,
        "sid": session_id,
        "exp": now + timedelta(days=TOKEN_EXPIRY_DAYS),
        "iat": now,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_jwt(token: str, secret: str) -> dict | None:
    """Verify and decode a JWT. Returns ``None`` on failure."""
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def create_browser_session(
    session: Session,
    *,
    user: User,
    secret: str,
) -> tuple[BrowserSession, str]:
    """Persist a revocable browser session and return its signed cookie value."""
    now = utcnow()
    browser_session = BrowserSession(
        id=secrets.token_urlsafe(32),
        user_id=user.id,  # type: ignore[arg-type]
        expires_at=now + timedelta(days=TOKEN_EXPIRY_DAYS),
        created_at=now,
        last_seen_at=now,
    )
    session.add(browser_session)
    session.commit()
    session.refresh(browser_session)
    token = create_jwt(
        user.id,  # type: ignore[arg-type]
        user.email,
        secret,
        browser_session.id,
    )
    return browser_session, token


def revoke_browser_session(session: Session, session_id: str) -> None:
    browser_session = session.get(BrowserSession, session_id)
    if browser_session is None or browser_session.revoked_at is not None:
        return
    browser_session.revoked_at = utcnow()
    session.add(browser_session)
    session.commit()


def _verify_api_key_record(
    raw_key: str,
    session: Session,
    *,
    internal_key_id: int | None = None,
) -> tuple[User, ApiKey, frozenset[int]] | None:
    """Resolve an active, workspace-bound key and its project allow-list.

    Returns ``None`` if the key is not found, revoked, or expired.
    Updates ``last_used_at`` on successful verification.
    """
    key_hash = hash_api_key(raw_key)
    api_key = (
        session.get(ApiKey, internal_key_id)
        if internal_key_id is not None
        else session.exec(select(ApiKey).where(ApiKey.key_hash == key_hash)).first()
    )
    if api_key is None or api_key.revoked or api_key.workspace_id is None:
        return None
    if api_key.expires_at is not None:
        if api_key.expires_at < utcnow():
            return None
    user = session.get(User, api_key.user_id)
    if user is None:
        return None
    membership = session.exec(
        select(WorkspaceMembership.id).where(
            WorkspaceMembership.workspace_id == api_key.workspace_id,
            WorkspaceMembership.user_id == user.id,
        )
    ).first()
    if membership is None:
        return None
    project_ids = frozenset(
        session.exec(
            select(ApiKeyProject.project_id).where(
                ApiKeyProject.api_key_id == api_key.id
            )
        ).all()
    )
    api_key.last_used_at = utcnow()
    session.add(api_key)
    session.commit()
    return user, api_key, project_ids


def verify_api_key(raw_key: str, session: Session) -> User | None:
    """Return a user only for a full-workspace local browser bootstrap key.

    Exchanging a restricted integration key for an unrestricted browser
    session would be a scope escalation, so read-only or project-limited keys
    are deliberately ineligible.
    """
    verified = _verify_api_key_record(raw_key, session)
    if verified is None:
        return None
    user, api_key, project_ids = verified
    if not {READ_SCOPE, WRITE_SCOPE}.issubset(api_key.scopes) or project_ids:
        return None
    return user


async def get_current_user(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> User:
    """Dependency that extracts and validates the user from either:
    1. An explicit per-user API key (Authorization: Bearer <key>), or
    2. A revocable browser-session cookie.
    """
    # An explicit bearer credential takes precedence over ambient cookies.
    # This avoids privilege confusion when a signed-in browser intentionally
    # tests or uses a restricted integration key.
    authorization = request.headers.get("Authorization", "")
    # Set only by the in-process hosted MCP adapter, never from HTTP headers.
    internal_key_id = request.scope.get("mouvadah.mcp_key_id")
    if authorization or internal_key_id is not None:
        bearer_token = parse_bearer_token(authorization)
        verified = (
            _verify_api_key_record(
                bearer_token or "", session, internal_key_id=internal_key_id
            )
            if bearer_token is not None or internal_key_id is not None
            else None
        )
        if verified is not None:
            user, api_key, project_ids = verified
            if request.method in SAFE_METHODS:
                required_scope = READ_SCOPE
            elif request.method == "DELETE":
                required_scope = DELETE_SCOPE
            else:
                required_scope = WRITE_SCOPE
            scopes = frozenset(api_key.scopes)
            if internal_key_id is not None:
                scopes &= request.scope["mouvadah.mcp_scopes"]
            if required_scope not in scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"API key lacks required scope: {required_scope}.",
                    headers={
                        "WWW-Authenticate": (
                            'Bearer error="insufficient_scope", '
                            f'scope="{required_scope}"'
                        )
                    },
                )
            set_api_key_authorization(
                ApiKeyAuthorization(
                    api_key_id=api_key.id,  # type: ignore[arg-type]
                    user_id=user.id,  # type: ignore[arg-type]
                    workspace_id=api_key.workspace_id,
                    scopes=scopes,
                    project_ids=project_ids,
                )
            )
            request.state.auth_method = "api_key"
            request.state.api_key_id = api_key.id
            request.state.user_id = user.id
            request.state.workspace_id = api_key.workspace_id
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Try the signed, server-revocable browser session first.
    token = request.cookies.get(COOKIE_NAME)
    if token:
        payload = decode_jwt(token, settings.jwt_secret)
        if payload is not None:
            try:
                user_id = int(payload["sub"])
                session_id = str(payload["sid"])
            except (KeyError, TypeError, ValueError):
                user_id = 0
                session_id = ""
            browser_session = (
                session.get(BrowserSession, session_id) if session_id else None
            )
            now = utcnow()
            if (
                browser_session is not None
                and browser_session.user_id == user_id
                and browser_session.revoked_at is None
                and browser_session.expires_at >= now
            ):
                user = session.get(User, user_id)
                if user is not None:
                    # Avoid a database write on every static/API read.
                    if browser_session.last_seen_at < now - timedelta(minutes=5):
                        browser_session.last_seen_at = now
                        session.add(browser_session)
                        session.commit()
                    request.state.auth_method = "cookie"
                    request.state.browser_session_id = session_id
                    request.state.user_id = user.id
                    return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_stream_current_user(
    request: Request,
    session: FunctionSessionDep,
    settings: SettingsDep,
) -> User:
    """Authenticate an SSE request without retaining a request-long session."""
    return await get_current_user(request, session, settings)


StreamCurrentUser = Annotated[User, Depends(get_stream_current_user)]
