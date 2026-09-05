"""Strictly monitor batch500 and power off only on an explicit terminal condition."""
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
MONITOR_LOG = ROOT / "logs" / f"{RUN_TAG}_strict_shutdown_monitor.log"
OUTPUT = ROOT / "evaluation_delivery" / "outputs" / f"cmb_{RUN_TAG}.jsonl"
REPORT = ROOT / "evaluation_delivery" / "outputs" / f"cmb_report_{RUN_TAG}.md"
ENV_FILE = ROOT / "backend" / "competition_app" / ".env.local"
TOTAL = 500
POLL_SECONDS = 60
EXA_PROBE_EVERY = 5
CHAT_EXACT_MARKERS = (
    "all configured chat api keys are exhausted",
    "所有配置的 chat api key 均已余额或配额耗尽",
)
EXA_EXACT_MARKERS = (
    "credits exhausted",
    "insufficient credits",
    "quota exceeded",
    "payment required",
    "credit balance",
)

stopping = False


def log(message: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
    print(line, flush=True)
    with MONITOR_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"\'')
    return values


def evaluator_state() -> tuple[int | None, bool]:
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
    records: dict[str, dict] = {}
    if OUTPUT.is_file():
        for line in OUTPUT.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            case_id = str(row.get("case_id") or "")
            if case_id:
                records[case_id] = row
    return len(records), Counter(str(row.get("status")) for row in records.values())


def completion_is_final(count: int, alive: bool, monitor_started: float) -> bool:
    if count != TOTAL or alive or not REPORT.is_file():
        return False
    if REPORT.stat().st_mtime < monitor_started:
        return False
    report = REPORT.read_text(encoding="utf-8", errors="ignore")
    return "总用例: 500" in report


def read_new_log(offset: int) -> tuple[int, str]:
    if not RUN_LOG.is_file():
        return offset, ""
    size = RUN_LOG.stat().st_size
    if size < offset:
        offset = 0
    with RUN_LOG.open("rb") as handle:
        handle.seek(offset)
        payload = handle.read()
    return size, payload.decode("utf-8", errors="ignore").lower()


def probe_exa_quota() -> str | None:
    key = read_env().get("EXA_API_KEY", "")
    if not key:
        log("EXA probe skipped: API key missing; this is not treated as quota exhaustion")
        return None
    body_path = Path("/tmp/exa_strict_shutdown_probe")
    result = subprocess.run(
        [
            "curl", "-sS", "--max-time", "40", "--proxy", "socks5h://127.0.0.1:18080",
            "-o", str(body_path), "-w", "%{http_code}",
            "-H", f"x-api-key: {key}", "-H", "Content-Type: application/json",
            "-d", '{"query":"medical test","numResults":1}', "https://api.exa.ai/search",
        ],
        capture_output=True,
        text=True,
    )
    status = result.stdout.strip()
    body = body_path.read_text(errors="ignore").lower() if body_path.is_file() else ""
    if status == "200":
        log("EXA probe: HTTP 200")
        return None
    if status in {"402", "403", "429"} and any(marker in body for marker in EXA_EXACT_MARKERS):
        return f"HTTP {status}: {body[:240]}"
    log(f"EXA probe temporary/non-quota failure: HTTP {status}, body={body[:160]!r}")
    return None


def power_off(reason: str) -> None:
    marker = ROOT / "logs" / f"{RUN_TAG}_shutdown_reason.txt"
    marker.write_text(f"{datetime.now().isoformat()} {reason}\n", encoding="utf-8")
    log(f"VERIFIED STOP CONDITION: {reason}; powering off")
    subprocess.run(["sync"], check=False)
    result = subprocess.run(["shutdown", "-h", "now"], capture_output=True, text=True)
    log(f"shutdown rc={result.returncode} stderr={result.stderr[:160]!r}")
    if result.returncode != 0:
        subprocess.run(["poweroff"], check=False)


def stop_monitor(*_: object) -> None:
    global stopping
    stopping = True
    log("monitor stopped by signal; server will NOT be powered off")


def main() -> None:
    monitor_started = time.time()
    log_offset = RUN_LOG.stat().st_size if RUN_LOG.is_file() else 0
    log("strict monitor started; shutdown only after final report for 500 unique cases or fresh exact API-quota evidence")
    probe_counter = EXA_PROBE_EVERY - 1
    while not stopping:
        count, statuses = output_summary()
        pid, alive = evaluator_state()
        log(f"progress={count}/{TOTAL} statuses={dict(statuses)} pid={pid} alive={alive}")

        if completion_is_final(count, alive, monitor_started):
            power_off("500 unique cases completed and final 500-case Judge report written")
            return

        log_offset, fresh_log = read_new_log(log_offset)
        marker = next((item for item in CHAT_EXACT_MARKERS if item in fresh_log), None)
        if marker:
            power_off(f"all SiliconFlow chat keys exhausted (exact marker: {marker})")
            return

        probe_counter += 1
        if probe_counter >= EXA_PROBE_EVERY:
            probe_counter = 0
            exa_reason = probe_exa_quota()
            if exa_reason:
                power_off(f"EXA quota explicitly exhausted: {exa_reason}")
                return

        if not alive:
            log("evaluator is not running without a verified stop condition; NOT powering off")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop_monitor)
    signal.signal(signal.SIGINT, stop_monitor)
    main()
