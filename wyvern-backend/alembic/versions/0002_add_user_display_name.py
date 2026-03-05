"""Add user display name

Revision ID: 0002_add_user_display_name
Revises: 0001_initial
Create Date: 2026-03-04 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_add_user_display_name"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "display_name")
