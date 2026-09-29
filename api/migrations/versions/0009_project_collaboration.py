"""Direct project access, explicit credential boundaries, and attribution."""

from alembic import op
import sqlalchemy as sa

revision = "0009_project_collaboration"
down_revision = "0008_hosted_mcp_oauth"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "projectmembership",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column(
            "created_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_member"),
        sa.CheckConstraint(
            "role IN ('VIEWER', 'EDITOR')", name="ck_project_member_role"
        ),
    )
    op.create_table(
        "projectinvitation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column(
            "created_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column(
            "accepted_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True
        ),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "role IN ('VIEWER', 'EDITOR')", name="ck_project_invitation_role"
        ),
        sa.CheckConstraint(
            "length(token_hash) = 64", name="ck_project_invitation_hash"
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_project_invitation_expiry"
        ),
        sa.CheckConstraint(
            "NOT (accepted_at IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_project_invitation_terminal",
        ),
        sa.CheckConstraint(
            "(accepted_at IS NULL AND accepted_by_user_id IS NULL) OR (accepted_at IS NOT NULL AND accepted_by_user_id IS NOT NULL)",
            name="ck_project_invitation_acceptance",
        ),
    )
    op.create_table(
        "projectaccessevent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("subject_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
    )
    for table, columns in {
        "projectmembership": ["project_id", "user_id"],
        "projectinvitation": ["project_id", "email", "token_hash"],
        "projectaccessevent": ["project_id", "workspace_id"],
    }.items():
        for column in columns:
            op.create_index(
                f"ix_{table}_{column}", table, [column], unique=column == "token_hash"
            )
    op.add_column(
        "apikey",
        sa.Column(
            "resource_mode", sa.String(), nullable=False, server_default="WORKSPACE"
        ),
    )
    op.execute(
        "UPDATE apikey SET resource_mode = 'PROJECTS' WHERE id IN (SELECT api_key_id FROM apikeyproject)"
    )
    for table in (
        "agentsession",
        "comment",
        "auditlog",
        "knowledgenode",
        "knowledgeproposal",
    ):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("actor_user_id", sa.Integer(), nullable=True))
            batch.add_column(sa.Column("actor_name", sa.String(), nullable=True))
            batch.create_foreign_key(
                f"fk_{table}_actor_user", "user", ["actor_user_id"], ["id"]
            )


def downgrade():
    # Old application versions cannot authenticate direct project guests. Revoke
    # PROJECTS grants before removing their explicit scope discriminator.
    op.execute("UPDATE apikey SET revoked = true WHERE resource_mode = 'PROJECTS'")
    for table in (
        "agentsession",
        "comment",
        "auditlog",
        "knowledgenode",
        "knowledgeproposal",
    ):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_actor_user", type_="foreignkey")
            batch.drop_column("actor_user_id")
            batch.drop_column("actor_name")
    op.drop_column("apikey", "resource_mode")
    op.drop_table("projectinvitation")
    op.drop_table("projectmembership")
    op.drop_table("projectaccessevent")
