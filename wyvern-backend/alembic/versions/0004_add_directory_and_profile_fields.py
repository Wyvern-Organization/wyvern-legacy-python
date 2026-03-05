"""Add directory and profile fields

Revision ID: 0004_directory_profile
Revises: 0003_add_server_invites
Create Date: 2026-03-04 07:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_directory_profile"
down_revision = "0003_add_server_invites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("bio", sa.String(length=280), nullable=True))
    op.add_column(
        "users",
        sa.Column("directory_opt_in", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.add_column("servers", sa.Column("description", sa.String(length=512), nullable=True))
    op.add_column(
        "servers",
        sa.Column("directory_opt_in", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("servers", "directory_opt_in")
    op.drop_column("servers", "description")
    op.drop_column("users", "directory_opt_in")
    op.drop_column("users", "bio")
