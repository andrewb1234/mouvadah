/**
 * Direction: option 3, approved by the user. A dedicated People page keeps
 * project membership legible without mixing it with workspace administration.
 * Uses the incumbent charcoal surfaces, brass navigation, and UI controls.
 * First viewport: project context, People title/link, inline invitation, access
 * rows with inherited/direct source, then pending invitations. Mobile stacks
 * inputs and retains readable access controls. Agent access is personal and
 * project-scoped; its setup follows the people list.
 */
import { useState } from "react";
import { Copy, LockKeyhole, Users, KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAsync } from "@/hooks/useAsync";
import { useAuth } from "@/context/AuthContext";
import { useWorkspace } from "@/context/WorkspaceContext";
import {
  createApiKey,
  createProjectInvitation,
  leaveProject,
  listProjectInvitations,
  listProjectMembers,
  removeProjectMember,
  revokeProjectInvitation,
  updateProjectMember,
} from "@/lib/api";
import type { Project } from "@/types";

const roleLabel = (role: string) =>
  role === "ADMIN" ? "Admin" : role === "EDITOR" ? "Can edit" : "Can view";
const selectClass =
  "focus-ring min-h-11 rounded-md border border-input bg-background px-3 text-sm sm:min-h-9";

export function PeoplePage({ project }: { project: Project }) {
  const { user } = useAuth();
  const { refreshAccess } = useWorkspace();
  const members = useAsync(() => listProjectMembers(project.id), [project.id]);
  const invites = useAsync(
    () =>
      project.can_manage_access
        ? listProjectInvitations(project.id)
        : Promise.resolve([]),
    [project.id, project.can_manage_access],
  );
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"EDITOR" | "VIEWER">("EDITOR");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [link, setLink] = useState("");
  const [key, setKey] = useState("");
  const [agentOpen, setAgentOpen] = useState(false);
  const [keyName, setKeyName] = useState("");
  const [agentWrite, setAgentWrite] = useState(false);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNotice("");
    try {
      await action();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The change failed. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setNotice("Copied to clipboard.");
    } catch {
      setError(
        "Clipboard is unavailable. Select and copy the link or key below.",
      );
    }
  }
  const refresh = () => {
    members.refetch();
    invites.refetch();
  };

  return (
    <section
      className="min-h-0 flex-1 overflow-y-auto px-4 py-7 sm:px-8 sm:py-9"
      aria-labelledby="people-heading"
    >
      <div className="mx-auto max-w-5xl">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1
            id="people-heading"
            className="flex items-center gap-3 text-2xl font-semibold tracking-tight"
          >
            <Users className="h-6 w-6 text-muted-foreground" />
            People
          </h1>
          <Button
            variant="outline"
            onClick={() =>
              void copy(`${window.location.origin}/app?project=${project.id}`)
            }
          >
            <Copy className="h-4 w-4" />
            Copy project link
          </Button>
        </div>
        <p className="mt-3 max-w-2xl text-base text-muted-foreground">
          Work together on this project. Keep your workspaces separate.
        </p>
        <p className="mt-2 text-sm text-muted-foreground">
          A project link opens the project for people who already have access.
        </p>
        {project.can_manage_access ? (
          <form
            className="mt-7"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                const invitation = await createProjectInvitation(
                  project.id,
                  email.trim(),
                  role,
                );
                setLink(invitation.accept_url);
                setEmail("");
                invites.refetch();
                setNotice(
                  "Invitation created. Copy the link and send it to your collaborator.",
                );
              });
            }}
          >
            <label
              htmlFor="invite-email"
              className="mb-2 block text-sm font-medium"
            >
              Invite someone to {project.name}
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                id="invite-email"
                type="email"
                required
                autoComplete="email"
                placeholder="Email address"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="min-h-11 flex-1 sm:min-h-9"
              />
              <select
                aria-label="Invitation access"
                className={selectClass}
                value={role}
                onChange={(e) => setRole(e.target.value as typeof role)}
              >
                <option value="EDITOR">Can edit</option>
                <option value="VIEWER">Can view</option>
              </select>
              <Button type="submit" disabled={busy || !email.trim()}>
                {busy ? "Working…" : "Create invitation"}
              </Button>
            </div>
            <p className="mt-3 max-w-2xl text-xs leading-relaxed text-muted-foreground">
              Includes this project’s tickets, knowledge, comments, and existing
              history. Other projects and workspace settings stay private.
              Invitations expire after 7 days.
            </p>
          </form>
        ) : (
          <p className="mt-6 text-sm text-muted-foreground">
            A workspace owner or admin manages access to this project.
          </p>
        )}
        {link && (
          <div className="mt-4 rounded-md border border-border bg-card p-4">
            <label
              htmlFor="new-invite-link"
              className="block text-sm font-medium"
            >
              Invitation link · copy it now
            </label>
            <p className="mt-1 text-xs text-muted-foreground">
              Only the invited email can accept. This link will not be shown
              again.
            </p>
            <div className="mt-3 flex gap-2">
              <Input
                id="new-invite-link"
                value={link}
                readOnly
                onFocus={(e) => e.target.select()}
              />
              <Button variant="outline" onClick={() => void copy(link)}>
                Copy
              </Button>
            </div>
            <Button
              className="mt-2"
              variant="ghost"
              size="sm"
              onClick={() => setLink("")}
            >
              Done
            </Button>
          </div>
        )}
        {error && (
          <p role="alert" className="mt-4 text-sm text-destructive">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="mt-4 text-sm text-muted-foreground">
            {notice}
          </p>
        )}
        <div className="mt-9">
          <h2 className="text-base font-semibold">People with access</h2>
          {members.loading && (
            <p role="status" className="mt-3 text-sm text-muted-foreground">
              Loading people…
            </p>
          )}
          {members.error && (
            <p role="alert" className="mt-3 text-sm text-destructive">
              {members.error.message}{" "}
              <Button variant="link" onClick={members.refetch}>
                Retry
              </Button>
            </p>
          )}
          <div className="mt-3 hidden grid-cols-[minmax(0,1fr)_130px_140px_80px] gap-4 border-b border-border pb-3 text-xs text-muted-foreground md:grid">
            <span>Person</span>
            <span>Access</span>
            <span>Source</span>
            <span className="text-right">Actions</span>
          </div>
          <ul className="divide-y divide-border">
            {members.data?.map((person) => (
              <li
                key={person.user_id}
                className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-3 py-4 md:grid-cols-[minmax(0,1fr)_130px_140px_80px]"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <span
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent text-sm font-semibold"
                    aria-hidden
                  >
                    {person.name.slice(0, 1).toUpperCase()}
                  </span>
                  <div className="min-w-0">
                    <p className="break-words text-sm font-medium">
                      {person.name}
                      {person.user_id === user?.id && (
                        <span className="ml-2 text-xs text-muted-foreground">
                          You
                        </span>
                      )}
                    </p>
                    {person.email && (
                      <p className="break-all text-xs text-muted-foreground">
                        {person.email}
                      </p>
                    )}
                    <p className="mt-1 text-xs text-muted-foreground md:hidden">
                      {person.access_source === "workspace"
                        ? "Via workspace"
                        : "Added to project"}
                    </p>
                  </div>
                </div>
                {project.can_manage_access &&
                person.direct_role &&
                person.access_source === "project" ? (
                  <select
                    aria-label={`Access for ${person.name}`}
                    className={selectClass}
                    disabled={busy}
                    value={person.direct_role}
                    onChange={(e) => {
                      const next = e.target.value as "EDITOR" | "VIEWER";
                      void run(async () => {
                        await updateProjectMember(
                          project.id,
                          person.user_id,
                          next,
                        );
                        refresh();
                        setNotice(
                          `Access updated for ${person.name}. Their existing project credentials were revoked if access was reduced.`,
                        );
                      });
                    }}
                  >
                    <option value="EDITOR">Can edit</option>
                    <option value="VIEWER">Can view</option>
                  </select>
                ) : (
                  <span className="text-sm">
                    {roleLabel(person.effective_role)}
                  </span>
                )}
                <span className="hidden text-sm text-muted-foreground md:block">
                  {person.access_source === "workspace"
                    ? "Via workspace"
                    : "Added to project"}
                </span>
                <div className="col-span-2 flex justify-end md:col-span-1">
                  {project.can_manage_access && person.direct_role ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      onClick={() => {
                        if (
                          window.confirm(
                            `Remove ${person.name}’s direct project access? Any access through the workspace will remain.`,
                          )
                        )
                          void run(async () => {
                            await removeProjectMember(
                              project.id,
                              person.user_id,
                            );
                            refresh();
                          });
                      }}
                    >
                      Remove
                    </Button>
                  ) : (
                    <span className="hidden text-xs text-muted-foreground md:block">
                      —
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
        {project.can_manage_access && (
          <div className="mt-8">
            <h2 className="text-base font-semibold">
              Pending invitations{" "}
              <span className="ml-1 text-sm font-normal text-muted-foreground">
                {invites.data?.length ?? ""}
              </span>
            </h2>
            {invites.error && (
              <p role="alert" className="mt-3 text-sm text-destructive">
                {invites.error.message}
              </p>
            )}
            {invites.data?.length === 0 && (
              <p className="mt-3 text-sm text-muted-foreground">
                No pending invitations.
              </p>
            )}
            <ul className="mt-3 divide-y divide-border">
              {invites.data?.map((invite) => (
                <li
                  key={invite.id}
                  className="flex flex-wrap items-center gap-3 py-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="break-all text-sm">{invite.email}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {roleLabel(invite.role)} · Expires{" "}
                      {new Date(invite.expires_at + "Z").toLocaleDateString()}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await revokeProjectInvitation(project.id, invite.id);
                        setLink("");
                        invites.refetch();
                        setNotice("Invitation revoked.");
                      })
                    }
                  >
                    Revoke
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        )}
        <p className="mt-8 flex items-center gap-2 border-t border-border pt-5 text-xs text-muted-foreground">
          <LockKeyhole className="h-4 w-4 shrink-0" />
          Only people with access can open this project.
        </p>
        <div className="mt-8 border-t border-border pt-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold">Your agent connection</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                Connect an agent using your own access to this project.
              </p>
            </div>
            <Button
              variant="outline"
              onClick={() => setAgentOpen(!agentOpen)}
              aria-expanded={agentOpen}
            >
              <KeyRound className="h-4 w-4" />
              Connect an agent
            </Button>
          </div>
          {agentOpen && (
            <form
              className="mt-4 space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                void run(async () => {
                  const created = await createApiKey({
                    name: keyName.trim(),
                    project_ids: [project.id],
                    scopes:
                      agentWrite && project.can_edit
                        ? ["read", "write"]
                        : ["read"],
                    expires_in_days: 30,
                  });
                  setKey(created.key);
                });
              }}
            >
              <p className="max-w-2xl text-sm text-muted-foreground">
                For hosted MCP, connect to{" "}
                <code className="break-all">{window.location.origin}/mcp</code>{" "}
                and choose this project when signing in. For a local client,
                create a project key below. Manage and revoke connections in
                Settings → Agent credentials.
              </p>
              <label htmlFor="project-key-name" className="block text-sm">
                Connection name
              </label>
              <Input
                id="project-key-name"
                required
                maxLength={100}
                placeholder="My coding agent"
                value={keyName}
                onChange={(e) => setKeyName(e.target.value)}
              />
              {project.can_edit && (
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={agentWrite}
                    onChange={(e) => setAgentWrite(e.target.checked)}
                  />
                  Allow updates to project content
                </label>
              )}
              <Button type="submit" disabled={busy || !keyName.trim()}>
                Create project key
              </Button>
              <p className="text-xs text-muted-foreground">
                Expires in 30 days. Cannot delete data or manage sharing.
                Removing your access revokes this connection.
              </p>
            </form>
          )}
          {key && (
            <div className="mt-4">
              <label htmlFor="project-key" className="text-sm font-medium">
                Save your key now. It will not be shown again.
              </label>
              <div className="mt-2 flex gap-2">
                <Input
                  id="project-key"
                  readOnly
                  value={key}
                  onFocus={(e) => e.target.select()}
                />
                <Button variant="outline" onClick={() => void copy(key)}>
                  Copy
                </Button>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setKey("")}>
                I’ve saved the key
              </Button>
            </div>
          )}
        </div>
        {project.can_leave && (
          <div className="mt-8 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5">
            <p className="max-w-lg text-xs text-muted-foreground">
              Leaving removes your direct project access and its agent
              connections. Your own workspace stays available.
              {project.access_source === "workspace" &&
                " You will still have access through this workspace."}
            </p>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => {
                if (window.confirm(`Leave ${project.name}?`))
                  void run(async () => {
                    await leaveProject(project.id);
                    refreshAccess();
                  });
              }}
            >
              Leave project
            </Button>
          </div>
        )}
      </div>
    </section>
  );
}
