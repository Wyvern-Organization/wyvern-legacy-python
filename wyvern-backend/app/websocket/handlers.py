from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import and_, select

from app.database import AsyncSessionLocal
from app.models import Channel, ChannelType, DMParticipant, ServerMember, User
from app.models.enums import PresenceStatus
from app.services.access import ensure_channel_access
from app.services.presence import presence_service
from app.utils.security import TokenError, decode_token
from app.websocket.manager import manager


async def _auth_websocket_user(token: str | None) -> int:
    if token is None:
        raise TokenError("Missing token")

    payload = decode_token(token)
    if payload.get("type") != "access":
        raise TokenError("Invalid token type")

    user_id = payload.get("sub")
    if user_id is None:
        raise TokenError("Invalid token subject")

    return int(user_id)


async def _resolve_accessible_channel(db, user_id: int, channel_id: int) -> Channel | None:
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if channel is None:
        return None

    try:
        await ensure_channel_access(db, channel, user_id)
    except Exception:
        return None
    return channel


async def _voice_recipients_for_channel(db, channel: Channel) -> set[int]:
    if channel.type == ChannelType.dm:
        result = await db.execute(select(DMParticipant.user_id).where(DMParticipant.channel_id == channel.id))
        return set(result.scalars().all())

    if channel.server_id is None:
        return set()

    result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == channel.server_id))
    return set(result.scalars().all())


async def _emit_voice_participants(channel_id: int, participants: set[int]) -> None:
    async with AsyncSessionLocal() as db:
        channel_result = await db.execute(select(Channel).where(Channel.id == channel_id))
        channel = channel_result.scalar_one_or_none()
        if channel is None:
            return
        recipients = await _voice_recipients_for_channel(db, channel)
    recipients.update(participants)
    await manager.broadcast_to_users(
        recipients,
        {
            "event": "voice.participants",
            "data": {"channel_id": channel_id, "user_ids": sorted(participants)},
        },
    )


async def _handle_disconnect(user_id: int) -> None:
    left_channel_id, remaining = await manager.disconnect(user_id)
    if left_channel_id is not None:
        await _emit_voice_participants(left_channel_id, remaining)


async def websocket_endpoint(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")

    try:
        user_id = await _auth_websocket_user(token)
    except TokenError:
        await websocket.close(code=1008)
        return

    async with AsyncSessionLocal() as db:
        user_result = await db.execute(select(User).where(User.id == user_id))
        if user_result.scalar_one_or_none() is None:
            await websocket.close(code=1008)
            return

    await manager.connect(user_id, websocket)

    try:
        while True:
            payload = await websocket.receive_json()
            action = payload.get("action")

            if action == "subscribe":
                channel_ids = [int(item) for item in payload.get("channel_ids", [])]
                allowed: list[int] = []

                async with AsyncSessionLocal() as db:
                    for channel_id in channel_ids:
                        channel = await _resolve_accessible_channel(db, user_id, channel_id)
                        if channel is not None:
                            allowed.append(channel_id)

                manager.subscribe(user_id, allowed)
                await manager.send_personal_message(
                    user_id,
                    {"event": "subscribed", "data": {"channel_ids": allowed}},
                )
            elif action == "unsubscribe":
                channel_ids = [int(item) for item in payload.get("channel_ids", [])]
                manager.unsubscribe(user_id, channel_ids)
                await manager.send_personal_message(
                    user_id,
                    {"event": "unsubscribed", "data": {"channel_ids": channel_ids}},
                )
            elif action == "presence":
                status = payload.get("status", "online")
                try:
                    normalized = await presence_service.set_presence(user_id, status=status)
                    await manager.send_personal_message(
                        user_id,
                        {"event": "presence.updated", "data": {"status": normalized.value}},
                    )
                except ValueError:
                    await manager.send_personal_message(
                        user_id,
                        {
                            "event": "error",
                            "data": {
                                "message": "Invalid presence status",
                                "allowed": [item.value for item in PresenceStatus if item != PresenceStatus.offline],
                            },
                        },
                    )
            elif action == "join_voice":
                try:
                    channel_id = int(payload.get("channel_id"))
                except Exception:
                    await manager.send_personal_message(
                        user_id,
                        {"event": "error", "data": {"message": "Invalid voice channel id"}},
                    )
                    continue

                async with AsyncSessionLocal() as db:
                    channel = await _resolve_accessible_channel(db, user_id, channel_id)
                if channel is None or channel.type not in {ChannelType.voice, ChannelType.dm}:
                    await manager.send_personal_message(
                        user_id,
                        {"event": "error", "data": {"message": "Voice channel not accessible"}},
                    )
                    continue

                previous_channel_id, participants, previous_remaining = manager.join_voice(user_id, channel_id)
                await _emit_voice_participants(channel_id, participants)
                if previous_channel_id is not None and previous_channel_id != channel_id:
                    await _emit_voice_participants(previous_channel_id, previous_remaining)
            elif action == "leave_voice":
                channel_raw = payload.get("channel_id")
                channel_id: int | None = None
                if channel_raw is not None:
                    try:
                        channel_id = int(channel_raw)
                    except Exception:
                        channel_id = None

                left_channel_id, remaining = manager.leave_voice(user_id, channel_id)
                if left_channel_id is not None:
                    await _emit_voice_participants(left_channel_id, remaining)
            elif action == "voice.status":
                try:
                    channel_id = int(payload.get("channel_id"))
                except Exception:
                    await manager.send_personal_message(
                        user_id,
                        {"event": "error", "data": {"message": "Invalid voice channel id"}},
                    )
                    continue

                async with AsyncSessionLocal() as db:
                    channel = await _resolve_accessible_channel(db, user_id, channel_id)
                if channel is None or channel.type not in {ChannelType.voice, ChannelType.dm}:
                    await manager.send_personal_message(
                        user_id,
                        {"event": "error", "data": {"message": "Voice channel not accessible"}},
                    )
                    continue

                participants = manager.get_voice_participants(channel_id)
                await manager.send_personal_message(
                    user_id,
                    {"event": "voice.participants", "data": {"channel_id": channel_id, "user_ids": sorted(participants)}},
                )
            elif action == "call.signal":
                try:
                    channel_id = int(payload.get("channel_id"))
                    target_user_id = int(payload.get("target_user_id"))
                except Exception:
                    await manager.send_personal_message(
                        user_id,
                        {"event": "error", "data": {"message": "Invalid call signal payload"}},
                    )
                    continue

                signal_type = str(payload.get("signal_type") or "").lower()
                if signal_type not in {"offer", "answer", "ice"}:
                    await manager.send_personal_message(
                        user_id,
                        {"event": "error", "data": {"message": "Invalid signal type"}},
                    )
                    continue

                async with AsyncSessionLocal() as db:
                    channel = await _resolve_accessible_channel(db, user_id, channel_id)
                    if channel is None:
                        await manager.send_personal_message(
                            user_id,
                            {"event": "error", "data": {"message": "Call channel not accessible"}},
                        )
                        continue
                    if channel.type not in {ChannelType.voice, ChannelType.dm}:
                        await manager.send_personal_message(
                            user_id,
                            {"event": "error", "data": {"message": "Calls are only allowed in voice channels or DMs"}},
                        )
                        continue

                    target_access = await db.execute(
                        select(Channel.id)
                        .join(DMParticipant, isouter=True)
                        .where(Channel.id == channel_id)
                        .where(
                            and_(
                                Channel.type == ChannelType.dm,
                                DMParticipant.user_id == target_user_id,
                            )
                            if channel.type == ChannelType.dm
                            else Channel.id == channel_id
                        )
                    )
                    if channel.type == ChannelType.dm and target_access.scalar_one_or_none() is None:
                        await manager.send_personal_message(
                            user_id,
                            {"event": "error", "data": {"message": "Target user cannot access this call"}},
                        )
                        continue

                if not manager.is_user_in_voice_channel(user_id, channel_id):
                    await manager.send_personal_message(
                        user_id,
                        {"event": "error", "data": {"message": "Join voice channel before signaling"}},
                    )
                    continue
                if not manager.is_user_in_voice_channel(target_user_id, channel_id):
                    await manager.send_personal_message(
                        user_id,
                        {"event": "error", "data": {"message": "Target user is not in this voice channel"}},
                    )
                    continue

                await manager.send_personal_message(
                    target_user_id,
                    {
                        "event": "call.signal",
                        "data": {
                            "channel_id": channel_id,
                            "from_user_id": user_id,
                            "signal_type": signal_type,
                            "payload": payload.get("payload"),
                        },
                    },
                )
            elif action == "ping":
                await manager.send_personal_message(user_id, {"event": "pong"})
            else:
                await manager.send_personal_message(
                    user_id,
                    {"event": "error", "data": {"message": "Unknown action"}},
                )
    except WebSocketDisconnect:
        await _handle_disconnect(user_id)
    except Exception:
        await _handle_disconnect(user_id)
        await websocket.close(code=1011)
