"""Migrate integer IDs to ASPC partitioned IDs

Revision ID: 0012_partitioned_ids
Revises: 0011_add_workspace_language
Create Date: 2026-04-26 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.services.ids import ID_COLUMN_LENGTH, deterministic_legacy_id


revision = "0012_partitioned_ids"
down_revision = "0011_add_workspace_language"
branch_labels = None
depends_on = None


PRIMARY_TABLES = [
    ("users", "user", "created_at", "sync_id"),
    ("servers", "server", "created_at", "sync_id"),
    ("refresh_tokens", "refresh_token", "created_at", "token_hash"),
    ("channels", "channel", "created_at", "sync_id"),
    ("messages", "message", "created_at", "sync_id"),
    ("server_invites", "server_invite", "created_at", "sync_id"),
    ("message_bookmarks", "message_bookmark", "created_at", "sync_id"),
    ("server_webhooks", "server_webhook", "created_at", "sync_id"),
    ("webhook_delivery_logs", "webhook_delivery_log", "created_at", "request_id"),
    ("workspace_documents", "workspace_document", "created_at", "sync_id"),
    ("workspace_revisions", "workspace_revision", "created_at", None),
    ("server_activity_logs", "server_activity_log", "created_at", None),
    ("release_promotion_audit", "release_promotion_audit", "promoted_at", None),
    ("replication_outbox", "replication_outbox", "created_at", "event_id"),
]

FK_COLUMNS = [
    ("servers", "owner_id", "users", "legacy_owner_id", False),
    ("refresh_tokens", "user_id", "users", "legacy_user_id", False),
    ("server_members", "server_id", "servers", "legacy_server_id", False),
    ("server_members", "user_id", "users", "legacy_user_id", False),
    ("channels", "server_id", "servers", "legacy_server_id", True),
    ("channels", "created_by", "users", "legacy_created_by", False),
    ("dm_participants", "channel_id", "channels", "legacy_channel_id", False),
    ("dm_participants", "user_id", "users", "legacy_user_id", False),
    ("messages", "channel_id", "channels", "legacy_channel_id", False),
    ("messages", "author_id", "users", "legacy_author_id", False),
    ("messages", "reply_to_id", "messages", "legacy_reply_to_id", True),
    ("reactions", "message_id", "messages", "legacy_message_id", False),
    ("reactions", "user_id", "users", "legacy_user_id", False),
    ("server_invites", "server_id", "servers", "legacy_server_id", False),
    ("server_invites", "created_by", "users", "legacy_created_by", False),
    ("release_flags", "updated_by_user_id", "users", "legacy_updated_by_user_id", True),
    ("release_promotion_audit", "promoted_by_user_id", "users", "legacy_promoted_by_user_id", True),
    ("message_bookmarks", "user_id", "users", "legacy_user_id", False),
    ("message_bookmarks", "message_id", "messages", "legacy_message_id", False),
    ("server_webhooks", "server_id", "servers", "legacy_server_id", False),
    ("server_webhooks", "channel_id", "channels", "legacy_channel_id", False),
    ("server_webhooks", "created_by", "users", "legacy_created_by", True),
    ("webhook_delivery_logs", "webhook_id", "server_webhooks", "legacy_webhook_id", False),
    ("workspace_documents", "channel_id", "channels", "legacy_channel_id", False),
    ("workspace_documents", "owner_user_id", "users", "legacy_owner_user_id", True),
    ("workspace_documents", "updated_by_user_id", "users", "legacy_updated_by_user_id", True),
    ("workspace_revisions", "document_id", "workspace_documents", "legacy_document_id", False),
    ("workspace_revisions", "editor_user_id", "users", "legacy_editor_user_id", True),
    ("server_activity_logs", "server_id", "servers", "legacy_server_id", True),
    ("server_activity_logs", "actor_user_id", "users", "legacy_actor_user_id", True),
    ("dm_hidden_states", "channel_id", "channels", "legacy_channel_id", False),
    ("dm_hidden_states", "user_id", "users", "legacy_user_id", False),
]

FK_CONSTRAINTS = [
    ("servers", "fk_servers_owner_id_users"),
    ("refresh_tokens", "fk_refresh_tokens_user_id_users"),
    ("server_members", "fk_server_members_server_id_servers"),
    ("server_members", "fk_server_members_user_id_users"),
    ("channels", "fk_channels_created_by_users"),
    ("channels", "fk_channels_server_id_servers"),
    ("dm_participants", "fk_dm_participants_channel_id_channels"),
    ("dm_participants", "fk_dm_participants_user_id_users"),
    ("messages", "fk_messages_author_id_users"),
    ("messages", "fk_messages_channel_id_channels"),
    ("messages", "fk_messages_reply_to_id_messages"),
    ("reactions", "fk_reactions_message_id_messages"),
    ("reactions", "fk_reactions_user_id_users"),
    ("server_invites", "fk_server_invites_server_id_servers"),
    ("server_invites", "fk_server_invites_created_by_users"),
    ("release_flags", "fk_release_flags_updated_by_user_id_users"),
    ("release_promotion_audit", "fk_release_promotion_audit_promoted_by_user_id_users"),
    ("message_bookmarks", "fk_message_bookmarks_message_id_messages"),
    ("message_bookmarks", "fk_message_bookmarks_user_id_users"),
    ("server_webhooks", "fk_server_webhooks_channel_id_channels"),
    ("server_webhooks", "fk_server_webhooks_created_by_users"),
    ("server_webhooks", "fk_server_webhooks_server_id_servers"),
    ("webhook_delivery_logs", "fk_webhook_delivery_logs_webhook_id_server_webhooks"),
    ("workspace_documents", "fk_workspace_documents_channel_id_channels"),
    ("workspace_documents", "fk_workspace_documents_updated_by_user_id_users"),
    ("workspace_documents", "fk_workspace_documents_owner_user_id_users"),
    ("workspace_revisions", "fk_workspace_revisions_document_id_workspace_documents"),
    ("workspace_revisions", "fk_workspace_revisions_editor_user_id_users"),
    ("server_activity_logs", "fk_server_activity_logs_actor_user_id_users"),
    ("server_activity_logs", "fk_server_activity_logs_server_id_servers"),
    ("dm_hidden_states", "fk_dm_hidden_states_channel_id_channels"),
    ("dm_hidden_states", "fk_dm_hidden_states_user_id_users"),
]

ACTIVITY_TARGET_TYPES = [
    ("server", "servers"),
    ("channel", "channels"),
    ("message", "messages"),
    ("webhook", "server_webhooks"),
    ("workspace", "channels"),
    ("member", "users"),
    ("user", "users"),
]

JSON_REFERENCE_KEYS = [
    ("server_activity_logs", "metadata", "server_id", "servers"),
    ("server_activity_logs", "metadata", "channel_id", "channels"),
    ("server_activity_logs", "metadata", "message_id", "messages"),
    ("server_activity_logs", "metadata", "user_id", "users"),
    ("server_activity_logs", "metadata", "owner_id", "users"),
    ("server_activity_logs", "metadata", "actor_user_id", "users"),
    ("server_activity_logs", "metadata", "webhook_id", "server_webhooks"),
    ("server_activity_logs", "metadata", "document_id", "workspace_documents"),
]

PK_CONSTRAINTS = [
    ("users", "pk_users"),
    ("servers", "pk_servers"),
    ("refresh_tokens", "pk_refresh_tokens"),
    ("server_members", "pk_server_members"),
    ("channels", "pk_channels"),
    ("dm_participants", "pk_dm_participants"),
    ("messages", "pk_messages"),
    ("reactions", "pk_reactions"),
    ("server_invites", "pk_server_invites"),
    ("release_promotion_audit", "pk_release_promotion_audit"),
    ("message_bookmarks", "pk_message_bookmarks"),
    ("server_webhooks", "pk_server_webhooks"),
    ("webhook_delivery_logs", "pk_webhook_delivery_logs"),
    ("workspace_documents", "pk_workspace_documents"),
    ("workspace_revisions", "pk_workspace_revisions"),
    ("server_activity_logs", "pk_server_activity_logs"),
    ("dm_hidden_states", "pk_dm_hidden_states"),
    ("replication_outbox", "pk_replication_outbox"),
]


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _drop_constraint(table_name: str, constraint_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {_q(table_name)} DROP CONSTRAINT IF EXISTS {_q(constraint_name)}"))


def _drop_index(index_name: str) -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_q(index_name)}"))


def _create_mapping_table() -> None:
    op.create_table(
        "id_migration_map",
        sa.Column("id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("table_name", sa.String(length=64), nullable=False),
        sa.Column("legacy_id", sa.String(length=128), nullable=False),
        sa.Column("new_id", sa.String(length=ID_COLUMN_LENGTH), nullable=False),
        sa.Column("migrated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_id_migration_map")),
        sa.UniqueConstraint("entity_type", "legacy_id", name="uq_id_migration_map_entity_legacy"),
        sa.UniqueConstraint("new_id", name="uq_id_migration_map_new_id"),
    )
    op.create_index(op.f("ix_id_migration_map_entity_type"), "id_migration_map", ["entity_type"], unique=False)
    op.create_index(op.f("ix_id_migration_map_table_name"), "id_migration_map", ["table_name"], unique=False)
    op.create_index(op.f("ix_id_migration_map_legacy_id"), "id_migration_map", ["legacy_id"], unique=False)
    op.create_index(op.f("ix_id_migration_map_new_id"), "id_migration_map", ["new_id"], unique=False)


def _seed_mapping_rows() -> None:
    bind = op.get_bind()
    insert_sql = sa.text(
        """
        INSERT INTO id_migration_map (id, entity_type, table_name, legacy_id, new_id)
        VALUES (:id, :entity_type, :table_name, :legacy_id, :new_id)
        ON CONFLICT (entity_type, legacy_id) DO NOTHING
        """
    )
    for table_name, entity_type, created_col, seed_col in PRIMARY_TABLES:
        seed_expr = f", {_q(seed_col)} AS seed_value" if seed_col else ", NULL AS seed_value"
        rows = bind.execute(
            sa.text(f"SELECT id::text AS legacy_id, {_q(created_col)} AS created_at{seed_expr} FROM {_q(table_name)}")
        ).mappings()
        for row in rows:
            legacy_id = row["legacy_id"]
            new_id = deterministic_legacy_id(
                entity_type,
                legacy_id,
                node_id="aspc",
                created_at=row["created_at"],
                seed=row["seed_value"],
            )
            bind.execute(
                insert_sql,
                {
                    "id": deterministic_legacy_id("id_migration_map", f"{entity_type}:{legacy_id}", node_id="aspc"),
                    "entity_type": entity_type,
                    "table_name": table_name,
                    "legacy_id": legacy_id,
                    "new_id": new_id,
                },
            )


def _stage_primary_ids() -> None:
    bind = op.get_bind()
    for table_name, _entity_type, _created_col, _seed_col in PRIMARY_TABLES:
        op.add_column(table_name, sa.Column("legacy_id", sa.Integer(), nullable=True))
        op.add_column(table_name, sa.Column("new_id", sa.String(length=ID_COLUMN_LENGTH), nullable=True))
        bind.execute(
            sa.text(
                f"""
                UPDATE {_q(table_name)} AS target
                SET legacy_id = target.id,
                    new_id = mapping.new_id
                FROM id_migration_map AS mapping
                WHERE mapping.table_name = :table_name
                  AND mapping.legacy_id = target.id::text
                """
            ),
            {"table_name": table_name},
        )
        missing = bind.execute(
            sa.text(f"SELECT count(*) FROM {_q(table_name)} WHERE new_id IS NULL")
        ).scalar_one()
        if missing:
            raise RuntimeError(f"{table_name} has {missing} rows without partitioned IDs")


def _stage_fk_ids() -> None:
    bind = op.get_bind()
    for table_name, column_name, ref_table, legacy_column, nullable in FK_COLUMNS:
        op.add_column(table_name, sa.Column(legacy_column, sa.Integer(), nullable=True))
        op.add_column(table_name, sa.Column(f"new_{column_name}", sa.String(length=ID_COLUMN_LENGTH), nullable=True))
        bind.execute(
            sa.text(
                f"""
                UPDATE {_q(table_name)} AS target
                SET {_q(legacy_column)} = target.{_q(column_name)},
                    {_q(f"new_{column_name}")} = mapping.new_id
                FROM id_migration_map AS mapping
                WHERE target.{_q(column_name)} IS NOT NULL
                  AND mapping.table_name = :ref_table
                  AND mapping.legacy_id = target.{_q(column_name)}::text
                """
            ),
            {"ref_table": ref_table},
        )
        missing_sql = f"SELECT count(*) FROM {_q(table_name)} WHERE {_q(column_name)} IS NOT NULL AND {_q(f'new_{column_name}')} IS NULL"
        missing = bind.execute(sa.text(missing_sql)).scalar_one()
        if missing:
            raise RuntimeError(f"{table_name}.{column_name} has {missing} unmapped references")
        if not nullable:
            nulls = bind.execute(
                sa.text(f"SELECT count(*) FROM {_q(table_name)} WHERE {_q(f'new_{column_name}')} IS NULL")
            ).scalar_one()
            if nulls:
                raise RuntimeError(f"{table_name}.{column_name} has {nulls} null partitioned references")


def _rewrite_soft_references() -> None:
    bind = op.get_bind()
    for target_type, ref_table in ACTIVITY_TARGET_TYPES:
        bind.execute(
            sa.text(
                """
                UPDATE server_activity_logs AS target
                SET target_id = mapping.new_id
                FROM id_migration_map AS mapping
                WHERE target.target_type = :target_type
                  AND target.target_id ~ '^[0-9]+$'
                  AND mapping.table_name = :ref_table
                  AND mapping.legacy_id = target.target_id
                """
            ),
            {"target_type": target_type, "ref_table": ref_table},
        )

    for table_name, json_column, key_name, ref_table in JSON_REFERENCE_KEYS:
        json_path = "'{" + key_name.replace("'", "''") + "}'"
        bind.execute(
            sa.text(
                f"""
                UPDATE {_q(table_name)} AS target
                SET {_q(json_column)} = jsonb_set(
                    target.{_q(json_column)},
                    {json_path},
                    to_jsonb(mapping.new_id),
                    false
                )
                FROM id_migration_map AS mapping
                WHERE target.{_q(json_column)} IS NOT NULL
                  AND target.{_q(json_column)} ? :key_name
                  AND target.{_q(json_column)} ->> :key_name ~ '^[0-9]+$'
                  AND mapping.table_name = :ref_table
                  AND mapping.legacy_id = target.{_q(json_column)} ->> :key_name
                """
            ),
            {"key_name": key_name, "ref_table": ref_table},
        )


def _swap_primary_ids() -> None:
    for table_name, _entity_type, _created_col, _seed_col in PRIMARY_TABLES:
        op.drop_column(table_name, "id")
        op.alter_column(table_name, "new_id", new_column_name="id", existing_type=sa.String(length=ID_COLUMN_LENGTH))
        op.alter_column(table_name, "id", existing_type=sa.String(length=ID_COLUMN_LENGTH), nullable=False)


def _swap_fk_ids() -> None:
    for table_name, column_name, _ref_table, _legacy_column, nullable in FK_COLUMNS:
        op.drop_column(table_name, column_name)
        op.alter_column(
            table_name,
            f"new_{column_name}",
            new_column_name=column_name,
            existing_type=sa.String(length=ID_COLUMN_LENGTH),
        )
        op.alter_column(table_name, column_name, existing_type=sa.String(length=ID_COLUMN_LENGTH), nullable=nullable)


def _recreate_constraints() -> None:
    for table_name, columns in [
        ("users", ["id"]),
        ("servers", ["id"]),
        ("refresh_tokens", ["id"]),
        ("server_members", ["server_id", "user_id"]),
        ("channels", ["id"]),
        ("dm_participants", ["channel_id", "user_id"]),
        ("messages", ["id"]),
        ("reactions", ["message_id", "user_id", "emoji"]),
        ("server_invites", ["id"]),
        ("release_promotion_audit", ["id"]),
        ("message_bookmarks", ["id"]),
        ("server_webhooks", ["id"]),
        ("webhook_delivery_logs", ["id"]),
        ("workspace_documents", ["id"]),
        ("workspace_revisions", ["id"]),
        ("server_activity_logs", ["id"]),
        ("dm_hidden_states", ["channel_id", "user_id"]),
        ("replication_outbox", ["id"]),
    ]:
        cols = ", ".join(_q(column) for column in columns)
        op.execute(sa.text(f"ALTER TABLE {_q(table_name)} ADD CONSTRAINT {_q(f'pk_{table_name}')} PRIMARY KEY ({cols})"))

    for table_name, column_name, ref_table, _legacy_column, nullable in FK_COLUMNS:
        action = "SET NULL" if nullable and column_name in {"reply_to_id", "updated_by_user_id", "promoted_by_user_id", "created_by", "editor_user_id", "actor_user_id"} else "CASCADE"
        ref_column = "id"
        constraint_name = f"fk_{table_name}_{column_name}_{ref_table}"
        op.execute(
            sa.text(
                f"ALTER TABLE {_q(table_name)} ADD CONSTRAINT {_q(constraint_name)} "
                f"FOREIGN KEY ({_q(column_name)}) REFERENCES {_q(ref_table)} ({_q(ref_column)}) ON DELETE {action}"
            )
        )

    op.create_unique_constraint("uq_message_bookmarks_user_message", "message_bookmarks", ["user_id", "message_id"])
    op.create_index(
        "uq_workspace_documents_public_channel",
        "workspace_documents",
        ["channel_id"],
        unique=True,
        postgresql_where=sa.text("visibility = 'public' AND owner_user_id IS NULL"),
    )
    op.create_index(
        "uq_workspace_documents_private_channel_owner",
        "workspace_documents",
        ["channel_id", "owner_user_id"],
        unique=True,
        postgresql_where=sa.text("visibility = 'private' AND owner_user_id IS NOT NULL"),
    )


def upgrade() -> None:
    _create_mapping_table()
    _seed_mapping_rows()
    _stage_primary_ids()
    _stage_fk_ids()
    _rewrite_soft_references()

    _drop_index("uq_workspace_documents_private_channel_owner")
    _drop_index("uq_workspace_documents_public_channel")
    _drop_constraint("message_bookmarks", "uq_message_bookmarks_user_message")
    _drop_constraint("workspace_documents", "uq_workspace_documents_channel_id")

    for table_name, constraint_name in FK_CONSTRAINTS:
        _drop_constraint(table_name, constraint_name)
    for table_name, constraint_name in PK_CONSTRAINTS:
        _drop_constraint(table_name, constraint_name)

    _swap_fk_ids()
    _swap_primary_ids()
    _recreate_constraints()


def downgrade() -> None:
    raise RuntimeError("Downgrading partitioned IDs back to integer IDs is intentionally unsupported")
