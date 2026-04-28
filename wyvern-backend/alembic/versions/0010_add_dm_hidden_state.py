"""Add per-user DM hidden state

Revision ID: 0010_add_dm_hidden_state
Revises: 0009_add_workspace_visibility
Create Date: 2026-04-09 22:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_add_dm_hidden_state"
down_revision = "0009_add_workspace_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dm_hidden_states",
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("channel_id", "user_id"),
    )
    op.create_index(op.f("ix_dm_hidden_states_channel_id"), "dm_hidden_states", ["channel_id"], unique=False)
    op.create_index(op.f("ix_dm_hidden_states_user_id"), "dm_hidden_states", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_dm_hidden_states_user_id"), table_name="dm_hidden_states")
    op.drop_index(op.f("ix_dm_hidden_states_channel_id"), table_name="dm_hidden_states")
    op.drop_table("dm_hidden_states")
