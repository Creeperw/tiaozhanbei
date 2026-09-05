"""实验：验证 json_schema 严格模式 vs 宽松 json_object 模式对 Planner 第一阶段分类的影响。

用同一个 Planner 最小语义分类请求，分别用两种 response_format 调用模型，
对比任务类型。这是验证“严格模式导致分类退化”假设的直接实验。
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
from competition_app.llm.schemas import PlannerRouteSelectionOutput
from competition_app.llm.prompt_skills import prompt_skill_registry

# 从 .env.local 读取配置
from dotenv import load_dotenv

ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local"
)
load_dotenv(ENV_PATH)

BASE_URL = os.environ.get("CHAT_BASE_URL", "https://opencode.ai/zen/go/v1")
API_KEY = os.environ.get("LLM_API_KEY", "")
MODEL = os.environ.get("CHAT_MODELS", "deepseek-v4-flash").split(",")[0].strip()

# 测试请求：覆盖各种任务类型
TEST_REQUESTS = [
    ("复习卡", "给我生成一张复习卡，针对我最近学的中医学基础内容"),
    ("知识讲解", "请结合本地教材说明六君子汤的组成、功用和主治证候。"),
    ("闲聊", "你好呀，今天心情不错"),
    ("道别", "谢谢你的帮助，再见"),
    ("组卷", "请根据我的学习进度生成一份练习试卷"),
    ("学情查询", "我最近的学习进度怎么样？"),
]


def build_planner_payload(user_request: str) -> dict:
    """构造与生产第一阶段一致的最小语义分类 payload。"""
    routing_skill = prompt_skill_registry.load("planner_agent", "route_request")
    return {
        "prompt_skill_id": routing_skill.skill_id,
        "payload": {
            "user_request": user_request,
            "authoritative_constraints": {
                "plan_scope": None,
                "continued_plan_scope": None,
            },
            "output_schema": PlannerRouteSelectionOutput.model_json_schema(),
        },
        "task_instructions": routing_skill.instructions,
        "permission_note": (
            "只允许分类当前消息的主要任务并提供当前消息逐字引文与简短理由；"
            "不得选择 Agent、工具、Prompt Skill、文件路径或执行步骤。"
        ),
    }


async def run_one(model: OpenAICompatibleChatModel, payload: dict, strict: bool):
    """用指定模式调用模型，返回路由结果。"""
    messages = model._build_messages(
        "planner_agent",
        payload,
        strict_json=False,
        business_json=True,
    )
    if strict:
        schema = _flatten_schema_for_strict_mode(payload["payload"]["output_schema"])
        model._active_output_schema.set(schema)
    else:
        model._active_output_schema.set(None)
    try:
        content = await model._request(
            messages,
            json_mode=strict,  # strict 用 json_schema，宽松用 json_object
        )
        parsed = json.loads(content)
        return {
            "task_type": parsed.get("task_type"),
            "task_source_quote": parsed.get("task_source_quote"),
            "reason": (parsed.get("reason") or "")[:80],
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}
    finally:
        model._active_output_schema.set(None)


async def main():
    print(f"BASE_URL={BASE_URL}")
    print(f"MODEL={MODEL}")
    print(f"API_KEY={'***' if API_KEY else 'MISSING'}")
    print("=" * 80)

    model = OpenAICompatibleChatModel(
        base_url=BASE_URL,
        api_key=API_KEY,
        model=MODEL,
        timeout_seconds=60,
        total_timeout_seconds=180,
    )

    for label, request in TEST_REQUESTS:
        payload = build_planner_payload(request)
        print(f"\n### 请求: {label} | {request}")
        strict_result = await run_one(model, payload, strict=True)
        loose_result = await run_one(model, payload, strict=False)
        print(f"  [严格模式 json_schema] task_type={strict_result.get('task_type')} "
              f"quote={strict_result.get('task_source_quote')}")
        print(f"  [宽松模式 json_object] task_type={loose_result.get('task_type')} "
              f"quote={loose_result.get('task_source_quote')}")
        if strict_result.get("task_type") != loose_result.get("task_type"):
            print(f"  >>> 路由结果不同！严格={strict_result.get('task_type')} "
                  f"宽松={loose_result.get('task_type')}")
        print(f"  严格 reason: {strict_result.get('reason')}")
        print(f"  宽松 reason: {loose_result.get('reason')}")


if __name__ == "__main__":
    asyncio.run(main())
