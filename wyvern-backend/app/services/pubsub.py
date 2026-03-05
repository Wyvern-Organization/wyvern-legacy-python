import asyncio
import json

from app.services.redis_client import get_redis
from app.websocket.manager import manager


PUBSUB_CHANNEL = "wyvern:channel-events"
_listener_task: asyncio.Task | None = None


async def publish_channel_event(channel_id: int, payload: dict, extra_user_ids: set[int] | None = None) -> None:
    redis = get_redis()
    message = {
        "channel_id": channel_id,
        "payload": payload,
        "extra_user_ids": sorted(extra_user_ids or set()),
    }
    await redis.publish(PUBSUB_CHANNEL, json.dumps(message))


async def _pubsub_listener() -> None:
    redis = get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(PUBSUB_CHANNEL)

    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                try:
                    payload = json.loads(message.get("data", "{}"))
                    channel_id = int(payload.get("channel_id"))
                    event_payload = payload.get("payload")
                    extra_user_ids = {int(item) for item in payload.get("extra_user_ids", [])}
                except Exception:
                    continue

                if channel_id > 0 and isinstance(event_payload, dict):
                    await manager.broadcast_to_channel(channel_id, event_payload, extra_user_ids=extra_user_ids)

            await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        raise
    finally:
        await pubsub.unsubscribe(PUBSUB_CHANNEL)
        await pubsub.close()


async def start_pubsub_listener() -> None:
    global _listener_task
    if _listener_task is None or _listener_task.done():
        _listener_task = asyncio.create_task(_pubsub_listener())


async def stop_pubsub_listener() -> None:
    global _listener_task
    if _listener_task is not None:
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            pass
        _listener_task = None
