"""Stateless hosted MCP transport using the existing tool catalogue and API ACLs."""

from __future__ import annotations

import importlib.util
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import TextContent, ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from api.config import get_settings
from api.mcp_oauth import resolve_access, resource_url
from api.security import parse_bearer_token, rate_limiter

# Load the Apache-licensed shared tool module without shadowing the SDK package.
_spec = importlib.util.spec_from_file_location(
    "mouvadah_hosted_tools",
    Path(__file__).resolve().parents[1] / "mcp" / "mcp_server.py",
)
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)
permissions: ContextVar[frozenset[str]] = ContextVar(
    "mcp_permissions", default=frozenset()
)
server = Server("mouvadah", instructions=bridge.MOUVADAH_INSTRUCTIONS)


READ_TOOLS = {
    "get_all_projects",
    "read_subproject_context",
    "get_active_tasks",
    "read_comments",
    "get_ready_tickets",
    "list_knowledge_nodes",
    "read_knowledge_node",
    "find_context_trail",
}
HOSTED_TOOLS = [
    tool.model_copy(
        update={
            "annotations": ToolAnnotations(
                readOnlyHint=tool.name in READ_TOOLS,
                destructiveHint=False,
                openWorldHint=False,
            )
        }
    )
    for tool in bridge.SAFE_TOOLS
]


def available_tools():
    return [
        tool
        for tool in HOSTED_TOOLS
        if not tool.name.startswith("delete_")
        and (
            "write" in permissions.get()
            or (tool.annotations and tool.annotations.readOnlyHint)
        )
    ]


@server.list_tools()
async def list_tools():
    return available_tools()


@server.call_tool()
async def call_tool(name: str, arguments: dict | None):
    if bridge.request_client.get() is None or name not in {
        tool.name for tool in available_tools()
    }:
        raise ValueError("Tool is not permitted by this connection.")
    try:
        return [
            TextContent(
                type="text", text=await bridge.TOOL_DISPATCH[name](**(arguments or {}))
            )
        ]
    except (TypeError, httpx.RequestError) as exc:
        raise ValueError("The tool request could not be completed.") from exc


def make_manager():
    origin = get_settings().public_origin()
    return StreamableHTTPSessionManager(
        server,
        stateless=True,
        json_response=True,
        security_settings=TransportSecuritySettings(
            allowed_hosts=[urlsplit(origin).netloc],
            allowed_origins=[origin, "https://claude.ai"],
        ),
    )


class HostedMcp:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not get_settings().hosted_mcp_enabled:
            await JSONResponse({"error": "temporarily_unavailable"}, status_code=503)(
                scope, receive, send
            )
            return
        request = Request(scope, receive)
        raw = parse_bearer_token(request.headers.get("authorization", ""))
        grant = resolve_access(raw) if raw else None
        if grant is None:
            response = JSONResponse(
                {"error": "invalid_token"},
                status_code=401,
                headers={
                    "WWW-Authenticate": f'Bearer resource_metadata="{get_settings().public_origin()}/.well-known/oauth-protected-resource/mcp", scope="read write"',
                    "Cache-Control": "no-store",
                },
            )
            await response(scope, receive, send)
            return
        key_id, scopes = grant
        allowed, retry = rate_limiter.allow(
            f"hosted-mcp:{key_id}", limit=240, window_seconds=60
        )
        if not allowed:
            await JSONResponse(
                {"error": "rate_limited"},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )(scope, receive, send)
            return

        async def internal_api(api_scope, api_receive, api_send):
            # This value is ASGI metadata, never a client-supplied HTTP header.
            api_scope["mouvadah.mcp_key_id"] = key_id
            api_scope["mouvadah.mcp_scopes"] = scopes
            await self.app(api_scope, api_receive, api_send)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=internal_api),
            base_url=resource_url().removesuffix("/mcp") + "/api/v1/",
            timeout=30,
        ) as client:
            client_context = bridge.request_client.set(client)
            permission_context = permissions.set(scopes)
            try:
                await self.app.state.mcp_manager.handle_request(scope, receive, send)
            finally:
                permissions.reset(permission_context)
                bridge.request_client.reset(client_context)


def install(app):
    app.router.routes.append(
        Route("/mcp", endpoint=HostedMcp(app), methods=["GET", "POST", "DELETE"])
    )
