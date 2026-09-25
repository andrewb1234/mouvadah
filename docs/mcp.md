# Mouvadah MCP server

Mouvadah exposes the same tool catalogue through two transports:

- **Hosted:** `https://mouvadah.com/mcp`, Streamable HTTP with OAuth account
  linking, usable by Claude web/mobile without installation or API keys.
- **Local:** the `mouvadah-mcp` stdio process with an API key for either a local
  installation or the hosted REST API.

See [the setup guide](../mcp/README.md) for user instructions and
[hosted MCP operations](./hosted-mcp.md) for security and deployment details.

> **Source of truth:** `mcp/mcp_server.py`. Every tool below is registered in
> the `TOOLS` list and dispatched from `TOOL_DISPATCH`.

## Local bridge dependencies
* `mcp >=1.1` (Official Python SDK)
* `httpx` (Async HTTP client for REST calls)

See `mcp/pyproject.toml` for the packaging manifest. Install via
`pipx install mouvadah-mcp==0.1.1`, `uv tool install mouvadah-mcp==0.1.1`, or
`pip install -e ./mcp` to get the `mouvadah-mcp` console script.

## Local bridge configuration
* **API Target:** `MOUVADAH_API_URL` (default
  `http://localhost:8000/api/v1`).
  Trailing slashes are stripped at load time.
* **Transport:** Standard Input/Output (`stdio`).
* **Auth:** Every request sends `Authorization: Bearer {MOUVADAH_API_KEY}`.
  The value is a revocable per-user key stored only as a hash by the API.
  Supply it directly or point `MOUVADAH_CREDENTIALS_FILE` at the owner-only
  file created by `bootstrap.py`.

Configuration is read only from the bridge process environment and the
explicit credentials file. The bridge never searches its current or parent
directory for `.env` files.

The legacy `TASKABLE_API_URL`, `TASKABLE_API_KEY`,
`TASKABLE_CREDENTIALS_FILE`, and `taskable-mcp` names remain accepted for
existing installations.

## Tool Catalogue

All tools return a plain-text `TextContent` frame. Error responses begin with
`ERROR:` so callers can detect failure without parsing JSON.

### Read tools

1. **`get_all_projects() -> str`**
   * **Action:** `GET /api/v1/projects`
   * **Return:** Markdown list of projects with their IDs and descriptions.
     Returns a hint to call `create_project` if no projects exist.

2. **`get_active_tasks(project_id: int) -> str`**
   * **Action:** `GET /api/v1/projects/{project_id}/subprojects`
   * **Return:** Human-readable list of subprojects under the project,
     flagging the `ACTIVE` one(s). Useful before `read_subproject_context`.

3. **`read_subproject_context(subproject_id: int) -> str`**
   * **Action:** `GET /api/v1/agent/context/{subproject_id}`
   * **Return:** Full flattened LLM-optimized text including the
     `context_brief` and every non-DONE ticket (status / assignee / MR link).
     **Call this first** to orient before touching any tickets.

4. **`read_comments(ticket_id: int) -> str`**
   * **Action:** `GET /api/v1/tickets/{ticket_id}/comments`
   * **Return:** Chronologically ordered comments for one ticket, including
     each comment's ID, author, timestamp, and content.

### Create tools

5. **`create_project(name: str, description: str) -> str`**
   * **Action:** `POST /api/v1/projects`
   * **Payload:** `{"name": name, "description": description}`
   * **Return:** Confirmation with the new project's ID, formatted so
     subsequent `create_subproject` calls can extract it.

6. **`create_subproject(project_id: int, name: str, context_brief: str) -> str`**
   * **Action:** `POST /api/v1/projects/{project_id}/subprojects`
   * **Payload:** `{"name": name, "context_brief": context_brief}`
   * **Return:** Confirmation with the new subproject's ID and parent project.
     Returns an explicit `ERROR` if the project does not exist.

7. **`create_ticket(subproject_id: int, title: str, description: str, assignee: str) -> str`**
   * **Action:** `POST /api/v1/subprojects/{subproject_id}/tickets`
   * **Payload:** `{"title": title, "description": description, "assignee": assignee}`
   * **Constraint:** `assignee` must be `HUMAN`, `AGENT`, or `UNASSIGNED`
     (case-insensitive at tool entry; normalized to uppercase before POST).
     Enforced both by the tool's `inputSchema.enum` AND by an in-handler guard.
   * **Note:** Tickets always start in `TODO`. Use `update_ticket_status` to
     move them afterwards.

### Mutate tools

8. **`update_ticket_status(ticket_id: int, status: str) -> str`**
   * **Action:** `PATCH /api/v1/tickets/{ticket_id}`
   * **Payload:** `{"status": status, "assignee": "AGENT"}` (the agent always
     claims the ticket on status change — matches PRD expectations).
   * **Constraint:** `status` must be one of `TODO`, `IN_PROGRESS`, `BLOCKED`,
     `REVIEW`, `DONE`.

9. **`link_mr(ticket_id: int, url: str) -> str`**
   * **Action:** `POST /api/v1/tickets/{ticket_id}/mr`
   * **Payload:** `{"url": url}`
   * **Side effects:** Writes an `AuditLog` entry with `action=MR_LINKED`;
     broadcasts an SSE event so the UI refreshes live.

10. **`leave_comment(ticket_id: int, content: str) -> str`**
   * **Action:** `POST /api/v1/tickets/{ticket_id}/comments`
   * **Payload:** `{"author": "AGENT", "content": content}`

### Delete tools

Delete tools are hidden by default. They are registered only when
`MOUVADAH_ENABLE_DESTRUCTIVE_TOOLS=true`, and the API key must carry the
separate `delete` scope. Supporting clients receive destructive-operation
annotations and should require confirmation.

11. **`delete_project(project_id: int) -> str`**
    * **Action:** `DELETE /api/v1/projects/{project_id}`
    * **Side effects:** Cascades every subproject, ticket, comment, audit log,
      and knowledge node under the project. Broadcasts `PROJECT_DELETED`.

12. **`delete_subproject(subproject_id: int) -> str`**
    * **Action:** `DELETE /api/v1/subprojects/{subproject_id}`
    * **Side effects:** Cascades tickets, comments, and audit logs under the
      subproject. Broadcasts `SUBPROJECT_DELETED`.

13. **`delete_ticket(ticket_id: int) -> str`**
    * **Action:** `DELETE /api/v1/tickets/{ticket_id}`
    * **Side effects:** Cascades comments and audit logs under the ticket.
      Broadcasts `TICKET_DELETED`.

### Knowledge tree tools

14. **`list_knowledge_nodes(project_id: int) -> str`**
    * **Action:** `GET /api/v1/agent/projects/{project_id}/knowledge`
    * **Return:** Compact hierarchical outline of every RAW / SUMMARY / PRD /
      TDD node in a project, including source references.

15. **`read_knowledge_node(node_id: int) -> str`**
    * **Action:** `GET /api/v1/agent/knowledge/{node_id}`
    * **Return:** Single node detail with title, type, source references, and
      content. Use after `list_knowledge_nodes` to drill into relevant context.

16. **`find_context_trail(project_id: int, query: str, limit?: int) -> str`**
    * **Action:** `GET /api/v1/agent/projects/{project_id}/context-trail?query=...`
    * **Return:** Markdown load order and matched branches for a task-intent
      query such as `battle component`. Use this when a fresh agent window
      needs scoped memory without loading the whole tree.

17. **`create_knowledge_node(project_id, title, node_type, content, parent_id?, source_refs?) -> str`**
    * **Action:** `POST /api/v1/projects/{project_id}/knowledge`
    * **Payload:** `title`, `node_type` (`RAW`, `SUMMARY`, `PRD`, `TDD`),
      optional `content`, optional `parent_id`, and optional `source_refs`.
    * **Side effects:** Tags `created_by` from the bearer header and broadcasts
      `KNOWLEDGE_NODE_CREATED`.

18. **`update_knowledge_node(node_id, title?, node_type?, content?, parent_id?, source_refs?) -> str`**
    * **Action:** `PATCH /api/v1/knowledge/{node_id}`
    * **Payload:** Sparse update. Parent changes are rejected if they cross
      projects or create a cycle.
    * **Side effects:** Broadcasts `KNOWLEDGE_NODE_UPDATED`.

19. **`delete_knowledge_node(node_id: int) -> str`**
    * **Action:** `DELETE /api/v1/knowledge/{node_id}`
    * **Side effects:** Cascades descendant knowledge nodes and broadcasts
      `KNOWLEDGE_NODE_DELETED`.

## Execution Entrypoint
`Server("mouvadah")` is run via `mcp.server.stdio.stdio_server()`
inside `main() -> asyncio.run(amain())`. The installed console script
`mouvadah-mcp` calls `main()` directly.

## IDE / Client Configuration
See `mcp/mcp.json.example` for a drop-in Windsurf / Claude Desktop block with
both the `mouvadah-mcp`-on-`$PATH` form and the venv-relative fallback.

## Testing
* `api/tests/test_mcp_simulator.py` boots a real uvicorn + the MCP stdio
  server as subprocesses and drives the full JSON-RPC handshake. Scenarios
  cover: (a) the read/mutate flow, (b) invalid-status rejection, (c) the
  create-project → create-subproject → create-ticket chain with DB
  verification, and (d) the knowledge-tree create/list/trail/read/update flow. Run
  with `.venv/bin/pytest api/tests/test_mcp_simulator.py`.
