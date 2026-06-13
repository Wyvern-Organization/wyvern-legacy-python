from __future__ import annotations

import secrets
from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import quote

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import ApiToken, User


API_TOKEN_SECRET_PREFIX = "wy_ai_"
MCP_CONNECTION_TOKEN_PREFIX = "wy_mcp_"
MCP_CONNECTION_TOKEN_NAME = "Wyvern ChatGPT App"
api_token_scheme = HTTPBearer(auto_error=False)


def _fernet() -> Fernet:
    settings = get_settings()
    return Fernet(settings.resolve_openai_token_encryption_key().encode("ascii"))


def generate_api_token_secret() -> str:
    return f"{API_TOKEN_SECRET_PREFIX}{secrets.token_urlsafe(32)}"


def hash_api_token_secret(secret: str) -> str:
    return sha256(secret.encode("utf-8")).hexdigest()


def encrypt_api_token_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_api_token_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Invalid API token ciphertext") from exc


def encode_mcp_connection_secret(secret: str) -> str:
    ciphertext = encrypt_api_token_secret(secret)
    return f"{MCP_CONNECTION_TOKEN_PREFIX}{ciphertext}"


def encode_mcp_connection_ciphertext(ciphertext: str) -> str:
    return f"{MCP_CONNECTION_TOKEN_PREFIX}{str(ciphertext or '').strip()}"


def decode_mcp_connection_secret(connect_secret: str) -> str:
    value = str(connect_secret or "").strip()
    if not value.startswith(MCP_CONNECTION_TOKEN_PREFIX):
        raise ValueError("Invalid MCP connection secret")
    ciphertext = value.removeprefix(MCP_CONNECTION_TOKEN_PREFIX)
    return decrypt_api_token_secret(ciphertext)


def build_mcp_connection_url(base_url: str, connect_secret: str) -> str:
    normalized = str(base_url or "").rstrip("/")
    if not normalized:
        raise ValueError("Base URL is required")
    return f"{normalized}/mcp/{quote(connect_secret, safe='')}"


async def create_api_token(
    db: AsyncSession,
    user: User,
    name: str,
) -> tuple[ApiToken, str]:
    secret = generate_api_token_secret()
    token = ApiToken(
        user_id=user.id,
        name=name,
        token_hash=hash_api_token_secret(secret),
        token_ciphertext=encrypt_api_token_secret(secret),
    )
    db.add(token)
    await db.flush()
    await db.refresh(token)
    return token, secret


async def get_active_api_token_by_secret(db: AsyncSession, secret: str) -> ApiToken | None:
    token_hash = hash_api_token_secret(secret)
    result = await db.execute(
        select(ApiToken).where(
            ApiToken.token_hash == token_hash,
            ApiToken.revoked_at.is_(None),
        )
    )
    token = result.scalar_one_or_none()
    if token is None:
        return None
    token.last_used_at = datetime.now(tz=UTC)
    await db.flush()
    return token


async def get_current_api_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(api_token_scheme),
    db: AsyncSession = Depends(get_db),
) -> ApiToken:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    secret = credentials.credentials.strip()
    if not secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    token = await get_active_api_token_by_secret(db, secret)
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")

    await db.commit()
    return token


def serialize_api_token(token: ApiToken, secret: str | None = None) -> dict[str, object]:
    revealed_secret = secret
    if revealed_secret is None:
        try:
            revealed_secret = decrypt_api_token_secret(token.token_ciphertext)
        except ValueError:
            revealed_secret = None

    return {
        "id": token.id,
        "user_id": token.user_id,
        "name": token.name,
        "secret": revealed_secret,
        "created_at": token.created_at.isoformat() if hasattr(token.created_at, "isoformat") else token.created_at,
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at and hasattr(token.last_used_at, "isoformat") else token.last_used_at,
        "revoked_at": token.revoked_at.isoformat() if token.revoked_at and hasattr(token.revoked_at, "isoformat") else token.revoked_at,
    }
