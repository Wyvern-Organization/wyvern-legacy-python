"""Add channel read states

Revision ID: 0016_channel_read_states
Revises: 0015_ui_variant_votes
Create Date: 2026-05-24 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa

from app.services.ids import ID_COLUMN_LENGTH


revision = "0016_channel_read_states"
down_revision = "0015_ui_variant_votes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_read_states",
        sa.Column("id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("user_id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("channel_id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("last_read_message_id", sa.String(length=ID_COLUMN_LENGTH), nullable=True),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], name=op.f("fk_channel_read_states_channel_id_channels"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_read_message_id"], ["messages.id"], name=op.f("fk_channel_read_states_last_read_message_id_messages"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_channel_read_states_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_read_states")),
        sa.UniqueConstraint("user_id", "channel_id", name="uq_channel_read_states_user_channel"),
    )
    op.create_index(op.f("ix_channel_read_states_id"), "channel_read_states", ["id"], unique=False)
    op.create_index(op.f("ix_channel_read_states_user_id"), "channel_read_states", ["user_id"], unique=False)
    op.create_index(op.f("ix_channel_read_states_channel_id"), "channel_read_states", ["channel_id"], unique=False)


def downgrade() -> None:
    op.drop_table("channel_read_states")
