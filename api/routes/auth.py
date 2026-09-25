"""Google OAuth and loopback local-session routes.

Flow:
    1. ``GET /auth/login`` → redirect to Google consent screen with a random
       state stored in a short-lived cookie.
    2. Google redirects back to ``GET /auth/callback`` with an authorization
       code. We exchange the code for tokens, fetch the user's Google profile,
       upsert the ``User`` row, set a JWT session cookie, and redirect to the
       frontend.
    3. ``GET /auth/me`` → returns the current user's profile (or 401).
    4. ``POST /auth/logout`` → clears the session cookie.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, SecretStr
from sqlmodel import select

from api.auth import (
    COOKIE_NAME,
    create_browser_session,
    get_current_user,
    revoke_browser_session,
    verify_api_key,
)
from api.authorization import ensure_personal_workspace
from api.config import Settings
from api.dependencies import SessionDep, SettingsDep
from api.models.entities import BrowserSession, User
from api.utils.time import utcnow

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

SCOPES = ["openid", "email", "profile"]

STATE_COOKIE = "oauth_state"
STATE_COOKIE_MAX_AGE = 600  # 10 minutes


class AuthProviders(BaseModel):
    google: bool
    local_api_key: bool


class LocalSessionRequest(BaseModel):
    api_key: SecretStr


class BrowserSessionOut(BaseModel):
    id: str
    current: bool
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime


def _require_browser_auth(request: Request) -> None:
    if getattr(request.state, "auth_method", None) != "cookie":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Browser-session management requires a browser session.",
        )


def _redirect_uri(request: Request, settings: Settings) -> str:
    """Return a trusted callback in production and a port-aware local callback."""
    if settings.is_production():
        return f"{settings.public_origin()}/api/v1/auth/callback"
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/v1/auth/callback"


def _cookie_kwargs(settings: Settings, max_age: int | None = None) -> dict:
    """Build cookie kwargs. Use Secure in production (HTTPS)."""
    secure = settings.frontend_url.startswith("https://")
    kwargs: dict = {
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "path": "/",
    }
    if max_age is not None:
        kwargs["max_age"] = max_age
    return kwargs


def _mcp_return_path(token: str, state: str, settings: Settings) -> str:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience="mcp-login-return",
        )
        path = payload.get("path", "")
        if (
            payload.get("state") == state
            and isinstance(path, str)
            and path.startswith("/oauth/authorize?")
        ):
            return path
    except jwt.PyJWTError:
        pass
    return ""


@router.get("/providers")
async def auth_providers(settings: SettingsDep) -> AuthProviders:
    """Return the login methods that are safe and configured for this runtime."""
    return AuthProviders(
        google=bool(settings.google_client_id),
        local_api_key=(settings.local_auth_enabled and not settings.is_production()),
    )


@router.post("/local-session", status_code=status.HTTP_204_NO_CONTENT)
async def local_session(
    payload: LocalSessionRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    """Exchange a local per-user API key for an HttpOnly browser session."""
    if settings.is_production() or not settings.local_auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Local authentication is not enabled.",
        )

    origin = request.headers.get("origin")
    if origin is not None and origin != settings.public_origin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Untrusted local authentication origin.",
        )

    user = verify_api_key(payload.api_key.get_secret_value(), session)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    ensure_personal_workspace(session, user)
    _, jwt_token = create_browser_session(
        session,
        user=user,
        secret=settings.jwt_secret,
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.set_cookie(
        COOKIE_NAME,
        jwt_token,
        **_cookie_kwargs(settings, max_age=60 * 60 * 24 * 30),
    )
    return response


@router.get("/login")
async def auth_login(
    request: Request, settings: SettingsDep, return_to: str | None = None
) -> RedirectResponse:
    """Redirect the user to Google's OAuth consent screen."""
    if not settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GOOGLE_CLIENT_ID is not configured",
        )

    state = secrets.token_urlsafe(32)
    redirect_uri = _redirect_uri(request, settings)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "prompt": "select_account",
    }
    auth_url = GOOGLE_AUTH_URL + "?" + urlencode(params)

    response = RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        STATE_COOKIE,
        state,
        **_cookie_kwargs(settings, max_age=STATE_COOKIE_MAX_AGE),
    )
    if (
        return_to
        and return_to.startswith("/oauth/authorize?")
        and len(return_to.encode()) <= 2048
    ):
        return_token = jwt.encode(
            {
                "aud": "mcp-login-return",
                "path": return_to,
                "state": state,
                "exp": utcnow() + timedelta(seconds=STATE_COOKIE_MAX_AGE),
            },
            settings.jwt_secret,
            algorithm="HS256",
        )
        response.set_cookie(
            "mcp_return_to",
            return_token,
            **_cookie_kwargs(settings, max_age=STATE_COOKIE_MAX_AGE),
        )
    else:
        response.delete_cookie("mcp_return_to", **_cookie_kwargs(settings))
    return response


@router.get("/callback")
async def auth_callback(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle the OAuth callback: exchange code, upsert user, set JWT cookie."""
    if error:
        return RedirectResponse(
            url=f"{settings.public_origin()}/?auth_error=oauth_denied",
            status_code=status.HTTP_302_FOUND,
        )
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing code or state parameter",
        )

    # Verify state matches the cookie to prevent CSRF.
    cookie_state = request.cookies.get(STATE_COOKIE)
    if not cookie_state or not secrets.compare_digest(cookie_state, state):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth state",
        )

    redirect_uri = _redirect_uri(request, settings)

    # Exchange the authorization code for tokens.
    async with httpx.AsyncClient(timeout=10) as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Token exchange failed",
            )
        tokens = token_resp.json()

        # Fetch user profile from Google.
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Userinfo fetch failed",
            )
        profile = userinfo_resp.json()

    if profile.get("verified_email") is not True:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A verified Google email address is required.",
        )
    google_id = profile["id"]
    email = profile["email"].strip().lower()
    name = profile.get("name", email)
    avatar_url = profile.get("picture")

    # Upsert the user.
    existing = session.exec(select(User).where(User.google_id == google_id)).first()
    if existing is None:
        local_user = session.exec(select(User).where(User.email == email)).first()
        if local_user is not None and local_user.google_id.startswith("local:"):
            local_user.google_id = google_id
            existing = local_user
    if existing:
        existing.email = email
        existing.name = name
        existing.avatar_url = avatar_url
        session.add(existing)
        session.commit()
        session.refresh(existing)
        user = existing
    else:
        user = User(
            google_id=google_id,
            email=email,
            name=name,
            avatar_url=avatar_url,
        )
        session.add(user)
        session.commit()
        session.refresh(user)

    ensure_personal_workspace(session, user)

    # Issue a signed token backed by a revocable server-side session.
    _, jwt_token = create_browser_session(
        session,
        user=user,
        secret=settings.jwt_secret,
    )
    return_to = _mcp_return_path(
        request.cookies.get("mcp_return_to", ""), state, settings
    )
    destination = (
        settings.public_origin() + return_to
        if return_to.startswith("/oauth/authorize?")
        else settings.frontend_url
    )
    response = RedirectResponse(
        url=destination,
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        COOKIE_NAME,
        jwt_token,
        **_cookie_kwargs(settings, max_age=60 * 60 * 24 * 30),  # 30 days
    )
    response.delete_cookie(STATE_COOKIE, **_cookie_kwargs(settings))
    response.delete_cookie("mcp_return_to", **_cookie_kwargs(settings))
    return response


@router.get("/me")
async def auth_me(current_user: User = Depends(get_current_user)) -> dict:
    """Return the current authenticated user's profile."""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "avatar_url": current_user.avatar_url,
    }


@router.get("/sessions")
async def list_browser_sessions(
    request: Request,
    session: SessionDep,
    current_user: User = Depends(get_current_user),
) -> list[BrowserSessionOut]:
    """List active browser sessions so another session can revoke them."""
    _require_browser_auth(request)
    now = utcnow()
    rows = session.exec(
        select(BrowserSession)
        .where(
            BrowserSession.user_id == current_user.id,
            BrowserSession.revoked_at.is_(None),
            BrowserSession.expires_at >= now,
        )
        .order_by(BrowserSession.created_at.desc())
    ).all()
    current_id = getattr(request.state, "browser_session_id", None)
    return [
        BrowserSessionOut(
            id=row.id,
            current=row.id == current_id,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            expires_at=row.expires_at,
        )
        for row in rows
    ]


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_session(
    session_id: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    current_user: User = Depends(get_current_user),
) -> Response:
    """Revoke one browser session owned by the current user."""
    _require_browser_auth(request)
    row = session.get(BrowserSession, session_id)
    if row is None or row.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Browser session not found.",
        )
    revoke_browser_session(session, session_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    if getattr(request.state, "browser_session_id", None) == session_id:
        response.delete_cookie(COOKIE_NAME, **_cookie_kwargs(settings))
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def auth_logout(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    current_user: User = Depends(get_current_user),
) -> Response:
    """Revoke the current browser session and clear its cookie."""
    del current_user
    _require_browser_auth(request)
    session_id = getattr(request.state, "browser_session_id", None)
    if session_id:
        revoke_browser_session(session, session_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(COOKIE_NAME, **_cookie_kwargs(settings))
    return response
