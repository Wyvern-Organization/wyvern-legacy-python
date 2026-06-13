from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.main import app
from app.models import ApiToken
from app.routers import ai as ai_router
from app.routers import openai as openai_router
from app.routers import runtime as runtime_router
from app.services.api_tokens import (
    decrypt_api_token_secret,
    encrypt_api_token_secret,
    generate_api_token_secret,
    get_current_api_token,
    hash_api_token_secret,
)


class FakeResult:
    def __init__(self, value=None, values=None):
        self._value = value
        self._values = values or []

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._values


class FakeDB:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.commits = 0
        self.flushes = 0

    async def execute(self, _statement):
        if self.results:
            return self.results.pop(0)
        return FakeResult()

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_api_token_secret_encryption_round_trip() -> None:
    secret = generate_api_token_secret()
    ciphertext = encrypt_api_token_secret(secret)

    assert decrypt_api_token_secret(ciphertext) == secret
    assert hash_api_token_secret(secret) != hash_api_token_secret(f"{secret}x")


@pytest.mark.asyncio
async def test_get_current_api_token_updates_last_used() -> None:
    secret = generate_api_token_secret()
    token = ApiToken(
        id="token_1",
        user_id="user_1",
        name="Test Token",
        token_hash=hash_api_token_secret(secret),
        token_ciphertext=encrypt_api_token_secret(secret),
        created_at=datetime.now(tz=UTC),
    )
    db = FakeDB(results=[FakeResult(token)])

    resolved = await get_current_api_token(
        credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=secret),
        db=db,
    )

    assert resolved is token
    assert token.last_used_at is not None
    assert db.commits == 1


@pytest.mark.asyncio
async def test_get_current_api_token_rejects_invalid_secret() -> None:
    db = FakeDB(results=[FakeResult(None)])

    with pytest.raises(HTTPException) as exc:
        await get_current_api_token(
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="wy_ai_invalid"),
            db=db,
        )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_admin_blocks_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_is_admin(*_args, **_kwargs):
        return False

    monkeypatch.setattr(ai_router.admin_allowlist_service, "is_admin", fake_is_admin)

    with pytest.raises(HTTPException) as exc:
        await ai_router.require_admin(current_user=SimpleNamespace(username="Axel", discriminator="1234"))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_openai_routes_are_disabled_and_point_to_wyv() -> None:
    app.dependency_overrides[openai_router.get_current_api_token] = lambda: SimpleNamespace(id="token_1")

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            models = await client.get("/openai/v1/models")
            assert models.status_code == 410
            assert models.headers["cache-control"] == "no-store"
            assert models.json()["error"]["code"] == "GATEWAY_MOVED"
            assert "Wyv host" in models.json()["error"]["message"]

            chat = await client.post(
                "/openai/v1/chat/completions",
                json={"model": "wyv-nano", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert chat.status_code == 410
            assert chat.headers["cache-control"] == "no-store"

            responses = await client.post(
                "/openai/v1/responses",
                json={"model": "wyv-nano", "input": "hello"},
            )
            assert responses.status_code == 410
            assert responses.headers["cache-control"] == "no-store"
    finally:
        app.dependency_overrides.pop(openai_router.get_current_api_token, None)


@pytest.mark.asyncio
async def test_runtime_config_exposes_wyv_public_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_router.settings, "wyv_public_base_url", "https://wyv.example", raising=False)
    monkeypatch.setattr(runtime_router.settings, "edge_mode_enabled", False, raising=False)
    monkeypatch.setattr(runtime_router.settings, "node_role", "main", raising=False)
    monkeypatch.setattr(runtime_router.settings, "node_id", "node-1", raising=False)
    monkeypatch.setattr(runtime_router.settings, "indexing", False, raising=False)
    monkeypatch.setattr(runtime_router.settings, "sync_peer_api_url", None, raising=False)
    monkeypatch.setattr(runtime_router.settings, "sync_enabled", True, raising=False)
    monkeypatch.setattr(runtime_router.settings, "giphy_api_key", None, raising=False)
    monkeypatch.setattr(runtime_router.settings, "giphy_rating", "pg-13", raising=False)
    monkeypatch.setattr(runtime_router.settings, "giphy_limit", 25, raising=False)

    async def fake_load_release_flags(_db):
        return {}

    async def fake_build_bridge_health(_db):
        return {
            "configured": True,
            "node_role": "main",
            "peer_url": None,
            "sync_enabled": True,
            "edge_mode_enabled": False,
            "edge_mode_available": False,
            "pending_outbox": 0,
            "dead_letter_outbox": 0,
            "last_outbox_delivery_at": None,
            "last_inbound_at": None,
        }

    monkeypatch.setattr(runtime_router, "load_release_flags", fake_load_release_flags)
    monkeypatch.setattr(runtime_router, "build_bridge_health", fake_build_bridge_health)
    monkeypatch.setattr(runtime_router, "resolve_feature_flags", lambda _flags, _channel: {})
    monkeypatch.setattr(runtime_router, "resolve_release_channel", lambda _mode: "stable")
    monkeypatch.setattr(runtime_router, "edge_mode_is_available", lambda: False)
    monkeypatch.setattr(
        runtime_router,
        "legal_metadata",
        lambda _base_url: {
            "terms_version": "2026-01",
            "privacy_version": "2026-01",
            "effective_date": "2026-01-01",
            "effective_date_label": "January 1, 2026",
            "terms_url": "https://wyvern.example/terms",
            "privacy_url": "https://wyvern.example/privacy",
            "legal_contact_email": "legal@wyvern.example",
            "support_contact_email": "support@wyvern.example",
            "operator_name": "Wyvern",
        },
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/runtime-config",
            "headers": [],
            "query_string": b"",
            "scheme": "https",
            "server": ("wyvern.example", 443),
            "client": ("127.0.0.1", 12345),
        }
    )

    payload = await runtime_router.runtime_config(request=request, mode="stable", db=FakeDB())

    assert payload["success"] is True
    assert payload["data"]["wyv_public_base_url"] == "https://wyv.example"
