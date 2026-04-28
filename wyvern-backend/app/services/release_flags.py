from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ReleaseFlag, ReleasePromotionAudit, ReplicationInboundLedger, ReplicationOutbox
from app.schemas.release import BridgeHealthOut, ReleaseAuditOut, ReleaseFlagOut, ReleasePromotionOut, ReleaseStatusOut
from app.services.sync_bridge import bump_sync_version, bridge_is_configured, edge_mode_is_available, enqueue_upsert_event


settings = get_settings()

DEFAULT_RELEASE_FLAGS: list[dict[str, Any]] = [
    {
        "key": "edge_release_banner",
        "description": "Show the Edge release banner inside the Edge app shell. This flag is locked to the Edge channel.",
        "stable_enabled": False,
        "edge_enabled": True,
    },
    {
        "key": "community_tools",
        "description": "Enable search, pins, bookmarks, webhooks, and collaborative workspaces.",
        "stable_enabled": False,
        "edge_enabled": True,
    },
    {
        "key": "shell_refresh",
        "description": "Use the refreshed ChatGPT-style shell with labeled navigation rows.",
        "stable_enabled": False,
        "edge_enabled": True,
    },
]

CHANNEL_LOCKED_RELEASE_FLAGS: set[str] = {
    "edge_release_banner",
}


def is_channel_locked_release_flag(flag_key: str) -> bool:
    return flag_key in CHANNEL_LOCKED_RELEASE_FLAGS


def resolve_release_channel(mode: str | None) -> str:
    requested = (mode or "stable").strip().lower()
    return "edge" if requested == "edge" else "stable"


def resolve_feature_flags(flags: list[ReleaseFlag], release_channel: str) -> dict[str, bool]:
    channel = "edge" if release_channel == "edge" else "stable"
    resolved: dict[str, bool] = {}
    for flag in flags:
        if is_channel_locked_release_flag(flag.key):
            resolved[flag.key] = channel == "edge"
        else:
            resolved[flag.key] = bool(flag.edge_enabled if channel == "edge" else flag.stable_enabled)
    return resolved


def flag_to_out(flag: ReleaseFlag) -> ReleaseFlagOut:
    return ReleaseFlagOut(
        key=flag.key,
        description=flag.description,
        stable_enabled=flag.stable_enabled,
        edge_enabled=flag.edge_enabled,
        channel_locked=is_channel_locked_release_flag(flag.key),
        updated_by_user_id=flag.updated_by_user_id,
        updated_at=flag.updated_at,
        last_promoted_at=flag.last_promoted_at,
    )


def count_promotable_release_flags(flags: list[ReleaseFlag]) -> int:
    return sum(
        1
        for flag in flags
        if not is_channel_locked_release_flag(flag.key) and bool(flag.stable_enabled) != bool(flag.edge_enabled)
    )


async def build_bridge_health(db: AsyncSession) -> BridgeHealthOut:
    pending_result = await db.execute(
        select(func.count()).select_from(ReplicationOutbox).where(
            ReplicationOutbox.delivered_at.is_(None),
            ReplicationOutbox.dead_letter.is_(False),
        )
    )
    dead_result = await db.execute(
        select(func.count()).select_from(ReplicationOutbox).where(ReplicationOutbox.dead_letter.is_(True))
    )
    last_delivery_result = await db.execute(select(func.max(ReplicationOutbox.delivered_at)))
    last_inbound_result = await db.execute(select(func.max(ReplicationInboundLedger.processed_at)))

    return BridgeHealthOut(
        configured=bridge_is_configured(),
        node_role=settings.node_role,
        peer_url=settings.sync_peer_api_url,
        sync_enabled=settings.sync_enabled,
        edge_mode_enabled=settings.edge_mode_enabled,
        edge_mode_available=edge_mode_is_available(),
        pending_outbox=int(pending_result.scalar_one() or 0),
        dead_letter_outbox=int(dead_result.scalar_one() or 0),
        last_outbox_delivery_at=last_delivery_result.scalar_one_or_none(),
        last_inbound_at=last_inbound_result.scalar_one_or_none(),
    )


async def load_release_flags(db: AsyncSession) -> list[ReleaseFlag]:
    result = await db.execute(select(ReleaseFlag).order_by(ReleaseFlag.key.asc()))
    flags = result.scalars().all()
    existing_keys = {flag.key for flag in flags}
    missing_defaults = [item for item in DEFAULT_RELEASE_FLAGS if item["key"] not in existing_keys]
    if not missing_defaults:
        return flags

    now = datetime.now(tz=UTC)
    for item in missing_defaults:
        db.add(
            ReleaseFlag(
                sync_id=str(uuid4()),
                key=item["key"],
                description=item["description"],
                stable_enabled=bool(item.get("stable_enabled", False)),
                edge_enabled=bool(item.get("edge_enabled", True)),
                updated_at=now,
                last_promoted_at=None,
            )
        )
    await db.commit()

    result = await db.execute(select(ReleaseFlag).order_by(ReleaseFlag.key.asc()))
    return result.scalars().all()


async def build_release_status(db: AsyncSession, release_channel: str) -> ReleaseStatusOut:
    flags = await load_release_flags(db)
    stable_flags = resolve_feature_flags(flags, "stable")
    edge_flags = resolve_feature_flags(flags, "edge")
    resolved = edge_flags if release_channel == "edge" else stable_flags
    promotable_count = count_promotable_release_flags(flags)
    return ReleaseStatusOut(
        release_channel=release_channel,
        flags=[flag_to_out(flag) for flag in flags],
        stable_feature_flags=stable_flags,
        edge_feature_flags=edge_flags,
        resolved_feature_flags=resolved,
        promotable_count=promotable_count,
        bridge_health=await build_bridge_health(db),
    )


async def build_release_audit(db: AsyncSession, limit: int = 50) -> list[ReleaseAuditOut]:
    result = await db.execute(
        select(ReleasePromotionAudit)
        .order_by(desc(ReleasePromotionAudit.promoted_at), desc(ReleasePromotionAudit.id))
        .limit(limit)
    )
    audits = []
    for row in result.scalars().all():
        audits.append(
            ReleaseAuditOut(
                id=row.id,
                promoted_by_user_id=row.promoted_by_user_id,
                promoted_by_label=None,
                promoted_at=row.promoted_at,
                promoted_flag_keys=list(row.promoted_flag_keys or []),
            )
        )
    return audits


async def promote_release_flags(db: AsyncSession, promoted_by_user_id: str) -> ReleasePromotionOut:
    flags = await load_release_flags(db)
    now = datetime.now(tz=UTC)
    promoted_keys: list[str] = []

    for flag in flags:
        if is_channel_locked_release_flag(flag.key):
            continue
        if bool(flag.stable_enabled) == bool(flag.edge_enabled):
            continue
        flag.stable_enabled = bool(flag.edge_enabled)
        flag.updated_by_user_id = promoted_by_user_id
        flag.updated_at = now
        flag.last_promoted_at = now
        previous_version = bump_sync_version(flag)
        await enqueue_upsert_event(db, "release_flag", flag, base_sync_version=previous_version)
        promoted_keys.append(flag.key)

    audit = ReleasePromotionAudit(
        promoted_by_user_id=promoted_by_user_id,
        promoted_at=now,
        promoted_flag_keys=promoted_keys,
        stable_snapshot={flag.key: bool(flag.stable_enabled) for flag in flags},
    )
    db.add(audit)
    await db.commit()

    return ReleasePromotionOut(
        promoted_count=len(promoted_keys),
        promoted_flag_keys=promoted_keys,
        promoted_at=now,
    )
