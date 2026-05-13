"""Add message replies

Revision ID: 0005_add_message_replies
Revises: 0004_directory_profile
Create Date: 2026-04-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_add_message_replies"
down_revision = "0004_directory_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("reply_to_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_messages_reply_to_id"), "messages", ["reply_to_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_messages_reply_to_id_messages"),
        "messages",
        "messages",
        ["reply_to_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_messages_reply_to_id_messages"), "messages", type_="foreignkey")
    op.drop_index(op.f("ix_messages_reply_to_id"), table_name="messages")
    op.drop_column("messages", "reply_to_id")
