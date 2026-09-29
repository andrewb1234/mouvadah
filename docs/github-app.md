# GitHub App integration

This integration reads GitHub PR/issue evidence into Mouvadah. It does not
write code, create PRs, merge, deploy, or infer ticket commands from issue text.
The harness handles execution and release approval separately.

## Ownership and authorization

GitHub owns installation access, repository identity, and current PR/issue
state. Mouvadah owns the installation-to-workspace mapping, repository-to-project
mapping, explicit ticket links, and ticket workflow state. A merge never marks
a ticket DONE automatically. Existing `mr_link` URLs remain independent and
are not silently imported as tracked links.

One installation can be assigned to one workspace on a server. A browser-session
workspace administrator begins authorization with a random, hashed, ten-minute,
single-use state bound to their user, workspace and requested installation.
GitHub App user authorization proves access to that installation. Only repositories
with `permissions.admin=true` in the user-scoped repository inventory may be
delegated. A browser-supplied installation ID alone grants nothing. API keys
cannot configure connections. A reconnect rechecks privileges; newly installed
repositories require reconnecting before they can be mapped.

Tokens remain in process memory. Mouvadah does not store GitHub user tokens,
refresh tokens, installation tokens, or private keys in its database. Each
installation token is narrowed to one approved repository and read-only metadata,
pull requests and issues. There is no PAT fallback. Workspace exports include
mapping and evidence records, but exclude temporary authorization state.

## Configure and connect

Register a GitHub App for your deployment. Required repository permissions:
**Metadata: read**, **Pull requests: read**, **Issues: read**. No contents write,
administration, organization, merge, or deployment permission is requested.
Subscribe to `pull_request`, `issues`, `repository`, `installation`, and
`installation_repositories` events where offered by GitHub.

Set the callback URL to `<FRONTEND_URL>/api/v1/github/callback` and webhook URL
to `<FRONTEND_URL>/integrations/github/webhook`. Use HTTPS for hosted deployments.
Keep user authorization enabled; the settings UI starts it separately from
installation. Do not use a setup-URL installation ID as proof of ownership.

Configure these server/worker environment variables through your secret store:

- `GITHUB_APP_ENABLED=true` (default is false).
- `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_CLIENT_ID`.
- `GITHUB_APP_PRIVATE_KEY`: the App RSA private-key PEM.
- `GITHUB_APP_CLIENT_SECRET`: the App OAuth client secret.
- `GITHUB_WEBHOOK_SECRETS`: JSON array of one to three secrets, each at least
  32 characters. During rotation, deploy old and new secrets together, change
  the secret in GitHub, verify a new delivery, then remove the old secret.

In **Profile → GitHub**, select a workspace, install the app for selected
repositories, enter the installation ID from its GitHub settings URL, and
complete authorization. Map each allowed repository to a project. A repository
can be mapped to only one project; unmapping removes its tracked ticket links.
In a ticket's **GitHub evidence** section, choose a mapped repository and enter
the PR/issue number. The API verifies the object before storing the link.

The relevant authenticated REST routes are under `/api/v1/github`: workspace
connection settings and repository mappings, project repository discovery,
and ticket link reads/create/delete. They retain existing workspace roles and
project-scoped API-key restrictions. Only browser administrators can change
installation settings; authorized project writers can maintain ticket links.
Direct project collaborators may read that project's repository/evidence data
without access to the owning workspace's installation settings or other projects.
Editors may maintain links; viewers have read-only controls. Permission changes
and removal take effect on the next request.

## Delivery, reconciliation and recovery

The public webhook verifies SHA-256 HMAC over bounded raw bytes before JSON
parsing. A unique delivery ID and payload hash are committed before returning
202. Repeated deliveries do not produce new effects; conflicting reuse returns
409. Unmapped installations never create a tenant association. Receipt records
contain only identifiers, hashes, event type and processing timestamps, not
GitHub payload bodies.

Run `python -m api.github_sync --limit 10` against the same database and App
configuration every minute. This is a required deployment component, not an
in-process best-effort callback. Run a single worker for SQLite. PostgreSQL
workers serialize by installation using row locks. Each batch refreshes one
repository and at most ten tracked links, advancing durable cursors. A later
run resumes remaining work. The worker also reconciles without webhooks every
five minutes, so lost events eventually recover. Actual freshness depends on
worker cadence, inventory size and provider rate limits.

Webhooks are hints only: reconciliation fetches current provider state, never
applies stale event snapshots, and commits evidence plus receipt completion
together. Failures roll back partial evidence, retain pending receipts, store
a safe error code, and respect bounded retry delays and GitHub rate-limit
headers. New webhooks and the manual **Sync now** control cannot erase error
backoff. A terminal access error is visible as revoked; reconnection or restored
provider access is required. Repository transfers across account IDs fail
closed and require new human authorization. Renames within an authorized
account update the repository name while retaining numeric identity.

**Sync now** queues work; it does not run the worker. Refresh status to see the
last check and errors. Monitor worker exit status and connection `updated_at`,
`last_error`, and `next_sync_at` for stopped workers or growing lag. Completed
and ignored receipts are retained for 30 days; failed/pending receipts persist.
Expired OAuth state is pruned by the worker. Disconnect stops reconciliation
and preserves visibly historical evidence. Uninstall the app in GitHub to
revoke GitHub access as well. Workspace deletion disconnects installations;
restore does not silently reconnect them. Purge removes their records.

## Release and acceptance

Migration `0011_github_app` is additive after ticket-idempotency migration 0010
and project-collaboration migration 0009.
Deploy migrations before new code. The integration is disabled until its real
configuration and worker exist. To stop it, set `GITHUB_APP_ENABLED=false` in
both server and worker and stop the scheduled worker. Database downgrade drops
integration records; export/backup them first if preservation is needed.

Automated tests cover isolation, one-use consent, signature validation/rotation,
duplicate/conflicting deliveries, retry rollback/backoff, current-state ordering,
repository transfer/revocation and deletion. Before operational acceptance, use
a real staging App: authorize as a repository admin, map a selected repository,
track a PR and issue, open/close/reopen/merge and change the PR head, replay and
delay deliveries, revoke/suspend/uninstall access, rotate the webhook secret,
and verify worker recovery plus cross-workspace rejection. Record installation,
repository and commit IDs, timestamps, delivery IDs and results without tokens.
Mocked tests do not establish this live acceptance.

Sources: [GitHub installation setup security](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url),
[user-scoped installation and repository permissions](https://docs.github.com/en/rest/apps/installations#list-repositories-accessible-to-the-user-access-token),
[installation token narrowing](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app),
[raw-body signature validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).
