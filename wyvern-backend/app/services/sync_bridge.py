import asyncio
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from jose import JWTError, jwt
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import (
    Channel,
    DMHiddenState,
    DMParticipant,
    Message,
    Reaction,
    ReplicationInboundLedger,
    ReplicationOutbox,
    ReleaseFlag,
    Server,
    ServerInvite,
    ServerMember,
    User,
)
from app.models.enums import ChannelType, MemberRole
from app.schemas.message import MessageOut, MessageReactionOut, MessageReplyPreviewOut
from app.schemas.user import UserPublicOut
from app.services.realtime import (
    broadcast_channel_event,
    broadcast_public_user_update,
    broadcast_server_event,
    broadcast_server_member_event,
    broadcast_user_event,
)
from app.services.pubsub import publish_channel_event
from app.services.redis_client import get_redis
from app.utils.security import hash_password


logger = logging.getLogger(__name__)
settings = get_settings()

BRIDGE_SCHEMA_VERSION = 1
BRIDGE_SIGNATURE_TTL_SECONDS = 300
EDGE_HANDOFF_TYPE = "edge-handoff"
EDGE_HANDOFF_CONSUMED_PREFIX = "wyvern:edge-handoff:used:"
SYNC_BATCH_PATH = "/internal/sync/batch"
SYNC_BOOTSTRAP_PATH = "/internal/sync/bootstrap"
BOOTSTRAP_EVENT_NAMESPACE = uuid5(NAMESPACE_URL, "wyvern-sync-bootstrap-v1")
ENTITY_TYPES = {
    "user",
    "server",
    "channel",
    "server_member",
    "server_invite",
    "dm_participant",
    "message",
    "reaction",
    "release_flag",
}

EDGE_PROMOTABLE_ENTITY_TYPES = {
    "user",
    "server",
    "server_member",
    "server_invite",
    "channel",
    "dm_participant",
    "message",
    "reaction",
}

_sync_worker_task: asyncio.Task | None = None
_last_main_resync_attempt_at: datetime | None = None
_last_main_resync_succeeded = False


def bridge_is_configured() -> bool:
    return bool(settings.sync_enabled and settings.sync_peer_api_url and settings.sync_shared_secret)


def edge_mode_is_available() -> bool:
    return settings.node_role == "main" and bool(settings.edge_mode_enabled)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, default=_json_default, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _json_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, default=_json_default))


def _signature(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()


def build_bridge_headers(body: bytes) -> dict[str, str]:
    if not settings.sync_shared_secret:
        raise RuntimeError("SYNC_SHARED_SECRET is required for sync requests")
    timestamp = str(int(datetime.now(tz=UTC).timestamp()))
    return {
        "X-Wyvern-Bridge-Node": settings.node_role,
        "X-Wyvern-Bridge-Timestamp": timestamp,
        "X-Wyvern-Bridge-Signature": _signature(settings.sync_shared_secret, timestamp, body),
        "Content-Type": "application/json",
    }


def verify_bridge_signature(body: bytes, timestamp: str | None, signature: str | None) -> None:
    if not settings.sync_shared_secret:
        raise ValueError("Sync bridge secret is not configured")
    if not timestamp or not signature:
        raise ValueError("Missing sync bridge authentication headers")
    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise ValueError("Invalid sync bridge timestamp") from exc
    now = int(datetime.now(tz=UTC).timestamp())
    if abs(now - sent_at) > BRIDGE_SIGNATURE_TTL_SECONDS:
        raise ValueError("Expired sync bridge signature")
    expected = _signature(settings.sync_shared_secret, timestamp, body)
    if not hmac.compare_digest(expected, signature):
        raise ValueError("Invalid sync bridge signature")


def _handoff_secret() -> str:
    return settings.sync_shared_secret or settings.jwt_secret_key


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _result(status_value: str, event: dict[str, Any], error: str | None = None) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "status": status_value,
        "entity_type": event["entity_type"],
        "entity_sync_id": event["entity_sync_id"],
        "error": error,
    }


def _bootstrap_event_id(source_node: str, entity_type: str, sync_id: str, sync_version: int) -> str:
    return str(uuid5(BOOTSTRAP_EVENT_NAMESPACE, f"{source_node}:{entity_type}:{sync_id}:{sync_version}"))


def _should_check_conflict(source_node: str) -> bool:
    return settings.node_role == "main" and source_node == "edge"


def _should_echo_authoritative(source_node: str) -> bool:
    return settings.node_role == "main" and source_node == "edge" and bridge_is_configured()


def _batch_endpoint_url(path: str) -> str:
    base_url = settings.sync_peer_api_url or ""
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def _backoff_seconds(attempts: int) -> int:
    return min(60, max(2, 2 ** min(attempts, 5)))


def bump_sync_version(entity: Any) -> int:
    base_version = int(getattr(entity, "sync_version", 0) or 0)
    entity.sync_version = max(1, base_version + 1)
    return base_version


def _channel_type_value(channel: Channel | None) -> str:
    if channel is None:
        return ""
    if hasattr(channel.type, "value"):
        return str(channel.type.value)
    return str(channel.type)


def _edge_write_allowed(entity_type: str, action: str, payload: dict[str, Any], entity: Any | None = None) -> tuple[bool, str]:
    if entity_type not in EDGE_PROMOTABLE_ENTITY_TYPES:
        return False, f"Edge writes for {entity_type} are not promoted before stable approval"
    return True, ""


def _source_primary_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("id")
    if value is None:
        return None
    source_id = str(value).strip()
    return source_id or None


def _source_primary_id_kwargs(payload: dict[str, Any]) -> dict[str, str]:
    source_id = _source_primary_id(payload)
    return {"id": source_id} if source_id else {}


async def _find_by_source_primary_id(db: AsyncSession, model: Any, payload: dict[str, Any]) -> Any | None:
    source_id = _source_primary_id(payload)
    if not source_id:
        return None
    return await db.get(model, source_id)


async def _channel_member_ids(db: AsyncSession, channel: Channel) -> set[str]:
    if channel.type == ChannelType.dm:
        result = await db.execute(select(DMParticipant.user_id).where(DMParticipant.channel_id == channel.id))
        return {str(item) for item in result.scalars().all()}
    if channel.server_id is None:
        return set()
    result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == channel.server_id))
    return {str(item) for item in result.scalars().all()}


async def _load_message_with_relations(db: AsyncSession, message_id: str) -> Message | None:
    result = await db.execute(
        select(Message)
        .where(Message.id == message_id)
        .options(
            selectinload(Message.reply_to),
            selectinload(Message.reactions),
        )
    )
    return result.scalar_one_or_none()


async def _serialize_dm_channel_for_ws(db: AsyncSession, channel: Channel) -> dict[str, Any]:
    result = await db.execute(
        select(User)
        .join(DMParticipant, DMParticipant.user_id == User.id)
        .where(DMParticipant.channel_id == channel.id)
        .order_by(User.id.asc())
    )
    return {
        "id": channel.id,
        "server_id": channel.server_id,
        "name": channel.name,
        "type": channel.type.value if hasattr(channel.type, "value") else str(channel.type),
        "position": channel.position,
        "category": channel.category,
        "created_by": channel.created_by,
        "created_at": channel.created_at.isoformat() if channel.created_at else None,
        "participants": [
            {
                "id": user.id,
                "username": user.username,
                "discriminator": user.discriminator,
                "display_name": user.display_name,
                "avatar": user.avatar,
            }
            for user in result.scalars().all()
        ],
    }


def _serialize_reply_preview(message: Message | None) -> MessageReplyPreviewOut | None:
    if message is None:
        return None
    return MessageReplyPreviewOut(
        id=message.id,
        author_id=message.author_id,
        content=message.content,
        attachments=list(message.attachments or []),
        created_at=message.created_at,
        edited_at=message.edited_at,
    )


def _serialize_reactions(message: Message) -> list[MessageReactionOut]:
    grouped: dict[str, set[str]] = {}
    for reaction in message.reactions or []:
        grouped.setdefault(reaction.emoji, set()).add(reaction.user_id)
    items = [
        MessageReactionOut(emoji=emoji, count=len(user_ids), users=sorted(user_ids))
        for emoji, user_ids in grouped.items()
    ]
    return sorted(items, key=lambda item: (-item.count, item.emoji))


def _serialize_message_for_ws(message: Message) -> dict[str, Any]:
    return MessageOut(
        id=message.id,
        channel_id=message.channel_id,
        author_id=message.author_id,
        reply_to_id=message.reply_to_id,
        content=message.content,
        attachments=list(message.attachments or []),
        created_at=message.created_at,
        edited_at=message.edited_at,
        reply_to=_serialize_reply_preview(message.reply_to),
        reactions=_serialize_reactions(message),
    ).model_dump(mode="json")


async def _find_user_by_sync_id(db: AsyncSession, sync_id: str) -> User | None:
    result = await db.execute(select(User).where(User.sync_id == sync_id))
    return result.scalar_one_or_none()


async def _find_server_by_sync_id(db: AsyncSession, sync_id: str) -> Server | None:
    result = await db.execute(select(Server).where(Server.sync_id == sync_id))
    return result.scalar_one_or_none()


async def _find_channel_by_sync_id(db: AsyncSession, sync_id: str) -> Channel | None:
    result = await db.execute(select(Channel).where(Channel.sync_id == sync_id))
    return result.scalar_one_or_none()


async def _find_message_by_sync_id(db: AsyncSession, sync_id: str) -> Message | None:
    result = await db.execute(select(Message).where(Message.sync_id == sync_id))
    return result.scalar_one_or_none()


async def _find_release_flag_by_sync_id(db: AsyncSession, sync_id: str) -> ReleaseFlag | None:
    result = await db.execute(select(ReleaseFlag).where(ReleaseFlag.sync_id == sync_id))
    return result.scalar_one_or_none()


async def _serialize_user(_: AsyncSession, user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "sync_id": user.sync_id,
        "sync_version": user.sync_version,
        "username": user.username,
        "discriminator": user.discriminator,
        "display_name": user.display_name,
        "bio": user.bio,
        "directory_opt_in": user.directory_opt_in,
        "email": user.email,
        "avatar": user.avatar,
        "is_paid": user.is_paid,
        "created_at": user.created_at,
    }


async def _serialize_server(db: AsyncSession, server: Server) -> dict[str, Any]:
    owner_sync_id = (await db.execute(select(User.sync_id).where(User.id == server.owner_id))).scalar_one_or_none()
    return {
        "id": server.id,
        "sync_id": server.sync_id,
        "sync_version": server.sync_version,
        "name": server.name,
        "description": server.description,
        "icon": server.icon,
        "directory_opt_in": server.directory_opt_in,
        "owner_sync_id": owner_sync_id,
        "created_at": server.created_at,
    }


async def _serialize_channel(db: AsyncSession, channel: Channel) -> dict[str, Any]:
    creator_sync_id = (await db.execute(select(User.sync_id).where(User.id == channel.created_by))).scalar_one_or_none()
    server_sync_id = None
    if channel.server_id is not None:
        server_sync_id = (await db.execute(select(Server.sync_id).where(Server.id == channel.server_id))).scalar_one_or_none()
    return {
        "id": channel.id,
        "sync_id": channel.sync_id,
        "sync_version": channel.sync_version,
        "server_sync_id": server_sync_id,
        "name": channel.name,
        "type": channel.type.value if hasattr(channel.type, "value") else str(channel.type),
        "position": channel.position,
        "category": channel.category,
        "created_by_sync_id": creator_sync_id,
        "created_at": channel.created_at,
    }


async def _serialize_server_member(db: AsyncSession, member: ServerMember) -> dict[str, Any]:
    server_sync_id = (await db.execute(select(Server.sync_id).where(Server.id == member.server_id))).scalar_one_or_none()
    user_sync_id = (await db.execute(select(User.sync_id).where(User.id == member.user_id))).scalar_one_or_none()
    return {
        "sync_id": member.sync_id,
        "sync_version": member.sync_version,
        "server_sync_id": server_sync_id,
        "user_sync_id": user_sync_id,
        "role": member.role.value if hasattr(member.role, "value") else str(member.role),
        "joined_at": member.joined_at,
    }


async def _serialize_server_invite(db: AsyncSession, invite: ServerInvite) -> dict[str, Any]:
    server_sync_id = (await db.execute(select(Server.sync_id).where(Server.id == invite.server_id))).scalar_one_or_none()
    creator_sync_id = (await db.execute(select(User.sync_id).where(User.id == invite.created_by))).scalar_one_or_none()
    return {
        "id": invite.id,
        "sync_id": invite.sync_id,
        "sync_version": invite.sync_version,
        "server_sync_id": server_sync_id,
        "code": invite.code,
        "created_by_sync_id": creator_sync_id,
        "created_at": invite.created_at,
    }


async def _serialize_dm_participant(db: AsyncSession, participant: DMParticipant) -> dict[str, Any]:
    channel_sync_id = (await db.execute(select(Channel.sync_id).where(Channel.id == participant.channel_id))).scalar_one_or_none()
    user_sync_id = (await db.execute(select(User.sync_id).where(User.id == participant.user_id))).scalar_one_or_none()
    return {
        "sync_id": participant.sync_id,
        "sync_version": participant.sync_version,
        "channel_sync_id": channel_sync_id,
        "user_sync_id": user_sync_id,
        "created_at": participant.created_at,
    }


async def _serialize_message(db: AsyncSession, message: Message) -> dict[str, Any]:
    channel_sync_id = (await db.execute(select(Channel.sync_id).where(Channel.id == message.channel_id))).scalar_one_or_none()
    author_sync_id = (await db.execute(select(User.sync_id).where(User.id == message.author_id))).scalar_one_or_none()
    reply_sync_id = None
    if message.reply_to_id is not None:
        reply_sync_id = (await db.execute(select(Message.sync_id).where(Message.id == message.reply_to_id))).scalar_one_or_none()
    return {
        "id": message.id,
        "sync_id": message.sync_id,
        "sync_version": message.sync_version,
        "channel_sync_id": channel_sync_id,
        "author_sync_id": author_sync_id,
        "reply_to_sync_id": reply_sync_id,
        "content": message.content,
        "attachments": list(message.attachments or []),
        "created_at": message.created_at,
        "edited_at": message.edited_at,
    }


async def _serialize_reaction(db: AsyncSession, reaction: Reaction) -> dict[str, Any]:
    message_sync_id = (await db.execute(select(Message.sync_id).where(Message.id == reaction.message_id))).scalar_one_or_none()
    user_sync_id = (await db.execute(select(User.sync_id).where(User.id == reaction.user_id))).scalar_one_or_none()
    return {
        "sync_id": reaction.sync_id,
        "sync_version": reaction.sync_version,
        "message_sync_id": message_sync_id,
        "user_sync_id": user_sync_id,
        "emoji": reaction.emoji,
        "created_at": reaction.created_at,
    }


async def _serialize_release_flag(_: AsyncSession, flag: ReleaseFlag) -> dict[str, Any]:
    return {
        "sync_id": flag.sync_id,
        "sync_version": flag.sync_version,
        "key": flag.key,
        "description": flag.description,
        "stable_enabled": flag.stable_enabled,
        "edge_enabled": flag.edge_enabled,
        "updated_by_user_id": flag.updated_by_user_id,
        "updated_at": flag.updated_at,
        "last_promoted_at": flag.last_promoted_at,
    }


async def serialize_entity(db: AsyncSession, entity_type: str, entity: Any) -> dict[str, Any]:
    if entity_type == "user":
        return await _serialize_user(db, entity)
    if entity_type == "server":
        return await _serialize_server(db, entity)
    if entity_type == "channel":
        return await _serialize_channel(db, entity)
    if entity_type == "server_member":
        return await _serialize_server_member(db, entity)
    if entity_type == "server_invite":
        return await _serialize_server_invite(db, entity)
    if entity_type == "dm_participant":
        return await _serialize_dm_participant(db, entity)
    if entity_type == "message":
        return await _serialize_message(db, entity)
    if entity_type == "reaction":
        return await _serialize_reaction(db, entity)
    if entity_type == "release_flag":
        return await _serialize_release_flag(db, entity)
    raise ValueError(f"Unsupported entity type: {entity_type}")


async def enqueue_upsert_event(
    db: AsyncSession,
    entity_type: str,
    entity: Any,
    *,
    base_sync_version: int,
    source_node: str | None = None,
) -> None:
    if not bridge_is_configured():
        return
    payload = _json_payload(await serialize_entity(db, entity_type, entity))
    if settings.node_role == "edge":
        allowed, reason = _edge_write_allowed(entity_type, "upsert", payload, entity)
        if not allowed:
            logger.debug("Skipping non-promotable edge upsert for %s: %s", entity_type, reason)
            return
    db.add(
        ReplicationOutbox(
            event_id=str(uuid4()),
            schema_version=BRIDGE_SCHEMA_VERSION,
            source_node=source_node or settings.node_role,
            entity_type=entity_type,
            action="upsert",
            entity_sync_id=payload["sync_id"],
            base_sync_version=base_sync_version,
            payload=payload,
        )
    )


async def enqueue_delete_event(
    db: AsyncSession,
    entity_type: str,
    entity: Any,
    *,
    base_sync_version: int,
    source_node: str | None = None,
) -> None:
    if not bridge_is_configured():
        return
    payload = _json_payload({"id": getattr(entity, "id", None), "sync_id": entity.sync_id, "sync_version": entity.sync_version})
    if entity_type == "channel":
        payload["type"] = _channel_type_value(entity)
    if settings.node_role == "edge":
        allowed, reason = _edge_write_allowed(entity_type, "delete", payload, entity)
        if not allowed:
            logger.debug("Skipping non-promotable edge delete for %s: %s", entity_type, reason)
            return
    db.add(
        ReplicationOutbox(
            event_id=str(uuid4()),
            schema_version=BRIDGE_SCHEMA_VERSION,
            source_node=source_node or settings.node_role,
            entity_type=entity_type,
            action="delete",
            entity_sync_id=entity.sync_id,
            base_sync_version=base_sync_version,
            payload=payload,
        )
    )


def create_edge_handoff_grant(user: User) -> tuple[str, datetime]:
    now = datetime.now(tz=UTC)
    expires_at = now + timedelta(seconds=max(30, settings.edge_handoff_ttl_seconds))
    payload = {
        "sub": user.sync_id,
        "type": EDGE_HANDOFF_TYPE,
        "jti": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "user": {
            "sync_id": user.sync_id,
            "email": user.email,
            "username": user.username,
            "discriminator": user.discriminator,
            "display_name": user.display_name,
            "bio": user.bio,
            "directory_opt_in": user.directory_opt_in,
            "avatar": user.avatar,
            "is_paid": user.is_paid,
            "created_at": user.created_at.astimezone(UTC).isoformat() if user.created_at else None,
        },
    }
    token = jwt.encode(payload, _handoff_secret(), algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_edge_handoff_grant(grant: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(grant, _handoff_secret(), algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("Invalid edge handoff grant") from exc
    if payload.get("type") != EDGE_HANDOFF_TYPE:
        raise ValueError("Invalid edge handoff token type")
    return payload


async def consume_edge_handoff_grant(grant_payload: dict[str, Any]) -> None:
    jti = str(grant_payload.get("jti") or "").strip()
    if not jti:
        raise ValueError("Missing edge handoff grant id")
    ttl = max(30, int(grant_payload.get("exp", 0)) - int(datetime.now(tz=UTC).timestamp()))
    used = await get_redis().set(f"{EDGE_HANDOFF_CONSUMED_PREFIX}{jti}", "1", ex=ttl, nx=True)
    if not used:
        raise ValueError("Edge handoff grant has already been used")


async def _record_inbound(
    db: AsyncSession,
    *,
    event_id: str,
    source_node: str,
    entity_type: str,
    entity_sync_id: str,
    status_value: str,
    error: str | None = None,
) -> None:
    existing = await db.get(ReplicationInboundLedger, event_id)
    if existing is not None:
        existing.source_node = source_node
        existing.entity_type = entity_type
        existing.entity_sync_id = entity_sync_id
        existing.status = status_value
        existing.error = error
        existing.processed_at = datetime.now(tz=UTC)
        return

    db.add(
        ReplicationInboundLedger(
            event_id=event_id,
            source_node=source_node,
            entity_type=entity_type,
            entity_sync_id=entity_sync_id,
            status=status_value,
            error=error,
            processed_at=datetime.now(tz=UTC),
        )
    )


async def _existing_inbound_result(db: AsyncSession, event_id: str, event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("snapshot"):
        return None
    existing = await db.get(ReplicationInboundLedger, event_id)
    if existing is None:
        return None
    return _result(existing.status, event, existing.error)


def _parse_role(value: str, default: MemberRole = MemberRole.member) -> MemberRole:
    try:
        return MemberRole(str(value))
    except ValueError:
        return default


def _local_version(entity: Any | None) -> int:
    return int(getattr(entity, "sync_version", 0) or 0)


def _incoming_version(event: dict[str, Any]) -> int:
    return int((event.get("payload") or {}).get("sync_version") or 0)


def _conflict_error(local_entity: Any, event: dict[str, Any]) -> str | None:
    if not _should_check_conflict(event["source_node"]):
        return None
    expected = int(event.get("base_sync_version") or 0)
    current = _local_version(local_entity)
    if expected != current:
        return f"Sync conflict: expected base version {expected}, found {current}"
    return None


async def _mark_rejected(db: AsyncSession, event: dict[str, Any], error: str) -> dict[str, Any]:
    await _record_inbound(
        db,
        event_id=event["event_id"],
        source_node=event["source_node"],
        entity_type=event["entity_type"],
        entity_sync_id=event["entity_sync_id"],
        status_value="rejected",
        error=error,
    )
    await db.commit()
    return _result("rejected", event, error)


async def _mark_duplicate(db: AsyncSession, event: dict[str, Any]) -> dict[str, Any]:
    await _record_inbound(
        db,
        event_id=event["event_id"],
        source_node=event["source_node"],
        entity_type=event["entity_type"],
        entity_sync_id=event["entity_sync_id"],
        status_value="duplicate",
    )
    await db.commit()
    return _result("duplicate", event)


async def _apply_user(db: AsyncSession, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    local = await _find_user_by_sync_id(db, event["entity_sync_id"])
    if local is None:
        local = await _find_by_source_primary_id(db, User, payload)
    if local is None and payload.get("email"):
        local = (
            await db.execute(select(User).where(func.lower(User.email) == str(payload["email"]).lower()))
        ).scalar_one_or_none()
    is_new = local is None

    if event["action"] == "delete":
        if local is None:
            return _result("applied", event)
        conflict = _conflict_error(local, event)
        if conflict:
            return await _mark_rejected(db, event, conflict)
        if _should_echo_authoritative(event["source_node"]):
            await enqueue_delete_event(db, "user", local, base_sync_version=local.sync_version, source_node=settings.node_role)
        await db.delete(local)
        await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
        await db.commit()
        await broadcast_user_event("deleted", local, bridge=False)
        return _result("applied", event)

    if local is not None and _incoming_version(event) < _local_version(local):
        return await _mark_duplicate(db, event)
    conflict = _conflict_error(local, event) if local is not None else None
    if conflict:
        return await _mark_rejected(db, event, conflict)

    if local is None:
        local = User(
            **_source_primary_id_kwargs(payload),
            sync_id=event["entity_sync_id"],
            username=str(payload.get("username") or "edge-user"),
            discriminator=str(payload.get("discriminator") or "0001"),
            display_name=payload.get("display_name"),
            bio=payload.get("bio"),
            directory_opt_in=bool(payload.get("directory_opt_in")),
            email=str(payload.get("email") or f"{event['entity_sync_id']}@edge.invalid"),
            avatar=payload.get("avatar"),
            password_hash=hash_password(str(uuid4())),
            is_paid=bool(payload.get("is_paid")),
            created_at=_dt(payload.get("created_at")) or datetime.now(tz=UTC),
        )
        db.add(local)
    else:
        local.username = str(payload.get("username") or local.username)
        local.discriminator = str(payload.get("discriminator") or local.discriminator)
        local.display_name = payload.get("display_name")
        local.bio = payload.get("bio")
        local.directory_opt_in = bool(payload.get("directory_opt_in"))
        local.email = str(payload.get("email") or local.email)
        local.avatar = payload.get("avatar")
        local.is_paid = bool(payload.get("is_paid"))
    local.sync_id = event["entity_sync_id"]
    local.sync_version = max(1, _incoming_version(event))

    if _should_echo_authoritative(event["source_node"]):
        await enqueue_upsert_event(db, "user", local, base_sync_version=max(local.sync_version - 1, 0), source_node=settings.node_role)
    await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
    await db.commit()
    await broadcast_public_user_update(local, bridge=False)
    return _result("applied", event)


async def _apply_server(db: AsyncSession, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    local = await _find_server_by_sync_id(db, event["entity_sync_id"])
    if local is None:
        local = await _find_by_source_primary_id(db, Server, payload)
    is_new = local is None
    if event["action"] == "delete":
        if local is None:
            return _result("applied", event)
        conflict = _conflict_error(local, event)
        if conflict:
            return await _mark_rejected(db, event, conflict)
        recipient_ids_result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == local.id))
        recipient_ids = {str(user_id) for user_id in recipient_ids_result.scalars().all()}
        if _should_echo_authoritative(event["source_node"]):
            await enqueue_delete_event(db, "server", local, base_sync_version=local.sync_version, source_node=settings.node_role)
        await db.delete(local)
        await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
        await db.commit()
        await broadcast_server_event(db, "deleted", local, recipients=recipient_ids, bridge=False)
        return _result("applied", event)

    owner = await _find_user_by_sync_id(db, str(payload.get("owner_sync_id") or ""))
    if owner is None:
        return _result("deferred", event, "Owner is not available yet")
    if local is not None and _incoming_version(event) < _local_version(local):
        return await _mark_duplicate(db, event)
    conflict = _conflict_error(local, event) if local is not None else None
    if conflict:
        return await _mark_rejected(db, event, conflict)

    if local is None:
        local = Server(
            **_source_primary_id_kwargs(payload),
            sync_id=event["entity_sync_id"],
            name=str(payload.get("name") or "Server"),
            description=payload.get("description"),
            icon=payload.get("icon"),
            directory_opt_in=bool(payload.get("directory_opt_in")),
            owner_id=owner.id,
            created_at=_dt(payload.get("created_at")) or datetime.now(tz=UTC),
        )
        db.add(local)
    else:
        local.name = str(payload.get("name") or local.name)
        local.description = payload.get("description")
        local.icon = payload.get("icon")
        local.directory_opt_in = bool(payload.get("directory_opt_in"))
        local.owner_id = owner.id
    local.sync_id = event["entity_sync_id"]
    local.sync_version = max(1, _incoming_version(event))

    if _should_echo_authoritative(event["source_node"]):
        await enqueue_upsert_event(db, "server", local, base_sync_version=max(local.sync_version - 1, 0), source_node=settings.node_role)
    await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
    await db.commit()
    await broadcast_server_event(db, "created" if is_new else "updated", local, bridge=False)
    return _result("applied", event)


async def _apply_channel(db: AsyncSession, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    local = await _find_channel_by_sync_id(db, event["entity_sync_id"])
    if local is None:
        local = await _find_by_source_primary_id(db, Channel, payload)
    is_new = local is None
    if settings.node_role == "main" and event["source_node"] == "edge":
        if event["action"] == "delete":
            if local is not None and local.type != ChannelType.dm:
                return await _mark_rejected(db, event, "Edge can only delete DM channels before stable approval")
        else:
            allowed, reason = _edge_write_allowed("channel", event["action"], payload)
            if not allowed:
                return await _mark_rejected(db, event, reason)
    if event["action"] == "delete":
        if local is None:
            return _result("applied", event)
        conflict = _conflict_error(local, event)
        if conflict:
            return await _mark_rejected(db, event, conflict)
        recipient_ids = set()
        if local.type == ChannelType.dm:
            recipient_ids_result = await db.execute(select(DMParticipant.user_id).where(DMParticipant.channel_id == local.id))
            recipient_ids = {str(user_id) for user_id in recipient_ids_result.scalars().all()}
        elif local.server_id is not None:
            recipient_ids_result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == local.server_id))
            recipient_ids = {str(user_id) for user_id in recipient_ids_result.scalars().all()}
        if _should_echo_authoritative(event["source_node"]):
            await enqueue_delete_event(db, "channel", local, base_sync_version=local.sync_version, source_node=settings.node_role)
        await db.delete(local)
        await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
        await db.commit()
        await broadcast_channel_event(db, "deleted", local, recipients=recipient_ids, bridge=False)
        return _result("applied", event)

    creator = await _find_user_by_sync_id(db, str(payload.get("created_by_sync_id") or ""))
    if creator is None:
        return _result("deferred", event, "Channel creator is not available yet")
    server_id = None
    if payload.get("server_sync_id"):
        server = await _find_server_by_sync_id(db, str(payload.get("server_sync_id")))
        if server is None:
            return _result("deferred", event, "Channel server is not available yet")
        server_id = server.id

    if local is not None and _incoming_version(event) < _local_version(local):
        return await _mark_duplicate(db, event)
    conflict = _conflict_error(local, event) if local is not None else None
    if conflict:
        return await _mark_rejected(db, event, conflict)

    channel_type = ChannelType(str(payload.get("type") or "text"))
    if local is None:
        local = Channel(
            **_source_primary_id_kwargs(payload),
            sync_id=event["entity_sync_id"],
            server_id=server_id,
            name=str(payload.get("name") or "channel"),
            type=channel_type,
            position=int(payload.get("position") or 0),
            category=payload.get("category"),
            created_by=creator.id,
            created_at=_dt(payload.get("created_at")) or datetime.now(tz=UTC),
        )
        db.add(local)
    else:
        local.server_id = server_id
        local.name = str(payload.get("name") or local.name)
        local.type = channel_type
        local.position = int(payload.get("position") or 0)
        local.category = payload.get("category")
        local.created_by = creator.id
    local.sync_id = event["entity_sync_id"]
    local.sync_version = max(1, _incoming_version(event))

    if _should_echo_authoritative(event["source_node"]):
        await enqueue_upsert_event(db, "channel", local, base_sync_version=max(local.sync_version - 1, 0), source_node=settings.node_role)
    await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
    await db.commit()
    await broadcast_channel_event(db, "created" if is_new else "updated", local, bridge=False)
    return _result("applied", event)


async def _apply_server_member(db: AsyncSession, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    local = (await db.execute(select(ServerMember).where(ServerMember.sync_id == event["entity_sync_id"]))).scalar_one_or_none()
    is_new = local is None
    if event["action"] == "delete":
        if local is None:
            return _result("applied", event)
        conflict = _conflict_error(local, event)
        if conflict:
            return await _mark_rejected(db, event, conflict)
        recipient_ids_result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == local.server_id))
        recipient_ids = {str(user_id) for user_id in recipient_ids_result.scalars().all()}
        server = await db.get(Server, local.server_id)
        user = await db.get(User, local.user_id)
        if _should_echo_authoritative(event["source_node"]):
            await enqueue_delete_event(db, "server_member", local, base_sync_version=local.sync_version, source_node=settings.node_role)
        await db.delete(local)
        await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
        await db.commit()
        if server is not None:
            await broadcast_server_member_event(
                db,
                "deleted",
                local,
                user=user,
                server=server,
                recipients=recipient_ids,
                bridge=False,
            )
        return _result("applied", event)

    server = await _find_server_by_sync_id(db, str(payload.get("server_sync_id") or ""))
    user = await _find_user_by_sync_id(db, str(payload.get("user_sync_id") or ""))
    if server is None or user is None:
        return _result("deferred", event, "Server member dependencies are not available yet")
    if local is None:
        local = (
            await db.execute(
                select(ServerMember).where(and_(ServerMember.server_id == server.id, ServerMember.user_id == user.id))
            )
        ).scalar_one_or_none()

    if local is not None and _incoming_version(event) < _local_version(local):
        return await _mark_duplicate(db, event)
    conflict = _conflict_error(local, event) if local is not None else None
    if conflict:
        return await _mark_rejected(db, event, conflict)

    if local is None:
        local = ServerMember(
            sync_id=event["entity_sync_id"],
            server_id=server.id,
            user_id=user.id,
            role=_parse_role(str(payload.get("role") or MemberRole.member.value)),
            joined_at=_dt(payload.get("joined_at")) or datetime.now(tz=UTC),
        )
        db.add(local)
    else:
        local.server_id = server.id
        local.user_id = user.id
        local.role = _parse_role(str(payload.get("role") or local.role.value), local.role)
    local.sync_id = event["entity_sync_id"]
    local.sync_version = max(1, _incoming_version(event))

    if _should_echo_authoritative(event["source_node"]):
        await enqueue_upsert_event(db, "server_member", local, base_sync_version=max(local.sync_version - 1, 0), source_node=settings.node_role)
    await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
    await db.commit()
    await broadcast_server_member_event(db, "created" if is_new else "updated", local, user=user, server=server, bridge=False)
    return _result("applied", event)


async def _apply_server_invite(db: AsyncSession, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    local = (await db.execute(select(ServerInvite).where(ServerInvite.sync_id == event["entity_sync_id"]))).scalar_one_or_none()
    if local is None:
        local = await _find_by_source_primary_id(db, ServerInvite, payload)
    if event["action"] == "delete":
        if local is None:
            return _result("applied", event)
        conflict = _conflict_error(local, event)
        if conflict:
            return await _mark_rejected(db, event, conflict)
        if _should_echo_authoritative(event["source_node"]):
            await enqueue_delete_event(db, "server_invite", local, base_sync_version=local.sync_version, source_node=settings.node_role)
        await db.delete(local)
        await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
        await db.commit()
        return _result("applied", event)

    server = await _find_server_by_sync_id(db, str(payload.get("server_sync_id") or ""))
    creator = await _find_user_by_sync_id(db, str(payload.get("created_by_sync_id") or ""))
    if server is None or creator is None:
        return _result("deferred", event, "Server invite dependencies are not available yet")
    if local is None and payload.get("code"):
        local = (await db.execute(select(ServerInvite).where(ServerInvite.code == str(payload["code"])))).scalar_one_or_none()

    if local is not None and _incoming_version(event) < _local_version(local):
        return await _mark_duplicate(db, event)
    conflict = _conflict_error(local, event) if local is not None else None
    if conflict:
        return await _mark_rejected(db, event, conflict)

    if local is None:
        local = ServerInvite(
            **_source_primary_id_kwargs(payload),
            sync_id=event["entity_sync_id"],
            server_id=server.id,
            code=str(payload.get("code") or uuid4().hex[:12]),
            created_by=creator.id,
            created_at=_dt(payload.get("created_at")) or datetime.now(tz=UTC),
        )
        db.add(local)
    else:
        local.server_id = server.id
        local.code = str(payload.get("code") or local.code)
        local.created_by = creator.id
    local.sync_id = event["entity_sync_id"]
    local.sync_version = max(1, _incoming_version(event))

    if _should_echo_authoritative(event["source_node"]):
        await enqueue_upsert_event(db, "server_invite", local, base_sync_version=max(local.sync_version - 1, 0), source_node=settings.node_role)
    await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
    await db.commit()
    return _result("applied", event)


async def _apply_dm_participant(db: AsyncSession, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    local = (await db.execute(select(DMParticipant).where(DMParticipant.sync_id == event["entity_sync_id"]))).scalar_one_or_none()
    if event["action"] == "delete":
        if local is None:
            return _result("applied", event)
        conflict = _conflict_error(local, event)
        if conflict:
            return await _mark_rejected(db, event, conflict)
        if _should_echo_authoritative(event["source_node"]):
            await enqueue_delete_event(db, "dm_participant", local, base_sync_version=local.sync_version, source_node=settings.node_role)
        await db.delete(local)
        await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
        await db.commit()
        return _result("applied", event)

    channel = await _find_channel_by_sync_id(db, str(payload.get("channel_sync_id") or ""))
    user = await _find_user_by_sync_id(db, str(payload.get("user_sync_id") or ""))
    if channel is None or user is None:
        return _result("deferred", event, "DM participant dependencies are not available yet")
    if settings.node_role == "main" and event["source_node"] == "edge" and channel.type != ChannelType.dm:
        return await _mark_rejected(db, event, "Edge DM participant writes are only allowed for DM channels")
    if local is None:
        local = (
            await db.execute(
                select(DMParticipant).where(and_(DMParticipant.channel_id == channel.id, DMParticipant.user_id == user.id))
            )
        ).scalar_one_or_none()

    if local is not None and _incoming_version(event) < _local_version(local):
        return await _mark_duplicate(db, event)
    conflict = _conflict_error(local, event) if local is not None else None
    if conflict:
        return await _mark_rejected(db, event, conflict)

    if local is None:
        local = DMParticipant(
            sync_id=event["entity_sync_id"],
            channel_id=channel.id,
            user_id=user.id,
            created_at=_dt(payload.get("created_at")) or datetime.now(tz=UTC),
        )
        db.add(local)
    else:
        local.channel_id = channel.id
        local.user_id = user.id
    local.sync_id = event["entity_sync_id"]
    local.sync_version = max(1, _incoming_version(event))

    if _should_echo_authoritative(event["source_node"]):
        await enqueue_upsert_event(db, "dm_participant", local, base_sync_version=max(local.sync_version - 1, 0), source_node=settings.node_role)
    await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
    await db.commit()
    return _result("applied", event)


async def _apply_message(db: AsyncSession, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    local = await _find_message_by_sync_id(db, event["entity_sync_id"])
    if local is None:
        local = await _find_by_source_primary_id(db, Message, payload)
    if event["action"] == "delete":
        if local is None:
            return _result("applied", event)
        conflict = _conflict_error(local, event)
        if conflict:
            return await _mark_rejected(db, event, conflict)
        local_channel = await db.get(Channel, local.channel_id)
        local_id = local.id
        if _should_echo_authoritative(event["source_node"]):
            await enqueue_delete_event(db, "message", local, base_sync_version=local.sync_version, source_node=settings.node_role)
        await db.delete(local)
        await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
        await db.commit()
        if local_channel is not None:
            await publish_channel_event(
                local_channel.id,
                payload={"event": "message.deleted", "channel_id": local_channel.id, "data": {"id": local_id}},
                extra_user_ids=await _channel_member_ids(db, local_channel),
                bridge=False,
            )
        return _result("applied", event)

    channel = await _find_channel_by_sync_id(db, str(payload.get("channel_sync_id") or ""))
    author = await _find_user_by_sync_id(db, str(payload.get("author_sync_id") or ""))
    if channel is None or author is None:
        return _result("deferred", event, "Message dependencies are not available yet")
    if settings.node_role == "main" and event["source_node"] == "edge" and payload.get("attachments"):
        return await _mark_rejected(db, event, "Attachment replication is not supported in Edge Mode v1")

    reply_id = None
    if payload.get("reply_to_sync_id"):
        reply_message = await _find_message_by_sync_id(db, str(payload["reply_to_sync_id"]))
        if reply_message is None:
            return _result("deferred", event, "Reply target is not available yet")
        reply_id = reply_message.id

    if local is not None and _incoming_version(event) < _local_version(local):
        return await _mark_duplicate(db, event)
    conflict = _conflict_error(local, event) if local is not None else None
    if conflict:
        return await _mark_rejected(db, event, conflict)

    is_new = local is None
    if local is None:
        local = Message(
            **_source_primary_id_kwargs(payload),
            sync_id=event["entity_sync_id"],
            channel_id=channel.id,
            author_id=author.id,
            reply_to_id=reply_id,
            content=str(payload.get("content") or ""),
            attachments=list(payload.get("attachments") or []),
            created_at=_dt(payload.get("created_at")) or datetime.now(tz=UTC),
            edited_at=_dt(payload.get("edited_at")),
        )
        db.add(local)
    else:
        local.channel_id = channel.id
        local.author_id = author.id
        local.reply_to_id = reply_id
        local.content = str(payload.get("content") or "")
        local.attachments = list(payload.get("attachments") or [])
        local.edited_at = _dt(payload.get("edited_at"))
    local.sync_id = event["entity_sync_id"]
    local.sync_version = max(1, _incoming_version(event))

    if channel.type == ChannelType.dm:
        await db.execute(delete(DMHiddenState).where(DMHiddenState.channel_id == channel.id))

    if _should_echo_authoritative(event["source_node"]):
        await enqueue_upsert_event(db, "message", local, base_sync_version=max(local.sync_version - 1, 0), source_node=settings.node_role)
    await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
    await db.commit()

    hydrated = await _load_message_with_relations(db, local.id)
    if hydrated is not None:
        message_payload = _serialize_message_for_ws(hydrated)
        message_payload["author"] = UserPublicOut.model_validate(author).model_dump(mode="json")
        event_payload = {
            "event": "message.created" if is_new else "message.updated",
            "channel_id": channel.id,
            "data": message_payload,
        }
        if channel.type == ChannelType.dm:
            event_payload["channel"] = await _serialize_dm_channel_for_ws(db, channel)
        await publish_channel_event(
            channel.id,
            payload=event_payload,
            extra_user_ids=await _channel_member_ids(db, channel),
            bridge=False,
        )
    return _result("applied", event)


async def _apply_reaction(db: AsyncSession, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    local = (await db.execute(select(Reaction).where(Reaction.sync_id == event["entity_sync_id"]))).scalar_one_or_none()
    if event["action"] == "delete":
        if local is None:
            return _result("applied", event)
        conflict = _conflict_error(local, event)
        if conflict:
            return await _mark_rejected(db, event, conflict)
        emoji = local.emoji
        reaction_user_id = local.user_id
        message = await db.get(Message, local.message_id)
        if _should_echo_authoritative(event["source_node"]):
            await enqueue_delete_event(db, "reaction", local, base_sync_version=local.sync_version, source_node=settings.node_role)
        await db.delete(local)
        await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
        await db.commit()
        if message is not None:
            channel = await db.get(Channel, message.channel_id)
            hydrated = await _load_message_with_relations(db, message.id)
            if channel is not None and hydrated is not None:
                await publish_channel_event(
                    channel.id,
                    payload={
                        "event": "reaction.removed",
                        "channel_id": channel.id,
                        "data": {
                            "message_id": hydrated.id,
                            "channel_id": hydrated.channel_id,
                            "user_id": reaction_user_id,
                            "emoji": emoji,
                            "message": _serialize_message_for_ws(hydrated),
                        },
                    },
                    extra_user_ids=await _channel_member_ids(db, channel),
                    bridge=False,
                )
        return _result("applied", event)

    message = await _find_message_by_sync_id(db, str(payload.get("message_sync_id") or ""))
    user = await _find_user_by_sync_id(db, str(payload.get("user_sync_id") or ""))
    if message is None or user is None:
        return _result("deferred", event, "Reaction dependencies are not available yet")
    if local is None:
        local = (
            await db.execute(
                select(Reaction).where(
                    and_(
                        Reaction.message_id == message.id,
                        Reaction.user_id == user.id,
                        Reaction.emoji == str(payload.get("emoji") or ""),
                    )
                )
            )
        ).scalar_one_or_none()

    if local is not None and _incoming_version(event) < _local_version(local):
        return await _mark_duplicate(db, event)
    conflict = _conflict_error(local, event) if local is not None else None
    if conflict:
        return await _mark_rejected(db, event, conflict)

    if local is None:
        local = Reaction(
            sync_id=event["entity_sync_id"],
            message_id=message.id,
            user_id=user.id,
            emoji=str(payload.get("emoji") or ""),
            created_at=_dt(payload.get("created_at")) or datetime.now(tz=UTC),
        )
        db.add(local)
    else:
        local.message_id = message.id
        local.user_id = user.id
        local.emoji = str(payload.get("emoji") or local.emoji)
    local.sync_id = event["entity_sync_id"]
    local.sync_version = max(1, _incoming_version(event))

    if _should_echo_authoritative(event["source_node"]):
        await enqueue_upsert_event(db, "reaction", local, base_sync_version=max(local.sync_version - 1, 0), source_node=settings.node_role)
    await _record_inbound(db, event_id=event["event_id"], source_node=event["source_node"], entity_type=event["entity_type"], entity_sync_id=event["entity_sync_id"], status_value="applied")
    await db.commit()

    channel = await db.get(Channel, message.channel_id)
    hydrated = await _load_message_with_relations(db, message.id)
    if channel is not None and hydrated is not None:
        await publish_channel_event(
            channel.id,
            payload={
                "event": "reaction.added",
                "channel_id": channel.id,
                "data": {
                    "message_id": hydrated.id,
                    "channel_id": hydrated.channel_id,
                    "user_id": local.user_id,
                    "emoji": local.emoji,
                    "message": _serialize_message_for_ws(hydrated),
                },
            },
            extra_user_ids=await _channel_member_ids(db, channel),
            bridge=False,
        )
    return _result("applied", event)


async def _apply_release_flag(db: AsyncSession, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    local = await _find_release_flag_by_sync_id(db, event["entity_sync_id"])
    if local is None and payload.get("key"):
        local = (
            await db.execute(select(ReleaseFlag).where(ReleaseFlag.key == str(payload["key"])))
        ).scalar_one_or_none()

    if event["action"] == "delete":
        if local is None:
            return _result("applied", event)
        conflict = _conflict_error(local, event)
        if conflict:
            return await _mark_rejected(db, event, conflict)
        await db.delete(local)
        await _record_inbound(
            db,
            event_id=event["event_id"],
            source_node=event["source_node"],
            entity_type=event["entity_type"],
            entity_sync_id=event["entity_sync_id"],
            status_value="applied",
        )
        await db.commit()
        return _result("applied", event)

    if local is not None and _incoming_version(event) < _local_version(local):
        return await _mark_duplicate(db, event)
    conflict = _conflict_error(local, event) if local is not None else None
    if conflict:
        return await _mark_rejected(db, event, conflict)

    if local is None:
        local = ReleaseFlag(
            sync_id=event["entity_sync_id"],
            key=str(payload.get("key") or event["entity_sync_id"]),
            description=str(payload.get("description") or "Release flag"),
            stable_enabled=bool(payload.get("stable_enabled")),
            edge_enabled=bool(payload.get("edge_enabled")),
            updated_by_user_id=payload.get("updated_by_user_id"),
            updated_at=_dt(payload.get("updated_at")) or datetime.now(tz=UTC),
            last_promoted_at=_dt(payload.get("last_promoted_at")),
        )
        db.add(local)
    else:
        local.key = str(payload.get("key") or local.key)
        local.description = str(payload.get("description") or local.description)
        local.stable_enabled = bool(payload.get("stable_enabled"))
        local.edge_enabled = bool(payload.get("edge_enabled"))
        local.updated_by_user_id = payload.get("updated_by_user_id")
        local.updated_at = _dt(payload.get("updated_at")) or local.updated_at
        local.last_promoted_at = _dt(payload.get("last_promoted_at"))
    local.sync_id = event["entity_sync_id"]
    local.sync_version = max(1, _incoming_version(event))

    await _record_inbound(
        db,
        event_id=event["event_id"],
        source_node=event["source_node"],
        entity_type=event["entity_type"],
        entity_sync_id=event["entity_sync_id"],
        status_value="applied",
    )
    await db.commit()
    return _result("applied", event)


async def apply_event(db: AsyncSession, event: dict[str, Any]) -> dict[str, Any]:
    existing = await _existing_inbound_result(db, event["event_id"], event)
    if existing is not None:
        return existing
    if int(event.get("schema_version") or 0) != BRIDGE_SCHEMA_VERSION:
        return await _mark_rejected(db, event, f"Unsupported bridge schema version {event.get('schema_version')}")
    if event["entity_type"] not in ENTITY_TYPES:
        return await _mark_rejected(db, event, f"Unsupported entity type {event['entity_type']}")
    payload = event.get("payload") or {}
    if settings.node_role == "main" and event["source_node"] == "edge":
        if event["entity_type"] == "release_flag":
            return await _mark_rejected(db, event, "Edge release flags are managed by Stable promotion")
        if event["entity_type"] == "channel":
            allowed, reason = _edge_write_allowed(event["entity_type"], event["action"], payload)
            if not allowed:
                return await _mark_rejected(db, event, reason)
        elif event["entity_type"] not in EDGE_PROMOTABLE_ENTITY_TYPES:
            return await _mark_rejected(db, event, f"Edge writes for {event['entity_type']} are not promoted before stable approval")
    if event["entity_type"] == "user":
        return await _apply_user(db, event)
    if event["entity_type"] == "server":
        return await _apply_server(db, event)
    if event["entity_type"] == "channel":
        return await _apply_channel(db, event)
    if event["entity_type"] == "server_member":
        return await _apply_server_member(db, event)
    if event["entity_type"] == "server_invite":
        return await _apply_server_invite(db, event)
    if event["entity_type"] == "dm_participant":
        return await _apply_dm_participant(db, event)
    if event["entity_type"] == "message":
        return await _apply_message(db, event)
    if event["entity_type"] == "reaction":
        return await _apply_reaction(db, event)
    if event["entity_type"] == "release_flag":
        return await _apply_release_flag(db, event)
    return await _mark_rejected(db, event, "Unhandled entity type")


async def apply_replication_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as db:
        for event in events:
            try:
                results.append(await apply_event(db, event))
            except Exception as exc:  # pragma: no cover - defensive logging path
                await db.rollback()
                logger.exception("Failed to apply sync event %s", event.get("event_id"))
                results.append(_result("deferred", event, str(exc)))
    return results


async def build_bootstrap_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as db:
        entity_queries: list[tuple[str, Any]] = [
            ("release_flag", select(ReleaseFlag).order_by(ReleaseFlag.key.asc())),
            ("user", select(User).order_by(User.created_at.asc(), User.id.asc())),
            ("server", select(Server).order_by(Server.created_at.asc(), Server.id.asc())),
            ("channel", select(Channel).order_by(Channel.created_at.asc(), Channel.id.asc())),
            ("server_member", select(ServerMember).order_by(ServerMember.joined_at.asc(), ServerMember.server_id.asc(), ServerMember.user_id.asc())),
            ("server_invite", select(ServerInvite).order_by(ServerInvite.created_at.asc(), ServerInvite.id.asc())),
            ("dm_participant", select(DMParticipant).order_by(DMParticipant.created_at.asc(), DMParticipant.channel_id.asc(), DMParticipant.user_id.asc())),
            ("message", select(Message).order_by(Message.created_at.asc(), Message.id.asc())),
            ("reaction", select(Reaction).order_by(Reaction.created_at.asc(), Reaction.message_id.asc(), Reaction.user_id.asc())),
        ]
        for entity_type, query in entity_queries:
            result = await db.execute(query)
            for item in result.scalars().all():
                sync_version = max(int(item.sync_version or 1), 1)
                events.append(
                    {
                        "event_id": _bootstrap_event_id(settings.node_role, entity_type, item.sync_id, sync_version),
                        "schema_version": BRIDGE_SCHEMA_VERSION,
                        "source_node": settings.node_role,
                        "entity_type": entity_type,
                        "action": "upsert",
                        "entity_sync_id": item.sync_id,
                        "base_sync_version": max(sync_version - 1, 0),
                        "payload": await serialize_entity(db, entity_type, item),
                        "snapshot": True,
                    }
                )
    return events


async def process_outbox_batch() -> None:
    if not bridge_is_configured():
        return
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(ReplicationOutbox)
                .where(
                    ReplicationOutbox.delivered_at.is_(None),
                    ReplicationOutbox.dead_letter.is_(False),
                    ReplicationOutbox.available_at <= datetime.now(tz=UTC),
                )
                .order_by(ReplicationOutbox.created_at.asc(), ReplicationOutbox.id.asc())
                .limit(max(1, settings.sync_bridge_batch_size))
            )
        ).scalars().all()
        if not rows:
            return

        payload = {
            "events": [
                {
                    "event_id": row.event_id,
                    "schema_version": row.schema_version,
                    "source_node": row.source_node,
                    "entity_type": row.entity_type,
                    "action": row.action,
                    "entity_sync_id": row.entity_sync_id,
                    "base_sync_version": row.base_sync_version,
                    "payload": row.payload,
                    "snapshot": False,
                }
                for row in rows
            ]
        }
        body = _json_bytes(payload)
        try:
            async with httpx.AsyncClient(timeout=settings.sync_bridge_request_timeout_seconds) as client:
                response = await client.post(_batch_endpoint_url(SYNC_BATCH_PATH), content=body, headers=build_bridge_headers(body))
                response.raise_for_status()
            result_map = {
                item.get("event_id"): item
                for item in ((response.json().get("data", {}) or {}).get("results") or response.json().get("results") or [])
                if item.get("event_id")
            }
            now = datetime.now(tz=UTC)
            for row in rows:
                result_item = result_map.get(row.event_id)
                status_value = str((result_item or {}).get("status") or "").lower()
                if status_value in {"applied", "duplicate"}:
                    row.delivered_at = now
                    row.last_error = None
                elif status_value == "rejected":
                    row.dead_letter = True
                    row.last_error = (result_item or {}).get("error") or "Peer rejected the event"
                else:
                    row.attempts += 1
                    row.available_at = now + timedelta(seconds=_backoff_seconds(row.attempts))
                    row.last_error = (result_item or {}).get("error") or "Peer deferred the event"
            await db.commit()
        except Exception as exc:  # pragma: no cover - network failure path
            logger.warning("Sync bridge batch failed: %s", exc)
            now = datetime.now(tz=UTC)
            for row in rows:
                row.attempts += 1
                row.available_at = now + timedelta(seconds=_backoff_seconds(row.attempts))
                row.last_error = str(exc)
            await db.commit()


def _resync_interval_seconds() -> float:
    return max(5.0, float(settings.sync_bridge_resync_interval_seconds))


async def maybe_resync_from_main(*, force: bool = False) -> bool:
    global _last_main_resync_attempt_at, _last_main_resync_succeeded
    if not bridge_is_configured() or settings.node_role != "edge":
        return False
    if settings.sync_bridge_resync_interval_seconds <= 0 and not force:
        return False
    now = datetime.now(tz=UTC)
    if not force and _last_main_resync_attempt_at is not None:
        retry_interval = _resync_interval_seconds() if _last_main_resync_succeeded else min(30.0, _resync_interval_seconds())
        elapsed = (now - _last_main_resync_attempt_at).total_seconds()
        if elapsed < retry_interval:
            return False
    _last_main_resync_attempt_at = now

    body = _json_bytes({"request": "bootstrap", "schema_version": BRIDGE_SCHEMA_VERSION})
    try:
        async with httpx.AsyncClient(timeout=settings.sync_bridge_request_timeout_seconds) as client:
            response = await client.post(_batch_endpoint_url(SYNC_BOOTSTRAP_PATH), content=body, headers=build_bridge_headers(body))
            response.raise_for_status()
        events = (response.json().get("data", {}) or {}).get("events") or response.json().get("events") or []
        if events:
            await apply_replication_events(events)
        logger.info("Edge resynced %s records from main", len(events))
        _last_main_resync_succeeded = True
        return True
    except Exception as exc:  # pragma: no cover - bootstrap failure path
        logger.warning("Edge resync from main failed: %s", exc)
        _last_main_resync_succeeded = False
        return False


async def _sync_worker() -> None:
    await maybe_resync_from_main(force=True)
    try:
        while True:
            await process_outbox_batch()
            await maybe_resync_from_main()
            await asyncio.sleep(max(0.5, float(settings.sync_bridge_poll_interval_seconds)))
    except asyncio.CancelledError:
        raise


async def start_sync_bridge_worker() -> None:
    global _sync_worker_task
    if not bridge_is_configured():
        return
    if _sync_worker_task is None or _sync_worker_task.done():
        _sync_worker_task = asyncio.create_task(_sync_worker())


async def stop_sync_bridge_worker() -> None:
    global _sync_worker_task
    if _sync_worker_task is not None:
        _sync_worker_task.cancel()
        try:
            await _sync_worker_task
        except asyncio.CancelledError:
            pass
        _sync_worker_task = None
