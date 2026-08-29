"""Agent simulator integration test.

Boots a real FastAPI uvicorn process against a throwaway SQLite file, then
launches the MCP stdio server as a subprocess and drives it through a proper
JSON-RPC handshake. Asserts both the protocol response and the database side
effect of a ``tools/call`` → PATCH round-trip.

This test sits between the unit suite (in-memory DB, FastAPI TestClient) and
the Playwright browser spec (real Chromium, real Vite) so we have end-to-end
coverage of the MCP wire layer without paying the browser tax.

Marked ``integration`` so it can be skipped in ultra-fast CI loops with
``pytest -m 'not integration'``.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_PYTHON = Path(sys.executable)
MCP_SERVER_SCRIPT = REPO_ROOT / "mcp" / "mcp_server.py"
TEST_AGENT_KEY = "taskable_integration_test_key_12345"


# --------------------------------------------------------------------------
# Uvicorn subprocess fixture
# --------------------------------------------------------------------------


def _find_free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_health(url: str, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=1.0)
            if resp.status_code == 200:
                return
        except httpx.HTTPError as exc:
            last_exc = exc
        time.sleep(0.1)
    raise TimeoutError(f"API did not become healthy within {timeout_s}s: {last_exc}")


@pytest.fixture(scope="module")
def live_api(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    """Spawn a real uvicorn process backed by a throwaway SQLite file.

    Yields a dict with ``base_url`` (e.g. ``http://127.0.0.1:51234/api/v1``)
    and ``db_path`` so assertions can talk to SQLite directly.
    """
    if not VENV_PYTHON.exists():  # pragma: no cover - sanity guard
        pytest.skip(f"Expected interpreter not found at {VENV_PYTHON}")

    port = _find_free_port()
    temp_dir = tmp_path_factory.mktemp("taskable-int")
    db_path = temp_dir / "live.db"
    credentials_file = temp_dir / "credentials.env"
    credentials_file.write_text(
        f"MOUVADAH_API_KEY={TEST_AGENT_KEY}\n"
        f"TASKABLE_API_KEY={TEST_AGENT_KEY}\n",
        encoding="utf-8",
    )
    credentials_file.chmod(0o600)
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db_path}",
        "JWT_SECRET": "test-jwt-secret",
        "PYTHONUNBUFFERED": "1",
    }

    proc = subprocess.Popen(
        [
            str(VENV_PYTHON), "-m", "uvicorn",
            "api.main:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(f"{base}/healthz")
        yield {
            "base_url": f"{base}/api/v1",
            "db_path": str(db_path),
            "credentials_file": str(credentials_file),
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# --------------------------------------------------------------------------
# Helpers for talking JSON-RPC over stdio to mcp_server.py
# --------------------------------------------------------------------------


class MCPClient:
    """Tiny JSON-RPC over stdio helper."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        self._id = 0

    async def send(self, method: str, params: dict | None = None) -> None:
        self._id += 1
        message = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params or {},
        }
        assert self._proc.stdin is not None
        self._proc.stdin.write((json.dumps(message) + "\n").encode())
        await self._proc.stdin.drain()

    async def notify(self, method: str, params: dict | None = None) -> None:
        message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        assert self._proc.stdin is not None
        self._proc.stdin.write((json.dumps(message) + "\n").encode())
        await self._proc.stdin.drain()

    async def recv(self) -> dict:
        assert self._proc.stdout is not None
        line = await self._proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP server closed stdout unexpectedly")
        return json.loads(line.decode())


async def _spawn_mcp(
    api_url: str,
    credentials_file: str,
    *,
    legacy_environment: bool = False,
) -> asyncio.subprocess.Process:
    if legacy_environment:
        mcp_environment = {
            "TASKABLE_API_KEY": "",
            "TASKABLE_CREDENTIALS_FILE": credentials_file,
            "TASKABLE_API_URL": api_url,
        }
    else:
        mcp_environment = {
            "MOUVADAH_API_KEY": "",
            "MOUVADAH_CREDENTIALS_FILE": credentials_file,
            "MOUVADAH_API_URL": api_url,
        }
    env = {
        **os.environ,
        **mcp_environment,
        "PYTHONUNBUFFERED": "1",
    }
    return await asyncio.create_subprocess_exec(
        str(VENV_PYTHON),
        str(MCP_SERVER_SCRIPT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(REPO_ROOT),
    )


async def _initialize(client: MCPClient) -> dict:
    await client.send(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "simulator", "version": "0"},
        },
    )
    response = await client.recv()
    await client.notify("notifications/initialized", {})
    return response


# --------------------------------------------------------------------------
# The tests themselves
# --------------------------------------------------------------------------


@pytest.mark.integration
async def test_mcp_simulator_roundtrip(live_api: dict[str, str]) -> None:
    """Full MCP wire test: handshake + tools/list + tools/call + DB check.

    This test covers the default non-destructive MCP surface via one happy-path
    scenario, mirroring how an agent would actually use it: list → context →
    update → comment → link MR.
    """
    # Seed a user + API key, then a project with one ticket through the HTTP API.
    import hashlib

    db_path = live_api["db_path"]
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO user (google_id, email, name, avatar_url, created_at) "
        "VALUES ('test-google', 'test@example.com', 'Test', NULL, datetime('now'))"
    )
    conn.execute(
        "INSERT INTO workspace (id, name, slug, created_at) "
        "VALUES (1, 'Test workspace', 'test-workspace', datetime('now'))"
    )
    conn.execute(
        "INSERT INTO workspacemembership "
        "(workspace_id, user_id, role, created_at) "
        "VALUES (1, 1, 'OWNER', datetime('now'))"
    )
    key_hash = hashlib.sha256(TEST_AGENT_KEY.encode()).hexdigest()
    conn.execute(
        "INSERT INTO apikey "
        "(user_id, workspace_id, name, key_prefix, key_hash, scopes, "
        "expires_at, last_used_at, revoked, created_at) "
        "VALUES (1, 1, 'test-key', ?, ?, '[\"read\", \"write\"]', "
        "NULL, NULL, 0, datetime('now'))",
        (TEST_AGENT_KEY[:12], key_hash),
    )
    conn.commit()
    conn.close()

    headers = {"Authorization": f"Bearer {TEST_AGENT_KEY}"}
    async with httpx.AsyncClient(base_url=live_api["base_url"], timeout=5, headers=headers) as http:
        project = (await http.post("/projects", json={"name": "Sim"})).json()
        subproject = (
            await http.post(
                f"/projects/{project['id']}/subprojects",
                json={"name": "Sim sub", "context_brief": "Seeded by simulator."},
            )
        ).json()
        ticket = (
            await http.post(
                f"/subprojects/{subproject['id']}/tickets",
                json={"title": "Simulated task", "assignee": "HUMAN"},
            )
        ).json()

    # Now drive the MCP server.
    proc = await _spawn_mcp(
        live_api["base_url"],
        live_api["credentials_file"],
    )
    try:
        client = MCPClient(proc)
        init_response = await _initialize(client)
        assert init_response["result"]["serverInfo"]["name"] == "mouvadah"
        instructions = init_response["result"]["instructions"]
        assert "authoritative project-management" in instructions
        assert "deletion cascades" in instructions

        # tools/list - sanity check
        await client.send("tools/list", {})
        tools = (await client.recv())["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        assert tool_names == {
            # Read
            "get_all_projects",
            "get_active_tasks",
            "read_subproject_context",
            "read_comments",
            # Create
            "create_project",
            "create_subproject",
            "create_ticket",
            # Mutate
            "update_ticket_status",
            "link_mr",
            "leave_comment",
            # Coordination primitives
            "get_ready_tickets",
            "claim_ticket",
            "heartbeat_ticket",
            "requeue_expired",
            # Knowledge tree
            "list_knowledge_nodes",
            "read_knowledge_node",
            "find_context_trail",
            "create_knowledge_node",
            "update_knowledge_node",
        }

        # tools/call -> update_ticket_status (the mutation under test)
        await client.send(
            "tools/call",
            {
                "name": "update_ticket_status",
                "arguments": {"ticket_id": ticket["id"], "status": "IN_PROGRESS"},
            },
        )
        call_result = (await client.recv())["result"]
        body = call_result["content"][0]["text"]
        assert "IN_PROGRESS" in body and f"#{ticket['id']}" in body

        # tools/call -> leave_comment
        await client.send(
            "tools/call",
            {
                "name": "leave_comment",
                "arguments": {
                    "ticket_id": ticket["id"],
                    "content": "Picking this up now.",
                },
            },
        )
        comment_result = (await client.recv())["result"]["content"][0]["text"]
        assert comment_result.startswith("Posted comment")

        # tools/call -> read_comments
        await client.send(
            "tools/call",
            {
                "name": "read_comments",
                "arguments": {"ticket_id": ticket["id"]},
            },
        )
        comments_result = (await client.recv())["result"]["content"][0]["text"]
        assert f"# Comments for ticket #{ticket['id']}" in comments_result
        assert "— AGENT —" in comments_result
        assert "Picking this up now." in comments_result

        # tools/call -> link_mr
        await client.send(
            "tools/call",
            {
                "name": "link_mr",
                "arguments": {
                    "ticket_id": ticket["id"],
                    "url": "https://github.com/acme/sim/pull/1",
                },
            },
        )
        mr_result = (await client.recv())["result"]["content"][0]["text"]
        assert "https://github.com/acme/sim/pull/1" in mr_result
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()

    # Confirm the DB reflects every mutation the simulator performed.
    conn = sqlite3.connect(live_api["db_path"])
    try:
        row = conn.execute(
            "SELECT status, assignee, mr_link FROM ticket WHERE id = ?",
            (ticket["id"],),
        ).fetchone()
        assert row == ("IN_PROGRESS", "AGENT", "https://github.com/acme/sim/pull/1")

        comment_count = conn.execute(
            "SELECT COUNT(*) FROM comment WHERE ticket_id = ? AND author = 'AGENT'",
            (ticket["id"],),
        ).fetchone()[0]
        assert comment_count == 1

        audit_actions = {
            row[0]
            for row in conn.execute(
                "SELECT action FROM auditlog WHERE ticket_id = ?",
                (ticket["id"],),
            )
        }
        assert "STATUS_UPDATE" in audit_actions
        assert "MR_LINKED" in audit_actions
    finally:
        conn.close()


@pytest.mark.integration
async def test_mcp_simulator_rejects_bad_status(live_api: dict[str, str]) -> None:
    """Invalid status values must be rejected before the API is ever hit.

    The MCP SDK validates arguments against the tool's ``inputSchema.enum``
    BEFORE invoking our handler (defense-in-depth), and our handler also
    re-checks the status for the case where a caller has a permissive SDK.
    Either rejection path is acceptable; both keep bad data out of the DB.
    """
    proc = await _spawn_mcp(
        live_api["base_url"],
        live_api["credentials_file"],
    )
    try:
        client = MCPClient(proc)
        await _initialize(client)
        await client.send(
            "tools/call",
            {
                "name": "update_ticket_status",
                "arguments": {"ticket_id": 9999, "status": "WEEKEND_MODE"},
            },
        )
        payload = await client.recv()
        # The SDK returns either a JSON-RPC ``error`` when schema validation
        # fails, or a regular ``result`` with ``isError: true`` / an error
        # text frame from our handler. Accept either shape.
        if "error" in payload:
            message = json.dumps(payload["error"])
        else:
            message = payload["result"]["content"][0]["text"]
        assert "WEEKEND_MODE" in message, message
        lowered = message.lower()
        assert any(kw in lowered for kw in ("invalid", "error", "not one of")), message
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()


@pytest.mark.integration
async def test_mcp_simulator_creation_flow(live_api: dict[str, str]) -> None:
    """End-to-end creation flow via the four new tools.

    Walks the path an agent would take when bootstrapping a workspace from
    scratch: get_all_projects (empty) -> create_project -> create_subproject
    -> create_ticket. Asserts each MCP response parses correctly AND that the
    resulting DB rows exist with the expected parent/child linkage.
    """
    proc = await _spawn_mcp(
        live_api["base_url"],
        live_api["credentials_file"],
    )
    try:
        client = MCPClient(proc)
        await _initialize(client)

        async def call(name: str, arguments: dict) -> str:
            await client.send(
                "tools/call", {"name": name, "arguments": arguments}
            )
            payload = await client.recv()
            assert "result" in payload, payload
            return payload["result"]["content"][0]["text"]

        # 1. create_project
        created = await call(
            "create_project",
            {
                "name": "MCP Creation Flow",
                "description": "Exercised by test_mcp_simulator_creation_flow",
            },
        )
        assert created.startswith("Created project #"), created
        project_id = int(created.split("#")[1].split(":")[0])

        # 2. get_all_projects now includes our new project.
        listing = await call("get_all_projects", {})
        assert f"id={project_id}" in listing, listing
        assert "MCP Creation Flow" in listing

        # 3. create_subproject under it.
        created_sp = await call(
            "create_subproject",
            {
                "project_id": project_id,
                "name": "Creation sub",
                "context_brief": "Ship the four new MCP creation tools.",
            },
        )
        assert created_sp.startswith("Created subproject #"), created_sp
        subproject_id = int(created_sp.split("#")[1].split(" ")[0])

        # 4. create_ticket under that subproject.
        created_t = await call(
            "create_ticket",
            {
                "subproject_id": subproject_id,
                "title": "Wire up tool",
                "description": "Implement the MCP handler and register it.",
                "assignee": "AGENT",
            },
        )
        assert created_t.startswith("Created ticket #"), created_t
        assert "[TODO/AGENT]" in created_t, created_t
        ticket_id = int(created_t.split("#")[1].split(" ")[0])

        # 5. Bogus assignee should be rejected (same dual-layer behavior as
        #    the status test: SDK schema-check or handler guard).
        await client.send(
            "tools/call",
            {
                "name": "create_ticket",
                "arguments": {
                    "subproject_id": subproject_id,
                    "title": "Bad assignee",
                    "description": "Should not be created.",
                    "assignee": "ROBOT",
                },
            },
        )
        bad = await client.recv()
        body = (
            json.dumps(bad["error"])
            if "error" in bad
            else bad["result"]["content"][0]["text"]
        )
        assert "ROBOT" in body, body
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()

    # DB-level verification: parent chain is intact and nothing extra leaked.
    conn = sqlite3.connect(live_api["db_path"])
    try:
        row = conn.execute(
            "SELECT name FROM project WHERE id = ?", (project_id,)
        ).fetchone()
        assert row and row[0] == "MCP Creation Flow"

        row = conn.execute(
            "SELECT project_id, name FROM subproject WHERE id = ?",
            (subproject_id,),
        ).fetchone()
        assert row == (project_id, "Creation sub")

        row = conn.execute(
            "SELECT subproject_id, status, assignee, title FROM ticket "
            "WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        assert row == (subproject_id, "TODO", "AGENT", "Wire up tool")

        # Verify the "bad assignee" attempt didn't persist.
        rejected = conn.execute(
            "SELECT COUNT(*) FROM ticket WHERE title = 'Bad assignee'"
        ).fetchone()[0]
        assert rejected == 0
    finally:
        conn.close()


@pytest.mark.integration
async def test_mcp_simulator_knowledge_flow(live_api: dict[str, str]) -> None:
    """End-to-end knowledge tree build via the MCP wire protocol.

    Exercises the full upstream planning loop: a SUMMARY parent, a nested
    RAW child, a listed outline, a drilled-down read, and an update that
    promotes SUMMARY → PRD. Persistence is verified against the live SQLite.
    """
    proc = await _spawn_mcp(
        live_api["base_url"],
        live_api["credentials_file"],
    )
    try:
        client = MCPClient(proc)
        await _initialize(client)

        async def call(name: str, arguments: dict) -> str:
            await client.send(
                "tools/call", {"name": name, "arguments": arguments}
            )
            payload = await client.recv()
            assert "result" in payload, payload
            return payload["result"]["content"][0]["text"]

        # Seed a project directly via the MCP so the test is hermetic.
        project_msg = await call(
            "create_project",
            {
                "name": "Knowledge Flow",
                "description": "Exercised by test_mcp_simulator_knowledge_flow",
            },
        )
        project_id = int(project_msg.split("#")[1].split(":")[0])

        # 1. create_knowledge_node (SUMMARY at the root)
        summary_msg = await call(
            "create_knowledge_node",
            {
                "project_id": project_id,
                "title": "Architecture overview",
                "node_type": "SUMMARY",
                "content": "FastAPI + SQLModel + React via SSE.",
                "source_refs": ["/abs/README.md"],
            },
        )
        assert summary_msg.startswith("Created knowledge node #"), summary_msg
        summary_id = int(summary_msg.split("#")[1].split(" ")[0])

        # 2. create_knowledge_node (RAW child nested under the summary)
        child_msg = await call(
            "create_knowledge_node",
            {
                "project_id": project_id,
                "title": "api/main.py",
                "node_type": "RAW",
                "content": "from fastapi import FastAPI\napp = FastAPI()",
                "parent_id": summary_id,
                "source_refs": ["/abs/api/main.py"],
            },
        )
        child_id = int(child_msg.split("#")[1].split(" ")[0])
        assert f"parent=#{summary_id}" in child_msg, child_msg

        # 3. list_knowledge_nodes returns a hierarchical outline.
        outline = await call(
            "list_knowledge_nodes", {"project_id": project_id}
        )
        assert f"[SUMMARY #{summary_id}]" in outline, outline
        assert f"[RAW #{child_id}]" in outline, outline
        # Child line is indented under parent line.
        assert outline.index(f"#{summary_id}") < outline.index(f"#{child_id}")

        # 4. read_knowledge_node surfaces source refs + content.
        detail = await call("read_knowledge_node", {"node_id": child_id})
        assert "/abs/api/main.py" in detail, detail
        assert "from fastapi import FastAPI" in detail, detail

        # 5. find_context_trail returns a scoped load order for fresh windows.
        trail = await call(
            "find_context_trail",
            {
                "project_id": project_id,
                "query": "FastAPI app",
            },
        )
        assert "Suggested load order" in trail, trail
        assert f"[SUMMARY #{summary_id}]" in trail, trail
        assert f"[RAW #{child_id}]" in trail, trail

        # 6. update_knowledge_node promotes SUMMARY → PRD.
        promoted = await call(
            "update_knowledge_node",
            {"node_id": summary_id, "node_type": "PRD"},
        )
        assert "[PRD]" in promoted, promoted
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()

    # DB-level verification: parent chain + type promotion persisted.
    conn = sqlite3.connect(live_api["db_path"])
    try:
        row = conn.execute(
            "SELECT node_type, parent_id FROM knowledgenode WHERE id = ?",
            (summary_id,),
        ).fetchone()
        assert row == ("PRD", None)

        row = conn.execute(
            "SELECT node_type, parent_id FROM knowledgenode WHERE id = ?",
            (child_id,),
        ).fetchone()
        assert row == ("RAW", summary_id)
    finally:
        conn.close()


@pytest.mark.integration
async def test_legacy_mcp_environment_aliases_remain_accepted(
    live_api: dict[str, str],
) -> None:
    proc = await _spawn_mcp(
        live_api["base_url"],
        live_api["credentials_file"],
        legacy_environment=True,
    )
    try:
        client = MCPClient(proc)
        response = await _initialize(client)
        assert response["result"]["serverInfo"]["name"] == "mouvadah"
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()


@pytest.mark.integration
async def test_fresh_local_setup_reaches_authenticated_mcp_operation(
    tmp_path: Path,
) -> None:
    """Prove the documented clean install from CLI provisioning through MCP."""
    port = _find_free_port()
    database_path = tmp_path / "state" / "taskable.db"
    credentials_file = tmp_path / "config" / "credentials.env"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{database_path}",
        "FRONTEND_URL": "http://localhost:5173",
        "LOCAL_AUTH_ENABLED": "true",
        "JWT_SECRET": "fresh-local-setup-secret-at-least-32-bytes",
        "MIGRATION_MODE": "upgrade",
        "PYTHONUNBUFFERED": "1",
    }
    setup = subprocess.run(
        [
            str(VENV_PYTHON),
            "-m",
            "api.local_setup",
            "--email",
            "fresh-owner@example.com",
            "--name",
            "Fresh Owner",
            "--credentials-file",
            str(credentials_file),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert setup.returncode == 0, setup.stderr

    api_proc = subprocess.Popen(
        [
            str(VENV_PYTHON),
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    mcp_proc: asyncio.subprocess.Process | None = None
    try:
        _wait_for_health(f"{base}/healthz")
        mcp_proc = await _spawn_mcp(
            f"{base}/api/v1",
            str(credentials_file),
        )
        client = MCPClient(mcp_proc)
        await _initialize(client)
        await client.send(
            "tools/call",
            {
                "name": "create_project",
                "arguments": {
                    "name": "First authenticated project",
                    "description": "Created through the fresh-install MCP path.",
                },
            },
        )
        payload = await client.recv()
        text = payload["result"]["content"][0]["text"]
        assert text.startswith("Created project #"), text
    finally:
        if mcp_proc is not None:
            mcp_proc.terminate()
            try:
                await asyncio.wait_for(mcp_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                mcp_proc.kill()
        api_proc.terminate()
        try:
            api_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            api_proc.kill()
