"""实验：多次重放同一个真实 planner 请求，验证路由稳定性。

如果路由退化是模型随机性导致的，多次重放会得到不同结果。
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
        return parsed.get("task_type")
    except Exception as exc:
        return f"ERROR: {str(exc)[:100]}"
    finally:
        model._active_output_schema.set(None)


async def main():
    with open("/tmp/planner_raw_input_0820.json", "r", encoding="utf-8") as f:
        raw_input = json.load(f)

    user_request = raw_input["payload"].get("user_request", "")
    print(f"user_request: {user_request}")
    print("=" * 80)

    model = OpenAICompatibleChatModel(
        base_url=BASE_URL,
        api_key=API_KEY,
        model=MODEL,
        timeout_seconds=60,
        total_timeout_seconds=180,
    )

    # 每种模式跑 5 次
    for strict in [True, False]:
        mode = "严格模式(json_schema)" if strict else "宽松模式(json_object)"
        results = []
        for i in range(5):
            result = await run_one(model, raw_input, strict=strict)
            results.append(result)
            print(f"  [{mode}] 第{i+1}次: {result}")
        print(f"  [{mode}] 汇总: {results}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
