"""Pydantic request/response DTOs.

Kept separate from SQLModel table classes so input validation is explicit and
response payloads can embed relations without accidental recursion.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from api.models.enums import (
    ActorRole,
    AuditAction,
    BlockedByCategory,
    KnowledgeNodeStatus,
    KnowledgeNodeType,
    SubprojectStatus,
    TicketAssignee,
    TicketStatus,
    WorkspaceLifecycleAction,
    WorkspaceRole,
)

MAX_LONG_TEXT_LENGTH = 100_000
MAX_COMMENT_LENGTH = 20_000
MAX_REFERENCE_LENGTH = 2_048
MAX_REFERENCES = 100
MAX_DEPENDENCIES = 100
MAX_SESSION_NODE_IDS = 500

LongText = Annotated[str, Field(max_length=MAX_LONG_TEXT_LENGTH)]
CommentText = Annotated[
    str,
    Field(min_length=1, max_length=MAX_COMMENT_LENGTH),
]
SourceReference = Annotated[
    str,
    Field(min_length=1, max_length=MAX_REFERENCE_LENGTH),
]


# ---- Workspace ------------------------------------------------------------


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=80,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    role: WorkspaceRole
    created_at: datetime
    deletion_requested_at: Optional[datetime] = None
    purge_after: Optional[datetime] = None
    deletion_requested_by: Optional[int] = None
    deletion_export_sha256: Optional[str] = None


class WorkspaceDeletionCreate(BaseModel):
    confirmation: str = Field(min_length=1, max_length=80)
    export_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class WorkspaceDeletionRead(BaseModel):
    workspace_id: int
    deletion_requested_at: datetime
    purge_after: datetime
    deletion_export_sha256: str
    revoked_api_keys: int = 0
    revoked_invitations: int = 0


class WorkspaceLifecycleEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    action: WorkspaceLifecycleAction
    actor_user_id: Optional[int] = None
    occurred_at: datetime
    details: dict = Field(default_factory=dict)


class WorkspaceMemberRead(BaseModel):
    user_id: int
    email: str
    name: str
    role: WorkspaceRole
    created_at: datetime


class WorkspaceInvitationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: str = Field(
        min_length=3,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    role: WorkspaceRole = WorkspaceRole.MEMBER
    expires_in_days: int = Field(default=7, ge=1, le=30)


class WorkspaceInvitationRead(BaseModel):
    id: int
    workspace_id: int
    email: str
    role: WorkspaceRole
    created_by_user_id: int
    expires_at: datetime
    accepted_at: Optional[datetime] = None
    accepted_by_user_id: Optional[int] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime


class WorkspaceInvitationCreated(WorkspaceInvitationRead):
    token: str
    accept_url: str


class WorkspaceInvitationAccept(BaseModel):
    token: str = Field(min_length=32, max_length=200)


class WorkspaceMemberRoleUpdate(BaseModel):
    role: WorkspaceRole


class WorkspaceOwnershipTransfer(BaseModel):
    user_id: int
    confirmation: str = Field(min_length=1, max_length=80)


class WorkspaceMembershipMutationRead(BaseModel):
    workspace_id: int
    user_id: int
    role: Optional[WorkspaceRole] = None
    revoked_browser_sessions: int = 0
    revoked_api_keys: int = 0


class WorkspaceMembershipEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    action: str
    actor_user_id: Optional[int] = None
    subject_user_id: Optional[int] = None
    invitation_id: Optional[int] = None
    occurred_at: datetime
    details: dict = Field(default_factory=dict)


# ---- Project --------------------------------------------------------------


class ProjectCreate(BaseModel):
    workspace_id: Optional[int] = None
    name: str = Field(min_length=1, max_length=200)
    description: Optional[LongText] = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    name: str
    description: Optional[str] = None
    created_at: datetime

    workspace_name: str = ""
    access_source: str = "workspace"
    effective_role: str = "VIEWER"
    can_edit: bool = False
    can_manage_access: bool = False
    can_delete_project: bool = False
    can_leave: bool = False


class ControlRoomSubprojectRead(BaseModel):
    """The subproject fields needed to render the project Control Room."""

    id: int
    name: str
    context_preview: str
    status: SubprojectStatus


class ControlRoomSubprojectCounts(BaseModel):
    """Compact ticket totals for one subproject."""

    subproject_id: int
    total: int = 0
    moving: int = 0
    attention: int = 0


class ControlRoomSummary(BaseModel):
    """Bounded read model for the project-level operational dashboard.

    It intentionally omits knowledge content, proposal payloads, and complete
    agent-session history. Those records remain available from their detail
    endpoints when a user opens the corresponding workbench.
    """

    project: ProjectRead
    subprojects: list[ControlRoomSubprojectRead] = Field(default_factory=list)
    ticket_status_counts: dict[TicketStatus, int] = Field(default_factory=dict)
    ticket_assignee_counts: dict[TicketAssignee, int] = Field(default_factory=dict)
    subproject_ticket_counts: list[ControlRoomSubprojectCounts] = Field(
        default_factory=list
    )
    attention_tickets: list["TicketRef"] = Field(default_factory=list)
    attention_total: int = 0
    in_flight_tickets: list["TicketRef"] = Field(default_factory=list)
    in_flight_total: int = 0
    stale_knowledge_count: int = 0
    pending_proposal_count: int = 0
    resumable_sessions: list["AgentSessionRead"] = Field(default_factory=list)


# ---- Subproject -----------------------------------------------------------


class SubprojectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    context_brief: LongText = ""
    status: SubprojectStatus = SubprojectStatus.PLANNING


class SubprojectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    context_brief: Optional[LongText] = None
    status: Optional[SubprojectStatus] = None


class SubprojectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    context_brief: str
    status: SubprojectStatus


# ---- Ticket ---------------------------------------------------------------


class TicketRef(BaseModel):
    """Compact ticket reference used for dependency edges and backlinks."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    status: TicketStatus
    assignee: TicketAssignee
    subproject_id: int
    subproject_name: Optional[str] = None


class TicketCreate(BaseModel):
    client_ref: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    title: str = Field(min_length=1, max_length=200)
    description: Optional[LongText] = None
    assignee: TicketAssignee = TicketAssignee.UNASSIGNED
    status: TicketStatus = TicketStatus.TODO
    source_refs: list[SourceReference] = Field(
        default_factory=list,
        max_length=MAX_REFERENCES,
    )
    depends_on: list[int] = Field(
        default_factory=list,
        max_length=MAX_DEPENDENCIES,
    )


class TicketUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[LongText] = None
    status: Optional[TicketStatus] = None
    assignee: Optional[TicketAssignee] = None
    mr_link: Optional[str] = Field(
        default=None,
        max_length=MAX_REFERENCE_LENGTH,
        pattern=r"^https?://",
    )
    blocked_by: Optional[BlockedByCategory] = None
    blocked_reason: Optional[CommentText] = None
    source_refs: Optional[list[SourceReference]] = Field(
        default=None,
        max_length=MAX_REFERENCES,
    )
    depends_on: Optional[list[int]] = Field(
        default=None,
        max_length=MAX_DEPENDENCIES,
    )


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    client_ref: str | None = None

    id: int
    subproject_id: int
    project_id: Optional[int] = None
    title: str
    description: Optional[str] = None
    status: TicketStatus
    assignee: TicketAssignee
    mr_link: Optional[str] = None
    blocked_by: Optional[BlockedByCategory] = None
    blocked_reason: Optional[str] = None
    source_refs: list[str] = Field(default_factory=list)
    depends_on: list[int] = Field(default_factory=list)
    depends_on_refs: list[TicketRef] = Field(default_factory=list)
    claimed_by: Optional[str] = None
    claimed_at: Optional[datetime] = None
    lease_expires_at: Optional[datetime] = None


class MRLinkPayload(BaseModel):
    url: str = Field(
        min_length=1,
        max_length=MAX_REFERENCE_LENGTH,
        pattern=r"^https?://",
    )


# ---- Comment --------------------------------------------------------------


class CommentCreate(BaseModel):
    author: ActorRole
    content: CommentText


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    actor_user_id: Optional[int] = None
    actor_name: Optional[str] = None
    id: int
    ticket_id: int
    author: ActorRole
    content: str
    timestamp: datetime


# ---- AuditLog (read-only exposure) ---------------------------------------


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    actor_user_id: Optional[int] = None
    actor_name: Optional[str] = None
    id: int
    ticket_id: int
    action: AuditAction
    actor: ActorRole
    timestamp: datetime


# ---- Compound reads ------------------------------------------------------


class SubprojectDetail(SubprojectRead):
    """Returned from ``GET /subprojects/{id}`` — includes ordered tickets."""

    tickets: list[TicketRead] = Field(default_factory=list)


class TicketDetail(TicketRead):
    """Returned from ``GET /tickets/{id}`` — includes threaded comments."""

    comments: list[CommentRead] = Field(default_factory=list)
    audit_logs: list[AuditLogRead] = Field(default_factory=list)


class ClaimPayload(BaseModel):
    worker_id: str = Field(min_length=1, max_length=200)


class HeartbeatPayload(BaseModel):
    worker_id: str = Field(min_length=1, max_length=200)
    extend_seconds: int = Field(default=600, ge=60, le=86400)


# ---- KnowledgeNode -------------------------------------------------------


class KnowledgeNodeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    node_type: KnowledgeNodeType = KnowledgeNodeType.RAW
    content: LongText = ""
    parent_id: Optional[int] = None
    source_refs: list[SourceReference] = Field(
        default_factory=list,
        max_length=MAX_REFERENCES,
    )


class KnowledgeNodeUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    node_type: Optional[KnowledgeNodeType] = None
    status: Optional[KnowledgeNodeStatus] = None
    superseded_by: Optional[int] = None
    content: Optional[LongText] = None
    parent_id: Optional[int] = None
    source_refs: Optional[list[SourceReference]] = Field(
        default=None,
        max_length=MAX_REFERENCES,
    )


class KnowledgeNodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    actor_user_id: Optional[int] = None
    actor_name: Optional[str] = None
    id: int
    project_id: int
    parent_id: Optional[int] = None
    title: str
    node_type: KnowledgeNodeType
    status: KnowledgeNodeStatus = KnowledgeNodeStatus.CURRENT
    superseded_by: Optional[int] = None
    content: str
    source_refs: list[str] = Field(default_factory=list)
    created_by: ActorRole
    created_at: datetime
    updated_at: datetime


# ---- Context trails ------------------------------------------------------


class ContextTrailSegment(BaseModel):
    """Compact node identity used inside a breadcrumb path or load order."""

    id: int
    title: str
    node_type: KnowledgeNodeType


class ContextTrailChildHint(ContextTrailSegment):
    """Nearby child node that may be worth drilling into next."""

    content_preview: str = ""
    source_refs: list[str] = Field(default_factory=list)


class ContextTrailItem(BaseModel):
    """One matched branch in a contextual knowledge search."""

    id: int
    title: str
    node_type: KnowledgeNodeType
    parent_id: Optional[int] = None
    path: list[ContextTrailSegment] = Field(default_factory=list)
    score: int
    matched_terms: list[str] = Field(default_factory=list)
    reason: str
    content_preview: str = ""
    source_refs: list[str] = Field(default_factory=list)
    child_count: int = 0
    children: list[ContextTrailChildHint] = Field(default_factory=list)


class ContextTrailRead(BaseModel):
    """Response for a task-intent search over the knowledge tree."""

    project_id: int
    project_name: str
    query: str
    load_order: list[ContextTrailSegment] = Field(default_factory=list)
    items: list[ContextTrailItem] = Field(default_factory=list)


# ---- KnowledgeProposal ---------------------------------------------------


class KnowledgeProposalCreate(BaseModel):
    proposed_changes: dict
    rationale: LongText = ""


class KnowledgeProposalReview(BaseModel):
    action: Literal["accept", "reject"]
    reviewed_by: str = Field(default="HUMAN", max_length=200)


class KnowledgeProposalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    actor_user_id: Optional[int] = None
    actor_name: Optional[str] = None
    id: int
    node_id: int
    proposed_by: str
    proposed_changes: dict
    rationale: str
    status: str
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime


# ---- AgentSession --------------------------------------------------------


class AgentSessionCreate(BaseModel):
    intent: LongText = ""
    loaded_node_ids: list[int] = Field(
        default_factory=list,
        max_length=MAX_SESSION_NODE_IDS,
    )


class AgentSessionUpdate(BaseModel):
    loaded_node_ids: Optional[list[int]] = Field(
        default=None,
        max_length=MAX_SESSION_NODE_IDS,
    )
    handoff_note: Optional[LongText] = None
    status: Optional[str] = Field(default=None, max_length=40)


class AgentSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    actor_user_id: Optional[int] = None
    actor_name: Optional[str] = None
    id: int
    project_id: int
    intent: str
    loaded_node_ids: list[int] = Field(default_factory=list)
    started_at: datetime
    ended_at: Optional[datetime] = None
    handoff_note: Optional[str] = None
    status: str


# ---- Knowledge tickets backlink --------------------------------------------
# (TicketRef is defined above, near the Ticket schemas.)
