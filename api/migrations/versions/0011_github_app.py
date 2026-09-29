"""Workspace-scoped GitHub App connections and durable delivery receipts."""
from alembic import op
import sqlalchemy as sa

revision = "0011_github_app"
down_revision = "0010_ticket_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("githubconnection",
        sa.Column("installation_id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_login", sa.String(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("allowed_repositories", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("last_error", sa.String()),
        sa.Column("connected_by", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("next_sync_at", sa.DateTime(), nullable=False),
        sa.Column("sync_cursor", sa.Integer(), nullable=False),
        sa.Column("link_cursor", sa.Integer(), nullable=False),
        sa.Column("cycle_started_at", sa.DateTime()))
    op.create_index("ix_githubconnection_workspace_id", "githubconnection", ["workspace_id"])
    op.create_index("ix_githubconnection_next_sync_at", "githubconnection", ["next_sync_at"])
    op.create_table("githubconnectstate",
        sa.Column("state_hash", sa.String(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("installation_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False))
    op.create_table("githubrepository",
        sa.Column("repository_id", sa.Integer(), primary_key=True),
        sa.Column("installation_id", sa.Integer(), sa.ForeignKey("githubconnection.installation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False))
    op.create_index("ix_githubrepository_installation_id", "githubrepository", ["installation_id"])
    op.create_index("ix_githubrepository_project_id", "githubrepository", ["project_id"])
    op.create_table("githubticketlink",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False),
        sa.Column("repository_id", sa.Integer(), sa.ForeignKey("githubrepository.repository_id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime()),
        sa.UniqueConstraint("ticket_id", "repository_id", "kind", "number", name="uq_github_ticket_object"))
    op.create_index("ix_githubticketlink_ticket_id", "githubticketlink", ["ticket_id"])
    op.create_index("ix_githubticketlink_repository_id", "githubticketlink", ["repository_id"])
    op.create_table("githubdelivery",
        sa.Column("delivery_id", sa.String(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspace.id", ondelete="CASCADE")),
        sa.Column("installation_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("payload_sha256", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()))
    for column in ["workspace_id", "installation_id", "received_at"]:
        op.create_index(f"ix_githubdelivery_{column}", "githubdelivery", [column])


def downgrade():
    for table in ["githubdelivery", "githubticketlink", "githubrepository", "githubconnectstate", "githubconnection"]:
        op.drop_table(table)
