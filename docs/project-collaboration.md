# Project collaboration across independent workspaces

Implementation scope approved by the user on 2026-09-29. Visual direction requires separate user confirmation before UI implementation proceeds.

## Release manifest

- Mode: strict (authorization boundaries, migrations, production deployment).
- Mouvadah project: #5 — Taskable Platform — Harness Enablement. Subproject #48 — Project Collaboration v1.
- Tickets: #327 membership/invitations/lifecycle; #328 scoped credentials/hosted MCP; #329 UI approval, implementation, and end-to-end release.
- Branch: `codex/project-collaboration`. Preserve unrelated untracked workspace files.
- Authorized destination: Render `taskable`, service `srv-d91stg8js32c73a0vahg`, confirmed workspace `tea-d4mqscali9vc73f2nipg` (My Workspace), repository `andrewb1234/mouvadah` main. Auto-deploy is enabled; do not trigger duplicate deploys after merges.
- Checkpoints: plan commit; backward-compatible backend milestone with verified migration/authorization/lifecycle and deployment; scoped agent milestone; visually approved UI and final end-to-end deployment. Combine backend and credential deployment if intermediate compatibility cannot be proven.
- Visual checkpoint: three generated concepts shown in chat in order: centered Share dialog, right-side Project access panel, project People page. These are mockups, not implemented or deployed UI. Selection pending. Incumbent design tokens and brand components remain authoritative over incidental generated-image differences.
- Acceptance: focused and full relevant tests, PostgreSQL migrations/concurrency, frontend checks, two-account authenticated desktop/mobile scenarios, independent UI finish review, required protected CI, exact deployed commit and health/smoke evidence. Request user confirmation of rendered UI before its release.
- Rollback: additive schema and backward-compatible contracts; review credential resource-mode migration carefully because older code must not authenticate project-only guest keys. Verify rollback behavior before deploying any new credential mode. Never downgrade/delete live data as an automatic rollback.
- Current evidence: source matches remote main `56c8514`; baseline 86 focused tests passed during research. No implementation or deployment claim yet.

Repository: `andrewb1234/mouvadah`. Local `main` and GitHub `main` were compared through the connected GitHub API and were identical at `56c851470a46cbe8997fef329f77cf6bb41824aa` (hosted MCP/OAuth, PR #68). This establishes source freshness, not production deployment parity. Mouvadah context: project #5, Taskable Platform — Harness Enablement; Productization & Trust v1 (#14).

## Recommendation

Keep one owning workspace per project. Add direct user membership to individual projects, independently of workspace membership. Both people work on the same project ID, tickets, knowledge, and agent sessions; each retains their own workspace and credentials.

Sharing grants access to the entire project, including existing history and future descendants. It grants no access to sibling projects, the owner's workspace settings, membership directory, credentials, exports, or deletion controls. This proposal covers accounts on the same Mouvadah deployment; sharing between independently hosted servers would be a separate federation project.

Do not duplicate or synchronize projects across workspaces. A many-to-many project/workspace ownership model introduces competing deletion, export, billing, and administrative authority without being necessary for person-to-person collaboration. A separate shared workspace is possible with existing workspace APIs, but leaves the project boundary implicit and does not directly share an existing project in place.

## What exists today

| Area | Verified behavior | Consequence |
| --- | --- | --- |
| Ownership | `Project.workspace_id` names one workspace; `WorkspaceMembership` joins a user to a workspace. No project-member entity exists. | Project ownership and access currently travel together. |
| Object authorization | `require_project()` requires owning-workspace membership. Subprojects, tickets, comments, knowledge, proposals, and sessions resolve through it. | There is a good central seam for adding project grants. |
| Discovery | `GET /projects` joins projects to the caller's workspace memberships. Browser listing aggregates accessible workspaces; it does not filter by an active workspace. | Updating direct-object authorization alone would leave shared projects undiscoverable. |
| Roles | Workspace OWNER/ADMIN/MEMBER/SERVICE can write; VIEWER reads. Project deletion requires admin; deleting tickets/subprojects/knowledge generally requires write. Workspace membership administration is owner-only. | New roles need explicit capabilities, especially for access management versus content editing and deletion. |
| Invitations | Hashed, expiring, email-bound, single-use workspace invitations with explicit acceptance and an access ledger. The UI creates a link for manual copying. | Reuse the security pattern. Sending invitation email requires additional delivery work. |
| Live updates | Event envelopes identify workspace, entity, and parent; no uniform project ID. Delivery checks current workspace membership. Project-restricted API keys are denied the stream. | External collaborators would miss updates unless event authorization changes. |
| API keys | Keys belong to one workspace, optionally restrict projects, and require workspace membership at issuance and authentication. | Existing project restrictions narrow a workspace member's key; they do not enable project-only collaborators. |
| Hosted MCP | OAuth consent selects a workspace. Both OAuth `active_key()` and API authentication require workspace membership. Tools reuse the REST authorization path. | Fix both credential validation paths and consent, not just REST object checks. |
| Identity | Comments and audit records store HUMAN/AGENT, not the authenticated person's ID. Tickets assign HUMAN/AGENT/UNASSIGNED. Claims use a caller-supplied worker string. | Multi-person attribution and individual assignment are separate gaps. |
| Navigation | `WorkspaceContext` tracks project/subproject selection, not an active workspace. `App.tsx` only recognizes `/` and `/app`; invitations open Settings. | Project invitation entry and stable project links require explicit navigation handling. |
| Caching | Shared `useAsync` cache exists; failed refreshes retain previous data in hook state. | Access revocation needs explicit cache and mounted-view clearing. |
| Lifecycle | Workspace export and purge enumerate owned data. Member removal revokes all browser sessions for that user and workspace-specific API keys. | Guest access must be added to export/purge, while project removal should leave unrelated sessions and workspaces usable. |

## Backend design

### 1. Add access records without changing ownership

Proposed additive tables:

- `ProjectMembership`: project ID, user ID, VIEWER/EDITOR role, invited-by user, creation time. Unique `(project_id, user_id)`; indexes for both project roster and user discovery.
- `ProjectInvitation`: project ID, normalized target email, requested role, token hash, inviter, expiry, accepted/revoked timestamps, accepting user. Reuse existing constraints and single-use acceptance behavior.
- `ProjectAccessEvent`: project and owning-workspace IDs, authenticated actor, subject, action, timestamp, bounded non-content details. Retain suitable non-content history according to the existing lifecycle policy.

Use a new Alembic revision after `0008_hosted_mcp_oauth`. Existing projects need no ownership migration and existing workspace access remains inherited. Do not materialize every workspace member into every project's membership table: that duplicates grants and complicates revocation.

### 2. Centralize capability resolution

Introduce a resolver that returns both the project and effective capabilities. It should be used by object routes, project listing, event delivery, and credential issuance/validation. Keep `require_workspace()` strict: project access must never satisfy workspace access.

Suggested first-release policy:

| Capability | Direct viewer | Direct editor | Owning-workspace owner/admin |
| --- | --- | --- | --- |
| Read all project content/history | Yes | Yes | Yes |
| Edit tickets, comments, knowledge, subprojects | No | Yes | Yes |
| Delete ordinary project content | No | Yes, consistent with current write behavior | Yes |
| Manage invitations and direct grants | No | No | Yes |
| Delete the whole project | No | No | Yes |
| Change workspace membership/export/delete workspace | No | No | Existing workspace rules only |
| Connect own agent to this project | Read only | Read/write | Within own granted permissions |

Workspace members keep their existing inherited capabilities. Project sharing by workspace ADMIN would be an explicit new project-level capability; it must not widen today's owner-only workspace-member administration. If the initial policy should be owner-only sharing, that is a small product decision before implementation.

Combine direct and inherited grants by capability union; a direct viewer grant cannot downgrade an inherited editor. Show where access comes from. Removing one grant does not remove another: label removal as "Remove direct access" when inherited access remains, and provide a deliberate "Remove all access to this project" policy only if the model can actually enforce it. Private projects inside a broadly shared workspace would need an additional inheritance/visibility feature and are outside this proposal.

For credentials, effective authority is the intersection of current user capabilities, explicitly consented resource selection, and credential scopes. A personal-workspace credential must not silently gain shared projects from other workspaces.

Retain existence-neutral 404 responses for unauthorized objects. Maintain current transaction safety: check access before taking locks, take the owning-workspace lock, and re-read access before mutations. Have project grant changes take the same lock in consistent order. This avoids requests queued behind revocation writing with stale authority; an already-committed request is not retroactively undone. Project-level lock optimization can wait.

### 3. Add membership and invitation endpoints

Proposed API surface, names subject to implementation conventions:

- `GET /projects/{id}/members`: a project-scoped roster, minimal identity fields, direct versus inherited access. No workspace-wide email directory.
- `POST/GET /projects/{id}/invitations`: create and manage pending invitations for access managers.
- `DELETE /projects/{id}/invitations/{invitation_id}`: revoke a pending invitation.
- `POST /project-invitations/preview`: authenticated, email-bound preview from the token, with minimal project/inviter/role information before explicit acceptance.
- `POST /project-invitations/accept`: create direct membership atomically; never create workspace membership.
- `PATCH/DELETE /projects/{id}/members/{user_id}`: change/remove direct access, with inherited access explained.
- `DELETE /projects/{id}/membership`: leave one's own direct membership.

Invitation tokens remain high-entropy and hash-only, expire, bind to the signed-in verified email, and cannot be replayed. Handle concurrent acceptance, duplicate invitations, already-existing access, wrong account, revoked invitation, and deleted project. Permission management remains browser-only. Separate the new project invite URL/storage key from the existing workspace invitation flow. Keep tokens out of query logs, analytics, exports, and durable browser history.

### 4. Update project discovery and response metadata

`GET /projects` should use deduplicated `EXISTS` predicates or a union for inherited workspace access and direct project access, filtered to active owning workspaces. Apply credential resource restrictions afterward. Use the same access semantics as direct reads.

Extend `ProjectRead` with a minimal owning-workspace display label, access source, effective role/capabilities, and shared status. Return capabilities such as `can_edit`, `can_manage_access`, `can_delete_project`, and `can_leave`. Build the list and Control Room responses through a common serializer; currently the Control Room constructs `ProjectRead` independently.

Do not expose the owning workspace's settings or member count just to label the project. Do not add its workspace to the recipient's `/workspaces` response. Project creation stays explicitly tied to a workspace in which the caller can create; selecting a shared project must not change that destination.

### 5. Make realtime project-aware

Add `project_id` to every project-owned event at publication time, including descendant deletion events. After deletion, parent traversal may no longer be possible. Update serialization, PostgreSQL transport compatibility, every publisher, client types, and tests.

Check current project access before delivery. Workspace-only administrative events still require workspace membership. Never make an entire workspace's event stream available because one project is shared.

Use a content-free, user-addressed access-change invalidation for acceptance/removal and deletion, or an equivalently safe per-user resync mechanism. Ordinary project events are insufficient for removed users because they correctly fail the new access check. On access loss, refetch the authorized project list, clear affected caches and mounted views, close ticket/modals, and reject subsequent writes. Reconnect and return-to-tab revalidation cover missed nondurable notifications. Previously viewed/downloaded content cannot be recalled.

### 6. Support each collaborator's agents

Extend `issue_api_key()`, API key creation, `_verify_api_key_record()`, OAuth consent/approval, and OAuth `active_key()`. Keep API keys and OAuth connections owned by the individual who creates them. Never hand the collaborator the project owner's key.

For the first release, allow a connection explicitly scoped to one shared project. Keep its owning workspace ID as resource ownership metadata without requiring guest workspace membership. A project target must have live project authority and may never fall back to an unrestricted workspace target.

Add an explicit credential resource mode, e.g. WORKSPACE versus PROJECTS. Backfill existing restricted keys as PROJECTS and existing unrestricted keys as WORKSPACE. An empty PROJECTS set means no access or a revoked grant. Today an empty `ApiKeyProject` set means all workspace projects, and project deletion contains a special last-project revocation guard; avoid perpetuating that ambiguous representation.

On project removal revoke the affected single-project grants and OAuth token families through their underlying keys. On downgrade block writes immediately using current capabilities and narrow/revoke incompatible grants. Existing unrelated workspace and project connections keep working. Both token refresh and actual tool requests must enforce current rights. API scopes cannot grant sharing administration or whole-project deletion to an editor. Hosted MCP can preserve its existing read/write-only tool policy.

### 7. Add attributable collaboration

Record authenticated `actor_user_id` and optional credential/agent identity alongside HUMAN/AGENT for new comments, ticket audit events, knowledge changes, proposals/reviews, and sessions. Do not trust a submitted author identifier. Legacy records remain honestly labeled "Human" or "Agent"; their original person cannot reliably be reconstructed.

Keep person assignment separate from actor category: an optional `assigned_user_id` can coexist with HUMAN/AGENT and worker/lease state. Validate selected people against project access. Individual assignment can follow the sharing release, but named authors should ship with it.

For stronger multi-user agent ownership, bind ticket claims/heartbeats to authenticated principals as well as worker labels. Today a matching caller-supplied `worker_id` is sufficient for any authorized writer to renew a lease. Preserve the atomic claim/dependency checks and define deliberate takeover/recovery separately. This is a related collaboration hardening task, not evidence that the existing claim operation lacks atomicity.

### 8. Preserve lifecycle boundaries

The owning workspace remains authoritative for storage, export, eventual billing, project deletion, and recovery. Deleting that workspace makes its shared projects unavailable to guests; guests' own workspace deletion does not delete or transfer the owner's projects. Account deletion is a different lifecycle and must be handled explicitly if introduced.

Add new grant/invitation tables to project cascade deletion, workspace purge, export schema, backup parity checks, and recovery tests. Export only project-relevant collaborator identity fields; never export their personal workspace or credentials, invitation tokens, or identity-provider details merely to support attribution. Decide before release whether restoration reinstates direct memberships; recommend restoring memberships with the recovered content while keeping revoked invitations/credentials revoked, consistent with existing credential restoration behavior.

Copied links, source references, and externally hosted repositories/docs do not confer access to their targets. Sharing all project knowledge also shares any sensitive text previously pasted into that project; the Share dialog must clearly disclose that history is included. Internal node/dependency/session reference validation must continue enforcing project boundaries.

## UI proposal

Design target: extend the current desktop/web workbench so a person can invite someone into one project, both work there, and both retain independent workspaces. Reuse the existing warm-paper/ink/brass tokens, compact controls, Dialog, and mobile navigation patterns. This is an information-architecture proposal, not a rendered UI or visual QA result.

### Recommended entry and navigation

Add a persistent project header action **Share**, next to a small participant summary. Put it in `Workspace.tsx` so it is available in Control Room, Knowledge, and Kanban rather than only the dashboard. Viewers/editors without access-management authority see **People** instead.

Split sidebar discovery into **Your projects** (projects available through workspace membership) and **Shared with me** (direct-only access), with an owning-workspace subtitle where needed. A person with both sources appears once under the inherited group. Keep existing subproject expansion and project selection. Avoid requiring a workspace switch just to collaborate.

An alternative is a unified project list with a Shared badge and filter: less sidebar structure, but weaker separation when ownership is the user's main concern. A full workspace switcher plus a Shared hub is appropriate later for many-workspace navigation, but is unnecessary to deliver this feature.

### Share dialog

| Region | Content and behavior |
| --- | --- |
| Title | `Share “Project name”` |
| Scope explanation | `Invite someone to this project, including its tasks, knowledge, and history. Your other projects stay private.` |
| Invite row | Email, **Can edit / Can view**, **Create invitation**. Suggested default: Can edit for a collaboration action, with visible permission description. |
| New invitation | Copy invitation link, target email, role, expiration. Do not say "Email sent" unless delivery exists. |
| People | Name/avatar, effective role, `Via workspace` or `Project access`; role and removal controls only where allowed. Email visibility limited to what invitation administration needs. |
| Pending | Email, role, expiry, Revoke; replacing an invitation should issue a new token and revoke the old one. |
| General access | `Only people with access`. **Copy project link** navigates but does not grant access. No anonymous public-link sharing in v1. |

Treat direct guest access separately from inherited workspace access. A workspace role cannot be reduced or removed through a project-only dropdown. If an inherited grant remains, say so instead of falsely confirming removal.

### Recipient journey

1. Open invitation link. If needed, sign in while preserving the pending project invitation.
2. Show an invitation screen identifying inviter, project, owning workspace, and role, with **Accept invitation** and **Not now**.
3. Explain once: `You’ll join this project. Your workspace stays separate.` For a wrong account, offer switching accounts while retaining the invite.
4. Acceptance opens that exact project's Control Room and selects it in Shared with me; never route successful project acceptance through workspace Settings or default-select a different project.
5. Add stable project URLs, for example `/app?project=123`, and authorized initialization/popstate handling. This can fit the existing navigation approach without adding a router dependency. Keep ordinary project links distinct from invitation tokens.

### Everyday and exit states

- Keep tasks, knowledge, comments, proposals, and handoffs in their current views. The project itself is the shared interface.
- Show named comment/activity authors and "Agent via [person]" where appropriate. Do not imply live presence merely from membership avatars.
- A viewer sees a clear read-only state; mutation controls obey server-returned capabilities throughout the app. The current Sidebar exposes delete actions without a project capability model, so this is broader than the new dialog.
- A guest sees **Leave project**; authorized host admins see **Delete project** with impact on everyone stated. Leaving retains already-contributed content and attribution.
- On removal, close the project and show `You no longer have access to this project`, with a path to remaining projects. Do not sign the person out of their own workspace.
- Put **Connect an agent** in project context, showing the selected project and requested access. Keep global credential management in Settings.
- Reuse accessible dialogs/focus restoration; use a full-height dialog at narrow widths, with readable permission labels and 44px touch targets.

## Implementation sequence and acceptance

This is proposed sequencing for approval before implementation; no new tasks were claimed and no product behavior was changed during research.

1. **Project grants and policy:** agree on role matrix/ownership semantics; add schema, central access resolution, deduplicated discovery and capabilities. Prove Alice can share A with Bob while A's sibling B and Bob's C remain isolated.
2. **Invitations and access changes:** add creation/preview/acceptance/role/removal/leave and ledger. Prove wrong email, expiry, replay, concurrent acceptance, direct-plus-inherited grants, and revoke-versus-write races.
3. **Realtime and lifecycle:** add project identity to events, safe user access invalidation, cache clearing, exports/purge/restore. Prove no sibling event metadata leaks and no personal workspace/session loss on project removal.
4. **Credentials and agents:** add explicit resource modes, project API keys and hosted OAuth consent. Prove scoped agent listing/read/write, read-only enforcement, revocation on existing tokens and refresh, and no empty-allowlist escalation.
5. **UI and attribution:** deliver project header, Share/People dialog, invitation landing, sidebar grouping, stable links, capability-aware controls, and named authors. Exercise two independent browser accounts on desktop/mobile and through sign-in/reconnect/revocation.

The complete public feature should include scoped agent access because agent collaboration is central to Mouvadah. An internal browser-only milestone can precede it, but should not be described as a complete collaboration release. Defer public links, workspace-to-workspace federation, private-project overrides within existing shared workspaces, commenter-only roles, delegated project admins, ownership transfer, presence, document co-editing, mentions, notifications, and cross-host federation unless separately selected.

Current editing remains whole-field mutation without a version precondition; project sharing does not provide Google Docs-style concurrent text editing. Consider optimistic version/conflict detection for long knowledge/description edits if simultaneous editing becomes part of acceptance.

Future entitlement work must explicitly decide whether guests consume collaborator seats and ensure one canonical project is counted against its owning workspace. No billing behavior is assumed to exist in this proposal.

## Evidence and verification

Source anchors are relative to `/Users/andrewbetbadal/CascadeProjects/taskable` at the reviewed commit:

- `api/models/entities.py:136` workspace memberships; `:163` invitations; `:244` projects; `:288` tickets; `:332` comments; `:344` audit; `:441` API keys; `:476` project restrictions.
- `api/authorization.py:186` project access and locking; `:246` descendant authorization.
- `api/routes/projects.py:60` project discovery; `:412` project deletion and restricted-key handling.
- `api/routes/workspaces.py:555` removal and account-wide browser-session revocation.
- `api/routes/events.py:33` per-event workspace membership; `:66` stream and restricted-key denial; `api/events.py` event envelope/transport.
- `api/api_keys.py:45` issuance; `api/auth.py:108` API-key authentication; `api/routes/apikeys.py:128` creation.
- `api/mcp_oauth.py:214` consent; `:412` active grant validation; `api/hosted_mcp.py` shared REST adapter; `mcp/mcp_server.py` tool catalogue.
- `api/routes/comments.py` author payload; `api/routes/tickets.py` actor inference, claim and heartbeat; `api/routes/knowledge.py`, `proposals.py`, `sessions.py` project content paths.
- `api/workspace_export.py:50`, `api/lifecycle.py:106` lifecycle enumeration.
- `web/src/components/Sidebar.tsx:38`, `Workspace.tsx`, `WorkspaceMembersSection.tsx`, `ProfilePage.tsx`, `McpSetupModal.tsx` existing UI patterns.
- `web/src/App.tsx:26`, `web/src/context/WorkspaceContext.tsx:27`, `web/src/hooks/useAsync.ts:127`, `web/src/types.ts`, `api/schemas.py:178` navigation/cache/contracts.
- `docs/ui_design_system.md` visual and interaction constraints.

Ran `.venv/bin/python -m pytest api/tests/test_tenancy.py api/tests/test_workspace_membership_admin.py api/tests/test_sse.py api/tests/test_auth_security.py api/tests/test_hosted_mcp.py -q`: **86 passed**, one Starlette/httpx deprecation warning. These verify the current baseline, not the proposed feature. No new feature tests, PostgreSQL concurrency run, production verification, or rendered UI validation were performed. Existing unrelated working-tree files were left intact.

Mouvadah durable knowledge: no-op. This is a proposal grounded in code and a local research artifact, not an accepted architectural decision; existing implementation tickets were not marked complete or modified.
