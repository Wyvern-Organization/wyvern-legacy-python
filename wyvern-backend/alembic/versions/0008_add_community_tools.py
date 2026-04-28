"""Add community tools

Revision ID: 0008_add_community_tools
Revises: 0007_add_release_flags
Create Date: 2026-04-08 12:00:00.000000
"""

from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0008_add_community_tools"
down_revision = "0007_add_release_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("messages", sa.Column("webhook_name", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("webhook_avatar", sa.Text(), nullable=True))

    op.create_table(
        "message_bookmarks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sync_id", sa.String(length=36), nullable=False),
        sa.Column("sync_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_bookmarks")),
        sa.UniqueConstraint("user_id", "message_id", name=op.f("uq_message_bookmarks_user_message")),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_message_bookmarks_sync_id"), "message_bookmarks", ["sync_id"], unique=True)
    op.create_index(op.f("ix_message_bookmarks_user_id"), "message_bookmarks", ["user_id"], unique=False)
    op.create_index(op.f("ix_message_bookmarks_message_id"), "message_bookmarks", ["message_id"], unique=False)
    op.create_index(op.f("ix_message_bookmarks_created_at"), "message_bookmarks", ["created_at"], unique=False)

    op.create_table(
        "server_webhooks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sync_id", sa.String(length=36), nullable=False),
        sa.Column("sync_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_server_webhooks")),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name=op.f("uq_server_webhooks_token_hash")),
    )
    op.create_index(op.f("ix_server_webhooks_sync_id"), "server_webhooks", ["sync_id"], unique=True)
    op.create_index(op.f("ix_server_webhooks_server_id"), "server_webhooks", ["server_id"], unique=False)
    op.create_index(op.f("ix_server_webhooks_channel_id"), "server_webhooks", ["channel_id"], unique=False)
    op.create_index(op.f("ix_server_webhooks_created_by"), "server_webhooks", ["created_by"], unique=False)
    op.create_index(op.f("ix_server_webhooks_token_hash"), "server_webhooks", ["token_hash"], unique=True)

    op.create_table(
        "webhook_delivery_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("webhook_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_delivery_logs")),
        sa.ForeignKeyConstraint(["webhook_id"], ["server_webhooks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("request_id", name=op.f("uq_webhook_delivery_logs_request_id")),
    )
    op.create_index(op.f("ix_webhook_delivery_logs_webhook_id"), "webhook_delivery_logs", ["webhook_id"], unique=False)
    op.create_index(op.f("ix_webhook_delivery_logs_request_id"), "webhook_delivery_logs", ["request_id"], unique=True)
    op.create_index(op.f("ix_webhook_delivery_logs_created_at"), "webhook_delivery_logs", ["created_at"], unique=False)

    op.create_table(
        "workspace_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sync_id", sa.String(length=36), nullable=False),
        sa.Column("sync_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default=sa.text("'writing'")),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_documents")),
        sa.UniqueConstraint("channel_id", name=op.f("uq_workspace_documents_channel_id")),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_workspace_documents_sync_id"), "workspace_documents", ["sync_id"], unique=True)
    op.create_index(op.f("ix_workspace_documents_channel_id"), "workspace_documents", ["channel_id"], unique=True)
    op.create_index(op.f("ix_workspace_documents_updated_by_user_id"), "workspace_documents", ["updated_by_user_id"], unique=False)

    op.create_table(
        "workspace_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("editor_user_id", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_revisions")),
        sa.ForeignKeyConstraint(["document_id"], ["workspace_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["editor_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_workspace_revisions_document_id"), "workspace_revisions", ["document_id"], unique=False)
    op.create_index(op.f("ix_workspace_revisions_editor_user_id"), "workspace_revisions", ["editor_user_id"], unique=False)
    op.create_index(op.f("ix_workspace_revisions_created_at"), "workspace_revisions", ["created_at"], unique=False)

    op.create_table(
        "server_activity_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_server_activity_logs")),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_server_activity_logs_server_id"), "server_activity_logs", ["server_id"], unique=False)
    op.create_index(op.f("ix_server_activity_logs_actor_user_id"), "server_activity_logs", ["actor_user_id"], unique=False)
    op.create_index(op.f("ix_server_activity_logs_action"), "server_activity_logs", ["action"], unique=False)
    op.create_index(op.f("ix_server_activity_logs_created_at"), "server_activity_logs", ["created_at"], unique=False)

    release_flags = sa.table(
        "release_flags",
        sa.column("key", sa.String(length=64)),
        sa.column("sync_id", sa.String(length=36)),
        sa.column("sync_version", sa.Integer()),
        sa.column("description", sa.Text()),
        sa.column("stable_enabled", sa.Boolean()),
        sa.column("edge_enabled", sa.Boolean()),
        sa.column("updated_by_user_id", sa.Integer()),
        sa.column("last_promoted_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        release_flags,
        [
            {
                "key": "community_tools",
                "sync_id": str(uuid4()),
                "sync_version": 1,
                "description": "Enable search, pins, bookmarks, webhooks, and collaborative workspaces.",
                "stable_enabled": False,
                "edge_enabled": True,
                "updated_by_user_id": None,
                "last_promoted_at": None,
            }
        ],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM release_flags WHERE key = 'community_tools'"))

    op.drop_index(op.f("ix_server_activity_logs_created_at"), table_name="server_activity_logs")
    op.drop_index(op.f("ix_server_activity_logs_action"), table_name="server_activity_logs")
    op.drop_index(op.f("ix_server_activity_logs_actor_user_id"), table_name="server_activity_logs")
    op.drop_index(op.f("ix_server_activity_logs_server_id"), table_name="server_activity_logs")
    op.drop_table("server_activity_logs")

    op.drop_index(op.f("ix_workspace_revisions_created_at"), table_name="workspace_revisions")
    op.drop_index(op.f("ix_workspace_revisions_editor_user_id"), table_name="workspace_revisions")
    op.drop_index(op.f("ix_workspace_revisions_document_id"), table_name="workspace_revisions")
    op.drop_table("workspace_revisions")

    op.drop_index(op.f("ix_workspace_documents_updated_by_user_id"), table_name="workspace_documents")
    op.drop_index(op.f("ix_workspace_documents_channel_id"), table_name="workspace_documents")
    op.drop_index(op.f("ix_workspace_documents_sync_id"), table_name="workspace_documents")
    op.drop_table("workspace_documents")

    op.drop_index(op.f("ix_webhook_delivery_logs_created_at"), table_name="webhook_delivery_logs")
    op.drop_index(op.f("ix_webhook_delivery_logs_request_id"), table_name="webhook_delivery_logs")
    op.drop_index(op.f("ix_webhook_delivery_logs_webhook_id"), table_name="webhook_delivery_logs")
    op.drop_table("webhook_delivery_logs")

    op.drop_index(op.f("ix_server_webhooks_token_hash"), table_name="server_webhooks")
    op.drop_index(op.f("ix_server_webhooks_created_by"), table_name="server_webhooks")
    op.drop_index(op.f("ix_server_webhooks_channel_id"), table_name="server_webhooks")
    op.drop_index(op.f("ix_server_webhooks_server_id"), table_name="server_webhooks")
    op.drop_index(op.f("ix_server_webhooks_sync_id"), table_name="server_webhooks")
    op.drop_table("server_webhooks")

    op.drop_index(op.f("ix_message_bookmarks_created_at"), table_name="message_bookmarks")
    op.drop_index(op.f("ix_message_bookmarks_message_id"), table_name="message_bookmarks")
    op.drop_index(op.f("ix_message_bookmarks_user_id"), table_name="message_bookmarks")
    op.drop_index(op.f("ix_message_bookmarks_sync_id"), table_name="message_bookmarks")
    op.drop_table("message_bookmarks")

    op.drop_column("messages", "webhook_avatar")
    op.drop_column("messages", "webhook_name")
    op.drop_column("messages", "is_pinned")
