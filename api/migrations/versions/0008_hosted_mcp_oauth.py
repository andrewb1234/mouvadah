"""Persistent, revocable OAuth grants for hosted MCP.

Additive only: existing accounts, API keys and project data are unchanged.
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_hosted_mcp_oauth"
down_revision = "0007_workspace_membership_admin"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mcpoauthclient",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("secret_hash", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "mcpoauthcode",
        sa.Column("code_hash", sa.String(), primary_key=True),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column(
            "api_key_id",
            sa.Integer(),
            sa.ForeignKey("apikey.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("challenge", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "mcpoauthtoken",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column(
            "api_key_id",
            sa.Integer(),
            sa.ForeignKey("apikey.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
    )


def downgrade():
    op.drop_table("mcpoauthtoken")
    op.drop_table("mcpoauthcode")
    op.drop_table("mcpoauthclient")
