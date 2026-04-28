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
- Directories: opt-in User Directory + opt-in Server Directory
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
- `API_V1_PREFIX` default: `/api/v1`
- `DATABASE_URL` PostgreSQL async URL
- `REDIS_URL` Redis URL
- `JWT_SECRET_KEY` required strong secret
- `JWT_ALGORITHM` default: `HS256`
- `ACCESS_TOKEN_EXPIRE_MINUTES` default: `15`
- `REFRESH_TOKEN_EXPIRE_DAYS` default: `30`
- `CORS_ORIGINS` comma-separated origins
- `MIRROR_TARGET_URL` optional upstream base URL used by `/mirror/...` proxy (example: `https://your-tunnel.trycloudflare.com`)
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

## Admin Access Allowlist

- Admin API access is restricted by `admins.json` in the project root.
- File format:

```json
{
  "usernames": ["your_username_here", "your_username_here#1234"]
}
```

- Matching is case-insensitive.
- Entries can be either plain `username` or full `username#discriminator`.
- Update `admins.json` and refresh/retry the admin page.

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

## Landing + Mirror Proxy

- `GET /landing` serves `../landing/index.html` if present.
- `/mirror/{path}` forwards HTTP requests/responses to `MIRROR_TARGET_URL`.
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
- `GET /users/me`
- `PATCH /users/me`
- `GET /users/lookup?q=<username|username#1234>`
- `GET /users/directory`
- `PUT /users/me/presence`
- `GET /users/{user_id}`
- `GET /users/{user_id}/presence`
- `POST /servers`
- `GET /servers`
- `GET /servers/directory`
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

## Notes

- Rate limiting uses Redis fixed-window counters.
- Real-time events are published through Redis pub/sub channel `wyvern:channel-events`.
- Uploads are stored locally and served from `/media/...`.
- Uploads include MIME validation and a placeholder `virus_scan_hook` for integration.
- Alembic migration `0001_initial` creates all v1 tables and enums.
- Alembic migration `0004_directory_profile` adds user bios/privacy and server description/directory fields.
