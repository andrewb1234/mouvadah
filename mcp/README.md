# Mouvadah MCP

Choose one connection method. Hosted users do not need to download or host a server.

## Hosted connector — Claude web, desktop, and mobile

1. Sign in to [Mouvadah](https://mouvadah.com) with your own Google account.
   Accept any invitation to a shared workspace before connecting.
2. Open [Claude Settings → Connectors](https://claude.ai/settings/connectors)
   in a browser and choose **Add custom connector**. Name it **Mouvadah** and
   enter `https://mouvadah.com/mcp`. Leave advanced client ID and secret empty;
   Claude registers automatically.
3. Choose **Connect**. Sign in to Mouvadah, select the workspace and access
   level, then choose **Allow connection**. Each connection grants access to
   one workspace. Read-only access excludes tools that change data.
4. Open Claude mobile using the same Claude account. Enable Mouvadah in the
   chat’s connectors/tools and ask **“List my Mouvadah projects.”**

No installation or API key is required. If the mobile app has no custom
connector setup option, add it on claude.ai first. Your Claude organization
may restrict custom connectors; an administrator may need to enable them.

Connections appear in Mouvadah **Settings → API Keys** as **MCP: Claude**
(or the client’s registered name). Revoke a connection there to stop both
access and refresh tokens immediately. Authorization lasts 30 days; reconnect
when it expires. Disconnect in Claude as well if you no longer need it.

### Troubleshooting

- **Workspace missing:** confirm you signed in to the invited Mouvadah account
  and accepted the invitation. Your Claude and Mouvadah email addresses do not
  have to match.
- **Projects missing:** check which workspace you approved. Reconnect and
  select the intended workspace.
- **Cannot edit:** reconnect with read/write access. Workspace viewers can
  only approve read-only access.
- **Authorization expired or revoked:** reconnect from Claude’s connector settings.
- **Connection failed:** use the full `/mcp` URL, not `/api/v1`. This endpoint
  uses Streamable HTTP with OAuth; an ordinary Mouvadah API key is for the
  local bridge and will not authenticate this endpoint.

## Local MCP bridge — desktop clients

Choose this for a client that launches a process on your computer. It works
with either the hosted REST API or your own local Mouvadah installation.

```bash
pipx install mouvadah-mcp==0.1.1
# Or:
uv tool install mouvadah-mcp==0.1.1
# From a source checkout:
pipx install ./mcp
```

The installed command is `mouvadah-mcp` (`taskable-mcp` remains a legacy alias).
Create a scoped API key in **Settings → API Keys on the server you want to use**.
Configure your desktop client with:

```json
{
  "mcpServers": {
    "mouvadah": {
      "command": "mouvadah-mcp",
      "env": {
        "MOUVADAH_API_URL": "https://mouvadah.com/api/v1",
        "MOUVADAH_API_KEY": "YOUR_API_KEY"
      }
    }
  }
}
```

Use the desktop client’s configuration format (VS Code uses `servers` instead
of `mcpServers` and `type: "stdio"`). Save files with owner-only permissions,
restart the client, and ask it to list projects. The in-app **Setup Guide →
Local MCP bridge** generates the configuration for this server.

### Run the application locally too

Follow the [Community installation guide](../README.md#run-mouvadah-locally).
Use your local API URL, usually `http://localhost:8000/api/v1`, and a key issued
by that local installation. Hosted and local credentials are separate.

Source bootstrap (`python3 bootstrap.py`) also installs the bridge and can
configure Windsurf automatically. For another client, adapt
[`mcp.json.example`](./mcp.json.example). Use an absolute path to
`.venv/bin/mouvadah-mcp` if it is not on your client’s PATH.

The bridge can read its key from the owner-only
`MOUVADAH_CREDENTIALS_FILE`, such as `~/.config/mouvadah/credentials.env`,
instead of embedding it in JSON. Legacy `TASKABLE_API_URL`, `TASKABLE_API_KEY`,
and `TASKABLE_CREDENTIALS_FILE` settings remain supported.

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

- The standalone bridge uses `stdio` and opens no network port. The hosted
  connector uses Streamable HTTP at `/mcp` and never uses local credentials.
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
