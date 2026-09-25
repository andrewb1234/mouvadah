"""Request-level security controls shared by browser and API-key traffic."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.config import get_settings

COOKIE_NAME: Final = "session"
READ_SCOPE: Final = "read"
WRITE_SCOPE: Final = "write"
DELETE_SCOPE: Final = "delete"
VALID_API_KEY_SCOPES: Final = frozenset(
    {READ_SCOPE, WRITE_SCOPE, DELETE_SCOPE}
)
SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class ApiKeyAuthorization:
    """The least-privilege boundary attached to the current request."""

    api_key_id: int
    user_id: int
    workspace_id: int
    scopes: frozenset[str]
    project_ids: frozenset[int]


_api_key_authorization: ContextVar[ApiKeyAuthorization | None] = ContextVar(
    "api_key_authorization",
    default=None,
)


def set_api_key_authorization(value: ApiKeyAuthorization) -> None:
    _api_key_authorization.set(value)


def get_api_key_authorization() -> ApiKeyAuthorization | None:
    return _api_key_authorization.get()


def parse_bearer_token(authorization: str) -> str | None:
    """Parse an RFC case-insensitive Bearer scheme with a non-empty token."""
    scheme, separator, credential = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    token = credential.strip()
    return token or None


class SlidingWindowRateLimiter:
    """Small process-local sliding-window limiter for the single-instance stage."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> tuple[bool, int]:
        current = time.monotonic() if now is None else now
        cutoff = current - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (current - events[0])))
                return False, retry_after
            events.append(current)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = SlidingWindowRateLimiter()


class RequestBodyTooLarge(Exception):
    """Raised internally when a streamed request crosses the configured limit."""


class RequestBodyLimitMiddleware:
    """Bound API request bodies before FastAPI buffers or parses them."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] != "http"
            or not scope.get("path", "").startswith(("/api/v1", "/mcp", "/oauth/"))
        ):
            await self.app(scope, receive, send)
            return

        limit = get_settings().max_request_body_bytes
        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
        }
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > limit:
                    await self._reject(scope, receive, send, limit)
                    return
            except ValueError:
                pass

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._reject(scope, receive, send, limit)

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        limit: int,
    ) -> None:
        response = JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content={
                "detail": (
                    f"Request body exceeds the {limit}-byte limit."
                )
            },
        )
        await response(scope, receive, send)


def _client_identity(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _credential_fingerprint(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    credential = authorization or request.cookies.get(COOKIE_NAME, "")
    if not credential:
        return "anonymous"
    return hashlib.sha256(credential.encode()).hexdigest()[:24]


def _rate_limited(retry_after: int) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Too many requests. Try again later."},
        headers={"Retry-After": str(retry_after)},
    )


def _security_headers(response: Response, *, production: bool) -> None:
    response.headers.setdefault(
        "Content-Security-Policy",
        (
            "default-src 'self'; "
            "base-uri 'self'; "
            "connect-src 'self'; "
            "font-src 'self' data:; "
            "form-action 'self' https://accounts.google.com; "
            "frame-ancestors 'none'; "
            "img-src 'self' data: https://lh3.googleusercontent.com; "
            "object-src 'none'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'"
        ),
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
    )
    if production:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )


class SecurityMiddleware(BaseHTTPMiddleware):
    """Enforce exact-origin cookie writes, rate limits, and response headers."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        context_token = _api_key_authorization.set(None)
        settings = get_settings()
        try:
            path = request.url.path
            client = _client_identity(request)

            if path in {
                "/api/v1/auth/login",
                "/api/v1/auth/callback",
                "/api/v1/auth/local-session",
            }:
                allowed, retry_after = rate_limiter.allow(
                    f"auth:{client}:{path}",
                    limit=settings.auth_rate_limit,
                    window_seconds=settings.auth_rate_window_seconds,
                )
                if not allowed:
                    response = _rate_limited(retry_after)
                    _security_headers(response, production=settings.is_production())
                    return response

            if request.method not in SAFE_METHODS and path.startswith("/api/v1"):
                authorization = request.headers.get("authorization", "")
                cookie_authenticated = (
                    bool(request.cookies.get(COOKIE_NAME)) and not authorization
                )
                if cookie_authenticated:
                    origin = request.headers.get("origin")
                    if origin != settings.public_origin():
                        response = JSONResponse(
                            status_code=status.HTTP_403_FORBIDDEN,
                            content={
                                "detail": (
                                    "Unsafe browser requests require the trusted "
                                    "application Origin."
                                )
                            },
                        )
                        _security_headers(
                            response,
                            production=settings.is_production(),
                        )
                        return response

                allowed, retry_after = rate_limiter.allow(
                    (
                        f"action:{client}:"
                        f"{_credential_fingerprint(request)}"
                    ),
                    limit=settings.action_rate_limit,
                    window_seconds=settings.action_rate_window_seconds,
                )
                if not allowed:
                    response = _rate_limited(retry_after)
                    _security_headers(response, production=settings.is_production())
                    return response

            response = await call_next(request)
            _security_headers(response, production=settings.is_production())
            if path.startswith("/api/v1/auth/"):
                response.headers.setdefault("Cache-Control", "no-store")
            return response
        finally:
            _api_key_authorization.reset(context_token)
