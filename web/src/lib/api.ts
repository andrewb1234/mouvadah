/**
 * Thin typed fetch wrapper for the mouvadah API.
 *
 * The UI authenticates with an HttpOnly, server-revocable browser session.
 * All mutations go through these helpers so cookie and error behavior has one
 * typed seam.
 */

import type {
  AgentSession,
  ApiKey,
  ApiKeyCreated,
  BlockedByCategory,
  Comment,
  ControlRoomSummary,
  ContextTrail,
  KnowledgeNode,
  KnowledgeNodeStatus,
  KnowledgeNodeType,
  KnowledgeProposal,
  Project,
  ProjectMember,
  ProjectInvitation,
  ProjectInvitationPreview,
  Subproject,
  SubprojectDetail,
  Ticket,
  TicketAssignee,
  TicketDetail,
  TicketRef,
  TicketStatus,
  ActorRole,
  BrowserSession,
  Workspace,
  WorkspaceInvitation,
  WorkspaceInvitationCreated,
  WorkspaceMember,
  WorkspaceMembershipMutation,
} from "@/types";

const API_BASE =
  (import.meta as unknown as { env: Record<string, string> }).env
    .VITE_API_URL ?? "/api/v1";

export interface GitHubRepository {
  repository_id: number;
  installation_id: number;
  project_id: number;
  full_name: string;
  status: string;
}
export interface GitHubSettings {
  enabled: boolean;
  install_url: string | null;
  connections: Array<{
    installation_id: number;
    account_login: string;
    allowed_repositories: Array<{ id: number; full_name: string }>;
    status: string;
    last_error: string | null;
    updated_at: string;
    next_sync_at: string;
  }>;
  repositories: GitHubRepository[];
}
export interface GitHubLink {
  id: number;
  kind: "pull" | "issue";
  number: number;
  repository_id: number;
  repository_name: string;
  snapshot: { title?: string; state?: string; url?: string; head_sha?: string };
  synced_at: string | null;
  connection_status: string;
  repository_status: string;
}
export const getGitHubSettings = (workspaceId: number) => request<GitHubSettings>(`/github/workspaces/${workspaceId}`);
export const connectGitHub = (workspaceId: number, installationId: number) => request<{ authorization_url: string }>(`/github/workspaces/${workspaceId}/connect`, { method: "POST", body: JSON.stringify({ installation_id: installationId }) });
export const disconnectGitHub = (workspaceId: number, installationId: number) => request<void>(`/github/workspaces/${workspaceId}/connections/${installationId}`, { method: "DELETE" });
export const reconcileGitHub = (workspaceId: number, installationId: number) => request<void>(`/github/workspaces/${workspaceId}/connections/${installationId}/reconcile`, { method: "POST" });
export const mapGitHubRepository = (workspaceId: number, installationId: number, repositoryId: number, projectId: number) => request<GitHubRepository>(`/github/workspaces/${workspaceId}/repositories`, { method: "POST", body: JSON.stringify({ installation_id: installationId, repository_id: repositoryId, project_id: projectId }) });
export const unmapGitHubRepository = (workspaceId: number, repositoryId: number) => request<void>(`/github/workspaces/${workspaceId}/repositories/${repositoryId}`, { method: "DELETE" });
export const listGitHubRepositories = (projectId: number) => request<GitHubRepository[]>(`/github/projects/${projectId}/repositories`);
export const listGitHubLinks = (ticketId: number) => request<GitHubLink[]>(`/github/tickets/${ticketId}/links`);
export const createGitHubLink = (ticketId: number, repositoryId: number, kind: "pull" | "issue", number: number) => request<GitHubLink>(`/github/tickets/${ticketId}/links`, { method: "POST", body: JSON.stringify({ repository_id: repositoryId, kind, number }) });
export const removeGitHubLink = (ticketId: number, linkId: number) => request<void>(`/github/tickets/${ticketId}/links/${linkId}`, { method: "DELETE" });

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body?.detail) message = body.detail;
    } catch {
      /* ignore parse failure */
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ---- Projects -----------------------------------------------------------

export const listProjects = () => request<Project[]>("/projects");
export const listWorkspaces = () => request<Workspace[]>("/workspaces");

export const listWorkspaceMembers = (workspaceId: number) =>
  request<WorkspaceMember[]>(`/workspaces/${workspaceId}/members`);

export const listWorkspaceInvitations = (workspaceId: number) =>
  request<WorkspaceInvitation[]>(`/workspaces/${workspaceId}/invitations`);

export const createWorkspaceInvitation = (
  workspaceId: number,
  payload: {
    email: string;
    role: "ADMIN" | "MEMBER" | "VIEWER";
    expires_in_days: number;
  },
) =>
  request<WorkspaceInvitationCreated>(
    `/workspaces/${workspaceId}/invitations`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );

export const revokeWorkspaceInvitation = (
  workspaceId: number,
  invitationId: number,
) =>
  request<void>(`/workspaces/${workspaceId}/invitations/${invitationId}`, {
    method: "DELETE",
  });

export const acceptWorkspaceInvitation = (token: string) =>
  request<Workspace>("/workspaces/invitations/accept", {
    method: "POST",
    body: JSON.stringify({ token }),
  });

export const updateWorkspaceMemberRole = (
  workspaceId: number,
  userId: number,
  role: "ADMIN" | "MEMBER" | "VIEWER",
) =>
  request<WorkspaceMembershipMutation>(
    `/workspaces/${workspaceId}/members/${userId}`,
    {
      method: "PATCH",
      body: JSON.stringify({ role }),
    },
  );

export const removeWorkspaceMember = (workspaceId: number, userId: number) =>
  request<WorkspaceMembershipMutation>(
    `/workspaces/${workspaceId}/members/${userId}`,
    { method: "DELETE" },
  );

export const transferWorkspaceOwnership = (
  workspaceId: number,
  userId: number,
  confirmation: string,
) =>
  request<WorkspaceMembershipMutation>(
    `/workspaces/${workspaceId}/ownership-transfer`,
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId, confirmation }),
    },
  );

export interface WorkspaceExportResult {
  filename: string;
  sha256: string;
}

export async function exportWorkspace(
  workspaceId: number,
): Promise<WorkspaceExportResult> {
  const response = await fetch(`${API_BASE}/workspaces/${workspaceId}/export`, {
    credentials: "include",
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body?.detail) message = body.detail;
    } catch {
      /* ignore parse failure */
    }
    throw new ApiError(response.status, message);
  }

  const sha256 = response.headers.get("X-Mouvadah-Export-SHA256");
  if (!sha256) {
    throw new Error("The export response did not include its integrity hash.");
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/i);
  const filename = match?.[1] ?? `mouvadah-workspace-${workspaceId}.json`;
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
  return { filename, sha256 };
}

export const scheduleWorkspaceDeletion = (
  workspaceId: number,
  payload: { confirmation: string; export_sha256: string },
) =>
  request<{
    workspace_id: number;
    deletion_requested_at: string;
    purge_after: string;
    deletion_export_sha256: string;
    revoked_api_keys: number;
    revoked_invitations: number;
  }>(`/workspaces/${workspaceId}/deletion`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const restoreWorkspace = (workspaceId: number) =>
  request<Workspace>(`/workspaces/${workspaceId}/restore`, {
    method: "POST",
  });

export const getControlRoomSummary = (projectId: number) =>
  request<ControlRoomSummary>(`/projects/${projectId}/control-room`);
export const listProjectTickets = (projectId: number) =>
  request<TicketRef[]>(`/projects/${projectId}/tickets`);
export const createProject = (payload: {
  name: string;
  description?: string;
}) =>
  request<Project>("/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const deleteProject = (id: number) =>
  request<void>(`/projects/${id}`, { method: "DELETE" });

// ---- Subprojects --------------------------------------------------------

export const listSubprojects = (projectId: number) =>
  request<Subproject[]>(`/projects/${projectId}/subprojects`);

export const getSubproject = (id: number) =>
  request<SubprojectDetail>(`/subprojects/${id}`);

export const createSubproject = (
  projectId: number,
  payload: { name: string; context_brief?: string },
) =>
  request<Subproject>(`/projects/${projectId}/subprojects`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateSubproject = (
  id: number,
  payload: Partial<Pick<Subproject, "name" | "context_brief" | "status">>,
) =>
  request<Subproject>(`/subprojects/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const deleteSubproject = (id: number) =>
  request<void>(`/subprojects/${id}`, { method: "DELETE" });

// ---- Tickets ------------------------------------------------------------

export const getTicket = (id: number) =>
  request<TicketDetail>(`/tickets/${id}`);

export const createTicket = (
  subprojectId: number,
  payload: {
    title: string;
    description?: string;
    assignee?: TicketAssignee;
    status?: TicketStatus;
    depends_on?: number[];
  },
) =>
  request<Ticket>(`/subprojects/${subprojectId}/tickets`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateTicket = (
  id: number,
  payload: Partial<{
    title: string;
    description: string | null;
    status: TicketStatus;
    assignee: TicketAssignee;
    mr_link: string | null;
    blocked_by: BlockedByCategory | null;
    blocked_reason: string | null;
    source_refs: string[];
    depends_on: number[];
  }>,
) =>
  request<Ticket>(`/tickets/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const linkTicketMR = (id: number, url: string) =>
  request<Ticket>(`/tickets/${id}/mr`, {
    method: "POST",
    body: JSON.stringify({ url }),
  });

export const deleteTicket = (id: number) =>
  request<void>(`/tickets/${id}`, { method: "DELETE" });

export const claimTicket = (id: number, workerId: string) =>
  request<Ticket>(`/tickets/${id}/claim`, {
    method: "POST",
    body: JSON.stringify({ worker_id: workerId }),
  });

export const heartbeatTicket = (
  id: number,
  workerId: string,
  extendSeconds = 600,
) =>
  request<Ticket>(`/tickets/${id}/heartbeat`, {
    method: "POST",
    body: JSON.stringify({
      worker_id: workerId,
      extend_seconds: extendSeconds,
    }),
  });

export const requeueExpired = (subprojectId: number) =>
  request<Ticket[]>(`/tickets/subprojects/${subprojectId}/requeue-expired`, {
    method: "POST",
  });

// ---- Knowledge nodes ----------------------------------------------------

export const listKnowledgeNodes = (projectId: number) =>
  request<KnowledgeNode[]>(`/projects/${projectId}/knowledge`);

export const getContextTrail = (projectId: number, query: string, limit = 6) =>
  request<ContextTrail>(
    `/projects/${projectId}/knowledge/context-trail?query=${encodeURIComponent(query)}&limit=${limit}`,
  );

export const getKnowledgeNode = (id: number) =>
  request<KnowledgeNode>(`/knowledge/${id}`);

export const createKnowledgeNode = (
  projectId: number,
  payload: {
    title: string;
    node_type?: KnowledgeNodeType;
    content?: string;
    parent_id?: number | null;
    source_refs?: string[];
  },
) =>
  request<KnowledgeNode>(`/projects/${projectId}/knowledge`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const listKnowledgeNodesAll = (projectId: number) =>
  request<KnowledgeNode[]>(
    `/projects/${projectId}/knowledge?include_stale=true`,
  );

export const updateKnowledgeNode = (
  id: number,
  payload: Partial<{
    title: string;
    node_type: KnowledgeNodeType;
    status: KnowledgeNodeStatus;
    superseded_by: number | null;
    content: string;
    parent_id: number | null;
    source_refs: string[];
  }>,
) =>
  request<KnowledgeNode>(`/knowledge/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const deleteKnowledgeNode = (id: number) =>
  request<void>(`/knowledge/${id}`, { method: "DELETE" });

export const getTicketsForNode = (nodeId: number) =>
  request<TicketRef[]>(`/tickets/knowledge/${nodeId}/tickets`);

// ---- Knowledge proposals ------------------------------------------------

export const createProposal = (
  nodeId: number,
  payload: { proposed_changes: Record<string, unknown>; rationale?: string },
) =>
  request<KnowledgeProposal>(`/knowledge/${nodeId}/proposals`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const listProposalsForNode = (nodeId: number) =>
  request<KnowledgeProposal[]>(`/knowledge/${nodeId}/proposals`);

export const listProjectProposals = (projectId: number) =>
  request<KnowledgeProposal[]>(`/projects/${projectId}/knowledge/proposals`);

export const reviewProposal = (
  proposalId: number,
  action: "accept" | "reject",
  reviewedBy = "HUMAN",
) =>
  request<KnowledgeProposal>(`/knowledge/proposals/${proposalId}`, {
    method: "PATCH",
    body: JSON.stringify({ action, reviewed_by: reviewedBy }),
  });

// ---- Agent sessions -------------------------------------------------------

export const startSession = (
  projectId: number,
  payload: { intent: string; loaded_node_ids?: number[] },
) =>
  request<AgentSession>(`/projects/${projectId}/sessions`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const listSessions = (projectId: number) =>
  request<AgentSession[]>(`/projects/${projectId}/sessions`);

export const updateSession = (
  sessionId: number,
  payload: Partial<{
    handoff_note: string;
    status: string;
    loaded_node_ids: number[];
  }>,
) =>
  request<AgentSession>(`/agent/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

// ---- Comments -----------------------------------------------------------

export const createComment = (
  ticketId: number,
  payload: { author: ActorRole; content: string },
) =>
  request<Comment>(`/tickets/${ticketId}/comments`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const apiBase = API_BASE;

// ---- Auth ----------------------------------------------------------------

export interface AuthUser {
  id: number;
  email: string;
  name: string;
  avatar_url: string | null;
}

export interface AuthProviders {
  google: boolean;
  local_api_key: boolean;
}

export const getMe = () => request<AuthUser>("/auth/me");

export const getAuthProviders = () => request<AuthProviders>("/auth/providers");

export const createLocalSession = (apiKey: string) =>
  request<void>("/auth/local-session", {
    method: "POST",
    body: JSON.stringify({ api_key: apiKey }),
  });

export const logout = () => request<void>("/auth/logout", { method: "POST" });

export const listBrowserSessions = () =>
  request<BrowserSession[]>("/auth/sessions");

export const revokeBrowserSession = (id: string) =>
  request<void>(`/auth/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });

export const getLoginUrl = () => `${API_BASE}/auth/login`;

// ---- API Keys ------------------------------------------------------------

export const listApiKeys = () => request<ApiKey[]>("/apikeys");

export const createApiKey = (payload: {
  name: string;
  workspace_id?: number;
  scopes?: Array<"read" | "write" | "delete">;
  project_ids?: number[];
  expires_in_days?: number;
}) =>
  request<ApiKeyCreated>("/apikeys", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const revokeApiKey = (id: number) =>
  request<void>(`/apikeys/${id}`, { method: "DELETE" });

// Project-only sharing. Invitation links are returned once and never cached.
export const listProjectMembers = (id: number) =>
  request<ProjectMember[]>(`/projects/${id}/members`);
export const listProjectInvitations = (id: number) =>
  request<ProjectInvitation[]>(`/projects/${id}/invitations`);
export const createProjectInvitation = (
  id: number,
  email: string,
  role: "EDITOR" | "VIEWER",
) =>
  request<ProjectInvitation & { accept_url: string }>(
    `/projects/${id}/invitations`,
    { method: "POST", body: JSON.stringify({ email, role }) },
  );
export const revokeProjectInvitation = (id: number, invitation: number) =>
  request<void>(`/projects/${id}/invitations/${invitation}`, {
    method: "DELETE",
  });
export const updateProjectMember = (
  id: number,
  user: number,
  role: "EDITOR" | "VIEWER",
) =>
  request<void>(`/projects/${id}/members/${user}`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
export const removeProjectMember = (id: number, user: number) =>
  request<void>(`/projects/${id}/members/${user}`, { method: "DELETE" });
export const leaveProject = (id: number) =>
  request<void>(`/projects/${id}/membership`, { method: "DELETE" });
export const previewProjectInvitation = (token: string) =>
  request<ProjectInvitationPreview>("/project-invitations/preview", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
export const acceptProjectInvitation = (token: string) =>
  request<Project>("/project-invitations/accept", {
    method: "POST",
    body: JSON.stringify({ token }),
  });

export const getProject = (id: number) => request<Project>(`/projects/${id}`);
