# Wyvern Backend (v1)

Production-focused FastAPI backend for Wyvern, a server/channel/DM messaging platform.

## Stack

- FastAPI + WebSockets
- PostgreSQL (SQLAlchemy async + Alembic)
- Redis (presence + rate limiting)
- Local filesystem media storage (served by FastAPI)
- JWT auth (access + refresh tokens)

## Features

- Auth: register, login, refresh, logout
- Users: profile, update, presence
- Servers: CRUD, join/leave, role management, invite links
- Channels: CRUD for text/voice channels
- DMs: create/list/get/delete direct-message channels
- Messaging: send/edit/delete/history with cursor pagination
- Reactions: add/remove emoji reactions
- Uploads: local file upload with MIME checks + virus-scan hook placeholder
- Real-time: one WebSocket per user with channel subscription fanout
- Voice: basic voice channel presence + WebRTC signaling for audio calls
- Wyv bridge: browser handoff and API-token introspection for the separate Wyv AI product
- Directories: opt-in User Directory + opt-in Server Directory
- Recommendations: public-only directory suggestions with cached signals and optional EmbeddingGemma embeddings
- Admin: `/admin` UI + `/api/v1/admin/overview` feed for users/servers/activity

## Response Contract

All endpoints return:

```json
{
  "success": true,
  "data": {},
  "error": null
}
```

Errors return:

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "HTTP_ERROR",
    "message": "...",
    "details": null
  }
}
```

## Project Layout

```text
wyvern-backend/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models/
│   ├── schemas/
│   ├── routers/
│   ├── services/
│   ├── websocket/
│   └── utils/
├── alembic/
├── tests/
├── Dockerfile
├── docker-compose.yml
├── index.html
└── requirements.txt
../
└── wyvern_logo_transparent.png
```

## Environment Variables

Copy `.env.example` to `.env` and set values.

- `APP_NAME` default: `Wyvern Backend`
- `ENVIRONMENT` default: `development`
- `DEBUG` default: `false`
- `WYVERN_HOST_PORT` host port exposed by Docker Compose (ASPC: `8009`, nubu: `8000`)
- `API_V1_PREFIX` default: `/api/v1`
- `DATABASE_URL` required PostgreSQL async URL; do not rely on committed default database credentials
- `POSTGRES_USER` and `POSTGRES_PASSWORD` are required by Docker Compose; do not rely on a default database password
- `REDIS_URL` Redis URL
- `JWT_SECRET_KEY` required strong secret; startup fails if missing or shorter than 32 characters
- `JWT_ALGORITHM` default: `HS256`
- `ACCESS_TOKEN_EXPIRE_MINUTES` default: `15`
- `REFRESH_TOKEN_EXPIRE_DAYS` default: `30`
- `ADMIN_ALLOWLIST` comma- or newline-separated exact admin handles such as `axel#1234`
- `CORS_ORIGINS` comma-separated origins
- `MIRROR_TARGET_URL` optional upstream base URL used by `/mirror/...` proxy (example: `https://your-tunnel.trycloudflare.com`)
- `OLLAMA_BASE_URL` Ollama base URL used by the OpenAI-compatible gateway (default: `http://ollama:11434`)
- `OPENAI_TOKEN_ENCRYPTION_KEY` optional key material used to encrypt API tokens at rest; if omitted, the backend derives a key from `JWT_SECRET_KEY`
- `LOCAL_MEDIA_DIR` local directory used to persist uploads (default: `media`)
- `MEDIA_URL_PREFIX` URL path used to serve media files (default: `/media`)
- `GIPHY_API_KEY` public GIPHY API key used by the GIF picker
- `GIPHY_RATING` GIPHY content rating used for GIF search (default: `g`)
- `GIPHY_LIMIT` maximum GIF results to fetch in the picker (default: `24`)
- `FREE_UPLOAD_LIMIT_BYTES` default: `1073741824` (1 GiB)
- `RATE_LIMIT_MESSAGE_COUNT` default: `5`
- `RATE_LIMIT_MESSAGE_WINDOW_SECONDS` default: `1`
- `RATE_LIMIT_UPLOAD_COUNT` default: `10`
- `RATE_LIMIT_UPLOAD_WINDOW_SECONDS` default: `60`
- `RATE_LIMIT_AUTH_COUNT` default: `10`
- `RATE_LIMIT_AUTH_WINDOW_SECONDS` default: `60`
- `RATE_LIMIT_WEBHOOK_COUNT` default: `30`
- `RATE_LIMIT_WEBHOOK_WINDOW_SECONDS` default: `60`
- `RECOMMENDATIONS_ENABLED` default: `false`; starts the public recommendation worker when enabled
- `RECOMMENDATION_EMBEDDING_MODEL` default: `google/embeddinggemma-300m-qat-q8_0-unquantized`
- `RECOMMENDATION_ACTIVE_REFRESH_HOURS` default: `6`
- `RECOMMENDATION_NORMAL_REFRESH_HOURS` default: `24`
- `RECOMMENDATION_FULL_REFRESH_HOURS` default: `24`
- `RECOMMENDATION_MAX_CANDIDATES` default: `50`
- `RECOMMENDATION_WORKER_POLL_SECONDS` default: `300`

## Wyv AI Surface

- Wyv is the standalone AI product and OpenAI-compatible API host.
- Wyvern now owns:
  - browser identity for Wyv handoff
  - API-token issuance, rotation, and revocation
  - signed internal endpoints used by Wyv for session exchange and token introspection
- Clients should use the Wyv host with `/openai/v1` as the compatibility base path.
- Wyvern's legacy `/openai/v1` routes are intentionally disabled and return a `410 GATEWAY_MOVED` response that points callers to Wyv.
- Requests still authenticate with Wyvern API tokens created from the in-app `For Devs -> API Settings` panel.
- Token creation, rotation, and revocation remain admin-only for now while rollout expands.
- The `Chat` settings section is reserved for future Wyvern bot creation and is disabled for now.

## ChatGPT MCP App

- The published MCP server lives at one stable URL: `/mcp`.
- Wyvern uses OAuth 2.1 account linking for ChatGPT instead of private bearer-token links.
- V1 is read-only and exposes `search` and `fetch` tools over MCP for accessible Wyvern messages and workspace documents.
- OAuth discovery lives at `/mcp/.well-known/oauth-authorization-server`.
- Protected resource metadata lives at `/.well-known/oauth-protected-resource/mcp`.
- Document citation pages are served under signed `/mcp-doc/<ticket>` URLs.
- In the app UI, the MCP URL appears in `Settings -> Connectors`.
- For testing, create a custom connector in ChatGPT and paste the `/mcp` URL.
- For non-developer users, publish the same MCP URL through OpenAI's ChatGPT app submission flow so ChatGPT can show a normal Connect button and handle Wyvern sign-in through OAuth.

## Public Recommendations

- `GET /users/directory?recommended=true` and `GET /servers/directory?recommended=true` rank discoverable entries for the current user.
- V1 only indexes public opt-in users and public opt-in servers.
- Private servers, DMs, private workspaces, and non-opted-in profiles are not embedded or used as recommendation targets.
- Embeddings refresh in a background worker and are not computed on every directory request.
- The EmbeddingGemma runtime loads lazily; keep `RECOMMENDATIONS_ENABLED=false` until the model runtime is installed and verified on the node.

## Admin Access Allowlist

- Admin API access is restricted by the `ADMIN_ALLOWLIST` environment variable.
- Use exact `username#1234` handles separated by commas or newlines.
- Username matching is case-insensitive; discriminator matching is exact.
- If `ADMIN_ALLOWLIST` is empty or malformed, admin access fails closed.
- `admins.json` is not used as a runtime admin source.

## Local Run (Docker)

1. `cd wyvern-backend`
2. `cp .env.example .env`
3. Fill `.env`
4. `docker compose up --build`

The app runs on `http://localhost:8000`.

On ASPC, the one-command launcher lives at `../scripts/start-aspc.ps1` and starts the Docker stack on port `8009`.

## Local Run (without Docker)

1. Create PostgreSQL + Redis locally
2. `cd wyvern-backend`
3. `python -m venv .venv`
4. Activate env
5. `pip install -r requirements.txt`
6. `cp .env.example .env` and configure values
7. `alembic upgrade head`
8. `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

## Tests

From `wyvern-backend/`, run:

```bash
pytest -q
```

## Landing + Mirror Proxy

- `GET /landing` serves `../landing/index.html` if present.
- `/mirror/{path}` forwards read-only `GET`/`HEAD` requests to `MIRROR_TARGET_URL`.
- Example: with `MIRROR_TARGET_URL=https://abc.trycloudflare.com`, a call to `/mirror/api/v1/health` proxies to `https://abc.trycloudflare.com/api/v1/health`.

## WebSocket

- URL: `ws://localhost:8000/ws?token=<access_token>`
- Actions:
  - `{"action": "subscribe", "channel_ids": [1,2,3]}`
  - `{"action": "unsubscribe", "channel_ids": [1]}`
  - `{"action": "join_voice", "channel_id": 12}`
  - `{"action": "leave_voice", "channel_id": 12}`
  - `{"action": "voice.status", "channel_id": 12}`
  - `{"action": "call.signal", "channel_id": 12, "target_user_id": 34, "signal_type": "offer|answer|ice", "payload": {...}}`
  - `{"action": "ping"}`
- Server event examples:
  - `message.created`
  - `message.updated`
  - `message.deleted`
  - `reaction.added`
  - `reaction.removed`
  - `dm.created`
  - `dm.deleted`
  - `voice.participants`
  - `call.signal`

## API Overview

Base prefix: `/api/v1`

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/refresh`
- `POST /auth/logout`
- `GET /legal/current`
- `POST /legal/accept`
- `GET /users/me`
- `PATCH /users/me`
- `GET /users/lookup?q=<username|username#1234>`
- `GET /users/directory`
- `GET /users/directory?recommended=true`
- `PUT /users/me/presence`
- `GET /users/{user_id}`
- `GET /users/{user_id}/presence`
- `POST /servers`
- `GET /servers`
- `GET /servers/directory`
- `GET /servers/directory?recommended=true`
- `GET /servers/{server_id}`
- `PATCH /servers/{server_id}`
- `DELETE /servers/{server_id}`
- `POST /servers/{server_id}/join`
- `POST /servers/{server_id}/leave`
- `POST /servers/{server_id}/invites`
- `GET /servers/invites/{code}`
- `POST /servers/invites/{code}/join`
- `GET /servers/{server_id}/members`
- `PATCH /servers/{server_id}/members/{member_user_id}?role=admin|moderator|member`
- `POST /channels/server/{server_id}`
- `GET /channels/server/{server_id}`
- `GET /channels/{channel_id}`
- `PATCH /channels/{channel_id}`
- `DELETE /channels/{channel_id}`
- `POST /messages/channels/{channel_id}`
- `GET /messages/channels/{channel_id}?cursor=<id>&limit=<n>`
- `PATCH /messages/{message_id}`
- `DELETE /messages/{message_id}`
- `PUT /messages/{message_id}/reactions`
- `DELETE /messages/{message_id}/reactions?emoji=...`
- `POST /uploads` (multipart `file`)
- `POST /dms`
- `GET /dms`
- `GET /dms/{channel_id}`
- `DELETE /dms/{channel_id}`
- `GET /admin/overview`
- `GET /ai/api-tokens`
- `POST /ai/api-tokens`
- `DELETE /ai/api-tokens/{token_id}`
- `POST /ai/api-tokens/{token_id}/rotate`
- `POST /ai/api-tokens/revoke-all`
- `POST /ai/api-tokens/rotate-all`

## Notes

- Registration now requires explicit clickwrap acceptance of the current Terms of Service and Privacy Policy via `accepted_legal`, `terms_version`, and `privacy_version`.
- Auth and `/users/me` payloads include legal-version acceptance state plus `ai_opt_in` and `nsfw_18_verified` preference flags.
- Runtime config now includes a `legal` object with current versions, effective date, canonical URLs, and support/legal contact emails.
- Existing users with stale legal versions are blocked from active app routes until they complete `POST /legal/accept`.
- Messages now support `is_nsfw` so clients can gate sensitive content behind 18+ self-attestation.
- Rate limiting uses Redis fixed-window counters.
- Real-time events are published through Redis pub/sub channel `wyvern:channel-events`.
- Uploads are stored locally and served from `/media/...`.
- Uploads include MIME validation and a placeholder `virus_scan_hook` for integration.
- Alembic migration `0001_initial` creates all v1 tables and enums.
- Alembic migration `0004_directory_profile` adds user bios/privacy and server description/directory fields.
