from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.datastructures import Headers, UploadFile

from app.models import User
from app.routers import admin, users, webhooks
from app.schemas.auth import RegisterRequest
from app.schemas.community import WebhookMessageRequest
from app.schemas.message import MessageCreate
from app.schemas.server import ServerCreate
from app.services import rate_limiter as rate_limiter_module
from app.services.admin_allowlist import AdminAllowlistService
from app.services.community import hash_secret_token
from app.services.legal import TERMS_VERSION, PRIVACY_VERSION, apply_current_legal_acceptance, user_requires_legal_reacceptance
from app.services.storage import inspect_upload, upload_file_to_storage
from app.services.sync_bridge import (
    _apply_user_sync_payload,
    _build_user_from_sync_payload,
    _handoff_secret,
    _serialize_user,
)
from app.utils.security import create_access_token, decode_token, hash_password, verify_password


PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"


def make_upload(filename: str, content_type: str, content: bytes) -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def test_access_token_round_trip() -> None:
    token = create_access_token(123)
    payload = decode_token(token)
    assert payload["sub"] == "123"
    assert payload["type"] == "access"


def test_password_hash_round_trip() -> None:
    password = "correct horse battery"  # pragma: allowlist secret
    hashed = hash_password(password)

    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("wrong horse battery", hashed) is False


@pytest.mark.parametrize("user_id", [1, 2, 99])
def test_access_token_subject(user_id: int) -> None:
    token = create_access_token(user_id)
    payload = decode_token(token)
    assert payload["sub"] == str(user_id)


def test_username_rejects_handle_separator_and_controls() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(
            username="admin#0001",
            email="admin@example.com",
            password="correct horse battery",
            accepted_legal=True,
            terms_version=TERMS_VERSION,
            privacy_version=PRIVACY_VERSION,
        )  # pragma: allowlist secret

    with pytest.raises(ValidationError):
        users.UserUpdateRequest(username="bad\nname")


def test_legal_acceptance_helpers_track_current_versions() -> None:
    user = SimpleNamespace(accepted_terms_version=None, accepted_privacy_version=None, legal_accepted_at=None)

    assert user_requires_legal_reacceptance(user) is True

    apply_current_legal_acceptance(user)

    assert user.accepted_terms_version == TERMS_VERSION
    assert user.accepted_privacy_version == PRIVACY_VERSION
    assert user.legal_accepted_at is not None
    assert user_requires_legal_reacceptance(user) is False


def test_public_url_fields_reject_active_or_local_schemes() -> None:
    with pytest.raises(ValidationError):
        MessageCreate(content="x", attachments=["javascript:alert(1).png"])

    with pytest.raises(ValidationError):
        WebhookMessageRequest(content="x", avatar_url="data:image/svg+xml,<svg></svg>")

    with pytest.raises(ValidationError):
        ServerCreate(name="Test Server", icon="file:///etc/passwd")

    payload = MessageCreate(content="x", attachments=["/media/user_1/avatar.png", "https://example.com/file.pdf"])
    assert payload.attachments == ["/media/user_1/avatar.png", "https://example.com/file.pdf"]


@pytest.mark.asyncio
async def test_upload_rejects_spoofed_svg_png() -> None:
    upload = make_upload("avatar.PNG", "image/png", b"<SVG onload=alert(1)></SVG>")

    with pytest.raises(HTTPException) as exc:
        await inspect_upload(upload)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_rejects_blocked_extension() -> None:
    upload = make_upload("avatar.SVG", "image/svg+xml", b"<svg></svg>")

    with pytest.raises(HTTPException):
        await inspect_upload(upload)


@pytest.mark.asyncio
async def test_upload_storage_uses_safe_generated_path(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    settings = SimpleNamespace(
        media_url_prefix="/media",
        resolve_media_dir=lambda _project_root: tmp_path,
    )
    monkeypatch.setattr("app.services.storage.get_settings", lambda: settings)
    upload = make_upload("../evil.png", "image/png", PNG_BYTES)

    url = await upload_file_to_storage(upload, "user_abc")

    assert url.startswith("/media/user_abc/")
    assert "evil" not in url
    assert url.endswith(".png")
    stored_path = tmp_path / url.removeprefix("/media/")
    assert stored_path.resolve().is_relative_to(tmp_path.resolve())
    assert stored_path.exists()


@pytest.mark.asyncio
async def test_upload_storage_rejects_unsafe_user_path(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    settings = SimpleNamespace(
        media_url_prefix="/media",
        resolve_media_dir=lambda _project_root: tmp_path,
    )
    monkeypatch.setattr("app.services.storage.get_settings", lambda: settings)
    upload = make_upload("ok.png", "image/png", PNG_BYTES)

    with pytest.raises(HTTPException):
        await upload_file_to_storage(upload, "../escape")


def test_edge_handoff_requires_sync_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.sync_bridge.settings",
        SimpleNamespace(sync_shared_secret=None, jwt_secret_key="jwt-secret-that-must-not-be-used"),  # pragma: allowlist secret
    )

    with pytest.raises(RuntimeError):
        _handoff_secret()


@pytest.mark.asyncio
async def test_sync_bridge_omits_and_ignores_is_paid() -> None:
    user = User(
        id="user_1",
        sync_id="sync_1",
        username="Axel",
        discriminator="1234",
        email="axel@example.com",
        password_hash="hash",  # pragma: allowlist secret
        is_paid=True,
        created_at=datetime.now(tz=UTC),
    )
    serialized = await _serialize_user(SimpleNamespace(), user)
    assert "is_paid" not in serialized

    _apply_user_sync_payload(user, {"username": "Axel2", "is_paid": False})
    assert user.username == "Axel2"
    assert user.is_paid is True

    created = _build_user_from_sync_payload(
        {"entity_sync_id": "sync_new"},
        {"username": "Edge", "discriminator": "0007", "email": "edge@example.com", "is_paid": True},
    )
    assert created.is_paid is False


@pytest.mark.asyncio
async def test_admin_allowlist_exact_handle_only() -> None:
    settings = SimpleNamespace(admin_allowlist="Axel#1234,Other#0001")
    service = AdminAllowlistService(settings_provider=lambda: settings)

    assert await service.is_admin("axel", "1234") is True
    assert await service.is_admin("Axel", "9999") is False
    assert await service.is_admin("Other", "0001") is True


@pytest.mark.asyncio
async def test_username_change_collision_regenerates_discriminator(monkeypatch: pytest.MonkeyPatch) -> None:
    current_user = SimpleNamespace(id="user_1", username="Old", discriminator="1234")

    async def fake_taken(*_args, **_kwargs):
        return True

    async def fake_generate(*_args, **_kwargs):
        return "5678"

    monkeypatch.setattr(users, "username_discriminator_taken", fake_taken)
    monkeypatch.setattr(users, "generate_discriminator", fake_generate)

    await users.apply_username_update(SimpleNamespace(), current_user, "New")

    assert current_user.username == "New"
    assert current_user.discriminator == "5678"


def test_admin_overview_user_payload_redacts_email() -> None:
    user = SimpleNamespace(
        id="user_1",
        username="Axel",
        discriminator="1234",
        display_name="Axel",
        bio=None,
        directory_opt_in=False,
        avatar=None,
        is_paid=False,
        email="axel@example.com",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    payload = admin._serialize_admin_user(user)

    assert "email" not in payload


@pytest.mark.asyncio
async def test_webhook_invocation_uses_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class FakeLimiter:
        async def check(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(webhooks, "rate_limiter", FakeLimiter())
    monkeypatch.setattr(
        webhooks,
        "settings",
        SimpleNamespace(rate_limit_webhook_count=30, rate_limit_webhook_window_seconds=60),
    )

    await webhooks._throttle_webhook_invocation("webhook_1")

    assert calls == [
        {
            "key_prefix": "webhooks",
            "actor_id": "webhook_1",
            "limit": 30,
            "window_seconds": 60,
        }
    ]


def test_webhook_rotation_invalidates_old_token() -> None:
    old_token = "old-token"
    webhook = SimpleNamespace(
        token_hash=hash_secret_token(old_token),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    new_token = webhooks._rotate_webhook_secret(webhook)

    assert new_token != old_token
    assert webhook.token_hash != hash_secret_token(old_token)
    assert webhook.token_hash == hash_secret_token(new_token)


@pytest.mark.asyncio
async def test_rate_limiter_uses_redis_lua(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class FakeRedis:
        async def eval(self, script, key_count, key, window_seconds):
            calls.append((script, key_count, key, window_seconds))
            return 1

    monkeypatch.setattr(rate_limiter_module, "get_redis", lambda: FakeRedis())

    await rate_limiter_module.rate_limiter.check("messages", "user_1", 5, 60)

    assert "INCR" in calls[0][0]
    assert calls[0][1:] == (1, "rl:messages:user_1:60", 60)
