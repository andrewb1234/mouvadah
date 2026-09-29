/**
 * TypeScript mirrors of the Pydantic schemas in `api/schemas.py`.
 * Keep these in lock-step with the backend to preserve type safety.
 */

export type SubprojectStatus = "PLANNING" | "ACTIVE" | "COMPLETED";

export type TicketStatus =
  | "TODO"
  | "IN_PROGRESS"
  | "BLOCKED"
  | "REVIEW"
  | "DONE";

export type TicketAssignee = "HUMAN" | "AGENT" | "UNASSIGNED";

export type ActorRole = "HUMAN" | "AGENT";

export type AuditAction =
  | "STATUS_UPDATE"
  | "CONTENT_UPDATE"
  | "MR_LINKED"
  | "TICKET_CLAIMED"
  | "TICKET_REQUEUED";

export type SSEAction =
  | "SYNC_REQUIRED"
  | "PROJECT_ACCESS_CHANGED"
  | "PROJECT_CREATED"
  | "PROJECT_DELETED"
  | "SUBPROJECT_CREATED"
  | "SUBPROJECT_UPDATED"
  | "SUBPROJECT_DELETED"
  | "TICKET_CREATED"
  | "TICKET_UPDATED"
  | "TICKET_DELETED"
  | "COMMENT_CREATED"
  | "MR_LINKED"
  | "KNOWLEDGE_NODE_CREATED"
  | "KNOWLEDGE_NODE_UPDATED"
  | "KNOWLEDGE_NODE_DELETED"
  | "KNOWLEDGE_PROPOSAL_CREATED"
  | "KNOWLEDGE_PROPOSAL_REVIEWED"
  | "SESSION_STARTED"
  | "SESSION_ENDED"
  | "TICKET_CLAIMED"
  | "TICKET_REQUEUED";

export type KnowledgeNodeType = "RAW" | "SUMMARY" | "PRD" | "TDD";

export type KnowledgeNodeStatus = "CURRENT" | "STALE" | "ARCHIVED";

export type BlockedByCategory =
  | "WAITING_HUMAN"
  | "WAITING_DEPENDENCY"
  | "AMBIGUOUS_REQUIREMENT"
  | "EXTERNAL";

export interface Project {
  workspace_name: string;
  access_source: "workspace" | "project";
  effective_role: "ADMIN" | "EDITOR" | "VIEWER";
  can_edit: boolean;
  can_manage_access: boolean;
  can_delete_project: boolean;
  can_leave: boolean;
  id: number;
  workspace_id: number;
  name: string;
  description?: string | null;
  created_at: string;
}

export interface Subproject {
  id: number;
  project_id: number;
  name: string;
  context_brief: string;
  status: SubprojectStatus;
}

export interface Ticket {
  id: number;
  subproject_id: number;
  project_id?: number | null;
  title: string;
  description?: string | null;
  status: TicketStatus;
  assignee: TicketAssignee;
  mr_link?: string | null;
  blocked_by?: BlockedByCategory | null;
  blocked_reason?: string | null;
  source_refs: string[];
  depends_on: number[];
  depends_on_refs?: TicketRef[];
  claimed_by?: string | null;
  claimed_at?: string | null;
  lease_expires_at?: string | null;
}

export interface Comment {
  id: number;
  ticket_id: number;
  actor_name?: string | null;
  author: ActorRole;
  content: string;
  timestamp: string;
}

export interface AuditLog {
  id: number;
  ticket_id: number;
  action: AuditAction;
  actor_name?: string | null;
  actor: ActorRole;
  timestamp: string;
}

export interface SubprojectDetail extends Subproject {
  tickets: Ticket[];
}

export interface TicketDetail extends Ticket {
  comments: Comment[];
  audit_logs: AuditLog[];
}

export interface SSEPayload {
  action: SSEAction;
  entity: string;
  entity_id: number;
  parent_id: number | null;
  workspace_id: number | null;
}

export const TICKET_STATUSES: TicketStatus[] = [
  "TODO",
  "IN_PROGRESS",
  "BLOCKED",
  "REVIEW",
  "DONE",
];

export const TICKET_STATUS_LABELS: Record<TicketStatus, string> = {
  TODO: "Todo",
  IN_PROGRESS: "In Progress",
  BLOCKED: "Blocked",
  REVIEW: "Review",
  DONE: "Done",
};

export const ASSIGNEE_LABELS: Record<TicketAssignee, string> = {
  HUMAN: "Human",
  AGENT: "Agent",
  UNASSIGNED: "Unassigned",
};

export interface KnowledgeNode {
  id: number;
  project_id: number;
  parent_id: number | null;
  title: string;
  node_type: KnowledgeNodeType;
  status: KnowledgeNodeStatus;
  superseded_by?: number | null;
  content: string;
  source_refs: string[];
  created_by: ActorRole;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeProposal {
  id: number;
  node_id: number;
  proposed_by: string;
  proposed_changes: Record<string, unknown>;
  rationale: string;
  status: "PENDING" | "ACCEPTED" | "REJECTED";
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  created_at: string;
}

export interface TicketRef {
  id: number;
  title: string;
  status: TicketStatus;
  assignee: TicketAssignee;
  subproject_id: number;
  subproject_name?: string | null;
}

export interface ControlRoomSubproject {
  id: number;
  name: string;
  context_preview: string;
  status: SubprojectStatus;
}

export interface ControlRoomSubprojectCounts {
  subproject_id: number;
  total: number;
  moving: number;
  attention: number;
}

export interface ControlRoomSummary {
  project: Project;
  subprojects: ControlRoomSubproject[];
  ticket_status_counts: Record<TicketStatus, number>;
  ticket_assignee_counts: Record<TicketAssignee, number>;
  subproject_ticket_counts: ControlRoomSubprojectCounts[];
  attention_tickets: TicketRef[];
  attention_total: number;
  in_flight_tickets: TicketRef[];
  in_flight_total: number;
  stale_knowledge_count: number;
  pending_proposal_count: number;
  resumable_sessions: AgentSession[];
}

export interface AgentSession {
  id: number;
  project_id: number;
  intent: string;
  loaded_node_ids: number[];
  started_at: string;
  ended_at?: string | null;
  handoff_note?: string | null;
  status: "ACTIVE" | "COMPLETE" | "INTERRUPTED";
}

export interface ContextTrailSegment {
  id: number;
  title: string;
  node_type: KnowledgeNodeType;
}

export interface ContextTrailChildHint extends ContextTrailSegment {
  content_preview: string;
  source_refs: string[];
}

export interface ContextTrailItem {
  id: number;
  title: string;
  node_type: KnowledgeNodeType;
  parent_id: number | null;
  path: ContextTrailSegment[];
  score: number;
  matched_terms: string[];
  reason: string;
  content_preview: string;
  source_refs: string[];
  child_count: number;
  children: ContextTrailChildHint[];
}

export interface ContextTrail {
  project_id: number;
  project_name: string;
  query: string;
  load_order: ContextTrailSegment[];
  items: ContextTrailItem[];
}

export const KNOWLEDGE_NODE_TYPES: KnowledgeNodeType[] = [
  "RAW",
  "SUMMARY",
  "PRD",
  "TDD",
];

export const KNOWLEDGE_NODE_TYPE_LABELS: Record<KnowledgeNodeType, string> = {
  RAW: "Raw",
  SUMMARY: "Summary",
  PRD: "PRD",
  TDD: "TDD",
};

export const BLOCKED_BY_LABELS: Record<BlockedByCategory, string> = {
  WAITING_HUMAN: "Waiting: Human",
  WAITING_DEPENDENCY: "Waiting: Dependency",
  AMBIGUOUS_REQUIREMENT: "Ambiguous Requirement",
  EXTERNAL: "External",
};

export interface ApiKey {
  id: number;
  workspace_id: number | null;
  name: string;
  key_prefix: string;
  scopes: Array<"read" | "write" | "delete">;
  project_ids: number[];
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
  revoked: boolean;
}

export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export interface BrowserSession {
  id: string;
  current: boolean;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
}

export type WorkspaceRole = "OWNER" | "ADMIN" | "MEMBER" | "VIEWER" | "SERVICE";

export interface Workspace {
  id: number;
  name: string;
  slug: string;
  role: WorkspaceRole;
  created_at: string;
  deletion_requested_at: string | null;
  purge_after: string | null;
  deletion_requested_by: number | null;
  deletion_export_sha256: string | null;
}

export interface WorkspaceMember {
  user_id: number;
  email: string;
  name: string;
  role: WorkspaceRole;
  created_at: string;
}

export interface WorkspaceInvitation {
  id: number;
  workspace_id: number;
  email: string;
  role: WorkspaceRole;
  created_by_user_id: number;
  expires_at: string;
  accepted_at: string | null;
  accepted_by_user_id: number | null;
  revoked_at: string | null;
  created_at: string;
}

export interface WorkspaceInvitationCreated extends WorkspaceInvitation {
  token: string;
  accept_url: string;
}

export interface WorkspaceMembershipMutation {
  workspace_id: number;
  user_id: number;
  role: WorkspaceRole | null;
  revoked_browser_sessions: number;
  revoked_api_keys: number;
}

export interface ProjectMember {
  user_id: number;
  name: string;
  email: string | null;
  avatar_url: string | null;
  effective_role: "ADMIN" | "EDITOR" | "VIEWER";
  access_source: "workspace" | "project";
  direct_role: "EDITOR" | "VIEWER" | null;
}
export interface ProjectInvitation {
  id: number;
  email: string;
  role: "EDITOR" | "VIEWER";
  expires_at: string;
}
export interface ProjectInvitationPreview {
  project_id: number;
  project_name: string;
  workspace_name: string;
  inviter_name: string;
  role: "EDITOR" | "VIEWER";
  expires_at: string;
}
