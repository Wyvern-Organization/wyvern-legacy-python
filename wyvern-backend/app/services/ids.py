from __future__ import annotations

import hashlib
import os
import time
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings


ID_COLUMN_LENGTH = 96
NODE_IDS = {"aspc", "nubu"}
ENTITY_PREFIXES = {
    "user": "user",
    "server": "server",
    "channel": "channel",
    "message": "msg",
    "server_invite": "invite",
    "refresh_token": "session",  # nosec B105
    "message_bookmark": "bookmark",
    "server_webhook": "webhook",
    "webhook_delivery_log": "webhook_delivery",
    "workspace_document": "workspace",
    "workspace_revision": "workspace_rev",
    "server_activity_log": "activity",
    "recommendation_embedding": "rec_embed",
    "user_recommendation": "rec",
    "recommendation_signal": "rec_signal",
    "release_promotion_audit": "release_audit",
    "replication_outbox": "replication",
    "id_migration_map": "idmap",
}

_CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # pragma: allowlist secret


def normalize_node_id(value: str | None) -> str:
    normalized = (value or "aspc").strip().lower()
    return normalized if normalized in NODE_IDS else "aspc"


def entity_prefix(entity_type: str) -> str:
    normalized = (entity_type or "").strip().lower()
    return ENTITY_PREFIXES.get(normalized, normalized.replace("_", "-") or "record")


def _encode_ulid_bytes(raw: bytes) -> str:
    value = int.from_bytes(raw, "big")
    chars: list[str] = []
    for index in range(26):
        shift = (25 - index) * 5
        chars.append(_CROCKFORD_BASE32[(value >> shift) & 0x1F])
    return "".join(chars)


def _timestamp_ms(value: Any | None = None) -> int:
    if value is None:
        return int(time.time() * 1000)
    if isinstance(value, datetime):
        return int(value.astimezone(UTC).timestamp() * 1000)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return 0
    return int(parsed.astimezone(UTC).timestamp() * 1000)


def make_ulid(timestamp: Any | None = None, randomness: bytes | None = None) -> str:
    timestamp_bytes = max(0, _timestamp_ms(timestamp)).to_bytes(6, "big", signed=False)
    random_bytes = randomness if randomness is not None else os.urandom(10)
    return _encode_ulid_bytes(timestamp_bytes + random_bytes[:10])


def generate_entity_id(entity_type: str, *, node_id: str | None = None) -> str:
    node = normalize_node_id(node_id or get_settings().node_id)
    return f"{node}_{entity_prefix(entity_type)}_{make_ulid()}"


def deterministic_legacy_id(
    entity_type: str,
    legacy_id: int | str,
    *,
    node_id: str = "aspc",
    created_at: Any | None = None,
    seed: Any | None = None,
) -> str:
    legacy_value = str(legacy_id)
    seed_value = str(seed if seed is not None else "")
    digest = hashlib.sha256(f"{entity_type}:{legacy_value}:{seed_value}".encode("utf-8")).digest()
    return f"{normalize_node_id(node_id)}_{entity_prefix(entity_type)}_{make_ulid(created_at, digest[:10])}"
