# Mouvadah Security and Trust Baseline

Status: verified alpha posture and target control plan
Audit date: 2026-07-28
Scope: repository application, local deployment, proposed hosted service

## Plain-language posture

Mouvadah now has an application-layer workspace boundary suitable for
multi-user development evaluation: projects belong to workspaces, descendants
inherit that boundary, and every application route resolves the caller's
membership before returning or mutating an object.

It is **still not safe to describe as a production-ready public SaaS**.
Revocable sessions, exact-Origin cookie-write enforcement, scoped API keys,
baseline per-process rate limits, and browser security headers are now
verified. Shared PostgreSQL realtime is implemented and tested, but production
multi-instance operation and listener failover still lack hosted evidence.
Production backup control-plane configuration, distributed abuse controls,
and hosted failover evidence remain open release gates. Artifact signing and
independent assessment also remain open; runtime locking, image hardening,
SBOMs, provenance, and release image scanning are now implemented.

This document deliberately separates verified controls from target controls.
Security guarantees must be limited to what the code, tests, and operational
evidence support.

## Verified controls today

### Identity and credentials

- Google authorization-code sign-in uses a random state value bound to an
  HttpOnly cookie.
- Application sessions are HS256 JWTs in an HttpOnly, SameSite=Lax cookie and
  must resolve to an active server-side session record.
- The cookie is marked Secure when the configured frontend URL uses HTTPS.
- Agent API keys contain 32 bytes of random entropy, are returned only at
  creation, and are stored as SHA-256 hashes.
- API keys bind to one workspace, carry explicit read, write, and independent
  delete scopes, may limit access to selected projects, can expire, and can be
  revoked.
- Users can inspect active browser sessions and immediately revoke any one;
  logout revokes the server-side record before clearing the cookie.
- Unsafe cookie-authenticated API requests require the exact configured
  application Origin. Explicit bearer credentials never fall back to ambient
  cookies, and restricted keys cannot be exchanged for full browser sessions.
- Login/callback and unsafe-action sliding-window limits return explicit 429
  and Retry-After responses in the current single-instance stage.
- CSP, frame-ancestors/X-Frame-Options, nosniff, referrer, permissions, and
  production HSTS headers are applied across API and frontend responses.
- Authenticated requests resolve an actual user record.
- Loopback local mode provisions a real owner, personal workspace, and
  revocable per-user key instead of disabling auth or sharing a backend bypass
  secret.
- The local browser exchanges that key for an HttpOnly session only when
  `LOCAL_AUTH_ENABLED=true`; configured and request origins must be loopback
  and cross-origin exchanges are rejected.
- Local credentials are written atomically to an owner-only `0600` file. MCP
  can read that file without copying the raw key into client JSON.
- Production OAuth callbacks derive from the configured public origin rather
  than the request Host.
- Production startup rejects a default or short JWT secret and missing Google
  OAuth credentials when `FRONTEND_URL` uses HTTPS, and rejects local auth.

### Authorization and tenancy

- Workspaces own projects; project descendants inherit that tenant boundary.
- Membership roles are OWNER, ADMIN, MEMBER, VIEWER, and SERVICE.
- Central authorization helpers protect project, subproject, ticket, comment,
  knowledge, proposal, session, and agent routes.
- Inaccessible object IDs return 404, including cross-workspace dependency,
  knowledge-parent, and session-node references.
- API keys act as their owning user but are additionally constrained to their
  bound workspace, read/write scopes, and optional project allow-list.
- Every emitted application event carries a workspace ID; the SSE stream
  captures its API-key workspace boundary before streaming, closes the
  authentication session, and re-checks current membership in a short
  transaction before every delivery.
- PostgreSQL processes share content-free invalidations through direct
  LISTEN/NOTIFY. SQLite remains local. Connect, reconnect, listener recovery,
  and bounded-queue overflow trigger an explicit authorized-state resync.
- Legacy projects are adopted only by an explicitly configured owner or by the
  sole user of a local-development database. Ambiguous legacy projects remain
  inaccessible.

### Data and execution integrity

- SQLModel/SQLAlchemy uses parameterized database operations in normal paths.
- Ticket claiming uses a conditional compare-and-set update.
- Heartbeats cannot revive an expired lease.
- Requeue logic conditionally updates expired claims.
- Dependency cycles and invalid dependency targets are rejected.
- Ticket state changes and selected actions create audit records.
- Destructive project, subproject, ticket, and knowledge actions have explicit
  routes and tests.
- Workspace owners can create hashed tenant exports, schedule a 30-day
  recoverable deletion that immediately revokes API keys, restore before
  expiry, and permanently purge only after verified backup evidence.
- Database archives are encrypted with AES-256-GCM before object storage;
  wrong keys, ciphertext modification, schema drift, unsafe targets, and
  incomplete PostgreSQL restores fail closed.

### Verification

- The backend/MCP suite passes with separate PostgreSQL-only migration/claim,
  encrypted backup/restore, and cross-process realtime regressions against
  PostgreSQL 17.
- The suite includes concurrent claim, expiry, dependency, cascade, state,
  knowledge, cross-workspace read/write/delete isolation, role enforcement,
  tenant-filtered events, safe legacy adoption, OAuth hardening, tenant export
  and purge, backup tamper rejection, reconnect/overflow resync, and MCP
  subprocess coverage. Alembic upgrades, encrypted restore, restored tenant
  data, exact ORM parity, and two independent realtime broadcasters are
  exercised against ephemeral PostgreSQL 17.
- The frontend TypeScript and production build pass on the upgraded Vite 8
  toolchain.
- Three authenticated Chromium scenarios pass against an isolated
  migration-built database: local-key-to-HttpOnly-session exchange plus two
  realtime/SSE behaviors.
- GitHub workflows cover the test/build path on Python 3.12 and 3.14,
  a PostgreSQL 17 migration/claim/restore path, dependency review, Python and
  npm vulnerability audits, complete-history secret scanning, and CodeQL for
  Python and JavaScript/TypeScript.
- Workflow actions are pinned to full commit SHAs and Dependabot covers the
  Actions, Python, MCP, and npm manifests.

## Explicit non-guarantees

Mouvadah does not currently guarantee:

- database-enforced tenant isolation or PostgreSQL row-level security;
- granular custom roles or scopes beyond the verified read/write/delete
  boundary;
- independently assessed confidentiality controls;
- distributed rate limiting, workspace quotas, or denial-of-service
  resistance;
- production multi-instance availability or a durable/replayable event log;
- encrypted application-layer fields or customer-managed keys;
- migration rollback, configured provider point-in-time recovery, or tested
  production disaster recovery;
- cryptographically signed release artifacts or independently reproducible
  builds;
- a response-time SLA, RPO, or RTO;
- SOC 2, ISO 27001, HIPAA, FedRAMP, or other certification;
- independent penetration testing; or
- exactly-once external side effects from agents.

These omissions are release blockers for different offerings, not fine print to
hide in customer terms.

## Assets

High-value assets include:

- project descriptions, requirements, technical designs, and source references;
- tickets, dependencies, comments, handoffs, and execution history;
- repository and pull-request links;
- user identity and profile data;
- browser sessions and API keys;
- OAuth client credentials and application signing secrets;
- database backups and exported audit data; and
- future tracker, repository, and agent-runtime credentials.

Source references may reveal sensitive local paths even when file contents are
not stored. Treat them as customer data.

## Trust boundaries

1. Browser to API over cookie-authenticated HTTPS.
2. Local or remote MCP client to API using an API key or future OAuth token.
3. API to database.
4. API to Google OAuth.
5. Future API to GitHub, Linear, Jira, object storage, email, and observability
   vendors.
6. Human-approved project state to untrusted or semi-trusted agent actions.
7. Tenant data to operator tooling, backups, logs, and support workflows.

Agents are not trusted simply because they possess credentials. Credentials
need scopes, resource boundaries, expiry, revocation, and policy enforcement.

## Current findings

### Resolved critical: object-level tenant authorization

Workspace ownership, membership roles, centralized object lookups,
non-enumerating failures, cross-reference validation, and tenant-filtered SSE
are implemented and covered by adversarial two-user tests.

Residual risk:

- the boundary is enforced by application queries rather than database
  row-level security;
- no independent penetration test has been performed; and
- API-key permissions intentionally expose only read/write scopes rather than
  custom per-action policy.

### Resolved high: workspace access administration

Interactive owners can issue high-entropy, hash-only, expiring, single-use
invitations bound to a verified normalized email; manage `ADMIN`, `MEMBER`,
and `VIEWER` roles; remove members; and atomically transfer ownership. A
partial unique database index prevents a second owner, while application
guards prevent removing or generically demoting the final owner. Removal
revokes the member's keys for that workspace and all of their account-wide
browser sessions; role reduction to `VIEWER` revokes write-capable keys.
Every change is recorded in a content-free access ledger. No email-delivery
provider is configured, so the owner must copy the one-time link and send it
through an appropriate channel.

### Resolved high: trusted OAuth callback origin

Production redirects now derive from validated `FRONTEND_URL` configuration.
Loopback request ports remain available only for local development, and
focused tests cover spoofed Hosts, invalid public URLs, state-cookie cleanup,
and production secret requirements.

### Resolved high: stale shared-secret onboarding

The bootstrap path no longer creates `AGENT_API_KEY` or places a shared bypass
secret in the backend environment. It now installs the pinned runtimes,
generates an owner-only local JWT configuration, migrates the database,
creates or reuses a local owner and personal workspace, issues a revocable
per-user API key, stores that key in a permission-restricted credentials file,
and configures detected MCP clients to reference the file. Re-running setup is
idempotent; key rotation is explicit and revokes the prior bootstrap key.

Residual risk:

- the credentials file is intentionally decryptable by its owner and depends
  on host account/filesystem security; and
- Windows permission semantics are not part of the verified macOS/Linux
  community path.

### Resolved high: ordered migration and deployment gate

Alembic now owns the release schema. The migration runner recognizes fresh
databases, the supported unversioned 0.1.0 pre-tenancy schema, already-created
unversioned tenancy schemas, and known Alembic revisions. Partial or unknown
schemas fail closed.

Verified behavior:

- file-backed SQLite creates a consistent pre-migration backup automatically;
- an existing non-SQLite database requires explicit backup confirmation;
- production configuration rejects application-startup upgrade mode;
- application replicas in `check` mode refuse an unversioned or behind
  database;
- fresh and unversioned SQLite upgrades preserve data and match ORM metadata;
- fresh and unversioned PostgreSQL 17 upgrades preserve data and match ORM
  metadata;
- revision 0005 repairs native PostgreSQL `auditaction` values, the release
  parity gate now compares the actual `pg_enum` labels, and the CI regression
  commits real claim/requeue audit rows after reproducing the legacy drift; and
- the operator runbook defines backup, failure, forward-fix, and restore-based
  rollback procedures.

Residual risk:

- the independent encrypted backup job exists in the repository, but its
  production bucket, secrets, provider snapshot schedule, alerting, and
  recurring restore evidence are not yet verified;
- no historical schema older than the documented 0.1.0 baseline is guessed at
  or silently repaired; and
- production restore exercises have not yet established an RPO or RTO.

### Resolved high: process-local realtime fan-out

PostgreSQL deployments now publish content-free, workspace-tagged
invalidations over LISTEN/NOTIFY while delivering locally without echo
duplicates. A direct listener reconnects with bounded backoff and instructs
all local clients to resync after recovery. Two independent broadcaster
instances are exercised against PostgreSQL 17.

The stream captures immutable user/API-key workspace authorization before the
streaming response begins. Its authentication session uses function scope, and
each event gets a new short membership transaction, avoiding one checked-out
connection and idle transaction per browser stream.

Replay policy is explicit: invalidations are not a business-event log.
Connect/reconnect and subscriber overflow emit `SYNC_REQUIRED`, causing each UI
surface to refetch currently authorized state.

Residual risk:

- production still runs one instance and has not evidenced failover under
  real load;
- shared transport health needs metrics and paging in the observability
  release; and
- PostgreSQL notifications are not durable or exactly once, by design.

### Resolved medium: browser mutation and session revocation

Unsafe cookie-authenticated writes now require an exact match to the trusted
configured Origin, including rejection of same-site sibling origins and
missing Origin. SameSite=Lax remains defense in depth. Explicit bearer traffic
stays outside the cookie flow and invalid bearer credentials never fall back
to ambient cookies.

Browser JWTs now carry a random session ID backed by a database row recording
issue, last use, expiry, and revocation. Profile APIs and UI list active
sessions; logout and targeted revocation invalidate the row immediately.

Residual risk:

- if a future deployment permits a cross-origin frontend, reassess whether a
  synchronizer/double-submit token is required rather than weakening exact
  Origin policy;
- signing-key rotation and operator-wide emergency revocation need a runbook;
  and
- authentication-assurance level and device metadata are not yet recorded.

### Medium: authorization and audit identity are too coarse

Several audit records identify only HUMAN versus AGENT. They do not reliably
identify a user, API key, worker, source IP, request, before/after values, or
workspace.

Required outcome:

- immutable actor ID, credential ID, workspace, request/correlation ID, action,
  target, timestamp, and safe change metadata;
- never log raw session, OAuth, API, or integration credentials;
- append-only export and defined retention.

### Partially resolved medium: rate and resource limits

Login/callback paths and unsafe actions now have process-local sliding-window
limits with explicit 429 and Retry-After behavior. This matches the current
single-instance deployment stage but is not a distributed abuse-control
system. API request bodies are capped at 1 MiB by default; mutable long-text,
comment, source-reference, dependency, and session-node inputs have explicit
field and collection limits; and search queries are capped.

Residual outcome:

- replace the process-local limiter with a trusted shared edge/application
  limiter before scaling out;
- add per-user, API-key, and workspace quotas;
- connection and timeout limits;
- backpressure and explicit 429 behavior.

### Partially resolved medium: software supply-chain and delivery controls

Repository workflows now provide stable aggregate test, security, and CodeQL
checks. They exercise backend tests on Python 3.12 and 3.14, frontend
type/build, an authenticated Chromium realtime flow, dependency review, Python
and npm audits, and complete-history secret scanning. Actions are pinned by
full SHA and Dependabot covers every supported package ecosystem.

The dependency baselines were moved to fixed FastAPI/Starlette, pytest, Vite,
PostCSS, and related versions, with clean local Python and npm audits. Release
containers install a hash-locked runtime-only Python graph, use digest-pinned
base images, and run as non-root users. Tag builds emit SBOM and provenance
attestations, scan both images for high/critical findings, and publish a
Compose manifest pinned to the resulting image digests.

Residual risk:

- required-check enforcement, Actions policy, and secret-scanning settings are
  GitHub control-plane configuration and need periodic evidence;
- artifact signing is not implemented;
- release artifacts are not yet independently reproducible.

### Partially resolved medium: recovery and deletion

The repository now supplies authenticated application-layer encryption for
SQLite and PostgreSQL archives, download-and-verify S3 automation, 35-day
retention and least-privilege IAM templates, restore-target guards, a real
PostgreSQL 17 restore regression, owner tenant export, a configurable 30-day
workspace recovery window, immediate workspace-key revocation, and
backup-evidence-gated purge with a retained non-content ledger.

Residual risk:

- production S3 and database-provider recovery controls have not been
  configured and evidenced;
- recurring production-environment restore drills have not measured RPO/RTO;
- the backup job needs failure alerting and operator ownership;
- account-wide deletion is not implemented; and
- destructive MCP tools for individual projects, subprojects, tickets, and
  knowledge nodes still hard-delete immediately without an approval policy.

### Partially resolved low: operational visibility

The application now emits redacted JSON logs with server-generated request
IDs and W3C trace correlation, a protected Prometheus endpoint for
API/database/auth/SSE/job signals, database-aware readiness, optional
privacy-restricted Sentry error aggregation, and vendor-neutral OTLP tracing.
Backup/restore/purge commands emit bounded job outcomes and trace spans.
`docs/incident_response.md` defines initial alert thresholds, severity, roles,
containment, evidence, communications, postmortems, and drills.

Residual risk:

- production telemetry sinks, dashboards, notification destinations, and
  provider alerts are not yet configured and evidenced;
- transient backup jobs need a live scheduler failure alert and retained
  success signal;
- integrations do not yet exist, so end-to-end integration traces remain a
  future control; and
- alert thresholds and response objectives have not been calibrated against
  production traffic or exercised by the operator.

## Target security architecture

### Identity and access

- Workspaces own projects and all descendants inherit that boundary.
- Membership roles start with OWNER, ADMIN, MEMBER, VIEWER, and SERVICE.
- Permissions are checked through centralized policy helpers on every object
  read and action.
- API keys belong to a workspace and principal, have explicit scopes, optional
  project restrictions, expiry, last use, and revocation.
- Agent workers use distinct identities; never share one all-powerful token.
- Destructive and administrative operations support step-up or explicit
  approval policy.

### Remote MCP

The hosted `/mcp` endpoint now implements Streamable HTTP with OAuth account
linking, workspace consent, read-only access, rotating refresh tokens, and
immediate grant revocation. See [hosted MCP operations](./hosted-mcp.md) for
the implementation and verification boundary. The local stdio bridge remains
available with API-key credentials. Ongoing requirements:

- follow the current MCP authorization specification;
- expose protected-resource metadata;
- use an OAuth authorization server with PKCE and resource indicators;
- request least-privilege scopes;
- validate audience/resource on every token;
- support machine-to-machine identities separately from human delegation;
- provide a read-only connection option.

### Data protection

- TLS for every hosted connection.
- Managed database encryption at rest and encrypted backups.
- Secret manager for OAuth, signing, integration, and database credentials.
- Object storage with private buckets, short-lived signed access, MIME
  allowlists, and malware scanning before attachments launch.
- Redaction rules for logs, errors, support bundles, and analytics.
- Per-workspace export and deletion with documented retention.

### Application controls

- Exact trusted OAuth redirects and one-time state.
- Origin/CSRF enforcement for cookie writes.
- Strict input schemas, size limits, safe URL validation, and pagination.
- Idempotency keys for retryable create/action endpoints.
- Security headers: CSP, frame-ancestors, nosniff, referrer policy, and HSTS at
  the trusted TLS edge.
- Safe error messages and no stack traces or secrets in client responses.
- Authorization checks before existence-sensitive responses.

### Agent safety controls

- Tool scopes by workspace, project, action, and risk.
- Read-only defaults for newly connected agents where practical.
- Approval gates for destructive, external, billing, identity, and policy
  actions.
- Lease and idempotency semantics documented honestly.
- Egress/integration allowlists for managed execution connectors.
- Full agent action attribution and a kill/revoke control.
- Budget and concurrency limits before Mouvadah launches paid execution.

## Secure development program

Use OWASP ASVS 5.0 as the application control checklist and NIST SSDF as the
development-program structure. This is an internal verification target, not a
certification claim.

### Pull-request gates

- Backend tests, frontend type/build, and relevant Playwright tests.
- Authorization tests for every new object endpoint.
- Dependency and secret scan.
- Migration upgrade test for schema changes.
- Threat review for new credentials, integrations, uploads, or destructive
  actions.
- No high/critical known vulnerability without an explicit time-bounded risk
  acceptance.

### Patch targets

Initial internal targets:

- Critical exploitable issue: contain immediately; remediation target 24 hours.
- High: remediation target 7 days.
- Medium: remediation target 30 days.
- Low: prioritized with normal maintenance.

Targets become customer commitments only after the team proves it can meet
them.

### Release evidence

Each release should retain:

- commit and build identity;
- tests and security scans;
- dependency/SBOM snapshot;
- migration result;
- reviewer and approval;
- deployment and rollback record; and
- customer-impacting security changes.

## Availability, backup, and incident targets

Do not publish an SLA before collecting production evidence.

Private-beta internal objectives:

- daily automated backup plus managed PostgreSQL point-in-time recovery;
- quarterly restore exercise initially, moving to monthly before paid GA;
- documented restore ownership and evidence;
- single-region recovery objective measured in exercises;
- public status communication path;
- incident roles, severity, containment, evidence preservation, customer
  notification, and post-incident review.

RPO and RTO must be measured from actual restore exercises before they become
contract terms.

## Privacy and compliance path

Before accepting public customers:

- publish privacy policy, terms, security contact, and subprocessors;
- document data categories, purposes, retention, location, and deletion;
- support account/workspace export and deletion;
- execute DPAs with relevant vendors;
- minimize analytics and make product telemetry transparent;
- define law-enforcement and support-access handling;
- maintain an access-reviewed production operator roster.

SOC 2 Type I is a possible later milestone after the control system operates
consistently. It is not the first security task and must not substitute for
tenant isolation, restore testing, or secure authorization.

## Offering security gates

### Community/local

- Bind to loopback by default.
- Reject insecure defaults on non-loopback or HTTPS-style production config.
- Document backup, upgrade, exposure, and credential rotation.
- CI and dependency/security scans on release.
- Published vulnerability reporting address.

### Cloud private beta

- Complete tenant authorization and cross-tenant tests.
- Trusted OAuth, CSRF, revocable sessions, rate limits, and headers.
- Alembic migration evidence and a production-like PostgreSQL restore
  exercise.
- Tenant-scoped realtime and logs.
- Operational monitoring, incident process, export, and deletion.

### Enterprise

- SSO/SCIM, granular RBAC, group mapping, and service identities.
- Independent penetration test with remediation.
- SIEM export, retention, access reviews, and vendor evidence.
- Supported deployment architecture and upgrade policy.
- Contracted controls only where operating evidence exists.

## Immediate verification backlog

1. Configure provider snapshots and the independent production backup job,
   then run and record an isolated production-environment restore drill.
2. Exercise PostgreSQL listener failure/recovery in the hosted environment.
3. Add distributed abuse controls, monitoring, and operator alerting.
4. Add cryptographic release signing and independently reproduce a release.
5. Create a threat model for GitHub/Linear integrations before implementation.

## Standards and primary references

- OWASP API1:2023 Broken Object Level Authorization:
  https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/
- OWASP Application Security Verification Standard 5.0:
  https://owasp.org/www-project-application-security-verification-standard/
- OWASP CSRF Prevention Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- OAuth 2.0 Security Best Current Practice, RFC 9700:
  https://www.rfc-editor.org/rfc/rfc9700.html
- MCP Authorization specification (2025-11-25):
  https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- NIST Secure Software Development Framework, SP 800-218:
  https://csrc.nist.gov/pubs/sp/800/218/final
