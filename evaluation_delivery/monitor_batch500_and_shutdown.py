"""Monitor the CMB batch until completion/quota exhaustion, then shut AutoDL down."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("TIAOZHANBEI_ROOT", "/root/autodl-tmp/tiaozhanbei"))
RUN_TAG = "batch500_server_w16_exa"
PID_FILE = ROOT / "logs" / f"{RUN_TAG}.pid"
RUN_LOG = ROOT / "logs" / f"{RUN_TAG}.log"
MONITOR_LOG = ROOT / "logs" / f"{RUN_TAG}_shutdown_monitor.log"
OUTPUT = ROOT / "evaluation_delivery" / "outputs" / f"cmb_{RUN_TAG}.jsonl"
ENV_FILE = ROOT / "backend" / "competition_app" / ".env.local"
TOTAL = 500
POLL_SECONDS = 60
QUOTA_MARKERS = (
    "all configured api keys",
    "all siliconflow api keys",
    "all exa api keys",
    "api key exhausted",
    "balance is insufficient",
    "insufficient balance",
    "insufficient quota",
    "quota exhausted",
    "余额不足",
    "配额耗尽",
    "所有配置的 llm judge api key",
    "所有配置的 siliconflow",
)
EXA_QUOTA_MARKERS = (
    "credits exhausted",
    "insufficient credits",
    "quota exceeded",
    "payment required",
    "余额不足",
    "配额耗尽",
)


def log(message: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
    print(line, flush=True)
    with MONITOR_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"\'')
    return values


def pid_alive() -> tuple[int | None, bool]:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None, False
    try:
        os.kill(pid, 0)
    except OSError:
        return pid, False
    return pid, True


def output_summary() -> tuple[int, Counter[str]]:
    if not OUTPUT.is_file():
        return 0, Counter()
    rows = []
    for line in OUTPUT.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return len(rows), Counter(str(row.get("status")) for row in rows)


def recent_run_log(max_bytes: int = 1_000_000) -> str:
    if not RUN_LOG.is_file():
        return ""
    with RUN_LOG.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        return handle.read().decode("utf-8", errors="ignore").lower()


def log_reports_quota_exhaustion() -> str | None:
    tail = recent_run_log()
    for marker in QUOTA_MARKERS:
        if marker in tail:
            return marker
    return None


def probe_exa() -> tuple[bool, str]:
    key = read_env().get("EXA_API_KEY", "")
    if not key:
        return False, "EXA_API_KEY missing"
    command = [
        "curl", "-sS", "--max-time", "40", "--proxy", "socks5h://127.0.0.1:18080",
        "-o", "/tmp/exa_shutdown_monitor", "-w", "%{http_code}",
        "-H", f"x-api-key: {key}", "-H", "Content-Type: application/json",
        "-d", '{"query":"medical test","numResults":1}', "https://api.exa.ai/search",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    body_path = Path("/tmp/exa_shutdown_monitor")
    body = body_path.read_text(errors="ignore").lower() if body_path.is_file() else result.stderr.lower()
    return result.stdout.strip() == "200", body[:500]


def exa_quota_exhausted() -> str | None:
    ok, detail = probe_exa()
    if ok:
        return None
    for marker in EXA_QUOTA_MARKERS:
        if marker in detail:
            return marker
    log(f"EXA probe temporarily failed, continuing: {detail[:180]!r}")
    return None


def shutdown(reason: str) -> None:
    marker = ROOT / "logs" / f"{RUN_TAG}_shutdown_reason.txt"
    marker.write_text(f"{datetime.now().isoformat()} {reason}\n", encoding="utf-8")
    log(f"STOP CONDITION: {reason}; requesting host shutdown")
    subprocess.run(["sync"], check=False)
    for command in (["shutdown", "-h", "now"], ["poweroff"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=20)
            log(f"shutdown command {' '.join(command)} rc={result.returncode} stderr={result.stderr[:160]!r}")
            if result.returncode == 0:
                return
        except Exception as exc:
            log(f"shutdown command {' '.join(command)} failed: {exc}")
    raise SystemExit("unable to shut down host")


def main() -> None:
    log("shutdown monitor started")
    exa_probe_counter = 0
    while True:
        count, statuses = output_summary()
        pid, alive = pid_alive()
        log(f"progress={count}/{TOTAL} statuses={dict(statuses)} pid={pid} alive={alive}")
        if count >= TOTAL:
            shutdown(f"completed {count}/{TOTAL}")
            return
        quota_marker = log_reports_quota_exhaustion()
        if quota_marker:
            shutdown(f"SiliconFlow/API quota exhausted: {quota_marker}")
            return
        exa_probe_counter += 1
        if exa_probe_counter >= 10:
            exa_probe_counter = 0
            marker = exa_quota_exhausted()
            if marker:
                shutdown(f"EXA quota exhausted: {marker}")
                return
        if not alive:
            # The evaluator exited early. Give buffered JSONL/log writes a short grace period,
            # then power down rather than leaving a paid server running unattended.
            time.sleep(20)
            count2, _ = output_summary()
            marker = log_reports_quota_exhaustion()
            if count2 >= TOTAL:
                shutdown(f"completed {count2}/{TOTAL}")
            elif marker:
                shutdown(f"evaluator exited after quota exhaustion: {marker}")
            else:
                shutdown(f"evaluator exited unexpectedly at {count2}/{TOTAL}")
            return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: shutdown("monitor received SIGTERM"))
    main()
