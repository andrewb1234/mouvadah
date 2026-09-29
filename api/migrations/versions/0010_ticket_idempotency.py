"""Optional, subproject-scoped ticket creation idempotency."""

from alembic import op
import sqlalchemy as sa

revision = "0010_ticket_idempotency"
down_revision = "0009_project_collaboration"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ticket", sa.Column("client_ref", sa.String(128), nullable=True))
    op.add_column("ticket", sa.Column("creation_fingerprint", sa.String(64), nullable=True))
    op.create_index("uq_ticket_subproject_client_ref", "ticket", ["subproject_id", "client_ref"], unique=True)


def downgrade():
    op.drop_index("uq_ticket_subproject_client_ref", table_name="ticket")
    op.drop_column("ticket", "creation_fingerprint")
    op.drop_column("ticket", "client_ref")
