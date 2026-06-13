"""Add OAuth client registrations

Revision ID: 0018_oauth_clients
Revises: 0017_api_tokens
Create Date: 2026-06-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_oauth_clients"
down_revision = "0017_api_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_client_registrations",
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("client_id", name=op.f("pk_oauth_client_registrations")),
    )


def downgrade() -> None:
    op.drop_table("oauth_client_registrations")
