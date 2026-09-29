"""String enumerations shared across models, routes, and MCP tools."""

from __future__ import annotations

from enum import Enum


class SubprojectStatus(str, Enum):
    PLANNING = "PLANNING"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"


class WorkspaceRole(str, Enum):
    """Membership role inside a workspace security boundary."""

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    VIEWER = "VIEWER"
    SERVICE = "SERVICE"


class WorkspaceLifecycleAction(str, Enum):
    """Auditable workspace export and deletion lifecycle actions."""

    EXPORTED = "EXPORTED"
    DELETION_SCHEDULED = "DELETION_SCHEDULED"
    DELETION_RESTORED = "DELETION_RESTORED"
    PURGED = "PURGED"


class WorkspaceMembershipAction(str, Enum):
    """Auditable workspace access-administration actions."""

    INVITATION_CREATED = "INVITATION_CREATED"
    INVITATION_REVOKED = "INVITATION_REVOKED"
    INVITATION_ACCEPTED = "INVITATION_ACCEPTED"
    ROLE_CHANGED = "ROLE_CHANGED"
    MEMBER_REMOVED = "MEMBER_REMOVED"
    OWNERSHIP_TRANSFERRED = "OWNERSHIP_TRANSFERRED"


class TicketStatus(str, Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    REVIEW = "REVIEW"
    DONE = "DONE"


class TicketAssignee(str, Enum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    UNASSIGNED = "UNASSIGNED"


class ActorRole(str, Enum):
    """Used for Comment.author and AuditLog.actor (no UNASSIGNED here)."""

    HUMAN = "HUMAN"
    AGENT = "AGENT"


class AuditAction(str, Enum):
    STATUS_UPDATE = "STATUS_UPDATE"
    CONTENT_UPDATE = "CONTENT_UPDATE"
    MR_LINKED = "MR_LINKED"
    TICKET_CLAIMED = "TICKET_CLAIMED"
    TICKET_REQUEUED = "TICKET_REQUEUED"


class KnowledgeNodeType(str, Enum):
    """Category tag for a ``KnowledgeNode``.

    * ``RAW``      — raw pastable source (file excerpt, doc passage, URL dump).
    * ``SUMMARY``  — an agent-authored abstraction over one or more children.
    * ``PRD``      — a Product Requirements Document synthesized from summaries.
    * ``TDD``      — a Technical Design Document synthesized from summaries.
    """

    RAW = "RAW"
    SUMMARY = "SUMMARY"
    PRD = "PRD"
    TDD = "TDD"


class BlockedByCategory(str, Enum):
    """Structured reason a ticket is blocked."""

    WAITING_HUMAN = "WAITING_HUMAN"
    WAITING_DEPENDENCY = "WAITING_DEPENDENCY"
    AMBIGUOUS_REQUIREMENT = "AMBIGUOUS_REQUIREMENT"
    EXTERNAL = "EXTERNAL"


class KnowledgeNodeStatus(str, Enum):
    """Lifecycle status of a knowledge node."""

    CURRENT = "CURRENT"
    STALE = "STALE"
    ARCHIVED = "ARCHIVED"


class SSEAction(str, Enum):
    PROJECT_ACCESS_CHANGED = "PROJECT_ACCESS_CHANGED"
    """Vocabulary of events broadcast over ``GET /api/v1/events``."""

    SYNC_REQUIRED = "SYNC_REQUIRED"
    PROJECT_CREATED = "PROJECT_CREATED"
    PROJECT_DELETED = "PROJECT_DELETED"
    SUBPROJECT_CREATED = "SUBPROJECT_CREATED"
    SUBPROJECT_UPDATED = "SUBPROJECT_UPDATED"
    SUBPROJECT_DELETED = "SUBPROJECT_DELETED"
    TICKET_CREATED = "TICKET_CREATED"
    TICKET_UPDATED = "TICKET_UPDATED"
    TICKET_DELETED = "TICKET_DELETED"
    COMMENT_CREATED = "COMMENT_CREATED"
    MR_LINKED = "MR_LINKED"
    KNOWLEDGE_NODE_CREATED = "KNOWLEDGE_NODE_CREATED"
    KNOWLEDGE_NODE_UPDATED = "KNOWLEDGE_NODE_UPDATED"
    KNOWLEDGE_NODE_DELETED = "KNOWLEDGE_NODE_DELETED"
    KNOWLEDGE_PROPOSAL_CREATED = "KNOWLEDGE_PROPOSAL_CREATED"
    KNOWLEDGE_PROPOSAL_REVIEWED = "KNOWLEDGE_PROPOSAL_REVIEWED"
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_ENDED = "SESSION_ENDED"
    TICKET_CLAIMED = "TICKET_CLAIMED"
    TICKET_REQUEUED = "TICKET_REQUEUED"
