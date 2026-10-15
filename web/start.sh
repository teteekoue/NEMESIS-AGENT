#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_PORT="${NEMESIS_WEB_PORT:-8000}"
FRONTEND_PORT="${NEMESIS_FRONTEND_PORT:-5173}"

# --- pick a Python interpreter (prefer the project venv when present) ---
if [ -n "${NEMESIS_PYTHON:-}" ]; then
  PYTHON="$NEMESIS_PYTHON"
elif [ -x "$ROOT/venv/bin/python3" ]; then
  PYTHON="$ROOT/venv/bin/python3"
elif [ -x "$ROOT/.venv/bin/python3" ]; then
  PYTHON="$ROOT/.venv/bin/python3"
elif command -v python3 &>/dev/null; then
  PYTHON="$(command -v python3)"
else
  echo "ERREUR: python3 n'est pas installé." >&2
  exit 1
fi

if ! command -v npm &>/dev/null; then
  echo "ERREUR: Node.js/npm n'est pas installé (requis pour le frontend)." >&2
  exit 1
fi

if ! command -v curl &>/dev/null; then
  echo "ERREUR: curl n'est pas installé (requis pour vérifier le démarrage du backend)." >&2
  exit 1
fi

# --- ensure the backend web dependencies are available ---
if ! "$PYTHON" -c "import uvicorn, fastapi" &>/dev/null; then
  echo "Dépendances backend Web manquantes — installation..."
  if ! "$PYTHON" -m pip install -r "$ROOT/web/requirements.txt"; then
    echo "" >&2
    echo "ERREUR: impossible d'installer les dépendances backend Web." >&2
    echo "Essayez manuellement:" >&2
    echo "  \"$PYTHON\" -m pip install -r web/requirements.txt" >&2
    exit 1
  fi
fi

# --- start the FastAPI backend that wraps the NEMESIS agent core ---
cd "$ROOT"
NEMESIS_WEB_PORT="$BACKEND_PORT" "$PYTHON" -m web.backend &
BACKEND_PID=$!

# --- install frontend dependencies on first run ---
cd "$ROOT/web/frontend"
if [ ! -d node_modules ]; then
  echo "Installation des dépendances frontend (npm)..."
  npm install --no-audit --no-fund
fi

# --- wait for the backend to answer before starting the proxy ---
echo "Démarrage du backend sur http://localhost:$BACKEND_PORT ..."
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:$BACKEND_PORT/api/health" &>/dev/null; then
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "ERREUR: le backend s'est arrêté immédiatement. Vérifiez les logs ci-dessus." >&2
    exit 1
  fi
  sleep 0.5
done

# --- start the Vite dev server (proxies /api and /ws to the backend) ---
NEMESIS_BACKEND="http://localhost:$BACKEND_PORT" npm run dev -- --port "$FRONTEND_PORT" &
FRONTEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "Backend : http://localhost:$BACKEND_PORT"
echo "Frontend: http://localhost:$FRONTEND_PORT"
wait "$FRONTEND_PID"