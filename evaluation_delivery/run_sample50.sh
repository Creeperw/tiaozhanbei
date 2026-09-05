#!/usr/bin/env bash
set -euo pipefail

root="${TIAOZHANBEI_ROOT:-/root/autodl-tmp/tiaozhanbei}"
run_tag="sample50_w4"
input="outputs/cmb_sample50.json"
log="$root/logs/$run_tag.log"
pid_file="$root/logs/$run_tag.pid"
python_bin="/root/miniconda3/envs/cmb500/bin/python"
handoff="$root/backend/competition/知识星球视频知识库_前端交接包_2026-07-18"

cd "$root/evaluation_delivery"
mkdir -p "$root/logs"
[[ -f "$input" ]] || { echo "缺少输入：$PWD/$input" >&2; exit 1; }

if [[ -f "$pid_file" ]]; then
    old_pid="$(cat "$pid_file")"
    if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
        echo "评测已运行，PID=$old_pid" >&2
        exit 1
    fi
fi

printf '\n===== sample50 %s =====\n' "$(date -Is)" >>"$log"
nohup env \
    -u DATABASE_URL \
    -u MYSQL_PASSWORD \
    USE_SQLITE=false \
    SHIZHEN_ASSET_ROOT="$root/backend/competition" \
    KNOWLEDGE_HANDOFF_ROOT="$handoff" \
    KNOWLEDGE_RUNTIME_ROOT="$handoff/知识库管理组件/runtime" \
    ALL_PROXY=socks5://127.0.0.1:18080 \
    NO_PROXY=localhost,127.0.0.1,api.deepseek.com,api.siliconflow.cn \
    PYTHONPATH="$root/.deps:$root/backend" \
    "$python_bin" codex1_hallucination.py \
        --data "$input" \
        --limit 50 \
        --workers 4 \
        --run-tag "$run_tag" \
    >>"$log" 2>&1 </dev/null &

pid=$!
printf '%s\n' "$pid" >"$pid_file"
if ! kill -0 "$pid" 2>/dev/null; then
    echo "启动失败，最近日志：" >&2
    tail -80 "$log" >&2
    exit 1
fi

echo "启动成功：PID=$pid，日志=$log"
