"""Add public recommendation caches

Revision ID: 0013_recommendations
Revises: 0012_partitioned_ids
Create Date: 2026-05-07 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.services.ids import ID_COLUMN_LENGTH


revision = "0013_recommendations"
down_revision = "0012_partitioned_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendation_embeddings",
        sa.Column("id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendation_embeddings")),
        sa.UniqueConstraint("entity_type", "entity_id", "embedding_model", name="uq_recommendation_embeddings_entity_model"),
    )
    op.create_index(op.f("ix_recommendation_embeddings_id"), "recommendation_embeddings", ["id"], unique=False)
    op.create_index(op.f("ix_recommendation_embeddings_entity_type"), "recommendation_embeddings", ["entity_type"], unique=False)
    op.create_index(op.f("ix_recommendation_embeddings_entity_id"), "recommendation_embeddings", ["entity_id"], unique=False)
    op.create_index(op.f("ix_recommendation_embeddings_embedding_model"), "recommendation_embeddings", ["embedding_model"], unique=False)

    op.create_table(
        "user_recommendations",
        sa.Column("id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("user_id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reason", sa.String(length=80), nullable=True),
        sa.Column("factors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_recommendations_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_recommendations")),
        sa.UniqueConstraint("user_id", "target_type", "target_id", name="uq_user_recommendations_user_target"),
    )
    op.create_index(op.f("ix_user_recommendations_id"), "user_recommendations", ["id"], unique=False)
    op.create_index(op.f("ix_user_recommendations_user_id"), "user_recommendations", ["user_id"], unique=False)
    op.create_index(op.f("ix_user_recommendations_target_type"), "user_recommendations", ["target_type"], unique=False)
    op.create_index(op.f("ix_user_recommendations_target_id"), "user_recommendations", ["target_id"], unique=False)
    op.create_index(op.f("ix_user_recommendations_refreshed_at"), "user_recommendations", ["refreshed_at"], unique=False)

    op.create_table(
        "recommendation_signals",
        sa.Column("id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("user_id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_recommendation_signals_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendation_signals")),
        sa.UniqueConstraint("user_id", "target_type", "target_id", "action", name="uq_recommendation_signals_user_target_action"),
    )
    op.create_index(op.f("ix_recommendation_signals_id"), "recommendation_signals", ["id"], unique=False)
    op.create_index(op.f("ix_recommendation_signals_user_id"), "recommendation_signals", ["user_id"], unique=False)
    op.create_index(op.f("ix_recommendation_signals_target_type"), "recommendation_signals", ["target_type"], unique=False)
    op.create_index(op.f("ix_recommendation_signals_target_id"), "recommendation_signals", ["target_id"], unique=False)
    op.create_index(op.f("ix_recommendation_signals_action"), "recommendation_signals", ["action"], unique=False)
    op.create_index(op.f("ix_recommendation_signals_last_seen_at"), "recommendation_signals", ["last_seen_at"], unique=False)


def downgrade() -> None:
    op.drop_table("recommendation_signals")
    op.drop_table("user_recommendations")
    op.drop_table("recommendation_embeddings")
