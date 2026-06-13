"""Add API tokens

Revision ID: 0017_api_tokens
Revises: 0016_channel_read_states
Create Date: 2026-05-28 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

from app.services.ids import ID_COLUMN_LENGTH


revision = "0017_api_tokens"
down_revision = "0016_channel_read_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("user_id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("token_ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_api_tokens_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_tokens")),
        sa.UniqueConstraint("token_hash", name="uq_api_tokens_token_hash"),
    )
    op.create_index(op.f("ix_api_tokens_id"), "api_tokens", ["id"], unique=False)
    op.create_index(op.f("ix_api_tokens_user_id"), "api_tokens", ["user_id"], unique=False)
    op.create_index(op.f("ix_api_tokens_token_hash"), "api_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("api_tokens")
