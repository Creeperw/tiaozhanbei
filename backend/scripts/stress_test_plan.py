#!/usr/bin/env python3
"""长短期规划在线压力测试脚本。

通过 POST /api/v1/review-cards/stream 触发规划工作流（SSE 流式），
记录每类测试用例的结果（成功/失败/耗时/关键事件）。
"""
import argparse
import json
import sys
import time
import uuid

import requests

BASE_URL = "http://127.0.0.1:7860"
PASSWORD = "Test@2026"

# 各账号 learner_id
LEARNERS = {
    "zzz": "USER_4751a54ff1af4c5b8dcd6238f3e3146d",
    "qa_zero": "USER_c428d0ad682d473a87f5c545ffe0036b",
    "qa_basic": "USER_fb8e27301482485398beb64a47ad1b9e",
    "qa_rich": "USER_42b91478ad1e486e8cbb513d547bf607",
    "qa_edge": "USER_e5e6556fb30d449e804fc66b122995e3",
    "qa_0815_01": "USER_112fa3af84ab4f36bc3b20dda6cabe0d",
}


def login(username: str) -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    if r.status_code != 200:
        raise RuntimeError(f"login failed for {username}: {r.status_code} {r.text[:200]}")
    return s


def trigger_plan(
    session: requests.Session,
    learner_id: str,
    user_request: str,
    *,
    available_minutes: int = 30,
    plan_scope: str | None = None,
    timeout: int = 900,
) -> dict:
    """触发规划工作流，返回结果摘要。"""
    thread_id = f"THREAD_{uuid.uuid4().hex[:24]}"
    body = {
        "operation_id": f"OP_{uuid.uuid4().hex[:16]}",
        "thread_id": thread_id,
        "conversation_id": f"CONV_{uuid.uuid4().hex[:16]}",
        "learner_id": learner_id,
        "user_request": user_request,
        "available_minutes": available_minutes,
        "messages": [],
    }
    if plan_scope:
        body["plan_scope"] = plan_scope

    start = time.time()
    events = []
    try:
        with session.post(
            f"{BASE_URL}/api/v1/review-cards/stream",
            json=body,
            stream=True,
            timeout=timeout,
        ) as resp:
            if resp.status_code != 200:
                return {
                    "status": "http_error",
                    "http_status": resp.status_code,
                    "body": resp.text[:500],
                    "elapsed": round(time.time() - start, 1),
                }
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                if raw_line.startswith("data:"):
                    payload = raw_line[5:].strip()
                    try:
                        evt = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    events.append(evt)
                    if evt.get("event") in ("run_failed", "run_completed", "run_succeeded"):
                        break
    except requests.exceptions.Timeout:
        return {
            "status": "timeout",
            "elapsed": round(time.time() - start, 1),
            "events": events,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "exception",
            "error": str(exc),
            "elapsed": round(time.time() - start, 1),
            "events": events,
        }

    elapsed = round(time.time() - start, 1)
    # 提取关键事件
    terminal = None
    for evt in reversed(events):
        if evt.get("event") in ("run_failed", "run_completed", "run_succeeded"):
            terminal = evt
            break
    result = (terminal or {}).get("result") or {}
    task_type = result.get("task_type")
    direct_response = str(result.get("direct_response") or "")
    # 收集 agent 事件（step_started/step_completed 中的 agent）
    agents = []
    for evt in events:
        if evt.get("event") == "step_started" and evt.get("agent"):
            agents.append(evt["agent"])
    # 收集失败步骤
    failed_step = None
    for evt in events:
        if evt.get("event") == "step_failed":
            failed_step = evt.get("step_id") or evt.get("agent")
            break
    return {
        "status": terminal.get("event") if terminal else "no_terminal",
        "error_code": terminal.get("error_code") if terminal else None,
        "error_message": terminal.get("message") or terminal.get("user_message") if terminal else None,
        "task_type": task_type,
        "agents": agents,
        "failed_step": failed_step,
        "elapsed": elapsed,
        "n_events": len(events),
        "response_preview": direct_response[:300],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--request", dest="user_request", required=True)
    parser.add_argument("--scope", default=None)
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    session = login(args.username)
    learner_id = LEARNERS[args.username]
    result = trigger_plan(
        session,
        learner_id,
        args.user_request,
        available_minutes=args.minutes,
        plan_scope=args.scope,
        timeout=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
