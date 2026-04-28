import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import ServerActivityLog
from app.schemas.sync import ReplicationBatchIn
from app.services.live_bridge import LIVE_SYNC_SCHEMA_VERSION, verify_live_bridge_signature
from app.services.presence import presence_service
from app.services.pubsub import publish_channel_event, publish_user_event
from app.services.realtime import broadcast_presence_update
from app.services.sync_bridge import apply_replication_events, bridge_is_configured, build_bootstrap_events, verify_bridge_signature
from app.services.voice_realtime import apply_remote_voice_join, apply_remote_voice_leave, deliver_remote_call_signal
from app.utils.responses import success_response


router = APIRouter(prefix="/internal/sync", tags=["sync"])
settings = get_settings()


def _verify_bridge_request(request: Request, body: bytes) -> None:
    if not bridge_is_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sync bridge is not configured")
    try:
        verify_bridge_signature(
            body,
            request.headers.get("X-Wyvern-Bridge-Timestamp"),
            request.headers.get("X-Wyvern-Bridge-Signature"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


def _verify_live_bridge_request(request: Request, body: bytes) -> None:
    if not bridge_is_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Realtime bridge is not configured")
    try:
        verify_live_bridge_signature(
            body,
            request.headers.get("X-Wyvern-Bridge-Timestamp"),
            request.headers.get("X-Wyvern-Bridge-Signature"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _persist_realtime_activity(payload: dict) -> None:
    if payload.get("event") != "server.activity.created":
        return
    data = payload.get("data") or {}
    activity_id = str(data.get("id") or "")
    if not activity_id:
        return

    async with AsyncSessionLocal() as db:
        if await db.get(ServerActivityLog, activity_id):
            return
        entry = ServerActivityLog(
            id=activity_id,
            server_id=data.get("server_id"),
            actor_user_id=data.get("actor_user_id"),
            action=str(data.get("action") or "activity"),
            target_type=data.get("target_type"),
            target_id=data.get("target_id"),
            activity_metadata=data.get("activity_metadata"),
        )
        created_at = _parse_datetime(data.get("created_at"))
        if created_at is not None:
            entry.created_at = created_at
        db.add(entry)
        try:
            await db.commit()
        except Exception:
            await db.rollback()


@router.post("/batch")
async def apply_sync_batch(request: Request) -> dict:
    raw_body = await request.body()
    _verify_bridge_request(request, raw_body)
    payload = ReplicationBatchIn.model_validate_json(raw_body)
    results = await apply_replication_events([item.model_dump() for item in payload.events])
    return success_response({"results": results})


@router.post("/bootstrap")
async def bootstrap_sync_snapshot(request: Request) -> dict:
    raw_body = await request.body()
    _verify_bridge_request(request, raw_body)
    if settings.node_role != "main":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bootstrap snapshots are only available from main")
    events = await build_bootstrap_events()
    return success_response({"events": events})


@router.post("/realtime")
async def apply_realtime_event(request: Request) -> dict:
    raw_body = await request.body()
    _verify_live_bridge_request(request, raw_body)
    try:
        envelope = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid realtime bridge payload") from exc

    if int(envelope.get("schema_version") or 0) != LIVE_SYNC_SCHEMA_VERSION:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported realtime bridge schema version")

    event = envelope.get("event") or {}
    kind = str(event.get("kind") or "")

    if kind == "channel":
        channel_id = str(event.get("channel_id") or "")
        payload = event.get("payload")
        if channel_id and isinstance(payload, dict):
            await publish_channel_event(
                channel_id,
                payload=payload,
                extra_user_ids={str(item) for item in event.get("extra_user_ids", []) if str(item)},
                bridge=False,
            )
    elif kind == "users":
        payload = event.get("payload")
        user_ids = {str(item) for item in event.get("user_ids", []) if str(item)}
        if user_ids and isinstance(payload, dict):
            await _persist_realtime_activity(payload)
            await publish_user_event(user_ids, payload=payload, bridge=False)
    elif kind == "presence":
        user_id = str(event.get("user_id") or "")
        status_value = str(event.get("status") or "online")
        if user_id:
            normalized = await presence_service.set_presence(user_id, status_value)
            await broadcast_presence_update(user_id, normalized)
    elif kind == "voice.join":
        user_id = str(event.get("user_id") or "")
        channel_id = str(event.get("channel_id") or "")
        if user_id and channel_id:
            user = event.get("user")
            await apply_remote_voice_join(user_id, channel_id, user if isinstance(user, dict) else None)
    elif kind == "voice.leave":
        user_id = str(event.get("user_id") or "")
        channel_id = str(event.get("channel_id") or "") or None
        if user_id:
            await apply_remote_voice_leave(user_id, channel_id)
    elif kind == "call.signal":
        await deliver_remote_call_signal(
            channel_id=str(event.get("channel_id") or ""),
            from_user_id=str(event.get("from_user_id") or ""),
            target_user_id=str(event.get("target_user_id") or ""),
            signal_type=str(event.get("signal_type") or ""),
            payload=event.get("payload"),
        )
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported realtime bridge event")

    return success_response({"ok": True})
