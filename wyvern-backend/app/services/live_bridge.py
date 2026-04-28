from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import httpx

from app.config import get_settings


logger = logging.getLogger(__name__)
settings = get_settings()

LIVE_SYNC_PATH = "/internal/sync/realtime"
LIVE_SYNC_SCHEMA_VERSION = 1
LIVE_SYNC_SIGNATURE_TTL_SECONDS = 300


def live_bridge_is_configured() -> bool:
    return bool(settings.sync_enabled and settings.sync_peer_api_url and settings.sync_shared_secret)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, default=_json_default, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _signature(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()


def build_live_bridge_headers(body: bytes) -> dict[str, str]:
    if not settings.sync_shared_secret:
        raise RuntimeError("SYNC_SHARED_SECRET is required for realtime bridge requests")
    timestamp = str(int(datetime.now(tz=UTC).timestamp()))
    return {
        "X-Wyvern-Bridge-Node": settings.node_role,
        "X-Wyvern-Bridge-Timestamp": timestamp,
        "X-Wyvern-Bridge-Signature": _signature(settings.sync_shared_secret, timestamp, body),
        "Content-Type": "application/json",
    }


def verify_live_bridge_signature(body: bytes, timestamp: str | None, signature: str | None) -> None:
    if not settings.sync_shared_secret:
        raise ValueError("Realtime bridge secret is not configured")
    if not timestamp or not signature:
        raise ValueError("Missing realtime bridge authentication headers")
    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise ValueError("Invalid realtime bridge timestamp") from exc

    now = int(datetime.now(tz=UTC).timestamp())
    if abs(now - sent_at) > LIVE_SYNC_SIGNATURE_TTL_SECONDS:
        raise ValueError("Expired realtime bridge signature")

    expected = _signature(settings.sync_shared_secret, timestamp, body)
    if not hmac.compare_digest(expected, signature):
        raise ValueError("Invalid realtime bridge signature")


async def send_realtime_event(event: dict[str, Any]) -> None:
    if not live_bridge_is_configured():
        return

    body = _json_bytes(
        {
            "schema_version": LIVE_SYNC_SCHEMA_VERSION,
            "source_node": settings.node_role,
            "event": event,
        }
    )
    url = urljoin(f"{settings.sync_peer_api_url.rstrip('/')}/", LIVE_SYNC_PATH.lstrip("/"))
    headers = build_live_bridge_headers(body)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
            response = await client.post(url, content=body, headers=headers)
            response.raise_for_status()
    except Exception as exc:
        logger.debug("Realtime bridge delivery failed: %s", exc)


def queue_realtime_event(event: dict[str, Any]) -> None:
    if not live_bridge_is_configured():
        return
    try:
        asyncio.create_task(send_realtime_event(event))
    except RuntimeError:
        logger.debug("Realtime bridge event dropped because no event loop is running")
