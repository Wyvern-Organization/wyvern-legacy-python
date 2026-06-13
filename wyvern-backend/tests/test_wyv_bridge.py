from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.main import app
from app.routers import auth as auth_router
from app.routers import wyv_internal
from app.services import wyv_bridge


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True


class FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, result_value=None):
        self.result_value = result_value
        self.commits = 0

    async def execute(self, _statement):
        return FakeResult(self.result_value)

    async def commit(self):
        self.commits += 1


class FakeSessionContext:
    def __init__(self, db: FakeDB):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


def fake_user(user_id: str = "user_1") -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        sync_id=f"sync-{user_id}",
        username="axel",
        discriminator="1234",
        display_name="Axel",
        email="axel@example.com",
        avatar="https://example.com/avatar.png",
        bio="Builder",
        directory_opt_in=True,
        ai_opt_in=True,
        nsfw_18_verified=False,
    )


@pytest.mark.asyncio
async def test_wyv_bridge_signature_and_replay_protection(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(wyv_bridge.settings, "wyv_shared_secret", "shared-secret", raising=False)
    monkeypatch.setattr(wyv_bridge, "get_redis", lambda: redis)

    body = wyv_bridge.json_bytes({"hello": "world"})
    headers = wyv_bridge.build_wyv_bridge_headers(body)
    wyv_bridge.verify_wyv_bridge_signature(
        body,
        headers["X-Wyv-Bridge-Timestamp"],
        headers["X-Wyv-Bridge-Signature"],
    )

    grant, _expires_at = wyv_bridge.create_wyv_handoff_grant(fake_user())
    payload = wyv_bridge.decode_wyv_handoff_grant(grant)

    await wyv_bridge.consume_wyv_handoff_grant(payload)
    with pytest.raises(ValueError, match="already been used"):
        await wyv_bridge.consume_wyv_handoff_grant(payload)


@pytest.mark.asyncio
async def test_wyv_handoff_route_returns_signed_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides[auth_router.get_current_active_user] = lambda: fake_user()
    monkeypatch.setattr(auth_router, "wyv_bridge_is_configured", lambda: True)
    monkeypatch.setattr(
        auth_router,
        "create_wyv_handoff_grant",
        lambda _user: ("signed-grant", datetime(2026, 1, 1, tzinfo=UTC)),
    )

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/v1/auth/wyv-handoff")
    finally:
        app.dependency_overrides.pop(auth_router.get_current_active_user, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["grant"] == "signed-grant"


@pytest.mark.asyncio
async def test_internal_session_exchange_accepts_signed_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(wyv_bridge.settings, "wyv_shared_secret", "shared-secret", raising=False)
    monkeypatch.setattr(wyv_bridge, "get_redis", lambda: redis)

    db = FakeDB(result_value=None)
    monkeypatch.setattr(wyv_internal, "AsyncSessionLocal", lambda: FakeSessionContext(db))

    grant, _expires_at = wyv_bridge.create_wyv_handoff_grant(fake_user("user_bridge"))
    body = wyv_bridge.json_bytes({"grant": grant})
    headers = wyv_bridge.build_wyv_bridge_headers(body)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/internal/wyv/session-exchange", content=body, headers=headers)
        replay = await client.post("/internal/wyv/session-exchange", content=body, headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["user"]["user_id"] == "user_bridge"
    assert replay.status_code == 401
    replay_payload = replay.json()
    message = replay_payload.get("detail") or replay_payload.get("error", {}).get("message", "")
    assert "already been used" in message


@pytest.mark.asyncio
async def test_internal_api_token_introspection_returns_user_and_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wyv_bridge.settings, "wyv_shared_secret", "shared-secret", raising=False)

    db = FakeDB(result_value=fake_user("user_token"))
    monkeypatch.setattr(wyv_internal, "AsyncSessionLocal", lambda: FakeSessionContext(db))

    async def fake_get_active_api_token_by_secret(_db, secret: str):
        if secret != "wyvern-secret":
            return None
        return SimpleNamespace(id="token_1", name="Primary", user_id="user_token")

    monkeypatch.setattr(wyv_internal, "get_active_api_token_by_secret", fake_get_active_api_token_by_secret)

    body = wyv_bridge.json_bytes({"token": "wyvern-secret"})
    headers = wyv_bridge.build_wyv_bridge_headers(body)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/internal/wyv/api-token-introspect", content=body, headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["active"] is True
    assert payload["data"]["token_id"] == "token_1"
    assert payload["data"]["user"]["user_id"] == "user_token"
    assert db.commits == 1
