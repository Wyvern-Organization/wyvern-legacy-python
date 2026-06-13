from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import Channel, Message, Server, ServerMember, User
from app.schemas.server import ServerOut
from app.services.admin_allowlist import admin_allowlist_service
from app.services.release_flags import build_release_audit, build_release_status, promote_release_flags, resolve_release_channel
from app.utils.dependencies import get_current_active_user
from app.utils.responses import success_response


router = APIRouter(prefix="/admin", tags=["admin"])
settings = get_settings()


async def require_admin(current_user: User = Depends(get_current_active_user)) -> User:
    if not await admin_allowlist_service.is_admin(current_user.username, current_user.discriminator):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def _serialize_admin_user(user: User) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "discriminator": user.discriminator,
        "display_name": user.display_name,
        "bio": user.bio,
        "directory_opt_in": user.directory_opt_in,
        "avatar": user.avatar,
        "is_paid": user.is_paid,
        "created_at": user.created_at.isoformat() if hasattr(user.created_at, "isoformat") else user.created_at,
    }


@router.get("/overview")
async def admin_overview(
    activity_limit: int = Query(default=400, ge=50, le=1200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    users_result = await db.execute(select(User).order_by(desc(User.created_at), desc(User.id)))
    users = users_result.scalars().all()

    member_counts = (
        select(
            ServerMember.server_id.label("server_id"),
            func.count(ServerMember.user_id).label("member_count"),
        )
        .group_by(ServerMember.server_id)
        .subquery()
    )

    servers_result = await db.execute(
        select(Server, func.coalesce(member_counts.c.member_count, 0).label("member_count"))
        .outerjoin(member_counts, member_counts.c.server_id == Server.id)
        .order_by(desc(Server.created_at), desc(Server.id))
    )

    server_rows = servers_result.all()
    server_map = {server.id: server for server, _ in server_rows}

    users_payload = [_serialize_admin_user(user) for user in users]
    servers_payload = []
    for server, member_count in server_rows:
        payload = ServerOut.model_validate(server).model_dump(mode="json")
        payload["member_count"] = int(member_count or 0)
        servers_payload.append(payload)

    messages_result = await db.execute(
        select(Message.id, Message.channel_id, Message.author_id, Message.content, Message.created_at)
        .order_by(desc(Message.created_at), desc(Message.id))
        .limit(activity_limit)
    )
    message_rows = messages_result.all()

    joined_result = await db.execute(
        select(ServerMember.server_id, ServerMember.user_id, ServerMember.role, ServerMember.joined_at)
        .order_by(desc(ServerMember.joined_at))
        .limit(activity_limit)
    )
    joined_rows = joined_result.all()

    newest_users = users[:activity_limit]
    newest_servers = [server for server, _ in server_rows][:activity_limit]

    channel_ids = {str(row.channel_id) for row in message_rows if row.channel_id is not None}
    channel_map = {}
    if channel_ids:
        channels_result = await db.execute(
            select(Channel.id, Channel.name, Channel.type, Channel.server_id).where(Channel.id.in_(channel_ids))
        )
        for channel_id, channel_name, channel_type, server_id in channels_result.all():
            channel_map[str(channel_id)] = {
                "id": str(channel_id),
                "name": channel_name,
                "type": channel_type.value if hasattr(channel_type, "value") else str(channel_type),
                "server_id": str(server_id) if server_id is not None else None,
            }

    user_map = {user.id: user for user in users}

    def user_label(user_id: str) -> str:
        user = user_map.get(user_id)
        if user is None:
            return f"User {user_id}"
        display = user.display_name or user.username
        return f"{display} ({user.username}#{user.discriminator})"

    activities: list[dict] = []

    for row in message_rows:
        channel = channel_map.get(str(row.channel_id)) if row.channel_id is not None else None
        server_name = None
        if channel and channel.get("server_id"):
            server_name = server_map.get(channel["server_id"]).name if server_map.get(channel["server_id"]) else None

        channel_label = "Direct Message"
        if channel:
            if channel.get("type") == "dm":
                channel_label = "Direct Message"
            elif channel.get("type") == "voice":
                channel_label = f"Voice {channel.get('name') or 'channel'}"
            else:
                channel_label = f"#{channel.get('name') or 'channel'}"

        snippet = (row.content or "").strip()
        if len(snippet) > 120:
            snippet = f"{snippet[:120]}..."

        subtitle = channel_label
        if server_name:
            subtitle = f"{channel_label} in {server_name}"

        activities.append(
            {
                "type": "message.created",
                "timestamp": row.created_at,
                "title": f"{user_label(str(row.author_id))} sent a message",
                "subtitle": subtitle,
                "preview": snippet,
                "meta": {
                    "message_id": str(row.id),
                    "channel_id": str(row.channel_id),
                    "author_id": str(row.author_id),
                    "server_id": channel.get("server_id") if channel else None,
                },
            }
        )

    for row in joined_rows:
        role_value = row.role.value if hasattr(row.role, "value") else str(row.role)
        server = server_map.get(str(row.server_id))
        activities.append(
            {
                "type": "server.member.joined",
                "timestamp": row.joined_at,
                "title": f"{user_label(str(row.user_id))} joined a server",
                "subtitle": server.name if server else f"Server {row.server_id}",
                "preview": f"Role: {role_value}",
                "meta": {
                    "server_id": str(row.server_id),
                    "user_id": str(row.user_id),
                    "role": role_value,
                },
            }
        )

    for user in newest_users:
        activities.append(
            {
                "type": "user.created",
                "timestamp": user.created_at,
                "title": f"New user registered: {user.display_name or user.username}",
                "subtitle": f"{user.username}#{user.discriminator}",
                "preview": "Account created",
                "meta": {"user_id": str(user.id)},
            }
        )

    for server in newest_servers:
        activities.append(
            {
                "type": "server.created",
                "timestamp": server.created_at,
                "title": f"Server created: {server.name}",
                "subtitle": f"Owner {user_label(str(server.owner_id))}",
                "preview": (server.description or "").strip(),
                "meta": {"server_id": str(server.id), "owner_id": str(server.owner_id)},
            }
        )

    fallback_ts = datetime(1970, 1, 1, tzinfo=UTC)
    activities.sort(key=lambda item: item.get("timestamp") or fallback_ts, reverse=True)

    activity_payload = []
    for item in activities[:activity_limit]:
        ts = item.get("timestamp")
        item["timestamp"] = ts.isoformat() if hasattr(ts, "isoformat") else ts
        activity_payload.append(item)

    return success_response(
        {
            "users": users_payload,
            "servers": servers_payload,
            "activity": activity_payload,
        }
    )


@router.get("/releases")
async def admin_releases(
    mode: str = Query(default="stable"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    release_channel = resolve_release_channel(mode)
    payload = await build_release_status(db, release_channel)
    return success_response(payload.model_dump(mode="json"))


@router.get("/releases/audit")
async def admin_release_audit(
    limit: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    audits = await build_release_audit(db, limit)
    user_ids = {audit.promoted_by_user_id for audit in audits if audit.promoted_by_user_id is not None}
    user_labels: dict[str, str] = {}
    if user_ids:
        result = await db.execute(select(User).where(User.id.in_(user_ids)))
        for user in result.scalars().all():
            user_labels[str(user.id)] = user.display_name or user.username

    payload = []
    for audit in audits:
        item = audit.model_dump(mode="json")
        if audit.promoted_by_user_id is not None:
            item["promoted_by_label"] = user_labels.get(str(audit.promoted_by_user_id))
        payload.append(item)
    return success_response({"items": payload})


@router.post("/releases/promote")
async def admin_promote_releases(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> dict:
    if settings.node_role != "main":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Release promotion is only available on Stable")
    result = await promote_release_flags(db, current_user.id)
    return success_response(result.model_dump(mode="json"))
