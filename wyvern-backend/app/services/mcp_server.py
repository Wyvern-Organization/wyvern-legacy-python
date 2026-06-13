from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from jose import JWTError, jwt
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import AccessToken, AuthorizationCode, OAuthAuthorizationServerProvider, RefreshToken
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from mcp.types import ToolAnnotations
from pydantic import BaseModel
from sqlalchemy import String, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models import Channel, ChannelType, DMParticipant, Message, OAuthClientRegistration, Server, ServerMember, User, WorkspaceDocument
from app.services.access import ensure_channel_access
from app.services.legal import user_requires_legal_reacceptance
from app.utils.security import verify_password


settings = get_settings()

MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_SERVER_NAME = "Wyvern"
MCP_SERVER_VERSION = "2.0"
MCP_MOUNT_PREFIX = "/mcp"
MCP_DOCUMENT_PREFIX = "/mcp-doc"
MCP_SCOPE_READ = "wyvern.read"
MCP_OAUTH_SESSION_COOKIE = "wyvern_mcp_oauth_session"
MCP_SEARCH_RESULT_LIMIT = 8
MCP_SEARCH_SNIPPET_LIMIT = 220
MCP_FLOW_TTL_SECONDS = 10 * 60
MCP_AUTH_CODE_TTL_SECONDS = 5 * 60
MCP_ACCESS_TOKEN_TTL_SECONDS = 60 * 60
MCP_REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
MCP_DOCUMENT_TICKET_TTL_SECONDS = 7 * 24 * 60 * 60

MCP_CLIENT_REGISTRATION_OPTIONS = ClientRegistrationOptions(
    enabled=True,
    valid_scopes=[MCP_SCOPE_READ],
    default_scopes=[MCP_SCOPE_READ],
)


@dataclass(frozen=True)
class McpPrincipal:
    user_id: str
    base_url: str


class McpSearchResult(BaseModel):
    id: str
    title: str
    url: str
    text: str


class McpSearchResponse(BaseModel):
    results: list[McpSearchResult]


class McpFetchResponse(BaseModel):
    id: str
    title: str
    url: str
    text: str


def get_public_mcp_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}{MCP_MOUNT_PREFIX}"


def serialize_mcp_connector(base_url: str) -> dict[str, Any]:
    public_url = get_public_mcp_url(base_url)
    return {
        "server_url": public_url,
        "app_name": "Wyvern",
        "recommended_client": "ChatGPT Apps",
        "auth_method": "OAuth 2.1",
        "published_ready": True,
        "instructions": [
            "For testing in ChatGPT developer mode, create a custom connector and paste this URL.",
            "For a published ChatGPT app, submit this same MCP URL through OpenAI's app submission flow.",
            "Users connect by signing into their Wyvern account. No API token pasting is required.",
        ],
    }


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _encode_signed_token(token_type: str, *, expires_in_seconds: int, claims: dict[str, Any]) -> str:
    now = _now_utc()
    payload = {
        "iss": "wyvern-mcp",
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
        **claims,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _decode_signed_token(token: str, expected_type: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    if payload.get("type") != expected_type:
        return None
    return payload


def _is_local_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "testserver"}


def _is_local_hostname(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().strip("[]")
    if ":" in normalized and normalized.count(":") == 1:
        normalized = normalized.split(":", 1)[0]
    return normalized in {"127.0.0.1", "localhost", "testserver"}


def _is_client_metadata_url(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme == "https" and parsed.netloc and parsed.path not in {"", "/"}:
        return True
    return _is_local_url(value) and parsed.path not in {"", "/"}


def _build_redirect_uri(base_uri: str, **params: str) -> str:
    parsed = urlsplit(base_uri)
    query = list(parse_qsl(parsed.query, keep_blank_values=True))
    query.extend((key, value) for key, value in params.items() if value is not None)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _base_url_from_resource(resource: str | None) -> str | None:
    if not resource:
        return None
    normalized = resource.rstrip("/")
    if normalized.endswith(MCP_MOUNT_PREFIX):
        return normalized[: -len(MCP_MOUNT_PREFIX)] or None
    return None


def _public_base_url_from_request(request: Request) -> str:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(",", 1)[0].strip()
    host = forwarded_host or request.headers.get("host") or request.url.netloc
    scheme = forwarded_proto or request.url.scheme
    if host and scheme == "http" and not _is_local_hostname(host):
        scheme = "https"
    if host:
        return f"{scheme}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _resource_metadata_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/.well-known/oauth-protected-resource/mcp"


def _oauth_metadata_url(base_url: str) -> str:
    return f"{get_public_mcp_url(base_url)}/.well-known/oauth-authorization-server"


def _oauth_authorize_screen_url(base_url: str) -> str:
    return f"{get_public_mcp_url(base_url)}/oauth/authorize"


def _is_https_url(value: str) -> bool:
    return urlsplit(value).scheme == "https"


def _document_ticket(document_id: str, principal: McpPrincipal) -> str:
    return _encode_signed_token(
        "mcp_document_ticket",
        expires_in_seconds=MCP_DOCUMENT_TICKET_TTL_SECONDS,
        claims={
            "sub": principal.user_id,
            "document_id": document_id,
            "base_url": principal.base_url,
        },
    )


def _decode_document_ticket(ticket: str) -> tuple[McpPrincipal, str] | None:
    payload = _decode_signed_token(ticket, "mcp_document_ticket")
    if payload is None:
        return None
    user_id = str(payload.get("sub") or "")
    document_id = str(payload.get("document_id") or "")
    base_url = str(payload.get("base_url") or "").rstrip("/")
    if not user_id or not document_id or not base_url:
        return None
    return McpPrincipal(user_id=user_id, base_url=base_url), document_id


def _document_url(principal: McpPrincipal, document_id: str) -> str:
    return f"{principal.base_url}{MCP_DOCUMENT_PREFIX}/{quote(_document_ticket(document_id, principal), safe='')}"


def _message_document_id(message_id: str) -> str:
    return f"message:{message_id}"


def _workspace_document_id(document_id: str) -> str:
    return f"workspace:{document_id}"


def _truncate_text(value: str, limit: int = MCP_SEARCH_SNIPPET_LIMIT) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}..."


def _channel_title(channel_name: str | None, channel_type: ChannelType | str | None) -> str:
    normalized_type = channel_type.value if hasattr(channel_type, "value") else str(channel_type or "")
    if normalized_type == "dm":
        return channel_name or "Direct Message"
    if channel_name:
        return f"#{channel_name}"
    return "Channel"


def _message_title(channel_name: str | None, channel_type: ChannelType | str | None, server_name: str | None) -> str:
    location = _channel_title(channel_name, channel_type)
    return f"Message in {location}" if not server_name else f"Message in {location} · {server_name}"


def _workspace_title(title: str | None, channel_name: str | None, server_name: str | None) -> str:
    base = title or _channel_title(channel_name, "text")
    return base if not server_name else f"{base} · {server_name}"


async def _accessible_channel_lookup(db: AsyncSession, user_id: str) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}

    dm_result = await db.execute(
        select(
            Channel.id,
            Channel.name,
            Channel.type,
            Channel.server_id,
            Server.name.label("server_name"),
        )
        .join(DMParticipant, DMParticipant.channel_id == Channel.id)
        .outerjoin(Server, Server.id == Channel.server_id)
        .where(DMParticipant.user_id == user_id, Channel.type != ChannelType.voice)
    )
    for row in dm_result.all():
        lookup[str(row.id)] = {
            "channel_name": row.name,
            "channel_type": row.type,
            "server_id": str(row.server_id) if row.server_id is not None else None,
            "server_name": getattr(row, "server_name", None),
        }

    server_result = await db.execute(
        select(
            Channel.id,
            Channel.name,
            Channel.type,
            Channel.server_id,
            Server.name.label("server_name"),
        )
        .join(ServerMember, ServerMember.server_id == Channel.server_id)
        .outerjoin(Server, Server.id == Channel.server_id)
        .where(ServerMember.user_id == user_id, Channel.type != ChannelType.voice)
    )
    for row in server_result.all():
        lookup[str(row.id)] = {
            "channel_name": row.name,
            "channel_type": row.type,
            "server_id": str(row.server_id) if row.server_id is not None else None,
            "server_name": getattr(row, "server_name", None),
        }

    return lookup


async def _search_workspace_documents(
    db: AsyncSession,
    *,
    principal: McpPrincipal,
    channel_lookup: dict[str, dict[str, Any]],
    query_text: str,
) -> list[tuple[datetime, McpSearchResult]]:
    channel_ids = list(channel_lookup.keys())
    if not channel_ids:
        return []

    query = (
        select(WorkspaceDocument)
        .where(
            WorkspaceDocument.channel_id.in_(channel_ids),
            or_(
                (WorkspaceDocument.visibility == "public") & WorkspaceDocument.owner_user_id.is_(None),
                (WorkspaceDocument.visibility == "private") & (WorkspaceDocument.owner_user_id == principal.user_id),
            ),
        )
    )

    if query_text:
        term = f"%{query_text}%"
        query = query.where(
            or_(
                WorkspaceDocument.title.ilike(term),
                WorkspaceDocument.content.ilike(term),
            )
        )

    rows = (
        await db.execute(query.order_by(desc(WorkspaceDocument.updated_at), desc(WorkspaceDocument.id)).limit(MCP_SEARCH_RESULT_LIMIT))
    ).scalars().all()

    results: list[tuple[datetime, McpSearchResult]] = []
    for row in rows:
        channel_meta = channel_lookup.get(str(row.channel_id), {})
        title = _workspace_title(row.title, channel_meta.get("channel_name"), channel_meta.get("server_name"))
        snippet = _truncate_text(row.content or row.title or "Workspace document")
        document_id = _workspace_document_id(str(row.id))
        results.append(
            (
                row.updated_at or row.created_at or _now_utc(),
                McpSearchResult(
                    id=document_id,
                    title=title,
                    url=_document_url(principal, document_id),
                    text=snippet,
                ),
            )
        )
    return results


async def _search_messages(
    db: AsyncSession,
    *,
    principal: McpPrincipal,
    channel_lookup: dict[str, dict[str, Any]],
    query_text: str,
) -> list[tuple[datetime, McpSearchResult]]:
    channel_ids = list(channel_lookup.keys())
    if not channel_ids:
        return []

    query = (
        select(
            Message.id,
            Message.content,
            Message.created_at,
            Message.channel_id,
            Message.attachments,
            User.username,
            User.display_name,
            User.discriminator,
        )
        .join(User, User.id == Message.author_id)
        .where(Message.channel_id.in_(channel_ids))
    )

    if query_text:
        term = f"%{query_text}%"
        query = query.where(
            or_(
                Message.content.ilike(term),
                func.coalesce(func.cast(Message.attachments, String), "").ilike(term),
                func.coalesce(User.username, "").ilike(term),
                func.coalesce(User.display_name, "").ilike(term),
                func.coalesce(User.discriminator, "").ilike(term),
            )
        )

    rows = (await db.execute(query.order_by(desc(Message.created_at), desc(Message.id)).limit(MCP_SEARCH_RESULT_LIMIT))).all()

    results: list[tuple[datetime, McpSearchResult]] = []
    for row in rows:
        channel_meta = channel_lookup.get(str(row.channel_id), {})
        author_name = getattr(row, "display_name", None) or getattr(row, "username", None) or "Unknown user"
        title = _message_title(channel_meta.get("channel_name"), channel_meta.get("channel_type"), channel_meta.get("server_name"))
        body = row.content or ""
        if not body and row.attachments:
            body = "Message with attachments"
        snippet = _truncate_text(f"{author_name}: {body}".strip() or "Message")
        document_id = _message_document_id(str(row.id))
        results.append(
            (
                row.created_at or _now_utc(),
                McpSearchResult(
                    id=document_id,
                    title=title,
                    url=_document_url(principal, document_id),
                    text=snippet,
                ),
            )
        )
    return results


async def _require_existing_user(db: AsyncSession, user_id: str) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or user_requires_legal_reacceptance(user):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Wyvern authorization is no longer valid")
    return user


def require_mcp_principal() -> McpPrincipal:
    access_token = wyvern_oauth_provider.current_access_token
    if access_token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing MCP authorization")

    user_id = str(access_token.subject or "")
    base_url = str((access_token.claims or {}).get("base_url") or "").rstrip("/")
    if not user_id or not base_url:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MCP authorization")
    return McpPrincipal(user_id=user_id, base_url=base_url)


async def _build_mcp_search_results(principal: McpPrincipal, query: str) -> McpSearchResponse:
    normalized_query = (query or "").strip()

    async with AsyncSessionLocal() as db:
        await _require_existing_user(db, principal.user_id)
        channel_lookup = await _accessible_channel_lookup(db, principal.user_id)
        workspace_hits = await _search_workspace_documents(db, principal=principal, channel_lookup=channel_lookup, query_text=normalized_query)
        message_hits = await _search_messages(db, principal=principal, channel_lookup=channel_lookup, query_text=normalized_query)

    combined = workspace_hits + message_hits
    combined.sort(key=lambda item: item[0], reverse=True)
    results = [item for _, item in combined[:MCP_SEARCH_RESULT_LIMIT]]
    return McpSearchResponse(results=results)


async def build_mcp_search_results(query: str) -> McpSearchResponse:
    return await _build_mcp_search_results(require_mcp_principal(), query)


async def _fetch_message_document(db: AsyncSession, principal: McpPrincipal, message_id: str) -> McpFetchResponse:
    result = await db.execute(
        select(Message, Channel, User, Server.name.label("server_name"))
        .join(Channel, Channel.id == Message.channel_id)
        .join(User, User.id == Message.author_id)
        .outerjoin(Server, Server.id == Channel.server_id)
        .where(Message.id == message_id)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    message, channel, author = row[0], row[1], row[2]
    server_name = getattr(row, "server_name", None)
    await ensure_channel_access(db, channel, principal.user_id)

    title = _message_title(channel.name, channel.type, server_name)
    author_label = author.display_name or author.username or "Unknown user"
    attachment_lines = "\n".join(f"- {item}" for item in (message.attachments or []))
    sections = [
        f"Title: {title}",
        f"Author: {author_label}#{author.discriminator}",
        f"Created: {message.created_at.isoformat() if message.created_at else 'Unknown'}",
        f"Channel: {_channel_title(channel.name, channel.type)}",
    ]
    if server_name:
        sections.append(f"Server: {server_name}")
    sections.extend(["", "Message", message.content or "(empty)"])
    if attachment_lines:
        sections.extend(["", "Attachments", attachment_lines])

    document_id = _message_document_id(message.id)
    return McpFetchResponse(
        id=document_id,
        title=title,
        url=_document_url(principal, document_id),
        text="\n".join(sections).strip(),
    )


async def _fetch_workspace_document(db: AsyncSession, principal: McpPrincipal, document_id: str) -> McpFetchResponse:
    result = await db.execute(
        select(WorkspaceDocument, Channel, Server.name.label("server_name"))
        .join(Channel, Channel.id == WorkspaceDocument.channel_id)
        .outerjoin(Server, Server.id == Channel.server_id)
        .where(WorkspaceDocument.id == document_id)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    document, channel = row[0], row[1]
    server_name = getattr(row, "server_name", None)
    await ensure_channel_access(db, channel, principal.user_id)
    if document.visibility == "private" and document.owner_user_id != principal.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace is private")

    title = _workspace_title(document.title, channel.name, server_name)
    sections = [
        f"Title: {title}",
        f"Visibility: {document.visibility}",
        f"Updated: {document.updated_at.isoformat() if document.updated_at else 'Unknown'}",
        f"Channel: {_channel_title(channel.name, channel.type)}",
    ]
    if server_name:
        sections.append(f"Server: {server_name}")
    sections.extend(["", "Workspace Content", document.content or "(empty workspace)"])

    full_document_id = _workspace_document_id(document.id)
    return McpFetchResponse(
        id=full_document_id,
        title=title,
        url=_document_url(principal, full_document_id),
        text="\n".join(sections).strip(),
    )


async def _build_mcp_fetch_document(principal: McpPrincipal, document_id: str) -> McpFetchResponse:
    normalized_id = (document_id or "").strip()
    if not normalized_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing document id")

    async with AsyncSessionLocal() as db:
        await _require_existing_user(db, principal.user_id)
        if normalized_id.startswith("message:"):
            return await _fetch_message_document(db, principal, normalized_id.split(":", 1)[1])
        if normalized_id.startswith("workspace:"):
            return await _fetch_workspace_document(db, principal, normalized_id.split(":", 1)[1])

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unsupported document id")


async def build_mcp_fetch_document(document_id: str) -> McpFetchResponse:
    return await _build_mcp_fetch_document(require_mcp_principal(), document_id)


def render_mcp_document_html(document: McpFetchResponse) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(document.title)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #081118;
      --panel: rgba(13, 24, 31, 0.96);
      --text: #f4fbff;
      --muted: rgba(244, 251, 255, 0.72);
      --line: rgba(124, 236, 255, 0.2);
      --accent: #7cecff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      padding: 24px;
      background:
        radial-gradient(circle at top, rgba(124, 236, 255, 0.16), transparent 40%),
        linear-gradient(180deg, #07131a 0%, #02070b 100%);
      color: var(--text);
      font-family: "Inter", system-ui, sans-serif;
    }}
    main {{
      width: min(920px, 100%);
      margin: 0 auto;
      padding: 24px;
      border-radius: 24px;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.45);
    }}
    .eyebrow {{
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 10px 0 12px;
      font-size: clamp(28px, 4vw, 40px);
    }}
    p {{
      margin: 0 0 18px;
      color: var(--muted);
      line-height: 1.65;
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.65;
      font-size: 14px;
      padding: 20px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.07);
    }}
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">Wyvern MCP Document</div>
    <h1>{escape(document.title)}</h1>
    <p>This document was shared with ChatGPT through your Wyvern account connection.</p>
    <pre>{escape(document.text)}</pre>
  </main>
</body>
</html>"""


def _render_oauth_authorize_html(
    *,
    action_url: str,
    flow_token: str,
    client_name: str,
    scope_label: str,
    resource_label: str,
    remembered_user: User | None,
    error_message: str | None = None,
) -> str:
    error_html = f'<div class="error">{escape(error_message)}</div>' if error_message else ""

    if remembered_user is not None:
        account_html = f"""
        <div class="account-card">
          <div class="kicker">Signed In</div>
          <div class="account-name">{escape(remembered_user.display_name or remembered_user.username)}</div>
          <div class="account-meta">@{escape(remembered_user.username)}#{escape(remembered_user.discriminator)}</div>
          <div class="account-meta">{escape(remembered_user.email or '')}</div>
        </div>
        <button class="primary" type="submit" data-submitting-label="Authorizing...">Authorize ChatGPT</button>
        <p class="hint">ChatGPT will be able to search and read the Wyvern content this account can already access.</p>
        """
    else:
        account_html = """
        <label>
          <span>Email</span>
          <input name="email" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" />
        </label>
        <label>
          <span>Password</span>
          <input name="password" type="password" autocomplete="current-password" />
        </label>
        <div class="error" data-inline-error hidden>Enter your Wyvern email and password to continue.</div>
        <button class="primary" type="submit" data-submitting-label="Signing In...">Sign In And Authorize</button>
        <p class="hint">This uses your normal Wyvern account login. No API keys are needed.</p>
        """

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Connect Wyvern to ChatGPT</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #06111a;
      --panel: rgba(10, 22, 32, 0.94);
      --panel-2: rgba(255, 255, 255, 0.04);
      --text: #f5fbff;
      --muted: rgba(245, 251, 255, 0.7);
      --border: rgba(124, 236, 255, 0.18);
      --accent: #7cecff;
      --danger: #ff8d8d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      background:
        radial-gradient(circle at top, rgba(124, 236, 255, 0.16), transparent 42%),
        linear-gradient(180deg, #07131a 0%, #02070b 100%);
      color: var(--text);
      font-family: "Inter", system-ui, sans-serif;
    }}
    main {{
      width: min(560px, 100%);
      padding: 28px;
      border-radius: 28px;
      border: 1px solid var(--border);
      background: var(--panel);
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
    }}
    .eyebrow {{
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.16em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 10px 0 12px;
      font-size: clamp(28px, 4vw, 38px);
    }}
    p {{
      margin: 0 0 16px;
      color: var(--muted);
      line-height: 1.6;
    }}
    .summary {{
      display: grid;
      gap: 12px;
      margin: 22px 0;
      padding: 18px;
      border-radius: 20px;
      background: var(--panel-2);
      border: 1px solid rgba(255, 255, 255, 0.06);
    }}
    .summary-row {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: baseline;
    }}
    .summary-label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
    }}
    .summary-value {{
      font-weight: 600;
      text-align: right;
    }}
    form {{
      display: grid;
      gap: 14px;
      margin-top: 22px;
    }}
    label {{
      display: grid;
      gap: 8px;
    }}
    label span {{
      color: var(--muted);
      font-size: 13px;
    }}
    input {{
      width: 100%;
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid rgba(255, 255, 255, 0.1);
      background: rgba(255, 255, 255, 0.03);
      color: var(--text);
      font: inherit;
    }}
    .primary {{
      padding: 14px 18px;
      border: 0;
      border-radius: 16px;
      background: linear-gradient(135deg, #7cecff 0%, #54b9ff 100%);
      color: #042131;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }}
    .primary:disabled {{
      opacity: 0.72;
      cursor: wait;
    }}
    .hint {{
      margin: 0;
      font-size: 13px;
    }}
    .error {{
      margin: 18px 0 0;
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid rgba(255, 141, 141, 0.25);
      background: rgba(255, 141, 141, 0.08);
      color: var(--danger);
    }}
    .account-card {{
      display: grid;
      gap: 6px;
      padding: 16px 18px;
      border-radius: 18px;
      border: 1px solid rgba(255, 255, 255, 0.08);
      background: rgba(255, 255, 255, 0.03);
    }}
    .account-name {{
      font-size: 18px;
      font-weight: 700;
    }}
    .account-meta {{
      color: var(--muted);
      font-size: 14px;
    }}
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">Wyvern x ChatGPT</div>
    <h1>Connect your Wyvern account</h1>
    <p>Authorize ChatGPT to access your Wyvern data through the official MCP account-linking flow.</p>
    <div class="summary">
      <div class="summary-row">
        <div class="summary-label">App</div>
        <div class="summary-value">{escape(client_name)}</div>
      </div>
      <div class="summary-row">
        <div class="summary-label">Permissions</div>
        <div class="summary-value">{escape(scope_label)}</div>
      </div>
      <div class="summary-row">
        <div class="summary-label">Server</div>
        <div class="summary-value">{escape(resource_label)}</div>
      </div>
    </div>
    {error_html}
    <form method="post" action="{escape(action_url)}" novalidate data-auth-form>
      <input type="hidden" name="flow" value="{escape(flow_token)}" />
      {account_html}
    </form>
    <script>
      (() => {{
        const form = document.querySelector('[data-auth-form]');
        if (!form) return;
        const button = form.querySelector('button[type="submit"]');
        const inlineError = form.querySelector('[data-inline-error]');
        const email = form.querySelector('input[name="email"]');
        const password = form.querySelector('input[name="password"]');
        form.addEventListener('submit', (event) => {{
          if (email && password) {{
            const emailValue = email.value.trim();
            const passwordValue = password.value;
            if (!emailValue || !passwordValue) {{
              event.preventDefault();
              if (inlineError) {{
                inlineError.hidden = false;
                inlineError.textContent = 'Enter your Wyvern email and password to continue.';
              }}
              if (!emailValue) {{
                email.focus();
              }} else {{
                password.focus();
              }}
              return;
            }}
            email.value = emailValue;
          }}
          if (inlineError) {{
            inlineError.hidden = true;
          }}
          if (button) {{
            button.disabled = true;
            button.textContent = button.dataset.submittingLabel || 'Continuing...';
          }}
        }});
      }})();
    </script>
  </main>
</body>
</html>"""


def _render_oauth_redirect_html(*, destination: str) -> str:
    escaped_destination = escape(destination, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Returning to ChatGPT</title>
  <meta http-equiv="refresh" content="0; url={escaped_destination}">
  <style>
    :root {{
      color-scheme: dark;
      --bg: #06111a;
      --panel: rgba(10, 22, 32, 0.94);
      --text: #f5fbff;
      --muted: rgba(245, 251, 255, 0.7);
      --border: rgba(124, 236, 255, 0.18);
      --accent: #7cecff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      background:
        radial-gradient(circle at top, rgba(124, 236, 255, 0.16), transparent 42%),
        linear-gradient(180deg, #07131a 0%, #02070b 100%);
      color: var(--text);
      font-family: "Inter", system-ui, sans-serif;
    }}
    main {{
      width: min(560px, 100%);
      padding: 28px;
      border-radius: 28px;
      border: 1px solid var(--border);
      background: var(--panel);
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
      text-align: center;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: clamp(28px, 4vw, 38px);
    }}
    p {{
      margin: 0 0 16px;
      color: var(--muted);
      line-height: 1.6;
    }}
    a {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 14px 18px;
      border-radius: 16px;
      background: linear-gradient(135deg, #7cecff 0%, #54b9ff 100%);
      color: #042131;
      font: inherit;
      font-weight: 700;
      text-decoration: none;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Authorization complete</h1>
    <p>Wyvern is handing you back to ChatGPT now.</p>
    <p>If nothing happens, tap the button below to continue.</p>
    <a href="{escaped_destination}">Continue to ChatGPT</a>
  </main>
  <script>
    (() => {{
      const destination = {destination!r};
      const go = () => {{
        try {{
          if (window.top && window.top !== window) {{
            window.top.location.replace(destination);
            return;
          }}
        }} catch (_error) {{}}
        window.location.replace(destination);
      }};
      go();
      setTimeout(go, 250);
      setTimeout(go, 1000);
    }})();
  </script>
</body>
</html>"""


class WyvernMcpOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self) -> None:
        self._registered_clients: dict[str, OAuthClientInformationFull] = {}
        self.current_access_token: AccessToken | None = None

    async def _load_persisted_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(OAuthClientRegistration).where(OAuthClientRegistration.client_id == client_id)
            )
            registration = result.scalar_one_or_none()
        if registration is None:
            return None
        client = OAuthClientInformationFull.model_validate(registration.payload)
        self._registered_clients[client_id] = client
        return client

    async def _persist_client(self, client_info: OAuthClientInformationFull) -> None:
        client_id = str(client_info.client_id or "")
        if not client_id:
            return
        payload = client_info.model_dump(mode="json")
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(OAuthClientRegistration).where(OAuthClientRegistration.client_id == client_id)
            )
            registration = result.scalar_one_or_none()
            if registration is None:
                db.add(OAuthClientRegistration(client_id=client_id, payload=payload))
            else:
                registration.payload = payload
            await db.commit()

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        client = self._registered_clients.get(client_id)
        if client is not None:
            return client
        client = await self._load_persisted_client(client_id)
        if client is not None:
            return client
        if not _is_client_metadata_url(client_id):
            return None

        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client_http:
            response = await client_http.get(client_id, headers={"accept": "application/json"})
        if response.status_code != 200:
            return None

        metadata = OAuthClientMetadata.model_validate(response.json())
        return OAuthClientInformationFull(
            client_id=client_id,
            client_secret=None,
            client_id_issued_at=None,
            client_secret_expires_at=None,
            redirect_uris=metadata.redirect_uris,
            token_endpoint_auth_method=metadata.token_endpoint_auth_method or "none",
            grant_types=metadata.grant_types,
            response_types=metadata.response_types,
            client_name=metadata.client_name,
            client_uri=metadata.client_uri,
            logo_uri=metadata.logo_uri,
            scope=metadata.scope,
            contacts=metadata.contacts,
            tos_uri=metadata.tos_uri,
            policy_uri=metadata.policy_uri,
            jwks_uri=metadata.jwks_uri,
            jwks=metadata.jwks,
            software_id=metadata.software_id,
            software_version=metadata.software_version,
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        client_id = str(client_info.client_id or "")
        self._registered_clients[client_id] = client_info
        await self._persist_client(client_info)

    async def authorize(self, client: OAuthClientInformationFull, params) -> str:
        resource = str(params.resource or "").strip()
        base_url = _base_url_from_resource(resource) or settings.mirror_target_url
        if not base_url:
            raise ValueError("Missing resource server URL for OAuth authorization")

        flow_token = _encode_signed_token(
            "mcp_oauth_flow",
            expires_in_seconds=MCP_FLOW_TTL_SECONDS,
            claims={
                "client_id": str(client.client_id or ""),
                "client_name": client.client_name or "ChatGPT",
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": bool(params.redirect_uri_provided_explicitly),
                "code_challenge": params.code_challenge,
                "resource": resource,
                "scope": " ".join(params.scopes or [MCP_SCOPE_READ]),
                "state": params.state,
            },
        )
        return f"{get_public_mcp_url(base_url)}/oauth/authorize?flow={quote(flow_token, safe='')}"

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        payload = _decode_signed_token(authorization_code, "mcp_auth_code")
        if payload is None or str(payload.get("client_id") or "") != str(client.client_id or ""):
            return None
        redirect_uri = str(payload.get("redirect_uri") or "")
        code_challenge = str(payload.get("code_challenge") or "")
        if not redirect_uri or not code_challenge:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=str(payload.get("scope") or MCP_SCOPE_READ).split(),
            expires_at=float(payload.get("exp") or 0),
            client_id=str(client.client_id or ""),
            code_challenge=code_challenge,
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=bool(payload.get("redirect_uri_provided_explicitly")),
            resource=str(payload.get("resource") or "") or None,
            subject=str(payload.get("sub") or "") or None,
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        resource = authorization_code.resource
        base_url = _base_url_from_resource(resource) or ""
        scopes = authorization_code.scopes or [MCP_SCOPE_READ]
        subject = str(authorization_code.subject or "")
        access_token = _encode_signed_token(
            "mcp_access_token",
            expires_in_seconds=MCP_ACCESS_TOKEN_TTL_SECONDS,
            claims={
                "sub": subject,
                "client_id": str(client.client_id or ""),
                "scope": " ".join(scopes),
                "resource": resource,
                "base_url": base_url,
            },
        )
        refresh_token = _encode_signed_token(
            "mcp_refresh_token",
            expires_in_seconds=MCP_REFRESH_TOKEN_TTL_SECONDS,
            claims={
                "sub": subject,
                "client_id": str(client.client_id or ""),
                "scope": " ".join(scopes),
                "resource": resource,
                "base_url": base_url,
            },
        )
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=MCP_ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=refresh_token,
            scope=" ".join(scopes),
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        payload = _decode_signed_token(refresh_token, "mcp_refresh_token")
        if payload is None or str(payload.get("client_id") or "") != str(client.client_id or ""):
            return None
        scopes = str(payload.get("scope") or MCP_SCOPE_READ).split()
        return RefreshToken(
            token=refresh_token,
            client_id=str(client.client_id or ""),
            scopes=scopes,
            expires_at=int(payload.get("exp") or 0),
            subject=str(payload.get("sub") or "") or None,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        payload = _decode_signed_token(refresh_token.token, "mcp_refresh_token")
        if payload is None:
            raise ValueError("Refresh token is invalid")
        subject = str(payload.get("sub") or "")
        resource = str(payload.get("resource") or "") or None
        base_url = str(payload.get("base_url") or "")
        access_token = _encode_signed_token(
            "mcp_access_token",
            expires_in_seconds=MCP_ACCESS_TOKEN_TTL_SECONDS,
            claims={
                "sub": subject,
                "client_id": str(client.client_id or ""),
                "scope": " ".join(scopes),
                "resource": resource,
                "base_url": base_url,
            },
        )
        replacement_refresh_token = _encode_signed_token(
            "mcp_refresh_token",
            expires_in_seconds=MCP_REFRESH_TOKEN_TTL_SECONDS,
            claims={
                "sub": subject,
                "client_id": str(client.client_id or ""),
                "scope": " ".join(scopes),
                "resource": resource,
                "base_url": base_url,
            },
        )
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=MCP_ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=replacement_refresh_token,
            scope=" ".join(scopes),
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        payload = _decode_signed_token(token, "mcp_access_token")
        if payload is None:
            return None
        return AccessToken(
            token=token,
            client_id=str(payload.get("client_id") or ""),
            scopes=str(payload.get("scope") or MCP_SCOPE_READ).split(),
            expires_at=int(payload.get("exp") or 0),
            resource=str(payload.get("resource") or "") or None,
            subject=str(payload.get("sub") or "") or None,
            claims={"base_url": str(payload.get("base_url") or "")},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        _ = token
        return None


wyvern_oauth_provider = WyvernMcpOAuthProvider()
_oauth_authorize_handler = AuthorizationHandler(wyvern_oauth_provider)
_oauth_token_handler = TokenHandler(wyvern_oauth_provider, ClientAuthenticator(wyvern_oauth_provider))
_oauth_register_handler = RegistrationHandler(wyvern_oauth_provider, MCP_CLIENT_REGISTRATION_OPTIONS)


async def serve_protected_resource_metadata(request: Request) -> JSONResponse:
    base_url = _public_base_url_from_request(request)
    resource_url = get_public_mcp_url(base_url)
    return JSONResponse(
        {
            "resource": resource_url,
            "authorization_servers": [resource_url],
            "scopes_supported": [MCP_SCOPE_READ],
            "resource_documentation": resource_url,
        },
        headers={"Cache-Control": "no-store"},
    )


async def serve_oauth_metadata(request: Request) -> JSONResponse:
    base_url = _public_base_url_from_request(request)
    issuer = get_public_mcp_url(base_url)
    return JSONResponse(
        {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "registration_endpoint": f"{issuer}/register",
            "scopes_supported": [MCP_SCOPE_READ],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "code_challenge_methods_supported": ["S256"],
            "client_id_metadata_document_supported": True,
        },
        headers={"Cache-Control": "no-store"},
    )


async def serve_oauth_authorize(request: Request) -> Response:
    return await _oauth_authorize_handler.handle(request)


async def serve_oauth_token(request: Request) -> Response:
    return await _oauth_token_handler.handle(request)


async def serve_oauth_register(request: Request) -> Response:
    return await _oauth_register_handler.handle(request)


async def _load_session_user(request: Request) -> User | None:
    cookie_token = request.cookies.get(MCP_OAUTH_SESSION_COOKIE)
    if not cookie_token:
        return None

    payload = _decode_signed_token(cookie_token, "mcp_oauth_session")
    if payload is None:
        return None

    user_id = str(payload.get("sub") or "")
    if not user_id:
        return None

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None or user_requires_legal_reacceptance(user):
            return None
        return user


async def render_oauth_authorize_screen(request: Request) -> HTMLResponse:
    flow_token = str(request.query_params.get("flow") or "")
    payload = _decode_signed_token(flow_token, "mcp_oauth_flow")
    if payload is None:
        return HTMLResponse("This Wyvern authorization request has expired. Please try connecting again from ChatGPT.", status_code=400)

    user = await _load_session_user(request)
    action_url = _oauth_authorize_screen_url(_public_base_url_from_request(request))
    return HTMLResponse(
        _render_oauth_authorize_html(
            action_url=action_url,
            flow_token=flow_token,
            client_name=str(payload.get("client_name") or "ChatGPT"),
            scope_label="Read your accessible messages and workspace documents",
            resource_label=str(payload.get("resource") or ""),
            remembered_user=user,
        ),
        headers={"Cache-Control": "no-store"},
    )


async def submit_oauth_authorize_screen(request: Request) -> Response:
    form = await request.form()
    flow_token = str(form.get("flow") or "")
    payload = _decode_signed_token(flow_token, "mcp_oauth_flow")
    if payload is None:
        return HTMLResponse("This Wyvern authorization request has expired. Please try connecting again from ChatGPT.", status_code=400)

    action_url = _oauth_authorize_screen_url(_public_base_url_from_request(request))
    user = await _load_session_user(request)
    if user is None:
        email = str(form.get("email") or "").strip().lower()
        password = str(form.get("password") or "")
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(func.lower(User.email) == email))
            user = result.scalar_one_or_none()
            if user is None or not verify_password(password, user.password_hash):
                return HTMLResponse(
                    _render_oauth_authorize_html(
                        action_url=action_url,
                        flow_token=flow_token,
                        client_name=str(payload.get("client_name") or "ChatGPT"),
                        scope_label="Read your accessible messages and workspace documents",
                        resource_label=str(payload.get("resource") or ""),
                        remembered_user=None,
                        error_message="That email or password was not recognized.",
                    ),
                    status_code=401,
                    headers={"Cache-Control": "no-store"},
                )
            if user_requires_legal_reacceptance(user):
                return HTMLResponse(
                    _render_oauth_authorize_html(
                        action_url=action_url,
                        flow_token=flow_token,
                        client_name=str(payload.get("client_name") or "ChatGPT"),
                        scope_label="Read your accessible messages and workspace documents",
                        resource_label=str(payload.get("resource") or ""),
                        remembered_user=None,
                        error_message="Please open Wyvern directly and accept the current legal terms before connecting ChatGPT.",
                    ),
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )

    redirect_uri = str(payload.get("redirect_uri") or "")
    state = str(payload.get("state") or "")
    authorization_code = _encode_signed_token(
        "mcp_auth_code",
        expires_in_seconds=MCP_AUTH_CODE_TTL_SECONDS,
        claims={
            "sub": user.id,
            "client_id": str(payload.get("client_id") or ""),
            "scope": str(payload.get("scope") or MCP_SCOPE_READ),
            "resource": str(payload.get("resource") or ""),
            "redirect_uri": redirect_uri,
            "redirect_uri_provided_explicitly": bool(payload.get("redirect_uri_provided_explicitly")),
            "code_challenge": str(payload.get("code_challenge") or ""),
        },
    )

    callback_url = _build_redirect_uri(redirect_uri, code=authorization_code, state=state)
    response = HTMLResponse(
        _render_oauth_redirect_html(destination=callback_url),
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        MCP_OAUTH_SESSION_COOKIE,
        _encode_signed_token(
            "mcp_oauth_session",
            expires_in_seconds=MCP_REFRESH_TOKEN_TTL_SECONDS,
            claims={"sub": user.id},
        ),
        httponly=True,
        secure=_is_https_url(_public_base_url_from_request(request)),
        samesite="lax",
        max_age=MCP_REFRESH_TOKEN_TTL_SECONDS,
        path="/",
    )
    return response


class _AuthenticatedMcpMount:
    def __init__(self, inner_app) -> None:
        self.inner_app = inner_app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.inner_app(scope, receive, send)
            return

        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        auth_header = headers.get("authorization", "")
        token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
        access_token = await wyvern_oauth_provider.load_access_token(token) if token else None
        if access_token is None or (access_token.expires_at and access_token.expires_at < int(_now_utc().timestamp())):
            from starlette.requests import Request

            request = Request(scope)
            await self._send_json_error(
                send,
                status.HTTP_401_UNAUTHORIZED,
                "Missing or invalid MCP bearer token",
                _resource_metadata_url(_public_base_url_from_request(request)),
            )
            return

        previous = wyvern_oauth_provider.current_access_token
        wyvern_oauth_provider.current_access_token = access_token
        try:
            await self.inner_app(scope, receive, send)
        finally:
            wyvern_oauth_provider.current_access_token = previous

    async def _send_json_error(self, send, status_code: int, message: str, resource_metadata_url: str) -> None:
        import json

        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "server-error",
                "error": {"code": -32001, "message": message},
            }
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (
                b"www-authenticate",
                f'Bearer resource_metadata="{resource_metadata_url}", scope="{MCP_SCOPE_READ}"'.encode("utf-8"),
            ),
        ]
        await send({"type": "http.response.start", "status": status_code, "headers": headers})
        await send({"type": "http.response.body", "body": body})


class _AliasedMcpTransportApp:
    def __init__(self, inner_app, mount_prefix: str) -> None:
        self.inner_app = inner_app
        self.mount_prefix = mount_prefix.rstrip("/") or "/"

    async def __call__(self, scope, receive, send) -> None:
        path = str(scope.get("path") or "")
        if path == self.mount_prefix or path == f"{self.mount_prefix}/":
            rewritten_path = "/"
        else:
            rewritten_path = path

        child_scope = dict(scope)
        child_scope["path"] = rewritten_path
        child_scope["root_path"] = scope.get("root_path", "")
        await self.inner_app(child_scope, receive, send)


wyvern_mcp = FastMCP(
    MCP_SERVER_NAME,
    instructions=(
        "Use the search tool first to find relevant Wyvern messages or workspace documents. "
        "Then use fetch with the returned id to read the full content. This server is read-only."
    ),
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@wyvern_mcp.tool(
    name="search",
    title="Search Wyvern",
    description="Search the signed-in person's accessible Wyvern messages and workspace documents. Use this first before fetch.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    structured_output=True,
)
async def mcp_search(query: str) -> McpSearchResponse:
    return await build_mcp_search_results(query)


@wyvern_mcp.tool(
    name="fetch",
    title="Fetch Wyvern Document",
    description="Fetch the full contents of a Wyvern search result by id.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    structured_output=True,
)
async def mcp_fetch(id: str) -> McpFetchResponse:
    return await build_mcp_fetch_document(id)


wyvern_mcp_http_app = wyvern_mcp.streamable_http_app()
wyvern_authenticated_mcp_app = _AuthenticatedMcpMount(wyvern_mcp_http_app)
wyvern_public_mcp_transport_app = _AliasedMcpTransportApp(wyvern_authenticated_mcp_app, MCP_MOUNT_PREFIX)
