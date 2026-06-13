from __future__ import annotations

import base64
import hashlib
import html
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import Channel, ChannelType, DMParticipant, Message, OAuthClientRegistration, Server, ServerMember, User, WorkspaceDocument
from app.models.enums import MemberRole
from app.services import mcp_server
from app.services.legal import apply_current_legal_acceptance
from app.utils.dependencies import get_current_active_user
from app.utils.security import hash_password


class AsyncSessionAdapter:
    def __init__(self, session: Session):
        self.session = session

    async def execute(self, stmt):
        return self.session.execute(stmt)

    def add(self, obj) -> None:
        self.session.add(obj)

    async def flush(self) -> None:
        self.session.flush()

    async def commit(self) -> None:
        self.session.commit()

    async def refresh(self, _obj) -> None:
        return None


class AsyncSessionContext:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.session = None

    async def __aenter__(self):
        self.session = self.session_factory()
        return AsyncSessionAdapter(self.session)

    async def __aexit__(self, exc_type, exc, tb):
        if self.session is not None:
            self.session.close()


def _make_user() -> User:
    user = User(
        id="user_mcp_1",
        sync_id="sync_user_mcp_1",
        username="McpUser",
        discriminator="2026",
        display_name="MCP User",
        email="mcp@example.com",
        password_hash=hash_password("super-secret-password"),
    )
    apply_current_legal_acceptance(user)
    return user


@pytest.mark.asyncio
async def test_mcp_connection_endpoint_returns_single_public_url(tmp_path) -> None:
    db_path = tmp_path / "mcp_connection.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    User.__table__.create(bind=engine)
    OAuthClientRegistration.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    user = _make_user()
    with SessionLocal() as session:
        session.add(user)
        session.commit()

    async def override_get_db():
        session = SessionLocal()
        try:
            yield AsyncSessionAdapter(session)
        finally:
            session.close()

    async def override_current_user():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_active_user] = override_current_user

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/ai/mcp-connection")

        assert response.status_code == 200
        payload = response.json()
        assert payload["connected"] is True
        assert payload["connection"]["server_url"] == "http://testserver/mcp"
        assert payload["connection"]["auth_method"] == "OAuth 2.1"
        assert payload["connection"]["published_ready"] is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_mcp_oauth_flow_and_tools_work_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    db_path = tmp_path / "mcp_tools.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    User.__table__.create(bind=engine)
    Server.__table__.create(bind=engine)
    Channel.__table__.create(bind=engine)
    ServerMember.__table__.create(bind=engine)
    DMParticipant.__table__.create(bind=engine)
    Message.__table__.create(bind=engine)
    WorkspaceDocument.__table__.create(bind=engine)
    OAuthClientRegistration.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(tz=UTC)
    user = _make_user()
    server = Server(
        id="server_mcp_1",
        sync_id="sync_server_mcp_1",
        name="Wyvern Labs",
        owner_id=user.id,
    )
    channel = Channel(
        id="channel_mcp_1",
        sync_id="sync_channel_mcp_1",
        server_id=server.id,
        name="research",
        type=ChannelType.text,
        created_by=user.id,
    )
    message = Message(
        id="message_mcp_1",
        sync_id="sync_message_mcp_1",
        channel_id=channel.id,
        author_id=user.id,
        content="MCP rollout notes for ChatGPT access.",
        created_at=now,
    )
    workspace = WorkspaceDocument(
        id="workspace_mcp_1",
        sync_id="sync_workspace_mcp_1",
        channel_id=channel.id,
        title="ChatGPT Setup Guide",
        content="Paste the public Wyvern MCP URL into ChatGPT and sign in with OAuth.",
        visibility="public",
        owner_user_id=None,
        updated_by_user_id=user.id,
        created_at=now,
        updated_at=now,
    )

    with SessionLocal() as session:
        session.add(user)
        session.add(server)
        session.add(ServerMember(server_id=server.id, user_id=user.id, role=MemberRole.owner))
        session.add(channel)
        session.add(message)
        session.add(workspace)
        session.commit()

    async def override_get_db():
        session = SessionLocal()
        try:
            yield AsyncSessionAdapter(session)
        finally:
            session.close()

    monkeypatch.setattr(mcp_server, "AsyncSessionLocal", lambda: AsyncSessionContext(SessionLocal))
    app.dependency_overrides[get_db] = override_get_db

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
            protected = await client.get("/.well-known/oauth-protected-resource/mcp")
            metadata = await client.get("/mcp/.well-known/oauth-authorization-server")
            unauthorized = await client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={"accept": "application/json"},
            )

            assert protected.status_code == 200
            assert protected.json()["resource"] == "http://testserver/mcp"
            assert protected.json()["authorization_servers"] == ["http://testserver/mcp"]

            assert metadata.status_code == 200
            oauth_metadata = metadata.json()
            assert oauth_metadata["issuer"] == "http://testserver/mcp"
            assert oauth_metadata["client_id_metadata_document_supported"] is True
            assert "none" in oauth_metadata["token_endpoint_auth_methods_supported"]

            assert unauthorized.status_code == 401
            assert "/.well-known/oauth-protected-resource/mcp" in unauthorized.headers["www-authenticate"]

            registration = await client.post(
                "/mcp/register",
                json={
                    "redirect_uris": ["https://chatgpt.example/callback"],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                    "scope": mcp_server.MCP_SCOPE_READ,
                    "client_name": "ChatGPT Test Client",
                },
            )
            assert registration.status_code == 201
            client_id = registration.json()["client_id"]
            mcp_server.wyvern_oauth_provider._registered_clients.clear()

            code_verifier = "mcp-test-verifier"
            code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode().rstrip("=")
            authorize = await client.get(
                "/mcp/authorize",
                params={
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": "https://chatgpt.example/callback",
                    "scope": mcp_server.MCP_SCOPE_READ,
                    "state": "opaque-state",
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                    "resource": "http://testserver/mcp",
                },
            )
            assert authorize.status_code == 302
            interactive_url = authorize.headers["location"]
            assert interactive_url.startswith("http://testserver/mcp/oauth/authorize?flow=")

            interactive = await client.get(interactive_url)
            assert interactive.status_code == 200
            assert "Connect your Wyvern account" in interactive.text

            flow_token = parse_qs(urlsplit(interactive_url).query)["flow"][0]
            consent = await client.post(
                "/mcp/oauth/authorize",
                data={
                    "flow": flow_token,
                    "email": "mcp@example.com",
                    "password": "super-secret-password",
                },
            )
            assert consent.status_code == 200
            assert "Continue to ChatGPT" in consent.text
            callback_match = re.search(r'href="(https://chatgpt\.example/callback[^"]+)"', consent.text)
            assert callback_match is not None
            callback_url = html.unescape(callback_match.group(1))
            callback_params = parse_qs(urlsplit(callback_url).query)
            assert callback_params["state"] == ["opaque-state"]
            authorization_code = callback_params["code"][0]

            token_response = await client.post(
                "/mcp/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": authorization_code,
                    "code_verifier": code_verifier,
                    "redirect_uri": "https://chatgpt.example/callback",
                },
            )
            assert token_response.status_code == 200
            tokens = token_response.json()
            access_token = tokens["access_token"]
            refresh_token = tokens["refresh_token"]

            refreshed = await client.post(
                "/mcp/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                },
            )
            assert refreshed.status_code == 200
            assert refreshed.json()["access_token"]

            headers = {"authorization": f"Bearer {access_token}", "accept": "application/json, text/event-stream"}
            with TestClient(mcp_server.wyvern_authenticated_mcp_app, base_url="http://testserver") as mcp_client:
                initialized = mcp_client.post(
                    "/",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": mcp_server.MCP_PROTOCOL_VERSION,
                            "capabilities": {},
                            "clientInfo": {"name": "pytest", "version": "1"},
                        },
                    },
                    headers=headers,
                )
                tools = mcp_client.post(
                    "/",
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    headers=headers,
                )
                searched = mcp_client.post(
                    "/",
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "search", "arguments": {"query": "ChatGPT"}},
                    },
                    headers=headers,
                )

                assert initialized.status_code == 200
                assert initialized.json()["result"]["protocolVersion"] == mcp_server.MCP_PROTOCOL_VERSION
                tool_names = [item["name"] for item in tools.json()["result"]["tools"]]
                assert tool_names == ["search", "fetch"]

                search_results = searched.json()["result"]["structuredContent"]["results"]
                assert search_results
                assert any("ChatGPT" in item["text"] for item in search_results)
                assert all(item["url"].startswith("http://testserver/mcp-doc/") for item in search_results)

                fetched = mcp_client.post(
                    "/",
                    json={
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {"name": "fetch", "arguments": {"id": search_results[0]["id"]}},
                    },
                    headers=headers,
                )
                assert fetched.status_code == 200
                fetched_payload = fetched.json()["result"]["structuredContent"]
                assert "ChatGPT" in fetched_payload["text"]

            document_path = urlsplit(search_results[0]["url"]).path
            document = await client.get(document_path)
            assert document.status_code == 200
            assert "Wyvern MCP Document" in document.text
    finally:
        app.dependency_overrides.clear()
