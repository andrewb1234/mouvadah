import { useCallback, useEffect, useState } from "react";
import {
  ArrowLeft,
  Copy,
  Check,
  Download,
  KeyRound,
  Plus,
  RotateCcw,
  Trash2,
  Terminal,
  AlertCircle,
  Loader2,
  ShieldCheck,
  LogOut,
  UserRound,
} from "lucide-react";
import { MouvadahLockup } from "@/components/brand/mouvadah-brand";
import { TechnicalLabel } from "@/components/ui/technical-label";
import { useAuth } from "@/context/AuthContext";
import {
  listApiKeys,
  createApiKey,
  revokeApiKey,
  listProjects,
  listWorkspaces,
  listBrowserSessions,
  revokeBrowserSession,
  exportWorkspace,
  restoreWorkspace,
  scheduleWorkspaceDeletion,
  acceptWorkspaceInvitation,
} from "@/lib/api";
import type {
  ApiKey,
  ApiKeyCreated,
  BrowserSession,
  Project,
  Workspace,
} from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { McpSetupModal } from "@/components/McpSetupModal";
import { WorkspaceMembersSection } from "@/components/WorkspaceMembersSection";

interface ProfilePageProps {
  onBack: () => void;
  pendingInvitationToken?: string | null;
  onInvitationHandled?: () => void;
  onInvitationTerminalFailure?: () => void;
  onInvitationSwitchAccount?: () => Promise<void>;
}

const profileSections = [
  ["identity", "Identity"],
  ["agent-credentials", "Agent credentials"],
  ["workspace-access", "Workspace access"],
  ["data-recovery", "Data & recovery"],
  ["browser-sessions", "Browser sessions"],
] as const;

function jumpToProfileSection(id: string) {
  document.getElementById(id)?.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
    block: "start",
  });
}

export function ProfilePage({
  onBack,
  pendingInvitationToken = null,
  onInvitationHandled,
  onInvitationTerminalFailure,
  onInvitationSwitchAccount,
}: ProfilePageProps) {
  const { user, logout } = useAuth();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newKeyName, setNewKeyName] = useState("");
  const [newKeyExpiry, setNewKeyExpiry] = useState("");
  const [newKeyWorkspaceId, setNewKeyWorkspaceId] = useState("");
  const [newKeyAccess, setNewKeyAccess] = useState<
    "read" | "read-write" | "destructive"
  >(
    "read-write",
  );
  const [newKeyProjectIds, setNewKeyProjectIds] = useState<number[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [browserSessions, setBrowserSessions] = useState<BrowserSession[]>([]);
  const [creating, setCreating] = useState(false);
  const [newlyCreatedKey, setNewlyCreatedKey] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [mcpOpen, setMcpOpen] = useState(false);
  const [mcpMode, setMcpMode] = useState<"hosted" | "local">("hosted");
  const [workspaceActionId, setWorkspaceActionId] = useState<number | null>(
    null,
  );
  const [exportHashes, setExportHashes] = useState<Record<number, string>>({});
  const [recoveryNotice, setRecoveryNotice] = useState<string | null>(null);
  const [invitationNotice, setInvitationNotice] = useState<string | null>(null);
  const [invitationError, setInvitationError] = useState<string | null>(null);
  const [acceptingInvitation, setAcceptingInvitation] = useState(false);

  const fetchKeys = useCallback(async () => {
    try {
      const data = await listApiKeys();
      setKeys(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load API keys");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchKeys();
  }, [fetchKeys]);

  const fetchAccessData = useCallback(async () => {
    const [workspaceRows, projectRows, sessionRows] = await Promise.all([
      listWorkspaces(),
      listProjects(),
      listBrowserSessions(),
    ]);
    const activeWorkspaceRows = workspaceRows.filter(
      (workspace) => !workspace.deletion_requested_at,
    );
    setWorkspaces(workspaceRows);
    setProjects(projectRows);
    setBrowserSessions(sessionRows);
    setNewKeyWorkspaceId((current) => {
      if (
        current &&
        activeWorkspaceRows.some(
          (workspace) => workspace.id === Number(current),
        )
      ) {
        return current;
      }
      return activeWorkspaceRows[0] ? String(activeWorkspaceRows[0].id) : "";
    });
  }, []);

  useEffect(() => {
    fetchAccessData().catch((err) => {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to load workspace access options",
      );
    });
  }, [fetchAccessData]);

  async function handleAcceptInvitation() {
    if (!pendingInvitationToken || acceptingInvitation) return;
    setAcceptingInvitation(true);
    setInvitationError(null);
    try {
      const workspace = await acceptWorkspaceInvitation(
        pendingInvitationToken,
      );
      setInvitationNotice(`You joined ${workspace.name} as ${workspace.role}.`);
      onInvitationHandled?.();
      await fetchAccessData();
    } catch (err) {
      setInvitationError(
        err instanceof Error
          ? err.message
          : "This invitation is not available for your account",
      );
      onInvitationTerminalFailure?.();
    } finally {
      setAcceptingInvitation(false);
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newKeyName.trim() || !newKeyWorkspaceId) return;
    setCreating(true);
    setError(null);
    try {
      const payload: {
        name: string;
        workspace_id: number;
        scopes: Array<"read" | "write" | "delete">;
        project_ids: number[];
        expires_in_days?: number;
      } = {
        name: newKeyName.trim(),
        workspace_id: Number(newKeyWorkspaceId),
        scopes:
          newKeyAccess === "read"
            ? ["read"]
            : newKeyAccess === "destructive"
              ? ["read", "write", "delete"]
              : ["read", "write"],
        project_ids: newKeyProjectIds,
      };
      if (newKeyExpiry) {
        const days = parseInt(newKeyExpiry, 10);
        if (!isNaN(days) && days > 0) {
          payload.expires_in_days = days;
        }
      }
      const created = await createApiKey(payload);
      setNewlyCreatedKey(created);
      setNewKeyName("");
      setNewKeyExpiry("");
      setNewKeyProjectIds([]);
      fetchKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create API key");
    } finally {
      setCreating(false);
    }
  }

  async function handleRevoke(id: number) {
    if (!window.confirm("Revoke this API key? Agents using it will lose access immediately.")) {
      return;
    }
    try {
      await revokeApiKey(id);
      fetchKeys();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke key");
    }
  }

  async function handleRevokeSession(id: string, current: boolean) {
    const message = current
      ? "Sign out this browser session now?"
      : "Revoke this browser session?";
    if (!window.confirm(message)) return;
    try {
      await revokeBrowserSession(id);
      if (current) {
        window.location.assign("/");
        return;
      }
      setBrowserSessions((sessions) =>
        sessions.filter((session) => session.id !== id),
      );
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to revoke browser session",
      );
    }
  }

  async function handleWorkspaceExport(workspace: Workspace) {
    setWorkspaceActionId(workspace.id);
    setError(null);
    setRecoveryNotice(null);
    try {
      const result = await exportWorkspace(workspace.id);
      setExportHashes((current) => ({
        ...current,
        [workspace.id]: result.sha256,
      }));
      setRecoveryNotice(
        `${workspace.name} was exported as ${result.filename}. Keep that file safe.`,
      );
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to export workspace",
      );
    } finally {
      setWorkspaceActionId(null);
    }
  }

  async function handleScheduleDeletion(workspace: Workspace) {
    const exportSha256 = exportHashes[workspace.id];
    if (!exportSha256) {
      setError(
        `Export ${workspace.name} in this browser before scheduling deletion.`,
      );
      return;
    }
    const confirmation = window.prompt(
      `Schedule "${workspace.name}" for permanent deletion after the recovery window.\n\nThis immediately revokes its API keys. Type ${workspace.slug} to continue:`,
    );
    if (confirmation === null) return;

    setWorkspaceActionId(workspace.id);
    setError(null);
    setRecoveryNotice(null);
    try {
      const result = await scheduleWorkspaceDeletion(workspace.id, {
        confirmation,
        export_sha256: exportSha256,
      });
      await Promise.all([fetchAccessData(), fetchKeys()]);
      setExportHashes((current) => {
        const next = { ...current };
        delete next[workspace.id];
        return next;
      });
      setRecoveryNotice(
        `${workspace.name} is hidden and recoverable until ${formatDateTime(result.purge_after)}. Its API keys and pending invitations were revoked.`,
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to schedule workspace deletion",
      );
    } finally {
      setWorkspaceActionId(null);
    }
  }

  async function handleRestoreWorkspace(workspace: Workspace) {
    setWorkspaceActionId(workspace.id);
    setError(null);
    setRecoveryNotice(null);
    try {
      await restoreWorkspace(workspace.id);
      await Promise.all([fetchAccessData(), fetchKeys()]);
      setRecoveryNotice(
        `${workspace.name} was restored. Previously revoked API keys remain revoked; create new keys when needed.`,
      );
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to restore workspace",
      );
    } finally {
      setWorkspaceActionId(null);
    }
  }

  async function copyKey(key: string) {
    try {
      await navigator.clipboard.writeText(key);
      setCopied(true);
      setError(null);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
      setError(
        "Clipboard access was denied. Select the one-time key and copy it manually before dismissing it.",
      );
    }
  }

  function formatDate(dateStr: string | null): string {
    if (!dateStr) return "—";
    const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(dateStr)
      ? dateStr
      : `${dateStr}Z`;
    const d = new Date(normalized);
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  }

  function formatDateTime(dateStr: string | null): string {
    if (!dateStr) return "—";
    const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(dateStr)
      ? dateStr
      : `${dateStr}Z`;
    return new Date(normalized).toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    });
  }

  const activeKeys = keys.filter((k) => !k.revoked);
  const revokedKeys = keys.filter((k) => k.revoked);
  const activeWorkspaces = workspaces.filter(
    (workspace) => !workspace.deletion_requested_at,
  );
  const deletedWorkspaces = workspaces.filter(
    (workspace) => workspace.deletion_requested_at,
  );
  const selectedWorkspaceProjects = projects.filter(
    (project) => project.workspace_id === Number(newKeyWorkspaceId),
  );
  const workspaceName = (workspaceId: number | null) =>
    workspaces.find((workspace) => workspace.id === workspaceId)?.name ??
    "Unknown workspace";

  return (
    <div className="flex h-dvh w-full min-w-0 flex-col overflow-hidden bg-background">
      <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border bg-card px-3 sm:px-6">
        <Button
          variant="ghost"
          size="icon"
          onClick={onBack}
          aria-label="Back to workspace"
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="min-w-0 flex-1">
          <TechnicalLabel>Trust administration</TechnicalLabel>
          <h1 className="truncate text-base font-semibold sm:text-lg">
            Profile & Settings
          </h1>
        </div>
        <div className="hidden sm:block">
          <MouvadahLockup size="sm" />
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
        <div className="mx-auto grid w-full max-w-6xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[220px_minmax(0,1fr)] lg:gap-10 lg:py-10">
          <aside className="self-start lg:sticky lg:top-6">
            <p className="technical-label mb-3">Account surfaces</p>
            <nav
              aria-label="Profile settings sections"
              className="grid grid-cols-2 gap-1 rounded-sm border border-border bg-card p-2 sm:grid-cols-3 lg:grid-cols-1"
            >
              {profileSections.map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => jumpToProfileSection(id)}
                  className="focus-ring transition-fast min-h-10 rounded-sm px-3 text-left text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  {label}
                </button>
              ))}
            </nav>
            <div className="mt-3 hidden rounded-sm border border-border bg-surface-subtle p-3 text-xs leading-relaxed text-muted-foreground lg:block">
              Security scope, expiry, role, and recovery consequences remain
              visible before you act.
            </div>
          </aside>

          <div className="min-w-0 space-y-10">
          {/* User info */}
          <section
            id="identity"
            aria-labelledby="identity-heading"
            className="scroll-mt-6 rounded-sm border border-border bg-card p-5 sm:p-6"
          >
            <div className="flex min-w-0 items-center gap-4">
              {user?.avatar_url ? (
                <img
                  src={user.avatar_url}
                  alt=""
                  className="h-14 w-14 shrink-0 rounded-full"
                />
              ) : (
                <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-primary text-xl font-medium text-primary-foreground">
                  {user?.name?.charAt(0).toUpperCase() ?? "?"}
                </div>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <UserRound
                    className="h-4 w-4 text-brand-brass"
                    aria-hidden
                  />
                  <h2 id="identity-heading" className="text-sm font-semibold">
                    Identity
                  </h2>
                </div>
                <p className="mt-2 truncate text-lg font-semibold">
                  {user?.name}
                </p>
                <p className="truncate text-sm text-muted-foreground">
                  {user?.email}
                </p>
              </div>
            </div>
          </section>

          {invitationNotice && (
            <div
              role="status"
              aria-live="polite"
              className="rounded-sm border border-status-done-border bg-status-done p-3 text-xs text-status-done-foreground"
            >
              {invitationNotice}
            </div>
          )}

          {pendingInvitationToken && !invitationNotice && (
            <section
              aria-labelledby="invitation-heading"
              className="space-y-3 rounded-sm border border-brand-brass/40 bg-brand-brass/10 p-4"
            >
              <div>
                <p className="technical-label">Pending access request</p>
                <h2
                  id="invitation-heading"
                  className="mt-2 text-sm font-semibold"
                >
                  Workspace invitation for {user?.email}
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  The invitation is bound to this signed-in email and can be
                  used once. Its workspace and role are confirmed by the server
                  when you accept; the invitation secret is never displayed.
                </p>
              </div>
              {invitationError && (
                <p
                  role="alert"
                  aria-live="assertive"
                  className="text-xs text-destructive"
                >
                  {invitationError}
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                {!invitationError && (
                  <Button
                    size="sm"
                    disabled={acceptingInvitation}
                    onClick={handleAcceptInvitation}
                  >
                    {acceptingInvitation && (
                      <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                    )}
                    Accept invitation
                  </Button>
                )}
                {invitationError && onInvitationSwitchAccount && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={onInvitationSwitchAccount}
                  >
                    Switch account
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={onInvitationHandled}
                >
                  Dismiss
                </Button>
              </div>
            </section>
          )}

          {/* MCP Setup */}
          <section
            id="agent-credentials"
            aria-labelledby="agent-credentials-heading"
            className="scroll-mt-6 rounded-sm border border-border bg-card p-5"
          >
            <div className="flex flex-col items-start justify-between gap-4 sm:flex-row">
              <div>
                <h2
                  id="agent-credentials-heading"
                  className="flex items-center gap-2 text-sm font-semibold"
                >
                  <Terminal className="h-4 w-4" />
                  Agent credentials
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  Connect Claude on web or mobile through the hosted connector—no
                  installation or API key needed. For a local MCP bridge, create
                  a scoped API key below. Revoke hosted connections here too.
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => { setMcpMode("hosted"); setMcpOpen(true); }}
                className="self-start"
              >
                <Terminal className="mr-1.5 h-3.5 w-3.5" />
                Setup Guide
              </Button>
            </div>
          </section>

          {/* API Keys */}
          <section
            aria-labelledby="api-keys-heading"
            className="space-y-4"
          >
            <div className="flex items-center justify-between">
              <h2
                id="api-keys-heading"
                className="flex items-center gap-2 text-sm font-semibold"
              >
                <KeyRound className="h-4 w-4" />
                API Keys
              </h2>
              <span className="text-xs text-muted-foreground">
                {activeKeys.length} active
              </span>
            </div>

            {/* New key form */}
            <form
              onSubmit={handleCreate}
              className="grid gap-3 rounded-lg border border-border bg-card p-4 sm:grid-cols-2"
            >
              <div>
                <label
                  htmlFor="new-key-name"
                  className="mb-1 block text-xs font-medium text-muted-foreground"
                >
                  Key name
                </label>
                <Input
                  id="new-key-name"
                  placeholder="e.g. Claude Desktop, Windsurf, CI bot"
                  value={newKeyName}
                  onChange={(e) => setNewKeyName(e.target.value)}
                  className="h-8 text-sm"
                />
              </div>
              <div>
                <label
                  htmlFor="new-key-workspace"
                  className="mb-1 block text-xs font-medium text-muted-foreground"
                >
                  Workspace
                </label>
                <select
                  id="new-key-workspace"
                  value={newKeyWorkspaceId}
                  onChange={(event) => {
                    setNewKeyWorkspaceId(event.target.value);
                    setNewKeyProjectIds([]);
                  }}
                  className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
                >
                  {activeWorkspaces.map((workspace) => (
                    <option key={workspace.id} value={workspace.id}>
                      {workspace.name} · {workspace.role}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label
                  htmlFor="new-key-access"
                  className="mb-1 block text-xs font-medium text-muted-foreground"
                >
                  Access
                </label>
                <select
                  id="new-key-access"
                  value={newKeyAccess}
                  onChange={(event) =>
                    setNewKeyAccess(
                      event.target.value as
                        | "read"
                        | "read-write"
                        | "destructive",
                    )
                  }
                  className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
                >
                  <option value="read-write">Read and write</option>
                  <option value="read">Read only</option>
                  <option value="destructive">
                    Read, write, and irreversible delete
                  </option>
                </select>
                {newKeyAccess === "destructive" && (
                  <p className="mt-1 text-[11px] text-destructive">
                    Only use this for a deliberately trusted client. Ordinary
                    MCP keys cannot delete data.
                  </p>
                )}
              </div>
              <div>
                <label
                  htmlFor="new-key-expiry"
                  className="mb-1 block text-xs font-medium text-muted-foreground"
                >
                  Expires (days)
                </label>
                <Input
                  id="new-key-expiry"
                  type="number"
                  min="1"
                  max="365"
                  placeholder="Never"
                  value={newKeyExpiry}
                  onChange={(e) => setNewKeyExpiry(e.target.value)}
                  className="h-8 text-sm"
                />
              </div>
              {selectedWorkspaceProjects.length > 0 && (
                <fieldset className="space-y-2 sm:col-span-2">
                  <legend className="text-xs font-medium text-muted-foreground">
                    Project restriction (optional)
                  </legend>
                  <p className="text-[11px] text-muted-foreground">
                    No selection grants the key access to every project in this
                    workspace.
                  </p>
                  <div className="grid max-h-32 gap-1 overflow-auto rounded-md border border-border p-2 sm:grid-cols-2">
                    {selectedWorkspaceProjects.map((project) => (
                      <label
                        key={project.id}
                        className="flex items-center gap-2 text-xs"
                      >
                        <input
                          type="checkbox"
                          checked={newKeyProjectIds.includes(project.id)}
                          onChange={(event) =>
                            setNewKeyProjectIds((current) =>
                              event.target.checked
                                ? [...current, project.id]
                                : current.filter((id) => id !== project.id),
                            )
                          }
                        />
                        <span className="truncate">{project.name}</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
              )}
              <div className="sm:col-span-2">
                <Button
                  type="submit"
                  size="sm"
                  disabled={creating || !newKeyName.trim() || !newKeyWorkspaceId}
                >
                {creating ? (
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Plus className="mr-1 h-3.5 w-3.5" />
                )}
                Create Key
                </Button>
              </div>
            </form>

            {/* Newly created key banner */}
            {newlyCreatedKey && (
              <div
                role="status"
                aria-live="polite"
                className="rounded-sm border border-success/40 bg-success/10 p-4"
              >
                <div className="flex items-start gap-2">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-status-done-foreground" />
                  <div className="flex-1">
                    <p className="text-sm font-semibold text-status-done-foreground">
                      API key created — copy it now!
                    </p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      This secret will not be shown again. Store it in a file
                      readable only by its owner (<code>0600</code>).
                    </p>
                    <div className="mt-2 flex items-center gap-2">
                      <code className="flex-1 truncate rounded bg-status-done px-2 py-1.5 text-xs font-mono text-status-done-foreground">
                        {newlyCreatedKey.key}
                      </code>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 shrink-0"
                        onClick={() => void copyKey(newlyCreatedKey.key)}
                        aria-label={
                          copied ? "API key copied" : "Copy new API key"
                        }
                      >
                        {copied ? (
                          <Check className="h-3.5 w-3.5 text-status-done-foreground" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )}
                      </Button>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      className="mt-2"
                      onClick={() => {
                        setMcpMode("local");
                        setMcpOpen(true);
                      }}
                    >
                      <Terminal className="mr-1.5 h-3.5 w-3.5" />
                      Configure local MCP with this key
                    </Button>
                    <button
                      className="ml-2 text-xs text-muted-foreground hover:text-foreground"
                      onClick={() => setNewlyCreatedKey(null)}
                    >
                      Dismiss
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Error */}
            {error && (
              <div
                role="alert"
                aria-live="assertive"
                className="flex flex-wrap items-center gap-2 rounded-sm border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive"
              >
                <AlertCircle className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1">{error}</span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setError(null);
                    void Promise.all([fetchKeys(), fetchAccessData()]).catch(
                      (err) =>
                        setError(
                          err instanceof Error
                            ? err.message
                            : "Failed to refresh account data",
                        ),
                    );
                  }}
                >
                  Retry
                </Button>
              </div>
            )}

            {/* Key list */}
            {loading ? (
              <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading keys…
              </div>
            ) : (
              <div className="space-y-2">
                {activeKeys.map((key) => (
                  <div
                    key={key.id}
                    className="flex items-center gap-3 rounded-lg border border-border bg-card p-3"
                  >
                    <KeyRound className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium">{key.name}</p>
                        {key.expires_at && (
                          <span className="rounded border border-status-review-border bg-status-review px-1.5 py-0.5 text-xs text-status-review-foreground">
                            Expires {formatDate(key.expires_at)}
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground">
                        <code>{key.key_prefix}…</code>
                        {" · "}{workspaceName(key.workspace_id)}
                        {" · "}
                        {key.scopes.includes("delete")
                          ? "Read/write/delete"
                          : key.scopes.includes("write")
                            ? "Read/write"
                            : "Read only"}
                        {" · "}
                        {key.project_ids.length
                          ? `${key.project_ids.length} project${key.project_ids.length === 1 ? "" : "s"}`
                          : "All projects"}
                        {" · "}Created {formatDate(key.created_at)}
                        {" · "}Last used {formatDate(key.last_used_at)}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 shrink-0 text-muted-foreground hover:text-destructive"
                      onClick={() => handleRevoke(key.id)}
                      aria-label={`Revoke ${key.name}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ))}

                {revokedKeys.length > 0 && (
                  <>
                    <p className="pt-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                      Revoked
                    </p>
                    {revokedKeys.map((key) => (
                      <div
                        key={key.id}
                        className="flex items-center gap-3 rounded-lg border border-border bg-muted/30 p-3 opacity-60"
                      >
                        <KeyRound className="h-4 w-4 shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium line-through">{key.name}</p>
                          <p className="text-xs text-muted-foreground">
                            <code>{key.key_prefix}…</code>
                          </p>
                        </div>
                      </div>
                    ))}
                  </>
                )}

                {keys.length === 0 && !loading && (
                  <div className="rounded-lg border border-dashed border-border p-8 text-center">
                    <KeyRound className="mx-auto h-8 w-8 text-muted-foreground/50" />
                    <p className="mt-2 text-sm text-muted-foreground">
                      No API keys or hosted connections yet. Use the Setup Guide to connect Claude, or create a key for a local bridge.
                    </p>
                  </div>
                )}
              </div>
            )}
          </section>

          <div id="workspace-access" className="scroll-mt-6">
            {user && (
              <WorkspaceMembersSection
                workspaces={workspaces}
                currentUserId={user.id}
                onWorkspacesChanged={fetchAccessData}
                onError={setError}
              />
            )}
          </div>

          {/* Data recovery */}
          <section
            id="data-recovery"
            aria-labelledby="data-recovery-heading"
            className="scroll-mt-6 space-y-4"
          >
            <div>
              <h2
                id="data-recovery-heading"
                className="flex items-center gap-2 text-sm font-semibold"
              >
                <Download className="h-4 w-4" />
                Data & Recovery
              </h2>
              <p className="mt-1 text-xs text-muted-foreground">
                Owners can export a complete workspace, then schedule a
                recoverable deletion. Scheduling immediately revokes every API
                key for that workspace.
              </p>
            </div>

            {recoveryNotice && (
              <div
                role="status"
                aria-live="polite"
                className="rounded-sm border border-status-done-border bg-status-done p-3 text-xs text-status-done-foreground"
              >
                {recoveryNotice}
              </div>
            )}

            <div className="space-y-2">
              {activeWorkspaces.map((workspace) => (
                <div
                  key={workspace.id}
                  className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4 sm:flex-row sm:items-center"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{workspace.name}</p>
                    <p className="text-xs text-muted-foreground">
                      <code>{workspace.slug}</code>
                      {" · "}
                      {workspace.role}
                    </p>
                  </div>
                  {workspace.role === "OWNER" ? (
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={workspaceActionId === workspace.id}
                        onClick={() => handleWorkspaceExport(workspace)}
                      >
                        {workspaceActionId === workspace.id ? (
                          <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Download className="mr-1.5 h-3.5 w-3.5" />
                        )}
                        Export
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={
                          workspaceActionId === workspace.id ||
                          !exportHashes[workspace.id]
                        }
                        onClick={() => handleScheduleDeletion(workspace)}
                      >
                        <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                        Schedule deletion
                      </Button>
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      Owner controls only
                    </span>
                  )}
                </div>
              ))}
            </div>

            {deletedWorkspaces.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  Pending permanent deletion
                </p>
                {deletedWorkspaces.map((workspace) => (
                  <div
                    key={workspace.id}
                    className="flex flex-col gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4 sm:flex-row sm:items-center"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{workspace.name}</p>
                      <p className="text-xs text-muted-foreground">
                        Recoverable until{" "}
                        {formatDateTime(workspace.purge_after)}. API keys stay
                        revoked after restoration.
                      </p>
                    </div>
                    {workspace.role === "OWNER" && (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={workspaceActionId === workspace.id}
                        onClick={() => handleRestoreWorkspace(workspace)}
                      >
                        {workspaceActionId === workspace.id ? (
                          <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                        )}
                        Restore
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Browser sessions */}
          <section
            id="browser-sessions"
            aria-labelledby="browser-sessions-heading"
            className="scroll-mt-6 space-y-4"
          >
            <div>
              <h2
                id="browser-sessions-heading"
                className="flex items-center gap-2 text-sm font-semibold"
              >
                <ShieldCheck className="h-4 w-4" />
                Browser Sessions
              </h2>
              <p className="mt-1 text-xs text-muted-foreground">
                Revoke sessions you no longer recognize. Revocation takes effect
                immediately.
              </p>
            </div>
            <div className="space-y-2">
              {browserSessions.map((browserSession) => (
                <div
                  key={browserSession.id}
                  className="flex items-center gap-3 rounded-lg border border-border bg-card p-3"
                >
                  <ShieldCheck className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">
                      {browserSession.current ? "This browser" : "Browser session"}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      Last active {formatDate(browserSession.last_seen_at)}
                      {" · "}Expires {formatDate(browserSession.expires_at)}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      handleRevokeSession(
                        browserSession.id,
                        browserSession.current,
                      )
                    }
                  >
                    {browserSession.current ? "Sign out" : "Revoke"}
                  </Button>
                </div>
              ))}
              {browserSessions.length === 0 && (
                <div className="rounded-sm border border-dashed border-border p-5 text-sm text-muted-foreground">
                  No active browser sessions were returned. Refresh account
                  data or sign in again if this state is unexpected.
                </div>
              )}
            </div>
          </section>

          {/* Sign out */}
          <section className="rounded-sm border border-border bg-card p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="flex items-center gap-2 text-sm font-semibold">
                  <LogOut className="h-4 w-4" aria-hidden />
                  Sign out
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  End this browser session without changing other sessions or
                  agent credentials.
                </p>
              </div>
            <Button variant="outline" onClick={logout}>
              Sign out
            </Button>
            </div>
          </section>
          </div>
        </div>
      </div>

      <McpSetupModal
        key={`${mcpMode}-${mcpOpen}`}
        initialMode={mcpMode}
        open={mcpOpen}
        onOpenChange={setMcpOpen}
        apiKey={newlyCreatedKey?.key ?? null}
      />
    </div>
  );
}
