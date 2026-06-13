from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from jose import JWTError, jwt

from app.config import get_settings
from app.models import User
from app.schemas.wyv import WyvBridgeUserOut
from app.services.redis_client import get_redis


settings = get_settings()
WYV_HANDOFF_TYPE = "wyv-handoff"
WYV_BRIDGE_SIGNATURE_TTL_SECONDS = 300
WYV_HANDOFF_CONSUMED_PREFIX = "wyv:handoff:used:"


def wyv_bridge_is_configured() -> bool:
    return bool(settings.wyv_shared_secret)


def _secret() -> str:
    if not settings.wyv_shared_secret:
        raise RuntimeError("WYV_SHARED_SECRET is required for Wyv bridge operations")
    return settings.wyv_shared_secret


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, default=_json_default, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _signature(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()


def build_wyv_bridge_headers(body: bytes) -> dict[str, str]:
    timestamp = str(int(datetime.now(tz=UTC).timestamp()))
    return {
        "X-Wyv-Bridge-Timestamp": timestamp,
        "X-Wyv-Bridge-Signature": _signature(_secret(), timestamp, body),
        "Content-Type": "application/json",
    }


def verify_wyv_bridge_signature(body: bytes, timestamp: str | None, signature: str | None) -> None:
    if not timestamp or not signature:
        raise ValueError("Missing Wyv bridge authentication headers")
    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise ValueError("Invalid Wyv bridge timestamp") from exc

    now = int(datetime.now(tz=UTC).timestamp())
    if abs(now - sent_at) > WYV_BRIDGE_SIGNATURE_TTL_SECONDS:
        raise ValueError("Expired Wyv bridge signature")

    expected = _signature(_secret(), timestamp, body)
    if not hmac.compare_digest(expected, signature):
        raise ValueError("Invalid Wyv bridge signature")


def serialize_wyv_user(user: User) -> dict[str, Any]:
    return WyvBridgeUserOut(
        user_id=str(user.id),
        sync_id=str(getattr(user, "sync_id", "") or "") or None,
        username=str(user.username),
        discriminator=str(user.discriminator),
        display_name=user.display_name,
        email=str(user.email),
        avatar=user.avatar,
        bio=user.bio,
        directory_opt_in=bool(user.directory_opt_in),
        ai_opt_in=bool(user.ai_opt_in),
        nsfw_18_verified=bool(user.nsfw_18_verified),
    ).model_dump(mode="json")


def create_wyv_handoff_grant(user: User) -> tuple[str, datetime]:
    now = datetime.now(tz=UTC)
    expires_at = now + timedelta(seconds=max(30, settings.wyv_handoff_ttl_seconds))
    payload = {
        "sub": str(user.id),
        "type": WYV_HANDOFF_TYPE,
        "jti": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "user": serialize_wyv_user(user),
    }
    grant = jwt.encode(payload, _secret(), algorithm=settings.jwt_algorithm)
    return grant, expires_at


def decode_wyv_handoff_grant(grant: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(grant, _secret(), algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("Invalid Wyv handoff grant") from exc
    if payload.get("type") != WYV_HANDOFF_TYPE:
        raise ValueError("Invalid Wyv handoff token type")
    return payload


async def consume_wyv_handoff_grant(grant_payload: dict[str, Any]) -> None:
    jti = str(grant_payload.get("jti") or "").strip()
    if not jti:
        raise ValueError("Missing Wyv handoff grant id")
    ttl = max(30, int(grant_payload.get("exp", 0)) - int(datetime.now(tz=UTC).timestamp()))
    used = await get_redis().set(f"{WYV_HANDOFF_CONSUMED_PREFIX}{jti}", "1", ex=ttl, nx=True)
    if not used:
        raise ValueError("Wyv handoff grant has already been used")
