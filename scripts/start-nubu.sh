#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/wyvern-backend"
RUNTIME_DIR="$ROOT_DIR/runtime"
CF_LOG="$RUNTIME_DIR/nubu-cloudflared.log"
CF_ERR="$RUNTIME_DIR/nubu-cloudflared.err"
DT_LOG="$RUNTIME_DIR/nubu-devtunnel.log"

HOST_PORT="${WYVERN_HOST_PORT:-}"
if [[ -z "$HOST_PORT" && -f "$APP_DIR/.env" ]]; then
  HOST_PORT="$(awk -F= '/^WYVERN_HOST_PORT=/{print $2}' "$APP_DIR/.env" | tail -n 1 | tr -d '\r' || true)"
fi
HOST_PORT="${HOST_PORT:-8009}"
LOCAL_URL="http://127.0.0.1:${HOST_PORT}"
HEALTH_URL="${LOCAL_URL}/health"

mkdir -p "$RUNTIME_DIR"

start_if_missing() {
  local pattern="$1"
  shift

  if pgrep -af "$pattern" >/dev/null 2>&1; then
    echo "Already running: $pattern"
    return 0
  fi

  nohup "$@" >/dev/null 2>&1 &
}

cd "$APP_DIR"

echo "Starting nubu backend..."
DOCKER=(docker)
if ! docker ps >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1; then
    DOCKER=(sudo docker)
  fi
fi
"${DOCKER[@]}" compose up -d --build

for _ in $(seq 1 30); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if [[ -x "$HOME/.local/bin/cloudflared" ]]; then
  if ! pgrep -af "cloudflared tunnel --url ${LOCAL_URL}" >/dev/null 2>&1; then
    nohup "$HOME/.local/bin/cloudflared" tunnel --url "$LOCAL_URL" --no-autoupdate --loglevel info >"$CF_LOG" 2>"$CF_ERR" &
    echo "Started cloudflared tunnel for nubu."
  else
    echo "cloudflared tunnel for nubu is already running."
  fi
else
  echo "cloudflared was not found at $HOME/.local/bin/cloudflared"
fi

if [[ -x "$HOME/.local/bin/devtunnel" ]]; then
  if ! pgrep -af "devtunnel host .* -p ${HOST_PORT}" >/dev/null 2>&1; then
    nohup "$HOME/.local/bin/devtunnel" host -p "$HOST_PORT" --allow-anonymous >"$DT_LOG" 2>&1 &
    echo "Started Code Tunnel fallback for nubu."
  else
    echo "Code Tunnel fallback is already running."
  fi
else
  echo "devtunnel was not found at $HOME/.local/bin/devtunnel"
fi

sleep 2

if [[ -f "$CF_LOG" ]]; then
  CF_URL="$(grep -oE 'https://[A-Za-z0-9.-]+trycloudflare\.com' "$CF_LOG" | tail -n 1 || true)"
  if [[ -n "${CF_URL:-}" ]]; then
    echo "Cloudflared URL: $CF_URL"
  fi
fi

if [[ -f "$CF_ERR" ]]; then
  CF_URL="$(grep -oE 'https://[A-Za-z0-9.-]+trycloudflare\.com' "$CF_ERR" | tail -n 1 || true)"
  if [[ -n "${CF_URL:-}" ]]; then
    echo "Cloudflared URL: $CF_URL"
  fi
fi

if [[ -f "$DT_LOG" ]]; then
  DT_URL="$(grep -oE 'https://[A-Za-z0-9.-]+\.devtunnels\.ms/?' "$DT_LOG" | tail -n 1 || true)"
  if [[ -n "${DT_URL:-}" ]]; then
    echo "Code Tunnel URL: $DT_URL"
  fi
fi

echo "Nubu is ready."
echo "Health: $HEALTH_URL"
