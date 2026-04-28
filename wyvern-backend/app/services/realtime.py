from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, ChannelType, DMParticipant, ServerMember, User
from app.schemas.channel import ChannelOut
from app.schemas.server import ServerMemberOut, ServerOut
from app.schemas.user import UserPublicOut
from app.services.pubsub import publish_user_event
from app.websocket.manager import manager


def active_user_ids() -> set[str]:
    return set(manager.active_connections.keys())


async def _broadcast(recipients: set[str], payload: dict[str, Any], *, bridge: bool = True) -> None:
    if not recipients:
        return
    await publish_user_event(set(recipients), payload, bridge=bridge)


async def _server_recipient_ids(db: AsyncSession, server_id: str) -> set[str]:
    result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == server_id))
    return {str(user_id) for user_id in result.scalars().all()}


async def _channel_recipient_ids(db: AsyncSession, channel: Channel) -> set[str]:
    if channel.type == ChannelType.dm:
        result = await db.execute(select(DMParticipant.user_id).where(DMParticipant.channel_id == channel.id))
        return {str(user_id) for user_id in result.scalars().all()}
    if channel.server_id is None:
        return set()
    return await _server_recipient_ids(db, channel.server_id)


async def broadcast_public_user_update(
    user: User,
    *,
    extra_user_ids: set[str] | None = None,
    bridge: bool = True,
) -> None:
    await broadcast_user_event("updated", user, extra_user_ids=extra_user_ids, bridge=bridge)


async def broadcast_user_event(
    action: str,
    user: User,
    *,
    extra_user_ids: set[str] | None = None,
    bridge: bool = True,
) -> None:
    recipients = active_user_ids()
    if extra_user_ids:
        recipients.update(extra_user_ids)
    await _broadcast(
        recipients,
        {
            "event": f"user.{action}",
            "data": UserPublicOut.model_validate(user).model_dump(mode="json"),
        },
        bridge=bridge,
    )


async def broadcast_presence_update(
    user_id: str,
    status: Any,
    *,
    extra_user_ids: set[str] | None = None,
    bridge: bool = False,
) -> None:
    recipients = active_user_ids()
    if extra_user_ids:
        recipients.update(extra_user_ids)
    status_value = status.value if hasattr(status, "value") else str(status)
    await _broadcast(
        recipients,
        {
            "event": "presence.updated",
            "data": {
                "user_id": user_id,
                "status": status_value,
            },
        },
        bridge=bridge,
    )


async def broadcast_server_event(
    db: AsyncSession,
    action: str,
    server: Any,
    *,
    recipients: set[str] | None = None,
    extra_user_ids: set[str] | None = None,
    bridge: bool = True,
) -> None:
    recipients = set(await _server_recipient_ids(db, server.id) if recipients is None else recipients)
    if extra_user_ids:
        recipients.update(extra_user_ids)
    await _broadcast(
        recipients,
        {
            "event": f"server.{action}",
            "data": ServerOut.model_validate(server).model_dump(mode="json"),
        },
        bridge=bridge,
    )


async def broadcast_channel_event(
    db: AsyncSession,
    action: str,
    channel: Channel,
    *,
    recipients: set[str] | None = None,
    extra_user_ids: set[str] | None = None,
    bridge: bool = True,
) -> None:
    recipients = set(await _channel_recipient_ids(db, channel) if recipients is None else recipients)
    if extra_user_ids:
        recipients.update(extra_user_ids)
    await _broadcast(
        recipients,
        {
            "event": f"channel.{action}",
            "channel_id": channel.id,
            "data": ChannelOut.model_validate(channel).model_dump(mode="json"),
        },
        bridge=bridge,
    )


async def broadcast_server_member_event(
    db: AsyncSession,
    action: str,
    member: ServerMember,
    *,
    user: User | None = None,
    server: Any | None = None,
    recipients: set[str] | None = None,
    extra_user_ids: set[str] | None = None,
    bridge: bool = True,
) -> None:
    recipients = set(await _server_recipient_ids(db, member.server_id) if recipients is None else recipients)
    if extra_user_ids:
        recipients.update(extra_user_ids)
    resolved_user = user
    if resolved_user is None:
        resolved_user = await db.get(User, member.user_id)
    await _broadcast(
        recipients,
        {
            "event": f"server.member.{action}",
            "data": {
                "server_id": member.server_id,
                "user_id": member.user_id,
                "server": ServerOut.model_validate(server).model_dump(mode="json") if server is not None else None,
                "member": ServerMemberOut.model_validate(member).model_dump(mode="json"),
                "user": UserPublicOut.model_validate(resolved_user).model_dump(mode="json") if resolved_user is not None else None,
            },
        },
        bridge=bridge,
    )
