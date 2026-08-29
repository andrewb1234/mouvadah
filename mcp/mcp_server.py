# Copyright 2026 Andrew Betbadal and contributors.
# SPDX-License-Identifier: Apache-2.0

"""Mouvadah MCP server.

Exposes the Mouvadah REST API to agentic IDEs (Windsurf, Claude Desktop, etc.)
over MCP stdio. The underlying REST API must be reachable on
``MOUVADAH_API_URL`` (default ``http://localhost:8000/api/v1``).
The legacy ``TASKABLE_*`` names remain accepted during the compatibility
window.

Tools — kept in lock-step with ``docs/mcp.md``:
    Read:
        1. ``get_all_projects()``
        2. ``get_active_tasks(project_id)``
        3. ``read_subproject_context(subproject_id)``
        4. ``read_comments(ticket_id)``
    Create:
        5. ``create_project(name, description)``
        6. ``create_subproject(project_id, name, context_brief)``
        7. ``create_ticket(subproject_id, title, description, assignee)``
    Mutate:
        8. ``update_ticket_status(ticket_id, status)``
        9. ``link_mr(ticket_id, url)``
        10. ``leave_comment(ticket_id, content)``
    Delete (cascading):
        11. ``delete_project(project_id)``
        12. ``delete_subproject(subproject_id)``
        13. ``delete_ticket(ticket_id)``
    Knowledge tree (PRD / TDD synthesis upstream of tickets):
        14. ``list_knowledge_nodes(project_id)``
        15. ``read_knowledge_node(node_id)``
        16. ``find_context_trail(project_id, query, limit?)``
        17. ``create_knowledge_node(project_id, title, node_type, content,
                                    parent_id?, source_refs?)``
        18. ``update_knowledge_node(node_id, title?, node_type?, content?,
                                    parent_id?, source_refs?)``
        19. ``delete_knowledge_node(node_id)``

Docstrings are intentionally verbose so LLM clients have precise schemas and
behavioral expectations without having to re-read the spec.
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool, ToolAnnotations


def _environment_value(
    current_name: str,
    legacy_name: str,
    default: str = "",
) -> str:
    """Return a current Mouvadah setting, then its Taskable compatibility alias."""
    return os.getenv(current_name) or os.getenv(legacy_name) or default


API_URL = _environment_value(
    "MOUVADAH_API_URL",
    "TASKABLE_API_URL",
    "http://localhost:8000/api/v1",
).rstrip("/")
DEFAULT_CREDENTIALS_FILE = (
    Path.home() / ".config" / "mouvadah" / "credentials.env"
)


def _load_api_key() -> str:
    direct_key = _environment_value(
        "MOUVADAH_API_KEY",
        "TASKABLE_API_KEY",
    ).strip()
    if direct_key:
        return direct_key

    credentials_file = Path(
        _environment_value(
            "MOUVADAH_CREDENTIALS_FILE",
            "TASKABLE_CREDENTIALS_FILE",
            str(DEFAULT_CREDENTIALS_FILE),
        )
    ).expanduser()
    if not credentials_file.exists():
        return ""
    if (
        os.name == "posix"
        and stat.S_IMODE(credentials_file.stat().st_mode) & 0o077
    ):
        raise RuntimeError(
            f"{credentials_file} must be owner-only; run "
            f"`chmod 600 {credentials_file}`."
        )
    values: dict[str, str] = {}
    for line in credentials_file.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    for key_name in ("MOUVADAH_API_KEY", "TASKABLE_API_KEY"):
        if values.get(key_name):
            return values[key_name]
    return ""


API_KEY = _load_api_key()
DESTRUCTIVE_TOOLS_ENABLED = (
    os.getenv("MOUVADAH_ENABLE_DESTRUCTIVE_TOOLS", "").strip().lower()
    == "true"
)

VALID_TICKET_STATUSES = {"TODO", "IN_PROGRESS", "BLOCKED", "REVIEW", "DONE"}
VALID_TICKET_ASSIGNEES = {"HUMAN", "AGENT", "UNASSIGNED"}
VALID_KNOWLEDGE_NODE_TYPES = {"RAW", "SUMMARY", "PRD", "TDD"}

MOUVADAH_INSTRUCTIONS = """Mouvadah is the authoritative project-management
and durable-memory system. Before substantive work, call get_all_projects and
identify exactly one project; then load only its relevant active tasks and
knowledge context. Keep ticket status, dependencies, blockers, and verified
durable knowledge aligned as work changes. Search and read before creating or
updating knowledge. Attach new nodes to the closest semantic parent. Before
deleting a knowledge node, inspect its descendants and references because
deletion cascades."""

server: Server = Server(
    "mouvadah",
    instructions=MOUVADAH_INSTRUCTIONS,
)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain;q=0.9, */*;q=0.5",
    }


def _wrap(text: str) -> list[TextContent]:
    """Every MCP tool return value is a list of content parts."""
    return [TextContent(type="text", text=text)]


async def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15.0) as client:
        return await client.request(
            method, f"{API_URL}{path}", headers=_headers(), **kwargs
        )


# ---- Tool implementations -------------------------------------------------


async def get_all_projects() -> str:
    """List every project in the workspace with their IDs.

    Calls ``GET /api/v1/projects``. Intended as the first tool an agent uses
    to orient itself before drilling into a specific project's subprojects.
    """
    response = await _request("GET", "/projects")
    if response.status_code != 200:
        return (
            f"ERROR: list projects failed "
            f"(status={response.status_code}): {response.text}"
        )
    projects = response.json()
    if not projects:
        return "No projects exist yet. Use create_project to create one."

    lines = ["# Projects"]
    for project in projects:
        desc = (project.get("description") or "").strip()
        suffix = f" — {desc}" if desc else ""
        lines.append(f"- id={project['id']} | {project['name']}{suffix}")
    return "\n".join(lines)


async def create_project(name: str, description: str) -> str:
    """Create a new top-level project.

    Calls ``POST /api/v1/projects`` with ``{"name": name, "description":
    description}``. Returns the new project's ID so subsequent
    ``create_subproject`` calls can wire to it.
    """
    response = await _request(
        "POST",
        "/projects",
        json={"name": name, "description": description},
    )
    if response.status_code != 201:
        return (
            f"ERROR: create project failed "
            f"(status={response.status_code}): {response.text}"
        )
    project = response.json()
    return (
        f"Created project #{project['id']}: {project['name']}. "
        f"Use this id when calling create_subproject."
    )


async def create_subproject(
    project_id: int, name: str, context_brief: str
) -> str:
    """Create a subproject (sprint-like unit) under an existing project.

    Calls ``POST /api/v1/projects/{project_id}/subprojects`` with
    ``{"name": name, "context_brief": context_brief}``. The context brief is
    the primary thing an agent later reads via ``read_subproject_context``, so
    it should be rich and goal-oriented.
    """
    response = await _request(
        "POST",
        f"/projects/{int(project_id)}/subprojects",
        json={"name": name, "context_brief": context_brief},
    )
    if response.status_code == 404:
        return f"ERROR: project {project_id} does not exist."
    if response.status_code != 201:
        return (
            f"ERROR: create subproject failed "
            f"(status={response.status_code}): {response.text}"
        )
    subproject = response.json()
    return (
        f"Created subproject #{subproject['id']} "
        f"(project={subproject['project_id']}): {subproject['name']}."
    )


async def create_ticket(
    subproject_id: int,
    title: str,
    description: str,
    assignee: str,
    depends_on: list[int] | None = None,
) -> str:
    """Create a new ticket inside a subproject.

    Calls ``POST /api/v1/subprojects/{subproject_id}/tickets`` with
    ``{"title": title, "description": description, "assignee": assignee}``.
    ``assignee`` must be one of HUMAN, AGENT, or UNASSIGNED (case-insensitive
    on the way in; normalized before the request). New tickets always start
    in TODO status — use ``update_ticket_status`` afterwards to move them.
    Optionally pass ``depends_on`` as a list of ticket IDs this ticket depends on.
    """
    assignee_upper = assignee.upper()
    if assignee_upper not in VALID_TICKET_ASSIGNEES:
        return (
            f"ERROR: invalid assignee '{assignee}'. "
            f"Valid values: {sorted(VALID_TICKET_ASSIGNEES)}."
        )

    payload: dict[str, Any] = {
        "title": title,
        "description": description,
        "assignee": assignee_upper,
    }
    if depends_on:
        payload["depends_on"] = depends_on

    response = await _request(
        "POST",
        f"/subprojects/{int(subproject_id)}/tickets",
        json=payload,
    )
    if response.status_code == 404:
        return f"ERROR: subproject {subproject_id} does not exist."
    if response.status_code != 201:
        return (
            f"ERROR: create ticket failed "
            f"(status={response.status_code}): {response.text}"
        )
    ticket = response.json()
    return (
        f"Created ticket #{ticket['id']} [{ticket['status']}/{ticket['assignee']}] "
        f"{ticket['title']} (subproject={ticket['subproject_id']})."
    )


async def read_subproject_context(subproject_id: int) -> str:
    """Return the full flattened context for a subproject (LLM-optimized).

    Calls ``GET /api/v1/agent/context/{subproject_id}`` and returns the raw
    plain-text body, which already contains the context brief and the list of
    active tickets with statuses, assignees, and MR links.
    """
    response = await _request("GET", f"/agent/context/{int(subproject_id)}")
    if response.status_code == 200:
        return response.text
    return (
        f"ERROR: agent context request failed "
        f"(status={response.status_code}): {response.text}"
    )


async def get_active_tasks(project_id: int) -> str:
    """List subprojects inside a project, flagging which are ACTIVE.

    Intended for an agent that needs to decide which subproject to work on
    before calling ``read_subproject_context``.
    """
    response = await _request("GET", f"/projects/{int(project_id)}/subprojects")
    if response.status_code != 200:
        return (
            f"ERROR: list subprojects failed "
            f"(status={response.status_code}): {response.text}"
        )
    subprojects = response.json()
    if not subprojects:
        return f"No subprojects exist under project {project_id}."

    lines = [f"# Subprojects of project {project_id}"]
    for sp in subprojects:
        marker = "* ACTIVE" if sp["status"] == "ACTIVE" else f"  {sp['status']}"
        lines.append(
            f"{marker} — id={sp['id']} | {sp['name']}\n    {sp.get('context_brief', '').strip()}"
        )
    return "\n".join(lines)


async def update_ticket_status(ticket_id: int, status: str) -> str:
    """Transition a ticket to a new status and claim it for the agent.

    Payload: ``{"status": status, "assignee": "AGENT"}``. The ``status`` must
    be one of TODO, IN_PROGRESS, BLOCKED, REVIEW, DONE (case-sensitive).
    """
    status_upper = status.upper()
    if status_upper not in VALID_TICKET_STATUSES:
        return (
            f"ERROR: invalid status '{status}'. "
            f"Valid values: {sorted(VALID_TICKET_STATUSES)}."
        )

    response = await _request(
        "PATCH",
        f"/tickets/{int(ticket_id)}",
        json={"status": status_upper, "assignee": "AGENT"},
    )
    if response.status_code != 200:
        return (
            f"ERROR: patch ticket {ticket_id} failed "
            f"(status={response.status_code}): {response.text}"
        )
    ticket = response.json()
    return (
        f"Updated ticket #{ticket['id']} → status={ticket['status']}, "
        f"assignee={ticket['assignee']}."
    )


async def link_mr(ticket_id: int, url: str) -> str:
    """Attach a Merge Request URL to a ticket.

    Writes an ``MR_LINKED`` entry to the AuditLog and broadcasts the
    corresponding SSE event, which causes the UI to refresh automatically.
    """
    response = await _request(
        "POST", f"/tickets/{int(ticket_id)}/mr", json={"url": url}
    )
    if response.status_code != 200:
        return (
            f"ERROR: MR link failed "
            f"(status={response.status_code}): {response.text}"
        )
    ticket = response.json()
    return f"Linked MR on ticket #{ticket['id']}: {ticket['mr_link']}"


async def get_ready_tickets(subproject_id: int) -> str:
    """List tickets in a subproject that are ready to be claimed.

    A ticket is *ready* iff status==TODO and all tickets it depends_on are DONE.
    Calls ``GET /api/v1/subprojects/{subproject_id}?ready=true``.
    """
    response = await _request(
        "GET", f"/subprojects/{int(subproject_id)}?ready=true"
    )
    if response.status_code != 200:
        return (
            f"ERROR: ready-ticket query failed "
            f"(status={response.status_code}): {response.text}"
        )
    data = response.json()
    tickets = data.get("tickets", [])
    if not tickets:
        return f"No ready tickets in subproject {subproject_id}."
    lines = [f"# Ready tickets in subproject {subproject_id} ({len(tickets)})"]
    for t in tickets:
        deps = f" deps={t['depends_on']}" if t.get("depends_on") else ""
        lines.append(f"- #{t['id']} [{t['status']}] {t['title']}{deps}")
    return "\n".join(lines)


async def claim_ticket(ticket_id: int, worker_id: str) -> str:
    """Atomically claim a ticket for a worker.

    Transitions the ticket from TODO to IN_PROGRESS, sets ``claimed_by``,
    ``claimed_at``, and ``lease_expires_at``. Returns 409 if the ticket is
    not TODO or has unmet dependencies.
    Calls ``POST /api/v1/tickets/{ticket_id}/claim``.
    """
    response = await _request(
        "POST", f"/tickets/{int(ticket_id)}/claim", json={"worker_id": worker_id}
    )
    if response.status_code == 409:
        return f"ERROR: ticket {ticket_id} is not available for claiming: {response.json().get('detail', '')}"
    if response.status_code == 404:
        return f"ERROR: ticket {ticket_id} does not exist."
    if response.status_code != 200:
        return (
            f"ERROR: claim ticket failed "
            f"(status={response.status_code}): {response.text}"
        )
    ticket = response.json()
    return (
        f"Claimed ticket #{ticket['id']} for worker '{ticket['claimed_by']}'. "
        f"Status={ticket['status']}, lease_expires_at={ticket['lease_expires_at']}."
    )


async def heartbeat_ticket(
    ticket_id: int, worker_id: str, extend_seconds: int
) -> str:
    """Extend the lease on a claimed ticket.

    The ``worker_id`` must match the ticket's ``claimed_by``.
    Calls ``POST /api/v1/tickets/{ticket_id}/heartbeat``.
    """
    response = await _request(
        "POST",
        f"/tickets/{int(ticket_id)}/heartbeat",
        json={"worker_id": worker_id, "extend_seconds": extend_seconds},
    )
    if response.status_code == 409:
        return f"ERROR: worker '{worker_id}' does not own ticket {ticket_id}."
    if response.status_code == 404:
        return f"ERROR: ticket {ticket_id} does not exist."
    if response.status_code != 200:
        return (
            f"ERROR: heartbeat failed "
            f"(status={response.status_code}): {response.text}"
        )
    ticket = response.json()
    return (
        f"Heartbeat acknowledged on ticket #{ticket['id']}. "
        f"New lease_expires_at={ticket['lease_expires_at']}."
    )


async def requeue_expired(subproject_id: int) -> str:
    """Revert IN_PROGRESS tickets with expired leases back to TODO.

    Clears claim fields and writes a TICKET_REQUEUED audit log entry for each.
    Calls ``POST /api/v1/tickets/subprojects/{subproject_id}/requeue-expired``.
    """
    response = await _request(
        "POST", f"/tickets/subprojects/{int(subproject_id)}/requeue-expired"
    )
    if response.status_code != 200:
        return (
            f"ERROR: requeue-expired failed "
            f"(status={response.status_code}): {response.text}"
        )
    requeued = response.json()
    if not requeued:
        return f"No expired leases found in subproject {subproject_id}."
    lines = [f"# Requeued {len(requeued)} expired ticket(s) in subproject {subproject_id}"]
    for t in requeued:
        lines.append(f"- #{t['id']} {t['title']} → TODO (was claimed by expired lease)")
    return "\n".join(lines)


async def delete_project(project_id: int) -> str:
    """Delete a project and cascade its subprojects, tickets, and knowledge nodes.

    Calls ``DELETE /api/v1/projects/{project_id}``. Irreversible — every
    subproject, ticket, comment, audit log, and knowledge node under the
    project is removed. Use with care.
    """
    response = await _request("DELETE", f"/projects/{int(project_id)}")
    if response.status_code == 404:
        return f"ERROR: project {project_id} does not exist."
    if response.status_code != 204:
        return (
            f"ERROR: delete project failed "
            f"(status={response.status_code}): {response.text}"
        )
    return f"Deleted project #{project_id} and all nested subprojects, tickets, and knowledge nodes."


async def delete_subproject(subproject_id: int) -> str:
    """Delete a subproject and cascade its tickets and audit trail.

    Calls ``DELETE /api/v1/subprojects/{subproject_id}``. Leaves the parent
    project intact.
    """
    response = await _request("DELETE", f"/subprojects/{int(subproject_id)}")
    if response.status_code == 404:
        return f"ERROR: subproject {subproject_id} does not exist."
    if response.status_code != 204:
        return (
            f"ERROR: delete subproject failed "
            f"(status={response.status_code}): {response.text}"
        )
    return f"Deleted subproject #{subproject_id} and all its tickets."


async def delete_ticket(ticket_id: int) -> str:
    """Delete a ticket and cascade its comments and audit logs.

    Calls ``DELETE /api/v1/tickets/{ticket_id}``. Leaves the parent
    subproject intact.
    """
    response = await _request("DELETE", f"/tickets/{int(ticket_id)}")
    if response.status_code == 404:
        return f"ERROR: ticket {ticket_id} does not exist."
    if response.status_code != 204:
        return (
            f"ERROR: delete ticket failed "
            f"(status={response.status_code}): {response.text}"
        )
    return f"Deleted ticket #{ticket_id}."


async def delete_knowledge_node(node_id: int) -> str:
    """Delete a knowledge node and cascade its children.

    Calls ``DELETE /api/v1/knowledge/{node_id}``. Descendant RAW/SUMMARY/
    PRD/TDD nodes nested under this one are removed too.
    """
    response = await _request("DELETE", f"/knowledge/{int(node_id)}")
    if response.status_code == 404:
        return f"ERROR: knowledge node {node_id} does not exist."
    if response.status_code != 204:
        return (
            f"ERROR: delete knowledge node failed "
            f"(status={response.status_code}): {response.text}"
        )
    return f"Deleted knowledge node #{node_id} and any descendants."


async def leave_comment(ticket_id: int, content: str) -> str:
    """Append an AGENT-authored comment on a ticket's thread."""
    response = await _request(
        "POST",
        f"/tickets/{int(ticket_id)}/comments",
        json={"author": "AGENT", "content": content},
    )
    if response.status_code != 201:
        return (
            f"ERROR: comment create failed "
            f"(status={response.status_code}): {response.text}"
        )
    comment = response.json()
    return f"Posted comment #{comment['id']} on ticket #{comment['ticket_id']}."


async def read_comments(ticket_id: int) -> str:
    """Read the chronologically ordered comment thread for one ticket."""
    response = await _request("GET", f"/tickets/{int(ticket_id)}/comments")
    if response.status_code == 404:
        return f"ERROR: ticket {ticket_id} does not exist."
    if response.status_code != 200:
        return (
            f"ERROR: comment read failed "
            f"(status={response.status_code}): {response.text}"
        )

    comments = response.json()
    if not comments:
        return f"No comments exist on ticket #{ticket_id}."

    lines = [f"# Comments for ticket #{ticket_id}"]
    for comment in comments:
        lines.extend(
            [
                "",
                (
                    f"## Comment #{comment['id']} — {comment['author']} — "
                    f"{comment['timestamp']}"
                ),
                comment["content"],
            ]
        )
    return "\n".join(lines)


# ---- Knowledge tree tools -------------------------------------------------
#
# The knowledge tree lives upstream of subprojects and tickets. Typical agent
# flow: read files via the host IDE's built-in tools → call
# ``create_knowledge_node(RAW, ...)`` to persist the excerpts with source
# pointers → iteratively compress with ``create_knowledge_node(SUMMARY,
# parent_id=...)`` → emit a ``create_knowledge_node(PRD, ...)`` that the
# human reviews in the UI before breakdown.


async def list_knowledge_nodes(project_id: int) -> str:
    """Return a compact plain-text outline of a project's knowledge tree.

    Calls ``GET /agent/projects/{project_id}/knowledge``. The outline is
    pre-indented and only includes ``[TYPE #id] title`` plus source-ref
    previews — body text is omitted on purpose so the agent can fit the map
    in its context window and drill down with ``read_knowledge_node``.
    """
    response = await _request(
        "GET", f"/agent/projects/{int(project_id)}/knowledge"
    )
    if response.status_code == 404:
        return f"ERROR: project {project_id} does not exist."
    if response.status_code != 200:
        return (
            f"ERROR: knowledge map request failed "
            f"(status={response.status_code}): {response.text}"
        )
    return response.text


async def read_knowledge_node(node_id: int) -> str:
    """Return the full content of a single knowledge node.

    Calls ``GET /agent/knowledge/{node_id}``. Use this after
    ``list_knowledge_nodes`` has surfaced an interesting ``[TYPE #id]``
    breadcrumb you want to expand.
    """
    response = await _request("GET", f"/agent/knowledge/{int(node_id)}")
    if response.status_code == 404:
        return f"ERROR: knowledge node {node_id} does not exist."
    if response.status_code != 200:
        return (
            f"ERROR: knowledge node read failed "
            f"(status={response.status_code}): {response.text}"
        )
    return response.text


async def find_context_trail(project_id: int, query: str, limit: int = 6) -> str:
    """Find the relevant branch of the knowledge tree for a task query.

    Calls ``GET /agent/projects/{project_id}/context-trail``. The response is
    a load-order plan: read the listed nodes first, then drill into the child
    hints if the branch is still too broad. Use this when a fresh agent window
    needs scoped context, e.g. "battle component" in a game project.
    """
    params = urlencode({"query": query, "limit": max(1, min(int(limit), 12))})
    response = await _request(
        "GET", f"/agent/projects/{int(project_id)}/context-trail?{params}"
    )
    if response.status_code == 404:
        return f"ERROR: project {project_id} does not exist."
    if response.status_code != 200:
        return (
            f"ERROR: context trail request failed "
            f"(status={response.status_code}): {response.text}"
        )
    return response.text


async def create_knowledge_node(
    project_id: int,
    title: str,
    node_type: str,
    content: str,
    parent_id: int | None = None,
    source_refs: list[str] | None = None,
) -> str:
    """Persist a new knowledge node under a project.

    Calls ``POST /projects/{project_id}/knowledge``.

    * ``node_type`` must be one of ``RAW``, ``SUMMARY``, ``PRD``, ``TDD``
      (case-insensitive on the way in, normalized before the request).
    * ``parent_id`` nests the node underneath an existing summary so the
      human reviewer can drill down from high-level to raw source. Must
      reference a node in the *same* project.
    * ``source_refs`` is a free-form list of string pointers — absolute
      file paths (``/Users/me/project/api/main.py``), URLs, or
      ``node:<id>`` back-links. These are the breadcrumbs the human follows
      to audit the compression step.
    """
    node_type_upper = node_type.upper()
    if node_type_upper not in VALID_KNOWLEDGE_NODE_TYPES:
        return (
            f"ERROR: invalid node_type '{node_type}'. "
            f"Valid values: {sorted(VALID_KNOWLEDGE_NODE_TYPES)}."
        )

    payload: dict[str, Any] = {
        "title": title,
        "node_type": node_type_upper,
        "content": content,
        "source_refs": list(source_refs) if source_refs else [],
    }
    if parent_id is not None:
        payload["parent_id"] = int(parent_id)

    response = await _request(
        "POST",
        f"/projects/{int(project_id)}/knowledge",
        json=payload,
    )
    if response.status_code == 404:
        return f"ERROR: project {project_id} does not exist."
    if response.status_code == 400:
        return f"ERROR: {response.json().get('detail', response.text)}"
    if response.status_code != 201:
        return (
            f"ERROR: create knowledge node failed "
            f"(status={response.status_code}): {response.text}"
        )
    node = response.json()
    suffix = f" (parent=#{node['parent_id']})" if node.get("parent_id") else ""
    return (
        f"Created knowledge node #{node['id']} [{node['node_type']}]"
        f" {node['title']}{suffix}."
    )


async def update_knowledge_node(
    node_id: int,
    title: str | None = None,
    node_type: str | None = None,
    content: str | None = None,
    parent_id: int | None = None,
    source_refs: list[str] | None = None,
) -> str:
    """Patch an existing knowledge node.

    Calls ``PATCH /knowledge/{node_id}``. Any parameter left as ``None``
    is excluded from the request body so partial updates don't clobber
    unrelated fields. Typical uses:

    * Promote a SUMMARY to a PRD after the human reviews it.
    * Re-parent a node when reorganizing the tree.
    * Append newly discovered source references.
    """
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if node_type is not None:
        node_type_upper = node_type.upper()
        if node_type_upper not in VALID_KNOWLEDGE_NODE_TYPES:
            return (
                f"ERROR: invalid node_type '{node_type}'. "
                f"Valid values: {sorted(VALID_KNOWLEDGE_NODE_TYPES)}."
            )
        payload["node_type"] = node_type_upper
    if content is not None:
        payload["content"] = content
    if parent_id is not None:
        payload["parent_id"] = int(parent_id)
    if source_refs is not None:
        payload["source_refs"] = list(source_refs)

    if not payload:
        return "ERROR: no fields provided to update."

    response = await _request(
        "PATCH", f"/knowledge/{int(node_id)}", json=payload
    )
    if response.status_code == 404:
        return f"ERROR: knowledge node {node_id} does not exist."
    if response.status_code == 400:
        return f"ERROR: {response.json().get('detail', response.text)}"
    if response.status_code != 200:
        return (
            f"ERROR: update knowledge node failed "
            f"(status={response.status_code}): {response.text}"
        )
    node = response.json()
    return (
        f"Updated knowledge node #{node['id']} [{node['node_type']}]"
        f" {node['title']}."
    )


# ---- MCP wiring -----------------------------------------------------------


SAFE_TOOLS: list[Tool] = [
    Tool(
        name="get_all_projects",
        description=(
            "List every project in the workspace (id + name + description). "
            "Call this first when you have no project context."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="create_project",
        description=(
            "Create a new top-level project. Returns the new project's ID "
            "so you can create subprojects underneath it."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short, human-readable project name.",
                    "minLength": 1,
                },
                "description": {
                    "type": "string",
                    "description": "Longer prose explaining the project's purpose.",
                },
            },
            "required": ["name", "description"],
        },
    ),
    Tool(
        name="create_subproject",
        description=(
            "Create a sprint-like subproject under an existing project. "
            "The context_brief will be surfaced verbatim to future agents "
            "via read_subproject_context, so make it rich and goal-oriented."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "integer",
                    "description": "ID of the parent project (from get_all_projects).",
                },
                "name": {"type": "string", "minLength": 1},
                "context_brief": {
                    "type": "string",
                    "description": "Multi-sentence brief describing goals, scope, and constraints.",
                },
            },
            "required": ["project_id", "name", "context_brief"],
        },
    ),
    Tool(
        name="create_ticket",
        description=(
            "Create a new ticket inside a subproject. Tickets always start "
            "in TODO status; use update_ticket_status to move them. "
            "Assignee must be HUMAN, AGENT, or UNASSIGNED."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "subproject_id": {
                    "type": "integer",
                    "description": "ID of the parent subproject.",
                },
                "title": {"type": "string", "minLength": 1},
                "description": {
                    "type": "string",
                    "description": "Task details, acceptance criteria, links.",
                },
                "assignee": {
                    "type": "string",
                    "enum": sorted(VALID_TICKET_ASSIGNEES),
                    "description": "Who owns the ticket on creation.",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Ticket IDs this ticket depends on. All deps must be in the same project.",
                },
            },
            "required": [
                "subproject_id",
                "title",
                "description",
                "assignee",
            ],
        },
    ),
    Tool(
        name="read_subproject_context",
        description=(
            "Return the full flattened context for a subproject, including "
            "the context brief and the current ticket list (status, assignee, "
            "MR links). Call this first so you understand the overarching goal "
            "before touching any tickets."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "subproject_id": {
                    "type": "integer",
                    "description": "ID of the subproject to read.",
                }
            },
            "required": ["subproject_id"],
        },
    ),
    Tool(
        name="get_active_tasks",
        description=(
            "List the subprojects that belong to a project, annotating the "
            "ACTIVE one(s). Use this to choose which subproject to orient "
            "yourself in via read_subproject_context."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "integer",
                    "description": "ID of the parent project.",
                }
            },
            "required": ["project_id"],
        },
    ),
    Tool(
        name="update_ticket_status",
        description=(
            "Move a ticket to a new status and claim it for the agent. "
            "`status` must be one of TODO, IN_PROGRESS, BLOCKED, REVIEW, DONE."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "status": {
                    "type": "string",
                    "enum": sorted(VALID_TICKET_STATUSES),
                },
            },
            "required": ["ticket_id", "status"],
        },
    ),
    Tool(
        name="link_mr",
        description=(
            "Attach a Merge Request / Pull Request URL to a ticket. "
            "The backend records this in the audit log and notifies the UI."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "url": {
                    "type": "string",
                    "description": "Full URL of the MR, e.g. "
                    "https://github.com/org/repo/pull/123",
                },
            },
            "required": ["ticket_id", "url"],
        },
    ),
    Tool(
        name="get_ready_tickets",
        description=(
            "List tickets in a subproject that are ready to be claimed "
            "(status==TODO and all depends_on tickets are DONE)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "subproject_id": {"type": "integer"},
            },
            "required": ["subproject_id"],
        },
    ),
    Tool(
        name="claim_ticket",
        description=(
            "Atomically claim a ticket for a worker. Transitions TODO → IN_PROGRESS "
            "and sets claimed_by, claimed_at, lease_expires_at. Returns 409 if not "
            "TODO or dependencies are unmet."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "worker_id": {
                    "type": "string",
                    "description": "Unique identifier for the claiming worker.",
                    "minLength": 1,
                },
            },
            "required": ["ticket_id", "worker_id"],
        },
    ),
    Tool(
        name="heartbeat_ticket",
        description=(
            "Extend the lease on a claimed ticket. worker_id must match claimed_by."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "worker_id": {"type": "string", "minLength": 1},
                "extend_seconds": {
                    "type": "integer",
                    "description": "How many seconds to extend the lease (default 600, range 60-86400).",
                    "default": 600,
                },
            },
            "required": ["ticket_id", "worker_id"],
        },
    ),
    Tool(
        name="requeue_expired",
        description=(
            "Revert IN_PROGRESS tickets with expired leases back to TODO in a subproject. "
            "Clears claim fields and writes TICKET_REQUEUED audit log entries."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "subproject_id": {"type": "integer"},
            },
            "required": ["subproject_id"],
        },
    ),
    Tool(
        name="leave_comment",
        description=(
            "Post an agent-authored comment on a ticket so the human can see "
            "what the agent is doing and why."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "content": {"type": "string"},
            },
            "required": ["ticket_id", "content"],
        },
    ),
    Tool(
        name="read_comments",
        description=(
            "Read the chronologically ordered comments for one ticket, including "
            "comment IDs, authors, timestamps, and content."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "integer",
                    "description": "ID of the ticket whose comments to read.",
                },
            },
            "required": ["ticket_id"],
        },
    ),
]

DESTRUCTIVE_TOOLS: list[Tool] = [
    Tool(
        name="delete_project",
        description=(
            "Delete a project and cascade every subproject, ticket, comment, "
            "audit log, and knowledge node under it. Irreversible."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
            },
            "required": ["project_id"],
        },
        annotations=ToolAnnotations(
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
    ),
    Tool(
        name="delete_subproject",
        description=(
            "Delete a subproject and cascade its tickets, comments, and audit "
            "logs. Leaves the parent project intact."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "subproject_id": {"type": "integer"},
            },
            "required": ["subproject_id"],
        },
        annotations=ToolAnnotations(
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
    ),
    Tool(
        name="delete_ticket",
        description=(
            "Delete a ticket and cascade its comments and audit logs. Leaves "
            "the parent subproject intact."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
            },
            "required": ["ticket_id"],
        },
        annotations=ToolAnnotations(
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
    ),
    Tool(
        name="delete_knowledge_node",
        description=(
            "Delete a knowledge node and cascade its descendants. Use when "
            "pruning stale research or retiring a superseded PRD/TDD."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "integer"},
            },
            "required": ["node_id"],
        },
        annotations=ToolAnnotations(
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
    ),
]

SAFE_TOOLS.extend(
    [
    Tool(
        name="list_knowledge_nodes",
        description=(
            "Return a compact plain-text outline of a project's knowledge "
            "tree (raw excerpts, summaries, PRDs, TDDs). Call this BEFORE "
            "drafting a PRD/TDD so you know which branches already exist "
            "and can drill down with read_knowledge_node instead of "
            "re-reading raw files."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "integer",
                    "description": "ID of the project whose knowledge tree you want.",
                }
            },
            "required": ["project_id"],
        },
    ),
    Tool(
        name="read_knowledge_node",
        description=(
            "Return the full title, source references, and body content "
            "of a single knowledge node. Use after list_knowledge_nodes "
            "has surfaced an interesting breadcrumb you want to expand."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "integer",
                    "description": "ID of the knowledge node to read.",
                }
            },
            "required": ["node_id"],
        },
    ),
    Tool(
        name="find_context_trail",
        description=(
            "Search the knowledge tree for a task-intent query and return "
            "a suggested load order plus matched branches. Use when a fresh "
            "agent window needs scoped context, e.g. 'battle component'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "query": {
                    "type": "string",
                    "description": "Task or subsystem to orient around.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum matched branches to return (1-12).",
                    "minimum": 1,
                    "maximum": 12,
                },
            },
            "required": ["project_id", "query"],
        },
    ),
    Tool(
        name="create_knowledge_node",
        description=(
            "Persist a new knowledge node under a project. Use RAW for "
            "pasted file excerpts (with absolute paths in source_refs), "
            "SUMMARY for compressed abstractions over existing nodes, and "
            "PRD/TDD for synthesized specifications the human will review "
            "before breakdown. Set parent_id to nest a node under a "
            "summary so the reviewer can drill down."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "title": {"type": "string", "minLength": 1},
                "node_type": {
                    "type": "string",
                    "enum": sorted(VALID_KNOWLEDGE_NODE_TYPES),
                    "description": "RAW, SUMMARY, PRD, or TDD.",
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Full body text. For RAW, paste the file excerpt. "
                        "For SUMMARY/PRD/TDD, write the synthesized prose."
                    ),
                },
                "parent_id": {
                    "type": "integer",
                    "description": (
                        "Optional ID of an existing node to nest under. "
                        "Must be in the same project."
                    ),
                },
                "source_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Absolute file paths, URLs, or node:<id> back-links "
                        "that justify this node's existence."
                    ),
                },
            },
            "required": ["project_id", "title", "node_type", "content"],
        },
    ),
    Tool(
        name="update_knowledge_node",
        description=(
            "Patch an existing knowledge node. Omit any field you don't "
            "want to change. Use this to promote SUMMARY → PRD after "
            "review, re-parent nodes, or append newly discovered "
            "source_refs."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node_id": {"type": "integer"},
                "title": {"type": "string", "minLength": 1},
                "node_type": {
                    "type": "string",
                    "enum": sorted(VALID_KNOWLEDGE_NODE_TYPES),
                },
                "content": {"type": "string"},
                "parent_id": {"type": "integer"},
                "source_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["node_id"],
        },
    ),
    ]
)

TOOLS = [
    *SAFE_TOOLS,
    *(DESTRUCTIVE_TOOLS if DESTRUCTIVE_TOOLS_ENABLED else []),
]


SAFE_TOOL_DISPATCH = {
    "get_all_projects": get_all_projects,
    "create_project": create_project,
    "create_subproject": create_subproject,
    "create_ticket": create_ticket,
    "read_subproject_context": read_subproject_context,
    "get_active_tasks": get_active_tasks,
    "update_ticket_status": update_ticket_status,
    "link_mr": link_mr,
    "get_ready_tickets": get_ready_tickets,
    "claim_ticket": claim_ticket,
    "heartbeat_ticket": heartbeat_ticket,
    "requeue_expired": requeue_expired,
    "leave_comment": leave_comment,
    "read_comments": read_comments,
    "list_knowledge_nodes": list_knowledge_nodes,
    "read_knowledge_node": read_knowledge_node,
    "find_context_trail": find_context_trail,
    "create_knowledge_node": create_knowledge_node,
    "update_knowledge_node": update_knowledge_node,
}
DESTRUCTIVE_TOOL_DISPATCH = {
    "delete_project": delete_project,
    "delete_subproject": delete_subproject,
    "delete_ticket": delete_ticket,
    "delete_knowledge_node": delete_knowledge_node,
}
TOOL_DISPATCH = {
    **SAFE_TOOL_DISPATCH,
    **(
        DESTRUCTIVE_TOOL_DISPATCH
        if DESTRUCTIVE_TOOLS_ENABLED
        else {}
    ),
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[TextContent]:
    handler = TOOL_DISPATCH.get(name)
    if handler is None:
        return _wrap(f"ERROR: unknown tool '{name}'")
    try:
        result = await handler(**(arguments or {}))
    except TypeError as exc:
        return _wrap(f"ERROR: bad arguments for {name}: {exc}")
    except httpx.RequestError as exc:
        return _wrap(
            f"ERROR: API request to {API_URL} failed ({exc.__class__.__name__}): {exc}"
        )
    return _wrap(result)


async def amain() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    if not API_KEY:
        raise SystemExit(
            "MOUVADAH_API_KEY is missing. Run `python3 bootstrap.py`, set "
            "MOUVADAH_API_KEY, or set MOUVADAH_CREDENTIALS_FILE. Legacy "
            "TASKABLE_* aliases remain accepted."
        )
    asyncio.run(amain())


if __name__ == "__main__":
    main()
