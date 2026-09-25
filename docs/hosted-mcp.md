# Hosted MCP operations

The public resource is `FRONTEND_URL`’s origin plus `/mcp`; the canonical hosted
URL is `https://mouvadah.com/mcp`. Keep `FRONTEND_URL` set to the trusted HTTPS
origin. OAuth issuer, resource audience, callbacks, and MCP Host/Origin checks
use this setting, never forwarded headers. Other host aliases are not MCP URLs.

## Connection and permission model

- Streamable HTTP uses the official MCP Python SDK in stateless JSON mode.
  Each request authenticates independently; there is no worker affinity.
- RFC 9728 resource metadata is served at
  `/.well-known/oauth-protected-resource/mcp` (and the root compatibility path).
  RFC 8414 authorization metadata is at `/.well-known/oauth-authorization-server`.
- `/oauth/register` supports dynamic registration of public and confidential
  clients. Redirects must be exact registered HTTPS or loopback HTTP URLs.
  Client metadata URLs are not fetched, preventing a registration SSRF surface.
- `/oauth/authorize` uses existing Google/browser-session login, followed by
  explicit consent. The user chooses one workspace and read or read/write access.
  Consent is signed, short-lived, bound to the browser session, and requires the
  application Origin on submission. Workspace role checks apply at consent and
  on every subsequent tool operation.
- Five-minute authorization codes require S256 PKCE and exact client/redirect
  matching. Resource indicators, when supplied, must equal the canonical resource;
  omitted indicators bind to that sole resource for older clients.
- Access tokens last at most one hour. Refresh tokens rotate, cannot increase
  scope, and expire with the 30-day grant. Code or refresh reuse revokes the
  entire grant. Tokens and client secrets are stored only as SHA-256 digests.
- Grants reuse workspace-scoped API-key authorization and revocation records,
  named `MCP: <client name>`. The backing API-key secret is discarded. OAuth
  access tokens cannot authenticate REST endpoints, and API keys cannot
  authenticate `/mcp`. No upstream Google token is forwarded to MCP clients.
- The hosted adapter calls the existing API in process with per-request ASGI
  identity metadata. Clients cannot supply that metadata through HTTP headers.
  Each request has its own client and context; process-wide credentials are
  never changed. The local bridge loads its credentials only at CLI startup.
- Hosted tools exclude deletion regardless of local bridge environment flags.
  Read-only connections expose only read tools. API scope checks remain the
  final enforcement boundary. Account and workspace administration are not tools.
- OAuth endpoints and tool calls have bounded bodies and rate limits. Tokens,
  request bodies, and URL queries are excluded from structured access logs.

## Deploy and rollback

Render builds the frontend and installs `api/requirements.txt`. Container builds
use the hash-locked runtime requirements and copy the shared `mcp/` sources.
Both the development proxy and container web proxy forward MCP/OAuth/discovery
paths to the API. Public self-hosted HTTPS installations can use their own
canonical origin; the app’s hosted setup guide points to mouvadah.com.
Migration `0008_hosted_mcp_oauth` adds only client, code, and token tables.
Run the normal migration upgrade/check before starting the new application.
Existing `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `JWT_SECRET`, and trusted
`FRONTEND_URL` are sufficient; no additional Google callback is required.

The existing Google callback returns to the validated `/oauth/authorize` path
when connecting MCP. Ordinary sign-in continues to return to the application.
Rollback the application commit to remove the endpoint; the additive tables may
remain. Revoke affected `MCP: …` keys before restoring a rolled-back release if
connections must remain disabled. Follow existing database backup/restore policy.

## Verification

Run `pytest api/tests/test_hosted_mcp.py` for discovery, full MCP initialization,
listing/calling tools, PKCE/redirect/resource rejection, refresh rotation and
reuse, consent origin/session checks, tenant separation, and revocation.
Run migration, existing authentication/authorization, and stdio simulator tests
before deployment. After deployment, verify `/healthz`, `/readyz`, discovery,
and an authenticated MCP initialization/tool call against the deployed SHA.
Finally link through Claude’s custom connector UI, authorize the intended
workspace, and check a tool from the same Claude account on mobile. A protocol
smoke test alone does not verify the Claude mobile UI or its account policies.

Machine-to-machine OAuth grants and URL-based client metadata registration are
not supported. Use a scoped local bridge API key for unattended stdio clients.
