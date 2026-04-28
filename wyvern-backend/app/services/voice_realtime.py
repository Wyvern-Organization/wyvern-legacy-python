from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Channel, ChannelType, DMParticipant, ServerMember, User
from app.schemas.user import UserPublicOut
from app.services.live_bridge import queue_realtime_event
from app.websocket.manager import manager


async def _voice_recipients_for_channel(channel: Channel) -> set[str]:
    async with AsyncSessionLocal() as db:
        if channel.type == ChannelType.dm:
            result = await db.execute(select(DMParticipant.user_id).where(DMParticipant.channel_id == channel.id))
            return {str(item) for item in result.scalars().all()}

        if channel.server_id is None:
            return set()

        result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == channel.server_id))
        return {str(item) for item in result.scalars().all()}


def _serialize_user_for_voice(user: User) -> dict[str, Any]:
    return UserPublicOut.model_validate(user).model_dump(mode="json")


async def _voice_user_profiles(user_ids: set[str]) -> list[dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = manager.get_remote_voice_profiles(user_ids)
    if user_ids:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.id.in_(list(user_ids))))
            for user in result.scalars().all():
                profiles[str(user.id)] = _serialize_user_for_voice(user)
    return [profiles[user_id] for user_id in sorted(user_ids) if user_id in profiles]


async def voice_participants_payload(channel_id: str) -> dict[str, Any]:
    participants = {str(user_id) for user_id in manager.get_voice_participants(channel_id) if str(user_id)}
    return {
        "channel_id": channel_id,
        "user_ids": sorted(participants),
        "users": await _voice_user_profiles(participants),
    }


async def emit_voice_participants(channel_id: str) -> None:
    async with AsyncSessionLocal() as db:
        channel_result = await db.execute(select(Channel).where(Channel.id == channel_id))
        channel = channel_result.scalar_one_or_none()
    if channel is None:
        return

    participants = manager.get_voice_participants(channel_id)
    recipients = await _voice_recipients_for_channel(channel)
    recipients.update(participants)
    await manager.broadcast_to_users(
        recipients,
        {
            "event": "voice.participants",
            "data": await voice_participants_payload(channel_id),
        },
    )


async def bridge_voice_join(user_id: str, channel_id: str) -> None:
    event: dict[str, Any] = {"kind": "voice.join", "user_id": user_id, "channel_id": channel_id}
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is not None:
            event["user"] = _serialize_user_for_voice(user)
    queue_realtime_event(event)


def bridge_voice_leave(user_id: str, channel_id: str) -> None:
    queue_realtime_event({"kind": "voice.leave", "user_id": user_id, "channel_id": channel_id})


def bridge_call_signal(
    *,
    channel_id: str,
    from_user_id: str,
    target_user_id: str,
    signal_type: str,
    payload: object,
) -> None:
    queue_realtime_event(
        {
            "kind": "call.signal",
            "channel_id": channel_id,
            "from_user_id": from_user_id,
            "target_user_id": target_user_id,
            "signal_type": signal_type,
            "payload": payload,
        }
    )


async def apply_remote_voice_join(user_id: str, channel_id: str, user: dict[str, Any] | None = None) -> None:
    previous_channel_id = manager.join_remote_voice(user_id, channel_id, user=user)
    await emit_voice_participants(channel_id)
    if previous_channel_id is not None and previous_channel_id != channel_id:
        await emit_voice_participants(previous_channel_id)


async def apply_remote_voice_leave(user_id: str, channel_id: str | None = None) -> None:
    left_channel_id = manager.leave_remote_voice(user_id, channel_id)
    if left_channel_id is not None:
        await emit_voice_participants(left_channel_id)


async def deliver_remote_call_signal(
    *,
    channel_id: str,
    from_user_id: str,
    target_user_id: str,
    signal_type: str,
    payload: object,
) -> None:
    if not manager.is_local_user_in_voice_channel(target_user_id, channel_id):
        return
    await manager.send_personal_message(
        target_user_id,
        {
            "event": "call.signal",
            "data": {
                "channel_id": channel_id,
                "from_user_id": from_user_id,
                "signal_type": signal_type,
                "payload": payload,
            },
        },
    )
