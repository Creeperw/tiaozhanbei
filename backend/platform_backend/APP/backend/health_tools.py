import json
from typing import Any, Dict, List
from APP.backend.config import TOOL_RESULT_CHAR_LIMIT, EmbeddingConfig

def search_rag(query: str, user_id: int | None = None) -> str:
    try:
        from APP.backend.rag_core import rag_service
        results = rag_service.search(
            query,
            top_k=EmbeddingConfig.TOP_K,
            similarity_threshold=EmbeddingConfig.SIMILARITY_THRESHOLD,
            user_id=user_id,
        )
        if isinstance(results, str):
            text = results.strip()
        else:
            text = "\n".join(str(x) for x in results).strip()
        return text[:TOOL_RESULT_CHAR_LIMIT] if text else ""
    except Exception as exc:
        return f"本地知识库检索失败：{exc}"

def search_health_web(query: str) -> str:
    try:
        from APP.backend.search_tool import perform_search, format_search_results
        text = format_search_results(perform_search(query)).strip()
        return text[:TOOL_RESULT_CHAR_LIMIT] if text else "网络检索未返回相关内容。"
    except Exception as exc:
        return f"网络检索失败：{exc}"

def search_food_web(query: str) -> str:
    return search_health_web(query)

def search_health_video(query: str) -> str:
    try:
        from APP.backend.search_tool import perform_video_search, format_video_results
        text = format_video_results(perform_video_search(query)).strip()
        return text[:TOOL_RESULT_CHAR_LIMIT] if text else "视频检索未返回相关内容。"
    except Exception as exc:
        return f"视频检索失败：{exc}"

def schedule_email_reminder_tool(email: str = "", delay_minutes: int = 0, reminder: str = "", subject: str = "司宁健康管理提醒", user_id: int | None = None) -> str:
    try:
        from APP.backend.reminder_tool import schedule_email_reminder
        return schedule_email_reminder(
            email=email,
            delay_minutes=delay_minutes,
            reminder=reminder,
            subject=subject,
            user_id=user_id,
        )
    except Exception as exc:
        return f"定时邮件提醒创建失败：{exc}"

TOOL_REGISTRY = {
    "search_rag": search_rag,
    "search_health_web": search_health_web,
    "search_food_web": search_food_web,
    "search_health_video": search_health_video,
    "schedule_email_reminder": schedule_email_reminder_tool,
}

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_rag",
            "description": "检索本地健康管理、养生、中医与知识库资料。适合需要可靠本地知识依据的问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用于本地知识库检索的中文查询词"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_health_web",
            "description": "检索网络健康、饮食、运动、生活方式调理资料。适合需要外部补充信息的问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用于网络检索的中文查询词"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_health_video",
            "description": "检索健康管理、饮食、运动、康复、八段锦等具体演示/跟练/教学视频。仅当用户明确需要视频、动作演示、跟练教程或可视化教学时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用于视频检索的中文查询词，应包含动作/主题和‘演示、教学、跟练、视频’等关键词"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_email_reminder",
            "description": "创建一次性的定时邮件提醒。适合在用户明确提供邮箱，并且希望在未来某个时间自动收到健康提醒时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "提醒接收邮箱；如果未提供，工具会尝试回退到当前登录用户邮箱"},
                    "delay_minutes": {"type": "integer", "description": "距离当前时间的延迟分钟数，必须大于 0"},
                    "reminder": {"type": "string", "description": "提醒内容，建议简短明确"},
                    "subject": {"type": "string", "description": "邮件主题，可选"},
                },
                "required": ["delay_minutes", "reminder"],
            },
        },
    },
]

def run_tool_calls(calls: List[Dict[str, Any]], user_id: int | None = None) -> List[Dict[str, str]]:
    outputs = []
    for call in calls:
        name = call.get("name", "")
        query = call.get("query", "")
        args = call.get("args") or {}
        fn = TOOL_REGISTRY.get(name)
        if not fn:
            content = f"未知工具：{name}"
        elif name == "search_rag":
            content = fn(query, user_id=user_id)
        elif name == "schedule_email_reminder":
            if not isinstance(args, dict):
                args = {}
            if not args and query:
                try:
                    parsed = json.loads(query)
                    if isinstance(parsed, dict):
                        args = parsed
                except Exception:
                    args = {}
            reminder_text = str(args.get("reminder") or args.get("content") or query or "")
            content = fn(
                email=str(args.get("email") or ""),
                delay_minutes=args.get("delay_minutes") or args.get("minutes") or 0,
                reminder=reminder_text,
                subject=str(args.get("subject") or "司宁健康管理提醒"),
                user_id=user_id,
            )
        else:
            content = fn(query)
        outputs.append({"tool": name, "query": query, "content": content})
    return outputs