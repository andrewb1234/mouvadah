# Learnings Ledger

> Append-only record of frictions, divergences, and workarounds encountered while building Taskable.
> Protocol: read this file at the start of every session before mutating code. Never rewrite history — only append.

## Entry Format
Each entry MUST use this template:

```
### [ISO-8601 Timestamp] — [Short Title]
- **Context:** What operation was being attempted.
- **Friction:** The specific error, block, or hallucination.
- **Resolution:** The technical solution and reasoning.
```

## When to Append
- Resolving a complex bug, race condition, or framework limitation.
- Making an architectural divergence from the original specifications.
- Discovering a specific workaround for local Docker or MCP stdio networking.

---

## Entries

### 2026-04-17T07:02:00Z — Ledger Initialized
- **Context:** Pre-build ingestion of `docs/index.md`, `docs/prd.md`, `docs/folder_navigation.md`, and `docs/protocol.md` before scaffolding the Taskable Co-Pilot Workspace.
- **Friction:** None. Initialization step per `protocol.md`.
- **Resolution:** Created `learnings.md` with an explicit entry format so future sessions can append deterministically. Subsequent entries will follow the template above.

### 2026-04-17T07:10:00Z — Python 3.14 vs. pinned pydantic-core
- **Context:** First `pip install -r api/requirements.txt` on local Python 3.14.0 while bringing up the FastAPI scaffold.
- **Friction:** `pydantic==2.10.3` depends on `pydantic-core==2.27.x`, whose Rust/PyO3 build caps at Python 3.13: `error: the configured Python interpreter version (3.14) is newer than PyO3's maximum supported version (3.13)`.
- **Resolution:** Switched to inclusive version ranges (`pydantic>=2.11,<3`, `pydantic-core` resolved to 2.46.1) which ship prebuilt 3.14 wheels. Kept FastAPI/SQLModel on the latest compatible releases. This keeps the project forward-compatible without forcing users onto Python 3.11.

### 2026-04-17T07:12:00Z — Divergence: UI is unauthenticated on localhost
- **Context:** `docs/api_endpoints.md` declares the bearer token `(Agent only)`, but the React client writes via the same REST endpoints.
- **Friction:** Spec does not say how the UI authenticates.
- **Resolution:** Chose option (a): the bearer guard only applies to the `/agent/*` routes (explicitly Agent-only per the spec's "Agent Integrations" section). Mutation routes inspect the `Authorization` header softly to tag the audit log `actor` as `AGENT` vs `HUMAN`. Logged so we don't regress to per-request auth later.

### 2026-04-17T07:12:30Z — Divergence: `GET /tickets/{id}` added
- **Context:** `docs/client_server.md` assumes the UI can targeted-refetch with `GET /tickets/42`, but that route is missing from `docs/api_endpoints.md`.
- **Friction:** Implementing the SSE refetch lifecycle required this route.
- **Resolution:** Added `GET /tickets/{id}` returning a `TicketDetail` (ticket + ordered comments + audit log). Treated as an implicit requirement of the SSE refetch contract, not a scope expansion.

### 2026-04-17T07:13:00Z — Decision: `utcnow()` helper replaces `datetime.utcnow`
- **Context:** Schema spec says default `datetime.utcnow`. Python 3.12+ deprecates it.
- **Friction:** Direct use would spam DeprecationWarnings during tests.
- **Resolution:** Added `api/utils/time.py::utcnow()` returning a timezone-naive UTC datetime via `datetime.now(timezone.utc).replace(tzinfo=None)`. One-line swap if we later want timezone-aware storage.

### 2026-04-17T07:22:00Z — SQLAlchemy mapper vs PEP 563 string annotations
- **Context:** First pytest run after scaffolding the API surface. All 16 DB-touching tests failed identically.
- **Friction:** `sqlalchemy.exc.InvalidRequestError: ... expression "relationship("list['Subproject']")" seems to be using a generic class as the argument to relationship()`. Cause: `from __future__ import annotations` converts `list["Subproject"]` into a literal string, and SQLAlchemy's relationship introspection cannot unwrap stringified generics at class construction time.
- **Resolution:** Removed `from __future__ import annotations` from `api/models/entities.py` and switched to `typing.List[...]`. Kept the future import elsewhere where it helps. Added a header comment so future edits don't re-introduce the import. 17/17 API tests now green.

### 2026-04-17T17:05:00Z — Playwright `baseURL` strips its path when request paths start with `/`
- **Context:** Wrote a Playwright realtime spec with `request.newContext({ baseURL: "http://127.0.0.1:8000/api/v1" })` and `api.post("/projects", ...)`. The request returned `404 {"detail":"Not Found"}`.
- **Friction:** Playwright resolves paths via the WHATWG URL algorithm. A leading-slash request path is treated as absolute and **replaces** the baseURL's path, turning `http://.../api/v1` + `/projects` into `http://.../projects`. Invisible until you add error logging.
- **Resolution:** Give the baseURL a trailing slash AND use relative paths without a leading slash. Documented inline in `web/tests/realtime.spec.ts`. All subsequent paths (`projects`, `subprojects/{id}/tickets`, etc.) are relative.

### 2026-04-17T17:00:00Z — Packaging the MCP server with a flat `py-modules`
- **Context:** Wanted `pipx install ./mcp` / `uv tool install ./mcp` to produce a stable `taskable-mcp` binary so Windsurf configs can avoid venv-relative paths.
- **Friction:** Standard `setuptools.packages.find` would try to treat `mcp/` itself as a package but that conflicts with our rule that `mcp/` must NOT be a Python package (shadowing the upstream `mcp` SDK). Also conflicts with the flat single-file layout we actually have.
- **Resolution:** Used `[tool.setuptools] py-modules = ["mcp_server"]` plus an empty `packages.find` to declare a flat one-module wheel. Confirmed with `pip install -e ./mcp && taskable-mcp` running a full JSON-RPC handshake.

### 2026-04-17T16:55:00Z — Host-mounted SQLite beats named volumes for single-user local apps
- **Context:** The spec originally set `DATABASE_URL=sqlite:///./data/taskable.db` and the docker-compose used a `sqlite_data:` named volume.
- **Friction:** Both paths meant the user had to `docker exec` or traverse Docker's hidden volume mountpoint to back up / inspect the DB. The relative `./data/` form also broke when anyone ran `git clean -fdx`.
- **Resolution:** Defaulted to `~/.taskable/taskable.db` in `api/config.py`, bind-mounted `${HOME}/.taskable` → `/app/data` in docker-compose. `~` isn't expanded by compose; `$HOME` is, so the compose YAML uses the latter. Now users can open the DB with any desktop SQLite tool.

### 2026-04-17T16:50:00Z — pytest-asyncio on Python 3.14 emits DeprecationWarnings we can't filter
- **Context:** Every test run prints 10 DeprecationWarnings from `pytest_asyncio/plugin.py` about `asyncio.set_event_loop_policy` / `asyncio.get_event_loop_policy`.
- **Friction:** No `filterwarnings` regex in `pytest.ini` makes them go away. They fire inside pytest-asyncio's own fixture lifecycle, which runs *after* pytest has already installed our configured filters.
- **Resolution:** Kept the filters that CAN work, documented the rest inline so the next contributor doesn't chase the same wall. Will revisit once `pytest-asyncio` ships a 3.14-native release.

### 2026-04-17T07:35:00Z — Folder-name collision: `mcp/` vs installed `mcp` package
- **Context:** After `pip install mcp>=1.1`, attempted `python -c "import mcp.mcp_server"` to smoke-test tool definitions.
- **Friction:** `ModuleNotFoundError: No module named 'mcp.mcp_server'` because our local `mcp/` directory has no `__init__.py` so it isn't a package; Python resolves `mcp` to the installed SDK instead.
- **Resolution:** Don't make `mcp/` a package. Invoke `python mcp/mcp_server.py` directly (Python prepends the script's directory to `sys.path`, so `import mcp` still resolves to the installed SDK). Documented in `mcp/README.md`. If we ever need to import the server module programmatically, do `sys.path.insert(0, "mcp"); import mcp_server`.

### 2026-04-17T07:13:30Z — Decision: MR route is attach-only for MVP
- **Context:** `POST /tickets/{id}/mr` is described as "attach MR link or trigger branch generation."
- **Friction:** Scope of branch generation is undefined; `GITHUB_PAT` is mentioned but MCP tool `link_mr` only attaches.
- **Resolution:** MVP implements attach-only. Writes `AuditAction.MR_LINKED` and emits `SSEAction.MR_LINKED`. Branch creation can be added later behind a `GITHUB_PAT`-gated helper without changing the route contract.

### 2026-04-24T05:30:00Z — Delete endpoints + resizable layout + live source-ref chips
- **Context:** Human asked for four things in one shot: (1) draggable resize between projects/knowledge tabs and between kanban header and columns; (2) delete for projects, subprojects, tickets through UI + REST + MCP; (3) investigation of image-upload effort for tickets; (4) `node:N` source-ref chips that live-update with the referenced node's title.
- **Friction:**
  - Tempting to pull in `react-resizable-panels` (~10 KB gz) for the resize work. Skipped — three use-cases are all two-pane splits with bounded sizes, so a 60-line `ResizableSplit` primitive using pointer events + localStorage covers it without a new dep. Also dodges the Radix compatibility churn that `react-resizable-panels` has been through.
  - `TicketCard` was a `<button>`, which made a nested delete button invalid HTML and blocked hover-reveal pattern. Converted the card to a `<div role="button" tabIndex={0}>` with keyboard handlers so the delete pill can sit inside the clickable surface.
  - Sidebar and KnowledgePanel both hard-coded their widths (`w-72`, `w-80`). Had to strip those since `ResizableSplit` owns the outer width now. Left an `h-full w-full` so the children still stretch.
  - Live title updates for source-ref chips: the SSE event re-fetches `listKnowledgeNodes` in the panel, and the editor already derives its chip list from `allNodes` passed in as a prop, so the chip re-renders automatically. Zero new plumbing.
  - Cascade deletes work out of the box via existing `cascade="all, delete-orphan"` relationships on `Project.subprojects`, `Project.knowledge_nodes`, `Subproject.tickets`, `Ticket.comments`, and `KnowledgeNode.children`. Tests confirm the whole tree sweeps with a single `session.delete`.
- **Resolution:** Shipped three DELETE routes (`/projects/{id}`, `/subprojects/{id}`, `/tickets/{id}`), three SSE actions (`PROJECT_DELETED`, `SUBPROJECT_DELETED`, `TICKET_DELETED`), and four MCP tools (`delete_project`, `delete_subproject`, `delete_ticket`, `delete_knowledge_node`). Frontend wired trash icons into `Sidebar`, `SubprojectHeader`, `TicketCard`, and `TicketModal`. `ResizableSplit` handles all three gutters with per-split `storageKey`s. `NodeEditor` now receives `allNodes` + `onSelectNode`, parses each `source_refs` line for `node:<id>`, and renders live clickable chips that follow SSE title updates; dangling references show a red `GONE` pill. 5 new backend tests verify cascade behavior and SSE emission.
- **Image uploads (investigation):** Scoped but not implemented. Minimum viable path: (a) new `TicketAttachment` table (id, ticket_id, filename, content_type, size, stored_at, uploaded_by, created_at); (b) filesystem storage under `~/.taskable/attachments/<ticket_id>/<uuid>.<ext>` (no S3 for local-first); (c) three routes — `POST /tickets/{id}/attachments` (multipart), `GET /attachments/{id}` (streams bytes with proper content-type), `DELETE /attachments/{id}` (unlinks file + row); (d) frontend: file input + paste-handler in `TicketModal`, thumbnail grid in description area, enforce 10 MB and `image/*` mimes client+server. Effort: ~2-3 hours including tests. Deferred — the ask was to investigate only and the current deletion + resize + live-refs work already covers the high-value slice. Logging here so the next contributor has a starting plan.

### 2026-04-24T04:30:00Z — Knowledge tree: sharpened the "breadcrumb DAG" proposal before building
- **Context:** Proposal asked for a `KnowledgeNode` DAG plus three MCP tools — `ingest_context` (walks a directory), `compress_nodes` (LLM summarizes), `draft_specification` (outputs PRD/TDD) — and a node-link diagram UI.
- **Friction:** Several concerns: (1) the agent's host IDE already reads files, so `ingest_context` duplicates capability *and* opens a fs-walk-to-remote-POST footgun; (2) `compress_nodes` conflates "tool persists summary" with "LLM compresses" (the LLM does the compression in its own head); (3) `draft_specification` is just another node type, not a separate primitive; (4) `parent_id` alone is a tree, not a DAG (no multi-parent); (5) node-link diagrams render slowly vs a collapsible tree that matches how humans think about hierarchies.
- **Resolution:** Shipped a tree (not DAG — defer to edges table if needed) of `KnowledgeNode` with four types (`RAW`, `SUMMARY`, `PRD`, `TDD`), two UI-side CRUD routes (`/projects/{id}/knowledge` + `/knowledge/{id}`), and two agent-gated read routes (`/agent/projects/{id}/knowledge` returns a compact indented outline, `/agent/knowledge/{id}` returns a single node). The MCP exposes four tools: `list_knowledge_nodes`, `read_knowledge_node`, `create_knowledge_node` (handles RAW/SUMMARY/PRD/TDD through the `node_type` parameter), `update_knowledge_node` (re-parent, promote type, append refs). UI adds a project-level tab (`Workspace.tsx`) that swaps the Kanban board for a collapsible `KnowledgePanel` with type-coded left borders (slate/amber/sky/emerald). Full SSE coverage via three new actions (`KNOWLEDGE_NODE_CREATED|UPDATED|DELETED`) so agent-driven writes reconcile on the human's screen live. New backend tests (8) + MCP simulator flow assert the wire protocol end-to-end.

### 2026-05-03T11:37:13Z — Context trails as scoped agent memory
- **Context:** Human reframed the product away from PRD/TDD workflow enforcement and toward an agent memory cache: the agent should offload knowledge, later reload only the relevant branch (for example a game's battle component), and expose wrong or stale context for human correction.
- **Friction:**
  - Adding another durable entity for "agent sessions" would have created migration pressure and another process surface before proving the primitive. Kept the data model on `KnowledgeNode` and made context trails a read-side projection over the existing tree.
  - A vector/embedding search would be attractive but violates the local-first, dependency-light posture and makes human debugging harder. Used deterministic token scoring across title/content/source refs so a human can understand why a branch was selected.
  - The UI imports a `Map` icon from `lucide-react`; this shadowed the built-in `Map` constructor inside `KnowledgePanel.tsx`. Aliased it to `MapIcon` so `new Map<number, KnowledgeNode>()` remains typed correctly.
- **Resolution:** Added JSON endpoint `GET /projects/{id}/knowledge/context-trail` and bearer-gated markdown endpoint `GET /agent/projects/{id}/context-trail`, backed by `api/utils/context_trails.py`. Added MCP tool `find_context_trail(project_id, query, limit?)`. Knowledge UI now has a "Find context trail" panel that returns a load order, matched branches, child hints, and a one-click context checkpoint node with `node:<id>` refs. Node editor now has a correction-request box that creates a child `SUMMARY` under the questionable node so stale context is visible and actionable. Demo seed is now a game-oriented BattleForge project with battle knowledge, correction requests, and checkpoint examples.

### 2026-09-25 — OAuth consent needs same-origin request metadata
- **Context:** Adding browser consent for the hosted MCP OAuth connector.
- **Friction:** `Referrer-Policy: no-referrer` made Chromium send an opaque Origin on the consent form POST, so exact-origin CSRF validation rejected a legitimate approval. CSP `form-action` also applies to redirects following that POST.
- **Resolution:** The consent document uses `Referrer-Policy: same-origin`, keeps exact-origin POST validation, and permits only its validated registered callback origin in `form-action`. Verified with a real mobile-width browser approval, a loopback callback, and an official MCP SDK initialize/list/read round trip; the temporary grant was revoked afterward.
