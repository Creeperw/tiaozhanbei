#!/usr/bin/env bash
# Build the React frontend, then serve the frontend and every API from FastAPI
# on the single public port 7860.

set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_ROOT="$(cd "$SCRIPT_ROOT/.." && pwd)"
REPOSITORY_ROOT="$(cd "$SCRIPT_ROOT/../.." && pwd)"
FRONTEND_ROOT="$REPOSITORY_ROOT/frontend/llm"
SHARED_RUNTIME_ROOT="${SHIZHEN_RUNTIME_ROOT:-$REPOSITORY_ROOT/runtime}"
PID_DIR="$SHARED_RUNTIME_ROOT/launcher"
LOG_DIR="$PID_DIR/logs"
BACKEND_PID_FILE="$PID_DIR/backend.pid"
mkdir -p "$PID_DIR" "$LOG_DIR"

stop_existing() {
  [ -f "$BACKEND_PID_FILE" ] || return 0
  local pid
  pid="$(cat "$BACKEND_PID_FILE")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    echo "==> stopped backend (pid=$pid)"
  fi
  rm -f "$BACKEND_PID_FILE"
}

ensure_backend_deps() {
  python -c "import fastapi, uvicorn, sqlalchemy, langgraph, httpx, fastapi_mail, exa_py" 2>/dev/null \
    && return 0
  echo "==> installing backend dependencies"
  python -m pip install \
    fastapi "uvicorn[standard]" sqlalchemy pymysql langgraph typer \
    "python-jose[cryptography]" "passlib[argon2]" fastapi-mail exa-py \
    python-multipart email-validator python-docx numpy httpx
}

ensure_frontend_deps() {
  [ -d "$FRONTEND_ROOT/node_modules" ] && return 0
  echo "==> installing frontend dependencies"
  (cd "$FRONTEND_ROOT" && npm install)
}

build_frontend() {
  echo "==> building frontend for integrated port 7860"
  (cd "$FRONTEND_ROOT" && npm run build)
}

cmd="${1:-start}"
case "$cmd" in
  stop)
    stop_existing
    exit 0
    ;;
  deps)
    ensure_backend_deps
    ensure_frontend_deps
    echo "==> dependencies ready"
    exit 0
    ;;
  start|"")
    stop_existing
    ;;
  *)
    echo "usage: bash run.sh [start|stop|deps]"
    exit 1
    ;;
esac

ensure_backend_deps
ensure_frontend_deps
build_frontend

echo "==> starting integrated backend on 7860"
(
  cd "$BACKEND_ROOT"
  export COMPETITION_APP_MODE="${COMPETITION_APP_MODE:-stub}"
  export BACKEND_HANDOFF_ENABLED=true
  export BACKEND_HANDOFF_ROOT="${BACKEND_HANDOFF_ROOT:-platform_backend}"
  export API_PORT=7860
  exec python -m competition_app.cli.app serve --host 0.0.0.0 --port 7860
) > "$LOG_DIR/backend.log" 2> "$LOG_DIR/backend.err" &
echo $! > "$BACKEND_PID_FILE"

cat <<EOF
Started:
  - Frontend: http://127.0.0.1:7860
  - Swagger:  http://127.0.0.1:7860/docs
  - Log:      $LOG_DIR/backend.log
EOF

trap 'stop_existing' INT TERM EXIT
wait "$(cat "$BACKEND_PID_FILE")"
