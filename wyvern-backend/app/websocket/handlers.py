from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import and_, select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Channel, ChannelType, DMParticipant, ServerMember, User
from app.models.enums import PresenceStatus
from app.services.access import ensure_channel_access
from app.services.live_bridge import queue_realtime_event
from app.services.presence import presence_service
from app.services.realtime import broadcast_presence_update
from app.services.voice_realtime import bridge_call_signal, bridge_voice_join, bridge_voice_leave, emit_voice_participants, voice_participants_payload
from app.utils.security import TokenError, decode_token
from app.websocket.manager import manager


settings = get_settings()


async def _auth_websocket_user(token: str | None) -> str:
    if token is None:
        raise TokenError("Missing token")

    payload = decode_token(token)
    if payload.get("type") != "access":
        raise TokenError("Invalid token type")

    user_id = payload.get("sub")
    if user_id is None:
        raise TokenError("Invalid token subject")

    return str(user_id)


async def _resolve_accessible_channel(db, user_id: str, channel_id: str) -> Channel | None:
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if channel is None:
        return None

    try:
        await ensure_channel_access(db, channel, user_id)
    except Exception:
        return None
    return channel


async def _voice_recipients_for_channel(db, channel: Channel) -> set[str]:
    if channel.type == ChannelType.dm:
        result = await db.execute(select(DMParticipant.user_id).where(DMParticipant.channel_id == channel.id))
        return set(result.scalars().all())

    if channel.server_id is None:
        return set()

    result = await db.execute(select(ServerMember.user_id).where(ServerMember.server_id == channel.server_id))
    return set(result.scalars().all())


async def _emit_voice_participants(channel_id: str, participants: set[str]) -> None:
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


async def _handle_disconnect(user_id: str) -> None:
    left_channel_id, remaining = await manager.disconnect(user_id)
    await broadcast_presence_update(user_id, PresenceStatus.offline)
    queue_realtime_event({"kind": "presence", "user_id": user_id, "status": PresenceStatus.offline.value})
    if left_channel_id is not None:
        await emit_voice_participants(left_channel_id)
        bridge_voice_leave(user_id, left_channel_id)


async def websocket_endpoint(websocket: WebSocket) -> None:
    if settings.indexing:
        await websocket.accept()
        await websocket.send_json({"event": "error", "data": {"code": "INDEXING_MODE", "message": "Indexing..."}})
        await websocket.close(code=1013)
        return

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
    await broadcast_presence_update(user_id, PresenceStatus.online)
    queue_realtime_event({"kind": "presence", "user_id": user_id, "status": PresenceStatus.online.value})

    try:
        while True:
            payload = await websocket.receive_json()
            action = payload.get("action")

            if action == "subscribe":
                channel_ids = [str(item) for item in payload.get("channel_ids", []) if str(item)]
                allowed: list[str] = []

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
                channel_ids = [str(item) for item in payload.get("channel_ids", []) if str(item)]
                manager.unsubscribe(user_id, channel_ids)
                await manager.send_personal_message(
                    user_id,
                    {"event": "unsubscribed", "data": {"channel_ids": channel_ids}},
                )
            elif action == "presence":
                status = payload.get("status", "online")
                try:
                    normalized = await presence_service.set_presence(user_id, status=status)
                    await broadcast_presence_update(user_id, normalized)
                    queue_realtime_event({"kind": "presence", "user_id": user_id, "status": normalized.value})
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
                channel_id = str(payload.get("channel_id") or "")
                if not channel_id:
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
                await emit_voice_participants(channel_id)
                await bridge_voice_join(user_id, channel_id)
                if previous_channel_id is not None and previous_channel_id != channel_id:
                    await emit_voice_participants(previous_channel_id)
                    bridge_voice_leave(user_id, previous_channel_id)
            elif action == "leave_voice":
                channel_raw = payload.get("channel_id")
                channel_id: str | None = None
                if channel_raw is not None:
                    channel_id = str(channel_raw) or None

                left_channel_id, remaining = manager.leave_voice(user_id, channel_id)
                if left_channel_id is not None:
                    await emit_voice_participants(left_channel_id)
                    bridge_voice_leave(user_id, left_channel_id)
            elif action == "voice.status":
                channel_id = str(payload.get("channel_id") or "")
                if not channel_id:
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

                if manager.is_local_user_in_voice_channel(user_id, channel_id):
                    await bridge_voice_join(user_id, channel_id)
                await manager.send_personal_message(
                    user_id,
                    {"event": "voice.participants", "data": await voice_participants_payload(channel_id)},
                )
            elif action == "call.signal":
                channel_id = str(payload.get("channel_id") or "")
                target_user_id = str(payload.get("target_user_id") or "")
                if not channel_id or not target_user_id:
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

                if not manager.is_local_user_in_voice_channel(user_id, channel_id):
                    await manager.send_personal_message(
                        user_id,
                        {"event": "error", "data": {"message": "Join voice channel before signaling"}},
                    )
                    continue
                target_is_local = manager.is_local_user_in_voice_channel(target_user_id, channel_id)
                target_is_remote = manager.is_remote_user_in_voice_channel(target_user_id, channel_id)
                if not target_is_local and not target_is_remote:
                    await manager.send_personal_message(
                        user_id,
                        {"event": "error", "data": {"message": "Target user is not in this voice channel"}},
                    )
                    continue

                if target_is_local:
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
                else:
                    bridge_call_signal(
                        channel_id=channel_id,
                        from_user_id=user_id,
                        target_user_id=target_user_id,
                        signal_type=signal_type,
                        payload=payload.get("payload"),
                    )
            elif action == "ping":
                current_presence = await presence_service.get_presence(user_id)
                queue_realtime_event({"kind": "presence", "user_id": user_id, "status": current_presence.value})
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
