#!/usr/bin/env bash
# 本地开发启动脚本。
#
# 与原服务器版相比：
#   - 不再 service mysql start：默认 SQLite，DB 文件 ./health_agent.db 自动生成。
#   - 不再 vllm serve：LLM 走 DeepSeek Anthropic 兼容 API（LLM_MODE=api）。
#   - 保留启动后端 FastAPI 和前端 Vite 两件事。
#
# 用法：
#   bash run.sh         # 前台启动后端 + 前端，任一崩溃自动停另一
#   bash run.sh stop    # 停掉通过此脚本拉起的后端/前端进程
#   bash run.sh deps    # 仅补齐依赖（不启服务）
#
# 前置：node/npm 在 PATH 里。
# 切回生产部署：取消下面 LLM_MODE/USE_SQLITE 默认值，或通过环境变量覆盖。

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$PROJECT_ROOT/.run"
LOG_DIR="$PROJECT_ROOT/.run/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"
BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"

stop_existing() {
  for pf in "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE"; do
    [ -f "$pf" ] || continue
    pid="$(cat "$pf")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      echo "==> stopped pid=$pid ($(basename "$pf" .pid))"
    fi
    rm -f "$pf"
  done
}

ensure_backend_deps() {
  # 检查核心 Python 依赖是否齐全；缺则按精简集合安装（不含 torch/faiss/sentence-transformers/faster-whisper）。
  python -c "import fastapi, uvicorn, sqlalchemy, langgraph, httpx, fastapi_mail, exa_py" 2>/dev/null \
    && return 0
  echo "==> 后端 Python 依赖缺失，正在安装精简集合..."
  python -m pip install \
    fastapi "uvicorn[standard]" sqlalchemy pymysql langgraph \
    "python-jose[cryptography]" "passlib[argon2]" fastapi-mail exa-py \
    python-multipart email-validator python-docx numpy httpx
}

ensure_frontend_deps() {
  if [ -d "$PROJECT_ROOT/frontend/llm/node_modules" ]; then
    return 0
  fi
  echo "==> 前端 node_modules 缺失，正在 npm install..."
  ( cd "$PROJECT_ROOT/frontend/llm" && npm install )
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
    echo "==> 依赖就绪"
    exit 0
    ;;
  start|"")
    # 启动前清理残留进程（避免端口占用）。
    stop_existing
    ;;
  *)
    echo "用法: bash run.sh [start|stop|deps]"; exit 1
    ;;
esac

cd "$PROJECT_ROOT"
ensure_backend_deps
ensure_frontend_deps

echo "==> project root: $PROJECT_ROOT"

# 后端：SQLite + 远程 API（无需本地 MySQL / vLLM）。
echo "==> starting backend (uvicorn APP.backend.main:app)"
python -m uvicorn APP.backend.main:app --host 0.0.0.0 --port 7860 \
  > "$LOG_DIR/backend.log" 2>&1 &
echo $! > "$BACKEND_PID_FILE"
echo "    pid=$(cat "$BACKEND_PID_FILE"), log=$LOG_DIR/backend.log"

# 前端：Vite dev server（已配置 /api → http://127.0.0.1:7860 反代）。
echo "==> starting frontend (vite dev)"
( cd "$PROJECT_ROOT/frontend/llm" && npm run dev -- --host ) \
  > "$LOG_DIR/frontend.log" 2>&1 &
echo $! > "$FRONTEND_PID_FILE"
echo "    pid=$(cat "$FRONTEND_PID_FILE"), log=$LOG_DIR/frontend.log"

cat <<EOF

启动完成：
  - 后端 Swagger UI : http://127.0.0.1:7860/docs
  - 前端界面        : http://127.0.0.1:5173
  - 默认管理员账号  : admin / Admin@123456

查看日志（另开终端）：
  tail -f "$LOG_DIR/backend.log"
  tail -f "$LOG_DIR/frontend.log"

停止服务：
  bash run.sh stop
EOF

# 前台等待：任一子进程退出，自动关闭另一，避免端口泄漏。
trap 'echo; echo "==> shutting down"; stop_existing; exit' INT TERM EXIT
wait
