from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.database import engine  # noqa: E402
from app.services.ids import ENTITY_PREFIXES  # noqa: E402


PRIMARY_TABLES = [
    ("users", "user"),
    ("servers", "server"),
    ("refresh_tokens", "refresh_token"),
    ("channels", "channel"),
    ("messages", "message"),
    ("server_invites", "server_invite"),
    ("message_bookmarks", "message_bookmark"),
    ("server_webhooks", "server_webhook"),
    ("webhook_delivery_logs", "webhook_delivery_log"),
    ("workspace_documents", "workspace_document"),
    ("workspace_revisions", "workspace_revision"),
    ("server_activity_logs", "server_activity_log"),
    ("release_promotion_audit", "release_promotion_audit"),
    ("replication_outbox", "replication_outbox"),
]

FK_CHECKS = [
    ("servers", "owner_id", "users"),
    ("refresh_tokens", "user_id", "users"),
    ("server_members", "server_id", "servers"),
    ("server_members", "user_id", "users"),
    ("channels", "server_id", "servers"),
    ("channels", "created_by", "users"),
    ("dm_participants", "channel_id", "channels"),
    ("dm_participants", "user_id", "users"),
    ("messages", "channel_id", "channels"),
    ("messages", "author_id", "users"),
    ("messages", "reply_to_id", "messages"),
    ("reactions", "message_id", "messages"),
    ("reactions", "user_id", "users"),
    ("server_invites", "server_id", "servers"),
    ("server_invites", "created_by", "users"),
    ("release_flags", "updated_by_user_id", "users"),
    ("release_promotion_audit", "promoted_by_user_id", "users"),
    ("message_bookmarks", "user_id", "users"),
    ("message_bookmarks", "message_id", "messages"),
    ("server_webhooks", "server_id", "servers"),
    ("server_webhooks", "channel_id", "channels"),
    ("server_webhooks", "created_by", "users"),
    ("webhook_delivery_logs", "webhook_id", "server_webhooks"),
    ("workspace_documents", "channel_id", "channels"),
    ("workspace_documents", "owner_user_id", "users"),
    ("workspace_documents", "updated_by_user_id", "users"),
    ("workspace_revisions", "document_id", "workspace_documents"),
    ("workspace_revisions", "editor_user_id", "users"),
    ("server_activity_logs", "server_id", "servers"),
    ("server_activity_logs", "actor_user_id", "users"),
    ("dm_hidden_states", "channel_id", "channels"),
    ("dm_hidden_states", "user_id", "users"),
]

SOFT_TARGET_CHECKS = [
    ("server", "servers"),
    ("channel", "channels"),
    ("message", "messages"),
    ("webhook", "server_webhooks"),
    ("workspace", "channels"),
    ("member", "users"),
    ("user", "users"),
]

JSON_REFERENCE_CHECKS = [
    ("server_activity_logs", "metadata", "server_id", "servers"),
    ("server_activity_logs", "metadata", "channel_id", "channels"),
    ("server_activity_logs", "metadata", "message_id", "messages"),
    ("server_activity_logs", "metadata", "user_id", "users"),
    ("server_activity_logs", "metadata", "owner_id", "users"),
    ("server_activity_logs", "metadata", "actor_user_id", "users"),
    ("server_activity_logs", "metadata", "webhook_id", "server_webhooks"),
    ("server_activity_logs", "metadata", "document_id", "workspace_documents"),
]


def _sync_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def create_backup(database_url: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    backup_path = output_dir / f"wyvern-before-partitioned-ids-{stamp}.dump"
    subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--file",
            str(backup_path),
            _sync_database_url(database_url),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )
    if not backup_path.exists() or backup_path.stat().st_size <= 0:
        raise RuntimeError(f"Backup was not created correctly: {backup_path}")
    checksum = _sha256_file(backup_path)
    backup_path.with_suffix(backup_path.suffix + ".sha256").write_text(f"{checksum}  {backup_path.name}\n", encoding="utf-8")
    return backup_path


def run_alembic_upgrade() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=PROJECT_ROOT, check=True)


async def validate_partitioned_ids() -> dict[str, Any]:
    report: dict[str, Any] = {"tables": {}, "orphans": [], "soft_reference_orphans": []}
    async with engine.connect() as connection:
        mapping_count = (
            await connection.execute(text("SELECT count(*) FROM id_migration_map"))
        ).scalar_one()
        report["mapping_count"] = int(mapping_count or 0)

        for table_name, entity_type in PRIMARY_TABLES:
            prefix = ENTITY_PREFIXES[entity_type]
            row = (
                await connection.execute(
                    text(
                        f"""
                        SELECT
                          count(*) AS row_count,
                          count(*) FILTER (WHERE legacy_id IS NOT NULL) AS legacy_count,
                          count(*) FILTER (
                            WHERE legacy_id IS NOT NULL
                              AND id NOT LIKE :aspc_prefix
                          ) AS wrong_prefix_count
                        FROM {table_name}
                        """
                    ),
                    {"aspc_prefix": f"aspc_{prefix}_%"},
                )
            ).mappings().one()
            report["tables"][table_name] = {key: int(row[key] or 0) for key in row.keys()}

        for table_name, column_name, ref_table in FK_CHECKS:
            orphan_count = (
                await connection.execute(
                    text(
                        f"""
                        SELECT count(*)
                        FROM {table_name} AS child
                        LEFT JOIN {ref_table} AS parent ON parent.id = child.{column_name}
                        WHERE child.{column_name} IS NOT NULL
                          AND parent.id IS NULL
                        """
                    )
                )
            ).scalar_one()
            if int(orphan_count or 0):
                report["orphans"].append(
                    {"table": table_name, "column": column_name, "ref_table": ref_table, "count": int(orphan_count)}
                )

        for target_type, ref_table in SOFT_TARGET_CHECKS:
            orphan_count = (
                await connection.execute(
                    text(
                        f"""
                        SELECT count(*)
                        FROM server_activity_logs AS activity
                        LEFT JOIN {ref_table} AS parent ON parent.id = activity.target_id
                        WHERE activity.target_type = :target_type
                          AND activity.target_id IS NOT NULL
                          AND activity.target_id <> ''
                          AND parent.id IS NULL
                        """
                    ),
                    {"target_type": target_type},
                )
            ).scalar_one()
            if int(orphan_count or 0):
                report["soft_reference_orphans"].append(
                    {
                        "table": "server_activity_logs",
                        "column": "target_id",
                        "target_type": target_type,
                        "ref_table": ref_table,
                        "count": int(orphan_count),
                    }
                )

        for table_name, json_column, key_name, ref_table in JSON_REFERENCE_CHECKS:
            orphan_count = (
                await connection.execute(
                    text(
                        f"""
                        SELECT count(*)
                        FROM {table_name} AS child
                        LEFT JOIN {ref_table} AS parent ON parent.id = child.{json_column} ->> :key_name
                        WHERE child.{json_column} IS NOT NULL
                          AND child.{json_column} ? :key_name
                          AND child.{json_column} ->> :key_name IS NOT NULL
                          AND child.{json_column} ->> :key_name <> ''
                          AND parent.id IS NULL
                        """
                    ),
                    {"key_name": key_name},
                )
            ).scalar_one()
            if int(orphan_count or 0):
                report["soft_reference_orphans"].append(
                    {
                        "table": table_name,
                        "column": f"{json_column}.{key_name}",
                        "ref_table": ref_table,
                        "count": int(orphan_count),
                    }
                )

    if report["orphans"]:
        raise RuntimeError(f"Partitioned ID validation found orphaned references: {report['orphans']}")
    if report["soft_reference_orphans"]:
        raise RuntimeError(
            f"Partitioned ID validation found orphaned soft references: {report['soft_reference_orphans']}"
        )
    wrong_prefix = {
        table: values["wrong_prefix_count"]
        for table, values in report["tables"].items()
        if values["wrong_prefix_count"]
    }
    if wrong_prefix:
        raise RuntimeError(f"Legacy rows with non-ASPC IDs found: {wrong_prefix}")
    return report


async def export_mapping(output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"id-migration-map-{stamp}.json"
    csv_path = output_dir / f"id-migration-map-{stamp}.csv"

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT entity_type, table_name, legacy_id, new_id, migrated_at
                    FROM id_migration_map
                    ORDER BY table_name, legacy_id
                    """
                )
            )
        ).mappings().all()

    payload = [{key: _json_safe(value) for key, value in row.items()} for row in rows]
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["entity_type", "table_name", "legacy_id", "new_id", "migrated_at"])
        writer.writeheader()
        writer.writerows(payload)

    for path in (json_path, csv_path):
        checksum = _sha256_file(path)
        path.with_suffix(path.suffix + ".sha256").write_text(f"{checksum}  {path.name}\n", encoding="utf-8")
    return json_path, csv_path


async def main() -> None:
    parser = argparse.ArgumentParser(description="Back up and migrate Wyvern integer IDs to ASPC partitioned IDs.")
    parser.add_argument("--validate-only", action="store_true", help="Skip backup and Alembic upgrade; only validate/export mapping.")
    parser.add_argument("--allow-not-indexing", action="store_true", help="Allow running without WYVERN_INDEXING=true.")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.indexing and not args.allow_not_indexing:
        raise RuntimeError("Set WYVERN_INDEXING=true and restart the app before running this migration.")
    if settings.node_id != "aspc":
        raise RuntimeError("Initial partitioned ID migration must run on ASPC with WYVERN_NODE_ID=aspc.")

    backup_dir = PROJECT_ROOT / "private" / "backups"
    export_dir = PROJECT_ROOT / "private" / "migrations"

    if not args.validate_only:
        backup_path = create_backup(settings.database_url, backup_dir)
        print(f"Backup created: {backup_path}")
        run_alembic_upgrade()

    report = await validate_partitioned_ids()
    json_path, csv_path = await export_mapping(export_dir)
    print(json.dumps(report, indent=2))
    print(f"Mapping exported: {json_path}")
    print(f"Mapping exported: {csv_path}")


if __name__ == "__main__":
    asyncio.run(main())
