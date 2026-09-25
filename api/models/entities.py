"""SQLModel persistent entities for Mouvadah.

Schema aligned to ``docs/db_schema.md``. Relationships are defined so
``GET /subprojects/{id}`` can return nested tickets and ``GET /tickets/{id}``
can return threaded comments in one round trip.

We deliberately do NOT use ``from __future__ import annotations`` here:
SQLAlchemy's relationship mapper introspects the type annotation at class
construction time and expects concrete generics (e.g. ``list["Subproject"]``)
rather than stringified PEP 563 forms.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, text
from sqlmodel import Field, Relationship, SQLModel

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
    WorkspaceMembershipAction,
    WorkspaceRole,
)
from api.utils.time import utcnow


class AgentSession(SQLModel, table=True):
    """Records an agent work session for handoff and audit purposes."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    intent: str = Field(default="")
    loaded_node_ids: List[int] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False, default=list)
    )
    started_at: datetime = Field(default_factory=utcnow, nullable=False)
    ended_at: Optional[datetime] = Field(default=None)
    handoff_note: Optional[str] = Field(default=None)
    status: str = Field(default="ACTIVE")

    project: Optional["Project"] = Relationship(back_populates="sessions")


class Workspace(SQLModel, table=True):
    """Tenant boundary that owns projects and memberships."""

    __table_args__ = (
        CheckConstraint(
            "("
            "deletion_requested_at IS NULL "
            "AND purge_after IS NULL "
            "AND deletion_requested_by IS NULL "
            "AND deletion_export_sha256 IS NULL"
            ") OR ("
            "deletion_requested_at IS NOT NULL "
            "AND purge_after IS NOT NULL "
            "AND deletion_requested_by IS NOT NULL "
            "AND deletion_export_sha256 IS NOT NULL"
            ")",
            name="ck_workspace_deletion_state_complete",
        ),
        CheckConstraint(
            "purge_after IS NULL OR purge_after > deletion_requested_at",
            name="ck_workspace_purge_after_request",
        ),
        CheckConstraint(
            "deletion_export_sha256 IS NULL "
            "OR length(deletion_export_sha256) = 64",
            name="ck_workspace_deletion_export_sha256",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    slug: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    deletion_requested_at: Optional[datetime] = Field(default=None, index=True)
    purge_after: Optional[datetime] = Field(default=None, index=True)
    deletion_requested_by: Optional[int] = Field(default=None)
    deletion_export_sha256: Optional[str] = Field(default=None)

    projects: List["Project"] = Relationship(back_populates="workspace")
    memberships: List["WorkspaceMembership"] = Relationship(
        back_populates="workspace",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    invitations: List["WorkspaceInvitation"] = Relationship(
        back_populates="workspace",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class WorkspaceLifecycleEvent(SQLModel, table=True):
    """Durable non-content ledger retained after a workspace purge.

    ``workspace_id`` and ``actor_user_id`` intentionally are not foreign keys:
    a verified purge removes the tenant and may later remove the account, while
    operators still need evidence of when export, deletion, restore, and purge
    actions happened. ``details`` must contain identifiers, hashes, counts, and
    timestamps only—never exported tenant content or credentials.
    """

    __table_args__ = (
        CheckConstraint(
            "action IN ("
            "'EXPORTED', "
            "'DELETION_SCHEDULED', "
            "'DELETION_RESTORED', "
            "'PURGED'"
            ")",
            name="ck_workspace_lifecycle_action",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    action: WorkspaceLifecycleAction = Field(
        sa_column=Column(String, nullable=False, index=True)
    )
    actor_user_id: Optional[int] = Field(default=None, index=True)
    occurred_at: datetime = Field(default_factory=utcnow, nullable=False)
    details: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict),
    )


class WorkspaceMembership(SQLModel, table=True):
    """A user's role inside a workspace."""

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
        Index(
            "uq_workspacemembership_single_owner",
            "workspace_id",
            unique=True,
            postgresql_where=text("role = 'OWNER'"),
            sqlite_where=text("role = 'OWNER'"),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    role: WorkspaceRole = Field(
        default=WorkspaceRole.MEMBER,
        sa_column=Column(String, nullable=False, default="MEMBER"),
    )
    created_at: datetime = Field(default_factory=utcnow, nullable=False)

    workspace: Optional[Workspace] = Relationship(back_populates="memberships")
    user: Optional["User"] = Relationship(back_populates="memberships")


class WorkspaceInvitation(SQLModel, table=True):
    """Hashed, expiring, email-bound invitation to a workspace."""

    __table_args__ = (
        CheckConstraint(
            "length(token_hash) = 64",
            name="ck_workspace_invitation_token_hash",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_workspace_invitation_expiry",
        ),
        CheckConstraint(
            "role IN ('ADMIN', 'MEMBER', 'VIEWER')",
            name="ck_workspace_invitation_role",
        ),
        CheckConstraint(
            "NOT (accepted_at IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_workspace_invitation_terminal_state",
        ),
        CheckConstraint(
            "(accepted_at IS NULL AND accepted_by_user_id IS NULL) "
            "OR (accepted_at IS NOT NULL AND accepted_by_user_id IS NOT NULL)",
            name="ck_workspace_invitation_acceptance_complete",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    email: str = Field(index=True)
    role: WorkspaceRole = Field(
        default=WorkspaceRole.MEMBER,
        sa_column=Column(String, nullable=False, default="MEMBER"),
    )
    token_hash: str = Field(unique=True, index=True)
    created_by_user_id: int = Field(foreign_key="user.id", index=True)
    expires_at: datetime = Field(index=True)
    accepted_at: Optional[datetime] = Field(default=None, index=True)
    accepted_by_user_id: Optional[int] = Field(
        default=None,
        foreign_key="user.id",
        index=True,
    )
    revoked_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)

    workspace: Optional[Workspace] = Relationship(back_populates="invitations")


class WorkspaceMembershipEvent(SQLModel, table=True):
    """Immutable, content-free ledger of workspace access changes."""

    __table_args__ = (
        CheckConstraint(
            "action IN ("
            "'INVITATION_CREATED', "
            "'INVITATION_REVOKED', "
            "'INVITATION_ACCEPTED', "
            "'ROLE_CHANGED', "
            "'MEMBER_REMOVED', "
            "'OWNERSHIP_TRANSFERRED'"
            ")",
            name="ck_workspace_membership_action",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    action: WorkspaceMembershipAction = Field(
        sa_column=Column(String, nullable=False, index=True)
    )
    actor_user_id: Optional[int] = Field(default=None, index=True)
    subject_user_id: Optional[int] = Field(default=None, index=True)
    invitation_id: Optional[int] = Field(default=None, index=True)
    occurred_at: datetime = Field(default_factory=utcnow, nullable=False)
    details: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict),
    )


class Project(SQLModel, table=True):
    """Top-level container grouping a set of subprojects."""

    id: Optional[int] = Field(default=None, primary_key=True)
    # Nullable only while a legacy installation awaits an explicit safe
    # ownership backfill. All newly created projects set this field.
    workspace_id: Optional[int] = Field(
        default=None, foreign_key="workspace.id", index=True
    )
    name: str = Field(index=True)
    description: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)

    subprojects: List["Subproject"] = Relationship(
        back_populates="project",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    knowledge_nodes: List["KnowledgeNode"] = Relationship(
        back_populates="project",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    sessions: List["AgentSession"] = Relationship(
        back_populates="project",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    workspace: Optional[Workspace] = Relationship(back_populates="projects")


class Subproject(SQLModel, table=True):
    """Sprint-style context carrying a goal brief and ordered tickets."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str
    context_brief: str = Field(default="")
    status: SubprojectStatus = Field(default=SubprojectStatus.PLANNING)

    project: Optional[Project] = Relationship(back_populates="subprojects")
    tickets: List["Ticket"] = Relationship(
        back_populates="subproject",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Ticket(SQLModel, table=True):
    """Actionable unit tracked on the Kanban board."""

    id: Optional[int] = Field(default=None, primary_key=True)
    subproject_id: int = Field(foreign_key="subproject.id", index=True)
    title: str
    description: Optional[str] = Field(default=None)
    status: TicketStatus = Field(default=TicketStatus.TODO)
    assignee: TicketAssignee = Field(default=TicketAssignee.UNASSIGNED)
    mr_link: Optional[str] = Field(default=None)
    blocked_by: Optional[BlockedByCategory] = Field(
        default=None, sa_column=Column(String, nullable=True)
    )
    blocked_reason: Optional[str] = Field(default=None)
    source_refs: List[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False, default=list)
    )
    claimed_by: Optional[str] = Field(default=None, index=True)
    claimed_at: Optional[datetime] = Field(default=None)
    lease_expires_at: Optional[datetime] = Field(default=None)

    subproject: Optional[Subproject] = Relationship(back_populates="tickets")
    comments: List["Comment"] = Relationship(
        back_populates="ticket",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    audit_logs: List["AuditLog"] = Relationship(
        back_populates="ticket",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class TicketDependency(SQLModel, table=True):
    """Many-to-many edge: ticket_id depends_on depends_on_ticket_id.

    Kept separate from ``Ticket.blocked_by`` (a reason enum) — edges and
    reasons are different concepts. A ticket with unmet dependencies is
    "not ready" but may still be in TODO status.
    """

    ticket_id: int = Field(foreign_key="ticket.id", primary_key=True, index=True)
    depends_on_ticket_id: int = Field(foreign_key="ticket.id", primary_key=True, index=True)


class Comment(SQLModel, table=True):
    """Threaded discussion attached to a ticket."""

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: int = Field(foreign_key="ticket.id", index=True)
    author: ActorRole
    content: str
    timestamp: datetime = Field(default_factory=utcnow, nullable=False)

    ticket: Optional[Ticket] = Relationship(back_populates="comments")


class AuditLog(SQLModel, table=True):
    """Immutable ledger of ticket state changes.

    Exactly mirrors ``docs/db_schema.md`` — no diff payload — so audits stay
    cheap to write. If we need before/after values later, extend here.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: int = Field(foreign_key="ticket.id", index=True)
    action: AuditAction
    actor: ActorRole
    timestamp: datetime = Field(default_factory=utcnow, nullable=False)

    ticket: Optional[Ticket] = Relationship(back_populates="audit_logs")


class KnowledgeNode(SQLModel, table=True):
    """Self-referential node in the per-project knowledge tree.

    Upstream of subprojects and tickets: this is where agents persist raw
    research material, compressed summaries, and drafted PRD/TDD artifacts
    for a project. A tree (single ``parent_id``) is sufficient for v1 —
    relaxing to a DAG would require a separate edges table.

    ``source_refs`` stores arbitrary string pointers (absolute file paths,
    URLs, or ``node:<id>`` breadcrumbs) so a human can trace a summary back
    to its origin without the agent having to re-read the raw content.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    parent_id: Optional[int] = Field(
        default=None, foreign_key="knowledgenode.id", index=True
    )

    title: str
    node_type: KnowledgeNodeType = Field(default=KnowledgeNodeType.RAW)
    status: KnowledgeNodeStatus = Field(
        default=KnowledgeNodeStatus.CURRENT, sa_column=Column(String, nullable=False, default="CURRENT")
    )
    superseded_by: Optional[int] = Field(
        default=None, foreign_key="knowledgenode.id", index=True
    )
    content: str = Field(default="")
    source_refs: List[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False, default=list)
    )
    created_by: ActorRole = Field(default=ActorRole.AGENT)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)

    project: Optional[Project] = Relationship(back_populates="knowledge_nodes")
    parent: Optional["KnowledgeNode"] = Relationship(
        back_populates="children",
        sa_relationship_kwargs={"remote_side": "KnowledgeNode.id", "foreign_keys": "[KnowledgeNode.parent_id]"},
    )
    children: List["KnowledgeNode"] = Relationship(
        back_populates="parent",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "foreign_keys": "[KnowledgeNode.parent_id]"},
    )
    proposals: List["KnowledgeProposal"] = Relationship(
        back_populates="node",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class User(SQLModel, table=True):
    """Registered user authenticated via Google OAuth or loopback local setup.

    ``google_id`` is the historical identity-subject column name. Local users
    receive a collision-resistant ``local:<uuid>`` subject and can later link
    the same verified email through Google OAuth.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    google_id: str = Field(unique=True, index=True)
    email: str = Field(unique=True, index=True)
    name: str
    avatar_url: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)

    memberships: List[WorkspaceMembership] = Relationship(back_populates="user")


class BrowserSession(SQLModel, table=True):
    """Revocable server-side record backing a signed browser-session cookie."""

    id: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    expires_at: datetime = Field(index=True)
    revoked_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    last_seen_at: datetime = Field(default_factory=utcnow, nullable=False)

    user: Optional[User] = Relationship()


class ApiKey(SQLModel, table=True):
    """Workspace-bound, scoped API key for agent/MCP authentication.

    The full key is shown once on creation and never stored. We persist only:
    - ``key_prefix`` (first 12 chars) for display/identification
    - ``key_hash`` (SHA-256 of the full key) for lookup/verification

    ``workspace_id`` is nullable only for migrated ambiguous legacy keys, which
    authentication rejects. New keys always bind to one workspace. An empty
    ``ApiKeyProject`` set means every project in that workspace is allowed.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    workspace_id: Optional[int] = Field(
        default=None,
        foreign_key="workspace.id",
        index=True,
    )
    name: str = Field(default="Default")
    key_prefix: str = Field(index=True)
    key_hash: str = Field(unique=True, index=True)
    scopes: List[str] = Field(
        default_factory=lambda: ["read", "write"],
        sa_column=Column(JSON, nullable=False, default=lambda: ["read", "write"]),
    )
    expires_at: Optional[datetime] = Field(default=None)
    last_used_at: Optional[datetime] = Field(default=None)
    revoked: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)

    user: Optional[User] = Relationship()
    workspace: Optional[Workspace] = Relationship()


class ApiKeyProject(SQLModel, table=True):
    """Optional per-project allow-list for a workspace-bound API key."""

    api_key_id: int = Field(
        foreign_key="apikey.id",
        primary_key=True,
        index=True,
    )
    project_id: int = Field(
        foreign_key="project.id",
        primary_key=True,
        index=True,
    )


class KnowledgeProposal(SQLModel, table=True):
    """Agent-submitted proposed change to a knowledge node, pending human review."""

    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: int = Field(foreign_key="knowledgenode.id", index=True)
    proposed_by: str = Field(default="AGENT")
    proposed_changes: dict = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False, default=dict)
    )
    rationale: str = Field(default="")
    status: str = Field(default="PENDING")
    reviewed_by: Optional[str] = Field(default=None)
    reviewed_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)

    node: Optional[KnowledgeNode] = Relationship(back_populates="proposals")


class McpOAuthClient(SQLModel, table=True):
    """Registered OAuth client metadata; client secrets are hashed."""
    id: str = Field(primary_key=True)
    secret_hash: Optional[str] = None
    metadata_json: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, nullable=False)


class McpOAuthCode(SQLModel, table=True):
    """Short-lived, single-use PKCE authorization code."""
    code_hash: str = Field(primary_key=True)
    client_id: str
    api_key_id: int = Field(sa_column=Column(Integer, ForeignKey("apikey.id", ondelete="CASCADE"), nullable=False))
    redirect_uri: str
    challenge: str
    resource: str
    expires_at: datetime
    used: bool = False


class McpOAuthToken(SQLModel, table=True):
    """Hashed, audience-bound access/refresh tokens sharing a revocable grant."""
    token_hash: str = Field(primary_key=True)
    client_id: str
    api_key_id: int = Field(sa_column=Column(Integer, ForeignKey("apikey.id", ondelete="CASCADE"), nullable=False))
    kind: str
    resource: str
    scopes: List[str] = Field(sa_column=Column(JSON, nullable=False))
    expires_at: datetime
    used: bool = False
