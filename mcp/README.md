# Mouvadah MCP

A Model Context Protocol server that exposes the Mouvadah REST API to agentic
IDEs over `stdio`. The FastAPI backend must be running on the URL you supply
via `MOUVADAH_API_URL` (default `http://localhost:8000/api/v1`).

## Install

```bash
# Published package:
pipx install mouvadah-mcp==0.1.1

# Or install a checked-out source tree:
pipx install ./mcp

# uv alternative:
uv tool install ./mcp
```

The installed command is `mouvadah-mcp`. The legacy `taskable-mcp` alias is
also installed during the naming transition.

## Run locally (smoke test)

```bash
MOUVADAH_CREDENTIALS_FILE=~/.config/mouvadah/credentials.env \
  MOUVADAH_API_URL=http://localhost:8000/api/v1 \
  mouvadah-mcp
```

Stdio input/output is MCP protocol; use an MCP-aware client to interact.

## Windsurf configuration

Run `python3 bootstrap.py` from a source checkout to configure Windsurf
automatically, or add the
contents of `mcp.json.example` to its MCP config. The server reads a per-user
database-backed key from `MOUVADAH_API_KEY` or from the owner-only
`MOUVADAH_CREDENTIALS_FILE`; the backend never shares a static bypass secret.
The legacy `TASKABLE_API_URL`, `TASKABLE_API_KEY`, and
`TASKABLE_CREDENTIALS_FILE` names remain accepted for existing installations.

## Exposed Tools

| Tool                        | Action                                             |
| --------------------------- | -------------------------------------------------- |
| `get_all_projects`          | `GET /projects`                                    |
| `create_project`            | `POST /projects` (`name`, `description`)           |
| `create_subproject`         | `POST /projects/{id}/subprojects`                  |
| `create_ticket`             | `POST /subprojects/{id}/tickets`                   |
| `read_subproject_context`   | `GET /agent/context/{id}` — LLM-flat text brief    |
| `get_active_tasks`          | `GET /projects/{id}/subprojects`                   |
| `update_ticket_status`      | `PATCH /tickets/{id}` (`status`, `assignee=AGENT`) |
| `link_mr`                   | `POST /tickets/{id}/mr`                            |
| `leave_comment`             | `POST /tickets/{id}/comments` (`author=AGENT`)     |
| `read_comments`             | `GET /tickets/{id}/comments`                       |
| `delete_project`            | `DELETE /projects/{id}`                            |
| `delete_subproject`         | `DELETE /subprojects/{id}`                         |
| `delete_ticket`             | `DELETE /tickets/{id}`                             |
| `list_knowledge_nodes`      | `GET /agent/projects/{id}/knowledge`               |
| `read_knowledge_node`       | `GET /agent/knowledge/{id}`                        |
| `find_context_trail`        | `GET /agent/projects/{id}/context-trail?query=...` |
| `create_knowledge_node`     | `POST /projects/{id}/knowledge`                    |
| `update_knowledge_node`     | `PATCH /knowledge/{id}`                            |
| `delete_knowledge_node`     | `DELETE /knowledge/{id}`                           |

## Notes

- Transport is `stdio`; no network ports are opened by this process.
- Every HTTP call injects the authenticated user's
  `Authorization: Bearer <MOUVADAH_API_KEY>` header. The key is revocable and
  inherits only its owning user's workspace memberships.
- The bridge reads configuration only from its explicit process environment
  and the configured owner-only credentials file. It never loads `.env` from
  the repository where an agent happens to run.
- Cascading deletion tools are hidden by default. To expose them deliberately,
  create an API key with the separate `delete` scope and set
  `MOUVADAH_ENABLE_DESTRUCTIVE_TOOLS=true` in the MCP client configuration.
  Supporting clients receive destructive-operation annotations and should
  require confirmation.
- Error payloads from the API bubble up verbatim so the LLM can self-correct.

## License

The Mouvadah MCP bridge is licensed under
[Apache-2.0](./LICENSE). Copyright and attribution information is in
[`NOTICE`](./NOTICE). The server and web application in the parent repository
are separately licensed under AGPL-3.0-only.
