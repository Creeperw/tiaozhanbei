"""实验：用真实 planner 请求（含 shared_context 历史对话）复现路由退化。

从数据库提取 8月20日 19:59 复习卡请求的完整 raw_input，分别用严格/宽松模式重放，
看是否复现"误判为 learner_data_query"。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from competition_app.llm.openai_compatible import (
    OpenAICompatibleChatModel,
    _flatten_schema_for_strict_mode,
)
from dotenv import load_dotenv

ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local"
)
load_dotenv(ENV_PATH)

BASE_URL = os.environ.get("CHAT_BASE_URL", "https://opencode.ai/zen/go/v1")
API_KEY = os.environ.get("LLM_API_KEY", "")
MODEL = os.environ.get("CHAT_MODELS", "deepseek-v4-flash").split(",")[0].strip()


async def run_one(model: OpenAICompatibleChatModel, raw_input: dict, strict: bool):
    """用指定模式调用模型，返回路由结果。"""
    messages = model._build_messages(
        "planner_agent",
        raw_input,
        strict_json=False,
        business_json=True,
    )
    if strict:
        schema = _flatten_schema_for_strict_mode(
            raw_input["payload"]["output_schema"]
        )
        model._active_output_schema.set(schema)
    else:
        model._active_output_schema.set(None)
    try:
        content = await model._request(
            messages,
            json_mode=strict,
        )
        parsed = json.loads(content)
        return {
            "task_type": parsed.get("task_type"),
            "query_kind": parsed.get("query_kind"),
            "selected_agents": parsed.get("selected_agents"),
            "routing_reason": (parsed.get("routing_reason") or "")[:120],
        }
    except Exception as exc:
        return {"error": str(exc)[:300]}
    finally:
        model._active_output_schema.set(None)


async def main():
    with open("/tmp/planner_raw_input_0820.json", "r", encoding="utf-8") as f:
        raw_input = json.load(f)

    user_request = raw_input["payload"].get("user_request", "")
    print(f"user_request: {user_request}")
    print(f"payload keys: {list(raw_input['payload'].keys())}")
    sc = raw_input["payload"].get("shared_context", {})
    print(f"shared_context keys: {list(sc.keys()) if isinstance(sc, dict) else type(sc)}")
    rc = sc.get("recent_conversation") if isinstance(sc, dict) else None
    print(f"recent_conversation: {json.dumps(rc, ensure_ascii=False)[:300] if rc else None}")
    print("=" * 80)

    model = OpenAICompatibleChatModel(
        base_url=BASE_URL,
        api_key=API_KEY,
        model=MODEL,
        timeout_seconds=60,
        total_timeout_seconds=180,
    )

    print("\n### 严格模式 (json_schema)")
    strict_result = await run_one(model, raw_input, strict=True)
    print(json.dumps(strict_result, ensure_ascii=False, indent=2))

    print("\n### 宽松模式 (json_object)")
    loose_result = await run_one(model, raw_input, strict=False)
    print(json.dumps(loose_result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
