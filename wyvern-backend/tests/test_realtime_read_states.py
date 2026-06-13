import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import WebSocketDisconnect
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import get_db
from app.main import app
from app.models import Channel, ChannelReadState, ChannelType, DMParticipant, Message, User
from app.services.legal import apply_current_legal_acceptance
from app.utils.dependencies import get_current_active_user
from app.utils.security import create_access_token
from app.websocket import handlers
from app.websocket.manager import manager


class AsyncSessionAdapter:
    def __init__(self, session: Session):
        self.session = session

    async def execute(self, stmt):
        return self.session.execute(stmt)

    def add(self, obj) -> None:
        self.session.add(obj)

    async def commit(self) -> None:
        self.session.commit()


class AsyncSessionContext:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.session = None

    async def __aenter__(self):
        self.session = self.session_factory()
        return AsyncSessionAdapter(self.session)

    async def __aexit__(self, exc_type, exc, tb):
        self.session.close()


class FakeWebSocket:
    def __init__(self, token: str, incoming: list[dict]):
        self.query_params = {"token": token}
        self._incoming = list(incoming)
        self.sent: list[dict] = []
        self.accepted = False
        self.closed_code = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def receive_json(self) -> dict:
        if not self._incoming:
            raise WebSocketDisconnect(code=1000)
        item = self._incoming.pop(0)
        if "_sleep" in item:
            await asyncio.sleep(item["_sleep"])
            return await self.receive_json()
        return item

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code


def _make_user() -> User:
    user = User(
        id="user_rt_1",
        sync_id="sync_user_rt_1",
        username="RealtimeTester",
        discriminator="1111",
        email="realtime@example.com",
        password_hash="hash",
    )
    apply_current_legal_acceptance(user)
    return user


def _make_dm_channel() -> Channel:
    return Channel(
        id="channel_dm_1",
        sync_id="sync_channel_dm_1",
        name="DM",
        type=ChannelType.dm,
        created_by="user_rt_1",
    )


@pytest.mark.asyncio
async def test_read_state_endpoints_create_and_update(tmp_path) -> None:
    db_path = tmp_path / "read_states.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    User.__table__.create(bind=engine)
    Channel.__table__.create(bind=engine)
    DMParticipant.__table__.create(bind=engine)
    Message.__table__.create(bind=engine)
    ChannelReadState.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(tz=UTC)
    with SessionLocal() as session:
        user = _make_user()
        channel = _make_dm_channel()
        session.add(user)
        session.add(channel)
        session.add(DMParticipant(user_id=user.id, channel_id=channel.id))
        session.add(Message(id="msg_1", sync_id="sync_msg_1", channel_id=channel.id, author_id=user.id, content="first", created_at=now))
        session.add(Message(id="msg_2", sync_id="sync_msg_2", channel_id=channel.id, author_id=user.id, content="second", created_at=now))
        session.commit()

    async def override_get_db():
        session = SessionLocal()
        try:
            yield AsyncSessionAdapter(session)
        finally:
            session.close()

    async def override_current_user():
        return _make_user()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_active_user] = override_current_user

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            initial = await client.get("/api/v1/users/me/read-states")
            created = await client.put("/api/v1/channels/channel_dm_1/read-state", json={"last_read_message_id": "msg_1"})
            listed = await client.get("/api/v1/users/me/read-states")
            updated = await client.put("/api/v1/channels/channel_dm_1/read-state", json={"last_read_message_id": "msg_2"})

        assert initial.status_code == 200
        assert initial.json()["data"] == []

        created_payload = created.json()["data"]
        assert created_payload["channel_id"] == "channel_dm_1"
        assert created_payload["last_read_message_id"] == "msg_1"

        listed_payload = listed.json()["data"]
        assert len(listed_payload) == 1
        assert listed_payload[0]["last_read_message_id"] == "msg_1"

        updated_payload = updated.json()["data"]
        assert updated_payload["last_read_message_id"] == "msg_2"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_websocket_typing_events_publish_and_expire(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    db_path = tmp_path / "typing.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    User.__table__.create(bind=engine)
    Channel.__table__.create(bind=engine)
    DMParticipant.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        user = _make_user()
        channel = _make_dm_channel()
        session.add(user)
        session.add(channel)
        session.add(DMParticipant(user_id=user.id, channel_id=channel.id))
        session.commit()

    async def fake_set_presence(user_id: str, status):
        return status

    async def fake_get_presence(user_id: str):
        return "online"

    async def fake_broadcast_presence_update(*args, **kwargs):
        return None

    async def fake_publish_channel_event(channel_id: str, payload: dict, extra_user_ids=None, bridge: bool = True):
        await manager.broadcast_to_channel(channel_id, payload, extra_user_ids=set(extra_user_ids or []))

    manager.active_connections.clear()
    manager.channel_subscribers.clear()
    handlers._typing_users_by_channel.clear()
    handlers._typing_channels_by_user.clear()
    handlers._typing_expiry_tokens.clear()
    for task in handlers._typing_expiry_tasks.values():
        task.cancel()
    handlers._typing_expiry_tasks.clear()

    monkeypatch.setattr(handlers, "AsyncSessionLocal", lambda: AsyncSessionContext(SessionLocal))
    monkeypatch.setattr(handlers.presence_service, "set_presence", fake_set_presence)
    monkeypatch.setattr(handlers.presence_service, "get_presence", fake_get_presence)
    monkeypatch.setattr(handlers, "broadcast_presence_update", fake_broadcast_presence_update)
    monkeypatch.setattr(handlers, "publish_channel_event", fake_publish_channel_event)
    monkeypatch.setattr(handlers, "queue_realtime_event", lambda payload: None)
    monkeypatch.setattr(handlers, "TYPING_TTL_SECONDS", 0.01)

    token = create_access_token("user_rt_1")
    websocket = FakeWebSocket(token, [
        {"action": "subscribe", "channel_ids": ["channel_dm_1"]},
        {"action": "typing", "channel_id": "channel_dm_1", "active": True},
        {"_sleep": 0.03},
    ])

    await handlers.websocket_endpoint(websocket)

    typing_events = [event for event in websocket.sent if event.get("event") == "typing.updated"]
    assert websocket.accepted is True
    assert typing_events[0]["data"]["user_ids"] == ["user_rt_1"]
    assert typing_events[-1]["data"]["user_ids"] == []
