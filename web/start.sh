#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_PORT="${NEMESIS_WEB_PORT:-8000}"
FRONTEND_PORT="${NEMESIS_FRONTEND_PORT:-5173}"

if ! command -v python3 &>/dev/null; then
  echo "ERREUR: python3 n'est pas installé." >&2
  exit 1
fi
if ! command -v npm &>/dev/null; then
  echo "ERREUR: Node.js/npm n'est pas installé (requis pour le frontend)." >&2
  exit 1
fi

# Start the FastAPI backend that wraps the NEMESIS agent core
cd "$ROOT"
NEMESIS_WEB_PORT="$BACKEND_PORT" python3 -m web.backend &
BACKEND_PID=$!

# Install frontend dependencies on first run
cd "$ROOT/web/frontend"
if [ ! -d node_modules ]; then
  npm install --no-audit --no-fund
fi

# Start the Vite dev server (proxies /api and /ws to the backend)
NEMESIS_BACKEND="http://localhost:$BACKEND_PORT" npm run dev -- --port "$FRONTEND_PORT" &
FRONTEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "Backend : http://localhost:$BACKEND_PORT"
echo "Frontend: http://localhost:$FRONTEND_PORT"
wait "$FRONTEND_PID"
