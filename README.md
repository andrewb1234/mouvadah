# Mouvadah — shared control and memory for humans and coding agents

Mouvadah gives a software team and its agents one durable place for project
knowledge, dependency-aware work, claims, handoffs, and review. Humans use the
web interface; agents use the same state through the hosted
[Model Context Protocol (MCP)](https://modelcontextprotocol.io/) connector or a local MCP bridge.

**Current status:** Mouvadah is an alpha. You can evaluate the hosted
application at [mouvadah.com](https://mouvadah.com), install Mouvadah Community
`v0.1.1`, or run it from source. The hosted application does not yet carry
production availability, recovery, security-assessment, or support guarantees.

## What Mouvadah does

- Preserves project knowledge across short-lived agent sessions.
- Makes ticket dependencies, readiness, ownership, and blockers explicit.
- Prevents duplicate agent work with atomic claims, leases, and recovery.
- Keeps human and agent activity in one reviewable workflow.
- Connects local agent harnesses through a vendor-neutral MCP server.

## Connect Claude on web or mobile

Use the hosted service without downloading or hosting Mouvadah:

1. Sign in at [mouvadah.com](https://mouvadah.com). If you need a shared
   workspace, accept its invitation first.
2. In [Claude Settings → Connectors](https://claude.ai/settings/connectors),
   add a custom connector named **Mouvadah** with URL
   `https://mouvadah.com/mcp`. Leave advanced client ID/secret fields empty.
3. Choose **Connect**, sign in to your Mouvadah account, select a workspace
   and read-only or read/write access, and approve.
4. In Claude mobile, use the same Claude account, enable Mouvadah in your
   chat’s connectors/tools, and ask **“List my Mouvadah projects.”** If adding
   custom connectors is unavailable in the app, complete steps 2–3 in a browser.

No API key or local bridge is needed for this path. Organization policies may
restrict custom connectors. Grants expire after 30 days; reconnect to renew.
Revoke anytime in Mouvadah **Settings → API Keys**, where connections are named
**MCP: …**. See [the connector guide](./mcp/README.md) for troubleshooting.

## Run Mouvadah locally

Choose this path to keep the application and database on your own computer.
Use the local installation’s API key and API URL, not credentials from the
hosted service. The desktop MCP bridge uses stdio; Claude mobile cannot launch it.

### Install without cloning

The packaged Community installation requires Docker with Compose v2. On macOS
or Linux, install the release command with Homebrew:

```bash
brew install andrewb1234/tap/mouvadah
mouvadah install --email you@example.com --name "Your Name"
```

Or download and verify the release command directly:

```bash
curl -fsSLO https://github.com/andrewb1234/mouvadah/releases/download/v0.1.1/mouvadah
curl -fsSLO https://github.com/andrewb1234/mouvadah/releases/download/v0.1.1/SHA256SUMS
grep '  mouvadah$' SHA256SUMS | shasum -a 256 -c -
chmod +x mouvadah
./mouvadah install --email you@example.com --name "Your Name"
```

The command downloads the checksummed Compose manifest, binds the UI and API
to loopback, creates an authenticated local owner, and preserves its data when
you run `mouvadah uninstall`.

For a desktop client that needs a local stdio bridge, install the independently
licensed Python package (this is separate from the hosted connector above):

```bash
pipx install mouvadah-mcp==0.1.1
```

### Develop from source

The commands below are the recommended macOS/Linux development path.

### Prerequisites

- Git
- Python 3.12 or newer
- Node.js 20 or newer with `npm`

### 1. Install

```bash
git clone https://github.com/andrewb1234/mouvadah.git
cd mouvadah
python3 bootstrap.py
```

The interactive bootstrap asks for a local display name and email, then
installs the pinned dependencies and creates an authenticated local owner. It
does not start the application.

<details>
<summary>Files and settings created by bootstrap</summary>

- `.venv/` for the API and MCP Python dependencies
- `web/node_modules/` from the committed npm lockfile
- `.env` with a generated local JWT secret and loopback-only authentication
- `~/.taskable/taskable.db` for the local SQLite database
- `~/.config/mouvadah/credentials.env` for the revocable local API key
- a `mouvadah` entry in the Windsurf MCP configuration, when Windsurf is
  detected

The configuration and credential files are written with owner-only permissions
on the verified macOS/Linux path. Re-running bootstrap reuses the local owner
and active bootstrap key.

</details>

### 2. Start

Start the API in one terminal:

```bash
source .venv/bin/activate
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

Start the web interface in a second terminal:

```bash
cd web
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). On the local sign-in
screen:

1. Copy the `MOUVADAH_API_KEY` value from
   `~/.config/mouvadah/credentials.env`.
2. Paste it into **Local API key**.
3. Choose **Continue locally**.

The API runs at [http://127.0.0.1:8000](http://127.0.0.1:8000), and its OpenAPI
explorer is at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
Press `Ctrl+C` in each terminal to stop the servers.

### 3. Connect an agent

If bootstrap detected Windsurf, restart Windsurf and invoke the Mouvadah
`get_all_projects` tool.

For another MCP client, adapt [`mcp/mcp.json.example`](./mcp/mcp.json.example).
The installed local command is `.venv/bin/mouvadah-mcp`; it reads the API key
from the credentials file instead of embedding the secret in client JSON.
The MCP bridge uses `stdio` and opens no listening port.

See [`mcp/README.md`](./mcp/README.md) for standalone `pipx` and `uv` tool
installation options.

## Other ways to run

| Path | Availability | Use it when |
| --- | --- | --- |
| Source + local dev servers | Supported alpha path | You are evaluating or developing Mouvadah locally. |
| Docker Compose from source | Supported alpha path | You want the API and web application in containers. |
| GitHub Release installer | Published for `v0.1.1` | Install the full local application without cloning. |
| PyPI | Published as `mouvadah-mcp` | Install only the MCP bridge for an agent harness. |
| Homebrew | Published through `andrewb1234/tap` | Install and update the full-application release command. |
| Hosted alpha | Available at [mouvadah.com](https://mouvadah.com) | Evaluate the product without treating it as a production service. |

To run the application containers after completing bootstrap:

```bash
docker compose -f docker/docker-compose.yml up --build
```

Then open [http://localhost:3000](http://localhost:3000). The Compose profile
binds both published ports to loopback, reuses the local owner and SQLite
database, and keeps the MCP server on the host. Stop it with:

```bash
docker compose -f docker/docker-compose.yml down
```

Distribution plans, licensing boundaries, and release retraction limits are in
[`docs/distribution.md`](./docs/distribution.md).

## The core model

```text
Workspace
├── Projects
│   ├── Knowledge tree (evidence → summaries → PRD/TDD)
│   └── Subprojects
│       └── Tickets (dependencies → claim → work → review)
└── Members and scoped API keys
```

Knowledge sits upstream of work: humans and agents curate durable context,
derive project plans, and then execute dependency-aware tickets. Claims and
leases coordinate concurrent workers; comments, audit events, source
references, and handoffs preserve how the result was reached.

## Architecture

```text
React web UI ── REST + SSE ── FastAPI + SQLModel ── SQLite (local)
                                  │
MCP client ── local stdio bridge ── bearer-authenticated REST
Claude web/mobile ── /mcp + OAuth ── same workspace authorization
                                  │
                            PostgreSQL (hosted profile)
```

- `web/` contains the Vite/React interface.
- `api/` contains the FastAPI application, authorization, migrations, and
  tests.
- `mcp/` contains the independently installable Python MCP bridge.
- `docker/` contains the local and release container profiles.

SQLite is the default local database. PostgreSQL deployments use Alembic
migrations and direct `LISTEN/NOTIFY` for cross-process realtime
invalidations.

## Security and maturity boundary

Local mode remains authenticated: bootstrap creates a real owner, an HttpOnly
browser session, and a revocable per-user API key. Project data is
workspace-scoped, and API keys may be limited by workspace, read, write, and
separate delete scopes, project, expiry, and revocation.

Mouvadah is still an alpha and must not be represented as a production-ready
public SaaS. The repository has verified application-layer tenant checks,
session revocation, exact-Origin protection for cookie writes, migration
checks, encrypted backup/restore tooling, security headers, CI, CodeQL, and
supply-chain checks. Published release manifests pin container images by
digest; runtime dependencies and base images are locked; and release images run
without root privileges. Production backup-provider configuration, recurring
restore evidence, distributed abuse controls, hosted failover evidence,
artifact signing, and independent security assessment remain release gates.

Read [`docs/security_and_trust.md`](./docs/security_and_trust.md) before any
hosted deployment. Operator configuration is documented in
[`docs/deployment.md`](./docs/deployment.md),
[`docs/migrations.md`](./docs/migrations.md),
[`docs/recovery.md`](./docs/recovery.md), and
[`docs/incident_response.md`](./docs/incident_response.md).

## Develop and test

Run bootstrap once before using these commands:

```bash
.venv/bin/python -m pytest api/tests/ -v  # backend and MCP test suite
cd web && npm run build                   # TypeScript + production web build
cd web && npm run test:e2e                # authenticated Chromium tests
.venv/bin/python scripts/seed_demo.py     # add local demonstration data
```

The `Makefile` provides `make dev`, `make test`, `make build-web`, `make e2e`,
`make seed`, and `make docker` as optional shortcuts.

CI verifies Python 3.12 and 3.14, the frontend build, package and container
assembly, PostgreSQL migrations and recovery, authenticated realtime browser
behavior, dependency and secret scanning, and CodeQL.

For non-trivial changes, read [`docs/protocol.md`](./docs/protocol.md),
[`CONTRIBUTING.md`](./CONTRIBUTING.md), and [`learnings.md`](./learnings.md).

## Documentation map

| If you want to… | Read |
| --- | --- |
| Understand the product direction | [`docs/company_blueprint.md`](./docs/company_blueprint.md) |
| Install or distribute Mouvadah | [`docs/distribution.md`](./docs/distribution.md) |
| Connect an MCP client | [`mcp/README.md`](./mcp/README.md) |
| Understand client/server behavior | [`docs/client_server.md`](./docs/client_server.md) |
| Operate or host the application | [`docs/deployment.md`](./docs/deployment.md) |
| Review security guarantees and gaps | [`docs/security_and_trust.md`](./docs/security_and_trust.md) |
| Work with migrations and recovery | [`docs/migrations.md`](./docs/migrations.md) and [`docs/recovery.md`](./docs/recovery.md) |
| Contribute code | [`CONTRIBUTING.md`](./CONTRIBUTING.md) |

## Licensing

Mouvadah Community—the API, web application, deployment materials, and
repository default—is licensed under
[AGPL-3.0-only](./LICENSE). The independently installable MCP bridge is
licensed under [Apache-2.0](./mcp/LICENSE) so agent harnesses can integrate
without inheriting the server license.

The software licenses do not grant rights to the Mouvadah name or logo. See
[`TRADEMARKS.md`](./TRADEMARKS.md). Use `Mouvadah™`, not `Mouvadah®`, unless a
registration issues.

Copyright (C) 2026 Andrew Betbadal and contributors. See [`NOTICE`](./NOTICE)
for the repository’s copyright and license boundary.
