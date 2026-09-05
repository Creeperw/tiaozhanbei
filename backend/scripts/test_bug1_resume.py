#!/usr/bin/env python3
"""Bug ① 修复验证脚本：目标切换 + resume 完整链路。

模拟测试 7/8：
1. 用户请求"我不想考中医执业医师了，我要改考中西医结合执业医师"（long_term）
2. 期望 memory_conflict 中断
3. resume 确认"是的，我确认要改考中西医结合执业医师"
4. 期望成功生成中西医结合长期规划（修复前会失败）
"""
import json
import sys
import time
import uuid

import requests

BASE_URL = "http://127.0.0.1:7860"
PASSWORD = "Test@2026"
LEARNER_ID = "USER_fb8e27301482485398beb64a47ad1b9e"  # qa_basic


def login(username: str) -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    if r.status_code != 200:
        raise RuntimeError(f"login failed: {r.status_code} {r.text[:200]}")
    return s


def stream_events(session, body, url=None, timeout=900):
    """POST 并收集 SSE 事件，返回 (events, terminal_event)。"""
    events = []
    start = time.time()
    try:
        with session.post(
            url or f"{BASE_URL}/api/v1/review-cards/stream",
            json=body,
            stream=True,
            timeout=timeout,
        ) as resp:
            if resp.status_code != 200:
                return events, {"event": "http_error", "status": resp.status_code, "body": resp.text[:500]}
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
                    if evt.get("event") in ("run_failed", "run_completed", "run_succeeded", "interrupted"):
                        return events, evt
    except requests.exceptions.Timeout:
        return events, {"event": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return events, {"event": "exception", "error": str(exc)}
    return events, (events[-1] if events else {"event": "no_terminal"})


def summarize(events, terminal):
    result = (terminal or {}).get("result") or {}
    agents = [evt.get("agent") for evt in events if evt.get("event") == "step_started" and evt.get("agent")]
    return {
        "status": terminal.get("event"),
        "interrupt_type": (terminal.get("interrupt") or {}).get("interrupt_type") if terminal.get("event") == "interrupted" else None,
        "questions": (terminal.get("interrupt") or {}).get("questions") if terminal.get("event") == "interrupted" else None,
        "error_code": terminal.get("error_code"),
        "error_message": terminal.get("message") or terminal.get("user_message"),
        "task_type": result.get("task_type"),
        "agents": agents,
        "elapsed": round(time.time() - time.time(), 1),
    }


def main():
    session = login("qa_basic")
    thread_id = f"THREAD_{uuid.uuid4().hex[:24]}"
    conversation_id = f"CONV_{uuid.uuid4().hex[:16]}"

    # 步骤 1：触发目标切换（long_term）
    body = {
        "operation_id": f"OP_{uuid.uuid4().hex[:16]}",
        "thread_id": thread_id,
        "conversation_id": conversation_id,
        "learner_id": LEARNER_ID,
        "user_request": "我不想考中医执业医师了，我要改考中西医结合执业医师",
        "available_minutes": 30,
        "plan_scope": "long_term",
        "messages": [],
    }
    print("=== 步骤 1：触发目标切换 ===")
    t0 = time.time()
    events, terminal = stream_events(session, body)
    elapsed1 = round(time.time() - t0, 1)
    print(f"耗时: {elapsed1}s")
    print(f"terminal event: {terminal.get('event')}")
    if terminal.get("event") == "interrupted":
        intr = terminal.get("interrupt") or {}
        print(f"interrupt_type: {intr.get('interrupt_type')}")
        print(f"questions: {intr.get('questions')}")
    elif terminal.get("event") in ("run_failed", "run_completed", "run_succeeded"):
        print(f"error_code: {terminal.get('error_code')}")
        print(f"message: {terminal.get('message') or terminal.get('user_message')}")
        print(json.dumps(terminal, ensure_ascii=False)[:500])
        return

    if terminal.get("event") != "interrupted":
        print("未触发中断，无法继续 resume 测试")
        return

    # 步骤 2：resume 确认目标变更（使用专用 resume 端点）
    print("\n=== 步骤 2：resume 确认目标变更 ===")
    resume_body = {
        "answer": "是的，我确认要改考中西医结合执业医师",
        "plan_scope": "long_term",
    }
    t0 = time.time()
    events2, terminal2 = stream_events(
        session,
        resume_body,
        url=f"{BASE_URL}/api/v1/review-cards/runs/{thread_id}/resume/stream",
    )
    elapsed2 = round(time.time() - t0, 1)
    print(f"耗时: {elapsed2}s")
    print(f"terminal event: {terminal2.get('event')}")
    if terminal2.get("event") in ("run_failed", "run_completed", "run_succeeded"):
        print(f"error_code: {terminal2.get('error_code')}")
        print(f"message: {terminal2.get('message') or terminal2.get('user_message')}")
    elif terminal2.get("event") == "interrupted":
        intr = terminal2.get("interrupt") or {}
        print(f"interrupt_type: {intr.get('interrupt_type')}")
        print(f"questions: {intr.get('questions')}")

    # 打印关键事件
    print("\n=== 事件摘要 ===")
    for evt in events2:
        e = evt.get("event")
        if e in ("step_started", "step_completed", "step_failed", "run_failed", "run_completed", "interrupted"):
            print(f"  {e}: {evt.get('step_id') or evt.get('agent') or ''} {evt.get('error_code') or ''}")

    # 最终判定
    if terminal2.get("event") in ("run_completed", "run_succeeded"):
        print("\n✅ 修复验证通过：resume 后成功生成中西医结合长期规划")
    elif terminal2.get("event") == "run_failed":
        print("\n❌ 修复验证失败：resume 后仍失败")
        print(f"   error_code: {terminal2.get('error_code')}")
        print(f"   message: {terminal2.get('message') or terminal2.get('user_message')}")
    else:
        print(f"\n⚠️ 未完成：{terminal2.get('event')}")


if __name__ == "__main__":
    main()
