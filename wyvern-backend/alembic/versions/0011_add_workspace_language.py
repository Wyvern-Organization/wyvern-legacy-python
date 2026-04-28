"""Add workspace language selector support

Revision ID: 0011_add_workspace_language
Revises: 0010_add_dm_hidden_state
Create Date: 2026-04-09 22:55:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_add_workspace_language"
down_revision = "0010_add_dm_hidden_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_documents",
        sa.Column("language", sa.String(length=32), nullable=False, server_default=sa.text("'plaintext'")),
    )


def downgrade() -> None:
    op.drop_column("workspace_documents", "language")
