# Wyvern Deployment Runbook

This document covers deploying Wyvern to both backend nodes, updating the Netlify landing page, and pushing the release to `Wyvern-Organization/Wyvern` on `main`.

## Current Topology

- GitHub repository: `https://github.com/Wyvern-Organization/Wyvern.git`
- Production branch: `main`
- Netlify landing site: `https://wyvern0.netlify.app/`
- ASPC primary node:
  - Local repo path: `E:\Wyvern`
  - Backend path: `E:\Wyvern\wyvern-backend`
  - App port: `8009`
  - Local health: `http://127.0.0.1:8009/health`
  - Tunnel: Cloudflared quick tunnel
- Nubu fallback node:
  - SSH: `ssh axel@192.168.1.47`
  - Backend path: `/home/axel/wyvern-server/wyvern-backend`
  - App port: `8000`
  - Local LAN health: `http://192.168.1.47:8000/health`
  - Primary tunnel: Cloudflared quick tunnel
  - Fallback tunnel: Code Tunnels

ASPC is the primary/source-of-truth node. Nubu is the fallback node. Do not let both nodes run initial database migrations or ID re-indexing independently from different database states.

## Release Rules

1. Commit only source, config templates, migrations, docs, scripts, and app assets.
2. Do not commit `.env`, `.netlify/`, `.vscode/`, Playwright traces, generated `__pycache__`, database backups, or tunnel logs.
3. Keep ASPC primary in the landing-page route order. Nubu Cloudflared is the first fallback. Nubu Code Tunnels is the last fallback.
4. Keep both nodes on the same git commit before testing cross-node sync.
5. Never run destructive database commands without a fresh backup.

## Preflight

From repo root on ASPC:

```powershell
git status --short
git branch --show-current
git remote -v
```

Expected branch and remote:

```text
main
origin https://github.com/Wyvern-Organization/Wyvern.git
```

Run the lightweight checks:

```powershell
python -m py_compile `
  wyvern-backend/app/websocket/manager.py `
  wyvern-backend/app/services/voice_realtime.py `
  wyvern-backend/app/websocket/handlers.py `
  wyvern-backend/app/routers/sync.py `
  wyvern-backend/app/routers/messages.py `
  wyvern-backend/app/services/sync_bridge.py

$html = Get-Content -Raw wyvern-backend/index.html
$match = [regex]::Match($html, '<script>([\s\S]*)</script>')
$tmp = Join-Path $env:TEMP ('wyvern-index-' + [guid]::NewGuid() + '.js')
Set-Content -LiteralPath $tmp -Value $match.Groups[1].Value
node --check $tmp
Remove-Item -LiteralPath $tmp -Force
```

## Push To GitHub Main

Stage intended project files only. Avoid local-only/generated files.

```powershell
git add `
  DEPLOY.md `
  README.md `
  landing/index.html `
  scripts `
  wyvern-backend/.env.example `
  wyvern-backend/.gitignore `
  wyvern-backend/README.md `
  wyvern-backend/FAQ.md `
  wyvern-backend/PARTITIONED_ID_MIGRATION.md `
  wyvern-backend/alembic `
  wyvern-backend/app `
  wyvern-backend/changelog.md `
  wyvern-backend/docker-compose.yml `
  wyvern-backend/index.html `
  wyvern-backend/requirements.txt `
  wyvern-backend/scripts

git status --short
git commit -m "Deploy multi-node Wyvern sync"
git push wyvern-org main
```

If `wyvern-org` is missing, use:

```powershell
git remote add wyvern-org https://github.com/Wyvern-Organization/Wyvern.git
git push wyvern-org main
```

## Deploy ASPC

ASPC should run on host port `8009`.

```powershell
cd E:\Wyvern
git pull --ff-only wyvern-org main
pwsh .\scripts\start-aspc.ps1
curl.exe --max-time 15 -sS http://127.0.0.1:8009/health
```

If you only need to restart the running container:

```powershell
docker restart wyvern-app
curl.exe --max-time 15 -sS http://127.0.0.1:8009/health
```

When Cloudflared starts, copy the new ASPC quick-tunnel URL into `landing/index.html` as `WYVERN_ASPC_URL`.

## Deploy Nubu

SSH to nubu:

```powershell
ssh axel@192.168.1.47
```

On nubu:

```bash
cd /home/axel/wyvern-server/wyvern-backend
git pull --ff-only origin main
cd ..
bash ./scripts/start-nubu.sh
curl --max-time 15 -fsS http://127.0.0.1:8000/health
```

The nubu startup script starts Cloudflared first, then Code Tunnels as fallback. Copy the Cloudflared URL into `landing/index.html` as `WYVERN_NUBU_CLOUDFLARE_URL`. Keep the Code Tunnels URL in `WYVERN_NUBU_FALLBACK_URL`.

If Docker requires sudo:

```bash
echo 1145 | sudo -S docker restart wyvern-app
curl --max-time 15 -fsS http://127.0.0.1:8000/health
```

## Sync Configuration

Both nodes must share the same `SYNC_SHARED_SECRET`.

ASPC `.env` should include:

```env
WYVERN_NODE_ID=aspc
NODE_ROLE=main
SYNC_ENABLED=true
SYNC_PEER_API_URL=http://192.168.1.47:8000
SYNC_SHARED_SECRET=<same-secret-on-both-nodes>
SYNC_BRIDGE_RESYNC_INTERVAL_SECONDS=300
WYVERN_INDEXING=false
```

Nubu `.env` should include:

```env
WYVERN_NODE_ID=nubu
NODE_ROLE=edge
SYNC_ENABLED=true
SYNC_PEER_API_URL=http://192.168.1.12:8009
SYNC_SHARED_SECRET=<same-secret-on-both-nodes>
SYNC_BRIDGE_RESYNC_INTERVAL_SECONDS=300
WYVERN_INDEXING=false
```

`SYNC_BRIDGE_RESYNC_INTERVAL_SECONDS` makes the bridge retry after a severed connection. After a reconnect, verify both outbox queues drain to `0|0`.

ASPC queue check:

```powershell
docker exec wyvern-postgres psql -U postgres -d wyvern -t -A -c "select count(*) filter (where delivered_at is null and dead_letter is false), count(*) filter (where dead_letter is true) from replication_outbox;"
```

Nubu queue check:

```bash
echo 1145 | sudo -S docker exec wyvern-postgres psql -U wyvern -d wyvern -t -A -c "select count(*) filter (where delivered_at is null and dead_letter is false), count(*) filter (where dead_letter is true) from replication_outbox;"
```

## Netlify Landing Deploy

The landing page is a static site in `landing/`. Netlify project state lives in `landing/.netlify/` locally and must not be committed.

Install and authenticate the Netlify CLI if needed:

```powershell
npm install -g netlify-cli
netlify login
```

Link the site once if this checkout is not linked:

```powershell
cd E:\Wyvern\landing
netlify link
```

Deploy a preview:

```powershell
cd E:\Wyvern
netlify deploy --dir=landing
```

Deploy production:

```powershell
cd E:\Wyvern
netlify deploy --prod --dir=landing
```

After production deploy, open `https://wyvern0.netlify.app/` and confirm:

- ASPC is tried first.
- Nubu Cloudflared is tried if ASPC is unavailable.
- Nubu Code Tunnels is still available as a fallback link.
- Login, DMs, servers, messages, uploads, reactions, roles, invites, activity, and voice still work.

## ID Migration And Indexing

For partitioned ID migration or full re-indexing:

1. Back up the database.
2. Confirm the backup exists.
3. Enable indexing mode with `WYVERN_INDEXING=true`.
4. Restart the app. The frontend must show only `Indexing...`.
5. Run migration on ASPC first.
6. Verify relationships and queues.
7. Send the migrated ASPC state to nubu.
8. Disable indexing mode with `WYVERN_INDEXING=false`.
9. Restart both nodes.
10. Test core flows.

Do not run the initial ID migration separately on nubu. Nubu should receive ASPC's migrated database/state and should not invent a second old-to-new ID mapping.

## Post-Deploy Verification

ASPC:

```powershell
curl.exe --max-time 15 -sS http://127.0.0.1:8009/health
```

Nubu:

```powershell
curl.exe --max-time 15 -sS http://192.168.1.47:8000/health
```

Functional checks:

- Login succeeds on ASPC.
- Login succeeds on nubu.
- New ASPC records use `aspc_` IDs.
- New nubu records use `nubu_` IDs.
- Messages created on either node appear on both nodes.
- DMs created on either node appear on both nodes.
- Voice participants show real names on both nodes.
- Incoming DMs ping and appear in the DM list.
- Uploads, reactions, invites, roles, channels, servers, activity, and sessions resolve.
- Temporarily severing the node connection creates pending outbox rows, and reconnecting drains them.

## Rollback

Prefer forward fixes. If rollback is required:

1. Set `WYVERN_INDEXING=true` or stop public routing from the landing page.
2. Stop writes on both nodes.
3. Restore from the latest known-good database backup.
4. Check out the previous known-good commit on ASPC and nubu.
5. Restart both nodes.
6. Verify health, login, and replication queues.
7. Redeploy Netlify landing if route URLs changed.
