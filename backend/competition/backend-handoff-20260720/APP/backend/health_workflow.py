import json
from typing import Any, Dict, Iterator, List, Tuple, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from APP.intent_reply_template import INTENT_TEMPLATES
from APP.backend.config import (
    PLANNER_MAX_STEPS, CONTEXT_COMPRESS_TOKEN_LIMIT,
    CONTEXT_MANAGER_MAX_TOKENS, COMPRESSION_MAX_TOKENS, INFO_REFINER_MAX_TOKENS,
    PLANNER_MAX_TOKENS, EXECUTOR_MAX_TOKENS, REVIEWER_MAX_TOKENS,
    TRACE_CONTEXT_CHAR_LIMIT, MEMORY_TRACE_CHAR_LIMIT, TOOL_TRACE_CHAR_LIMIT,
    CONTEXT_MANAGER_TEMPERATURE, COMPRESSION_TEMPERATURE, PLANNER_TEMPERATURE,
    INFO_REFINER_TEMPERATURE, EXECUTOR_TEMPERATURE, REVIEWER_TEMPERATURE,
    REGENERATION_TEMPERATURE
)
from APP.backend.database import DbMessage, FeedbackRecord, AgentEvent, MemorySummary
from APP.backend.health_llm import build_llm_client
from APP.backend.health_memory import clean_message_for_context, retrieve_user_context, retrieve_compressed_context, save_extracted_memories, select_messages_for_compression, save_memory_summary, log_agent_event
from APP.backend.health_prompts import CONTEXT_MANAGER_PROMPT, INFO_REFINER_PROMPT, REVIEWER_PROMPT, COMPRESSION_PROMPT
from APP.backend.health_tools import OPENAI_TOOLS, run_tool_calls
from APP.backend.health_utils import extract_json_object, parse_tool_calls, split_think, safe_json_dumps
from APP.backend.memory_agent_service import build_learner_context_brief
from APP.backend.planner_agent_service import generate_agent_execution_plan

# 通过 build_llm_client 统一构建：api 模式走远程 API，local 模式走本地 vLLM。
planner_client = build_llm_client("planner")
executor_client = planner_client
manager_client = build_llm_client("manager")
reviewer_client = manager_client

NO_THINK_EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}

PLANNER_SENTINEL_TOOL = {
    "type": "function",
    "function": {
        "name": "__planner_no_tool__",
        "description": "内部占位工具：仅用于让模板进入规划阶段。当前用户未开启任何工具，禁止调用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

def _planner_assistant_segment(raw: str) -> str:
    text = raw or ""
    for marker in ["<|im_end|>", "<|im_start|>user", "<tool_response>"]:
        if marker in text:
            text = text.split(marker, 1)[0]
    return text.strip()

def _available_openai_tools(enable_search: bool = True, enable_rag: bool = True, tools_enabled: bool | None = None) -> List[Dict[str, Any]]:
    if tools_enabled is not None:
        return list(OPENAI_TOOLS) if tools_enabled else []
    tools = []
    for tool in OPENAI_TOOLS:
        name = tool.get("function", {}).get("name", "")
        if name == "search_rag" and not enable_rag:
            continue
        if name in {"search_health_web", "search_food_web", "search_health_video"} and not enable_search:
            continue
        tools.append(tool)
    return tools

def _planner_extra_body(tools: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    return {"tools": tools or [PLANNER_SENTINEL_TOOL], "tool_choice": "auto"}


def _tool_names(tools: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for tool in tools:
        name = tool.get("function", {}).get("name")
        if name and name != "__planner_no_tool__":
            names.append(name)
    return names


def _attach_global_execution_plan(state: "HealthState", available_tools: List[Dict[str, Any]]) -> None:
    try:
        learner_context = build_learner_context_brief(state["db"], state["user_id"])
        plan = generate_agent_execution_plan(
            learner_context=learner_context,
            user_request=state["user_question"],
            available_tools=_tool_names(available_tools),
        )
        state["global_execution_plan"] = plan.model_dump()
        state.setdefault("planner_trace", []).append({"step": 0, "action": "global_execution_plan", "plan": plan.model_dump()})
    except Exception as exc:
        state["global_execution_plan_error"] = str(exc)

def _planner_user_prompt(state: Dict[str, Any]) -> str:
    visual_names = state.get("visual_file_names") or []
    visual_note = "无"
    if visual_names:
        visual_note = "已上传视觉附件：" + "、".join(str(name) for name in visual_names) + "。请在规划时直接结合随消息附带的图片内容判断，不要假设未上传图片。"
    return (
        f"【用户个人信息】\n{state.get('user_context','无')}\n\n"
        f"【压缩历史记忆】\n{state.get('compressed_context','无')}\n\n"
        f"【历史上下文】\n{state.get('history_text','无')}\n\n"
        f"【用户问题】\n{state['user_question']}\n\n"
        f"【附件/文件内容】\n{state.get('file_context','无')}\n\n"
        f"【视觉附件】\n{visual_note}"
    )

def _planner_user_message(state: Dict[str, Any]) -> Dict[str, Any]:
    prompt = _planner_user_prompt(state)
    visual_content = state.get("visual_content") or []
    if not visual_content:
        return {"role": "user", "content": prompt}
    return {"role": "user", "content": [{"type": "text", "text": prompt}, *visual_content]}

def _executor_user_message(prompt: str, state: Dict[str, Any]) -> Dict[str, Any]:
    visual_content = state.get("visual_content") or []
    if not visual_content:
        return {"role": "user", "content": prompt}
    return {"role": "user", "content": [{"type": "text", "text": prompt}, *visual_content]}

def _fallback_tool_calls_from_text(action: Dict[str, Any], raw: str, user_question: str, available_tools: List[Dict[str, Any]] | None = None) -> List[Dict[str, str]]:
    if action.get("action") != "tool_call":
        return []
    text = raw or ""
    calls = []
    for tool in (available_tools if available_tools is not None else OPENAI_TOOLS):
        name = tool.get("function", {}).get("name", "")
        if name == "__planner_no_tool__":
            continue
        if name and name in text:
            calls.append({"name": name, "query": user_question})
    return calls

class HealthState(TypedDict, total=False):
    db: Session
    user_id: int
    session_id: str
    user_question: str
    file_context: str
    user_context: str
    history_text: str
    compressed_context: str
    intent: str
    planner_trace: List[Dict[str, Any]]
    tool_results: List[Dict[str, str]]
    refined_info: str
    executor_prompt: str
    answer_raw: str
    answer_think: str
    answer_visible: str
    review: Dict[str, Any]
    regeneration_count: int
    extracted_memories: Dict[str, Any]
    extraction_error: str
    enable_search: bool
    enable_rag: bool
    tools_enabled: bool
    visual_content: List[Dict[str, Any]]
    visual_file_names: List[str]
    context_before_message_id: int

def _short_text(text: str, limit: int = 1000) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + f"\n...（已截断 {len(text) - limit} 字）"

def _format_extracted_memories(extracted: Dict[str, Any]) -> str:
    important = extracted.get("important_short_term") or []
    candidates = extracted.get("non_important_candidates") or []
    lines = [f"抽取摘要：{extracted.get('summary') or '无'}"]
    if important:
        lines.append("\n重要信息 → 7 天短期记忆：")
        for idx, item in enumerate(important, 1):
            lines.append(
                f"{idx}. {item.get('title') or '未命名'}｜{item.get('content') or ''}"
                f"\n   重要性：{item.get('importance') or 'normal'}；原因：{item.get('reason') or '无'}"
            )
    else:
        lines.append("\n重要信息 → 7 天短期记忆：无")
    if candidates:
        lines.append("\n非重要信息 → 候选池：")
        for idx, item in enumerate(candidates, 1):
            lines.append(
                f"{idx}. {item.get('title') or '未命名'}｜{item.get('content') or ''}"
                f"\n   重要性：{item.get('importance') or 'normal'}；原因：{item.get('reason') or '无'}"
            )
    else:
        lines.append("\n非重要信息 → 候选池：无")
    return _short_text("\n".join(lines), MEMORY_TRACE_CHAR_LIMIT)

def load_context(state: HealthState) -> HealthState:
    db = state["db"]
    user_id = state["user_id"]
    session_id = state["session_id"]
    state["user_context"] = retrieve_user_context(db, user_id, state["user_question"])
    context_before_message_id = state.get("context_before_message_id")

    # 历史上下文策略：长历史优先压缩为 MemorySummary；原文只保留尚未被压缩覆盖的尾部消息。
    if not context_before_message_id:
        compress_session_context(db, user_id, session_id, reason="budget_warning", intent=state.get("intent") or "unknown")
    state["compressed_context"] = retrieve_compressed_context(
        db,
        user_id,
        state["user_question"],
        session_id=session_id,
        before_message_id=context_before_message_id,
    )

    last_summary = db.query(MemorySummary).filter(
        MemorySummary.session_id == session_id,
        MemorySummary.message_to_id.isnot(None),
    )
    if context_before_message_id:
        last_summary = last_summary.filter(MemorySummary.message_to_id < context_before_message_id)
    last_summary = last_summary.order_by(MemorySummary.message_to_id.desc()).first()
    last_compressed_id = last_summary.message_to_id if last_summary else None

    messages_query = db.query(DbMessage).filter(DbMessage.session_id == session_id)
    if last_compressed_id:
        messages_query = messages_query.filter(DbMessage.id > last_compressed_id)
    if context_before_message_id:
        messages_query = messages_query.filter(DbMessage.id < context_before_message_id)
    messages = messages_query.order_by(DbMessage.id).all()

    history_lines = []
    for m in messages:
        cleaned = clean_message_for_context(m.content or "")
        if cleaned:
            history_lines.append(f"{m.role}: {cleaned}")
    state["history_text"] = "\n".join(history_lines) or "无"
    log_agent_event(db, user_id, session_id, "load_context", "加载用户画像、压缩历史和未压缩尾部消息", {"tail_message_count": len(messages), "last_compressed_message_id": last_compressed_id, "context_before_message_id": context_before_message_id})
    return state

def context_manager(state: HealthState) -> HealthState:
    db = state["db"]
    text = f"用户问题：{state['user_question']}\n附件文本：{state.get('file_context','')}\n历史：{state.get('history_text','')}"
    try:
        raw = manager_client.chat([
            {"role": "system", "content": CONTEXT_MANAGER_PROMPT},
            {"role": "user", "content": text},
        ], temperature=CONTEXT_MANAGER_TEMPERATURE, max_tokens=CONTEXT_MANAGER_MAX_TOKENS)
        extracted = extract_json_object(raw)
        persisted_extracted = save_extracted_memories(db, state["user_id"], extracted, source="auto_extract", session_id=state["session_id"])
        if not (persisted_extracted.get("important_short_term") or persisted_extracted.get("non_important_candidates")):
            persisted_extracted["summary"] = "本轮未形成可写入个性化数据库的有效结构化信息。"
        state["extracted_memories"] = persisted_extracted
        log_agent_event(db, state["user_id"], state["session_id"], "context_manager", persisted_extracted.get("summary", "个性化信息抽取"), persisted_extracted)
    except Exception as exc:
        state["extraction_error"] = str(exc)
        log_agent_event(db, state["user_id"], state["session_id"], "context_manager", f"抽取失败：{exc}", {})

    compress_session_context(db, state["user_id"], state["session_id"], reason="budget_warning", intent=state.get("intent") or "unknown")
    return state

def compress_session_context(db: Session, user_id: int, session_id: str, reason: str = "budget_warning", intent: str = "unknown") -> bool:
    """Incrementally compress a session into persistent MemorySummary rows.

    The helper is intentionally idempotent: ``save_memory_summary`` deduplicates
    by ``session_id`` and ``message_to_id``, so it can safely run during context
    loading and again after a completed turn.
    """
    messages = select_messages_for_compression(db, session_id, CONTEXT_COMPRESS_TOKEN_LIMIT)
    if not messages:
        return False
    message_ids = [m.id for m in messages if m.id is not None]
    agent_events = db.query(AgentEvent).filter(
        AgentEvent.session_id == session_id,
    ).order_by(AgentEvent.id.desc()).limit(80).all()
    payload = {
        "session_id": session_id,
        "topic_segment": {
            "id": f"seg_{session_id}_{message_ids[0] if message_ids else 'unknown'}_{message_ids[-1] if message_ids else 'unknown'}",
            "topic_slug": "health-management-dialogue",
            "intent": intent or "unknown",
        },
        "message_range": {
            "from": f"msg_{message_ids[0]}" if message_ids else None,
            "to": f"msg_{message_ids[-1]}" if message_ids else None,
        },
        "messages": [{"id": f"msg_{m.id}", "role": m.role, "content": clean_message_for_context(m.content or ""), "created_at": str(m.created_at)} for m in messages],
        "agent_events": [{"id": f"evt_{e.id}", "agent_name": e.agent_name, "output_summary": clean_message_for_context(e.output_summary or "")} for e in reversed(agent_events)],
        "compression_reason": reason,
    }
    try:
        raw = manager_client.chat([
            {"role": "system", "content": COMPRESSION_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ], temperature=COMPRESSION_TEMPERATURE, max_tokens=COMPRESSION_MAX_TOKENS)
        summary = extract_json_object(raw)
        save_memory_summary(db, user_id, session_id, summary, messages, reason)
        log_agent_event(db, user_id, session_id, "compression_agent", "会话上下文压缩完成", {"reason": reason, "message_count": len(messages)})
        return True
    except Exception as exc:
        log_agent_event(db, user_id, session_id, "compression_agent", f"压缩失败：{exc}", {"reason": reason})
        return False

def intent_analyzer(state: HealthState) -> HealthState:
    tool_results: List[Dict[str, str]] = []
    planner_trace: List[Dict[str, Any]] = []
    intent = "其他"
    messages: List[Dict[str, Any]] = [_planner_user_message(state)]
    available_tools = _available_openai_tools(state.get("enable_search", True), state.get("enable_rag", True), state.get("tools_enabled"))
    _attach_global_execution_plan(state, available_tools)
    planner_trace.extend(state.get("planner_trace", []))
    extra_body = _planner_extra_body(available_tools)
    for step in range(PLANNER_MAX_STEPS):
        message = planner_client.chat_message(messages, temperature=PLANNER_TEMPERATURE, max_tokens=PLANNER_MAX_TOKENS, extra_body=extra_body)
        raw = message.get("content") or ""
        assistant_segment = _planner_assistant_segment(raw)
        action = extract_json_object(assistant_segment)
        intent = action.get("intent") or intent
        openai_tool_calls = []
        tool_calls_for_history = []
        for tool_call in message.get("tool_calls") or []:
            fn = tool_call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {"query": fn.get("arguments") or ""}
            if not isinstance(args, dict):
                args = {"query": str(args)}
            name = fn.get("name", "")
            openai_tool_calls.append({"id": tool_call.get("id"), "name": name, "query": args.get("query", ""), "args": args})
            tool_calls_for_history.append({
                "id": tool_call.get("id"),
                "type": tool_call.get("type", "function"),
                "function": {"name": name, "arguments": args},
            })
        calls = openai_tool_calls or parse_tool_calls(assistant_segment)
        if not calls:
            calls = _fallback_tool_calls_from_text(action, assistant_segment, state["user_question"], available_tools)
        planner_trace.append({"step": step + 1, "raw": assistant_segment, "action": action, "tool_calls": calls})
        if action.get("action") == "planning_finish":
            break
        if calls:
            outputs = run_tool_calls(calls, user_id=state.get("user_id"))
            tool_results.extend(outputs)
            if openai_tool_calls:
                messages.append({"role": "assistant", "content": raw or "", "tool_calls": tool_calls_for_history})
                for call, output in zip(openai_tool_calls, outputs):
                    messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": call.get("name"), "content": output.get("content", "")})
            else:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": "<tool_response>\n" + "\n".join(f"【{x['tool']}】\n{x['content']}" for x in outputs) + "\n</tool_response>"})
        else:
            break
    state["intent"] = intent
    state["planner_trace"] = planner_trace
    state["tool_results"] = tool_results
    log_agent_event(state["db"], state["user_id"], state["session_id"], "intent_analyzer", f"意图：{intent}", {"trace": planner_trace})
    return state

def tool_dispatcher(state: HealthState) -> HealthState:
    return state

def info_refiner(state: HealthState) -> HealthState:
    refs = [ref for ref in state.get("tool_results", []) if _has_reference_content(ref)]
    if not refs:
        state["refined_info"] = "无"
        return state
    raw_refs = "\n\n".join(f"【{i+1}. {r['tool']}】\n查询：{r['query']}\n{r['content']}" for i, r in enumerate(refs))
    try:
        raw = manager_client.chat([
            {"role": "system", "content": INFO_REFINER_PROMPT},
            {"role": "user", "content": raw_refs},
        ], temperature=INFO_REFINER_TEMPERATURE, max_tokens=INFO_REFINER_MAX_TOKENS)
        _, visible = split_think(raw)
        refined = visible or raw
    except Exception:
        refined = raw_refs
    state["refined_info"] = refined
    return state

def _extract_visible_answer(raw: str) -> tuple[str, str]:
    """Return (think, visible) without leaking unfinished <think> blocks to users."""
    think, visible = split_think(raw or "")
    if visible:
        return think, visible
    if "<think>" in (raw or "") or "</think>" in (raw or ""):
        return think, ""
    return think, (raw or "").strip()

def _format_executor_reference_info(state: HealthState, refs: List[Dict[str, str]]) -> str:
    reference_items: List[Tuple[str, str]] = []
    refined_info = (state.get("refined_info") or "").strip()

    if refined_info and refined_info != "无":
        reference_items.append(("info_refiner", refined_info))
    for ref in refs:
        if not _has_reference_content(ref):
            continue
        source = ref.get("tool") or "external_ref"
        content = (ref.get("content") or "").strip()
        if content:
            reference_items.append((source, content))

    if not reference_items:
        return "无"
    return "\n\n".join(f"【{idx}. {source}】\n{content}" for idx, (source, content) in enumerate(reference_items, 1))

def _format_executor_personal_context(state: HealthState) -> str:
    user_context = (state.get("user_context") or "").strip()
    return user_context if user_context and user_context != "无" else "无"

def _format_executor_history_context(state: HealthState) -> str:
    context_items: List[Tuple[str, str]] = []
    compressed_context = (state.get("compressed_context") or "").strip()
    history = (state.get("history_text") or "").strip()

    if compressed_context and compressed_context != "无":
        context_items.append(("压缩历史记忆", compressed_context))
    if history and history != "无":
        context_items.append(("近期对话上下文", history))

    if not context_items:
        return "无"
    return "\n\n".join(f"【{title}】\n{content}" for title, content in context_items)

def _has_reference_content(ref: Dict[str, str]) -> bool:
    content = (ref.get("content") or "").strip()
    if not content:
        return False
    empty_markers = [
        "本地知识库未检索到相关内容",
        "未找到相关本地知识库内容",
        "未检索到相关内容",
        "网络检索未返回相关内容",
        "未找到相关网络搜索结果",
    ]
    return not any(marker in content for marker in empty_markers)

def _reviewer_payload(state: HealthState) -> str:
    """构造审核输入：让 reviewer 看到回答所依据的完整上下文，避免误判为臆测。"""
    refs = state.get("tool_results", [])
    reference_info = _format_executor_reference_info(state, refs)
    visual_names = state.get("visual_file_names") or []
    visual_note = "、".join(str(name) for name in visual_names) if visual_names else "无"
    return (
        "请基于以下完整上下文审核回答。若回答中的个性化信息可由用户画像、压缩历史记忆、近期对话、附件或参考信息支持，"
        "不要判定为臆测；只有在所有上下文都无法支持时，才判定为无依据。\n\n"
        f"【用户问题】\n{state['user_question']}\n\n"
        f"【识别意图】\n{state.get('intent') or '其他'}\n\n"
        f"【用户个人信息/用户画像】\n{_short_text(state.get('user_context', '无'), 1800)}\n\n"
        f"【压缩历史记忆】\n{_short_text(state.get('compressed_context', '无'), 1800)}\n\n"
        f"【近期对话上下文】\n{_short_text(state.get('history_text', '无'), 1800)}\n\n"
        f"【附件/文件内容】\n{_short_text(state.get('file_context', '无'), 1400)}\n\n"
        f"【视觉附件文件名】\n{visual_note}\n\n"
        f"【工具/参考信息】\n{_short_text(reference_info, 2400)}\n\n"
        f"【执行器完整提示词】\n{_short_text(state.get('executor_prompt', '无'), 2400)}\n\n"
        f"【待审核回答】\n{_short_text(state.get('answer_visible', ''), 2400)}"
    )

def _build_executor_prompt(state: HealthState) -> str:
    intent = state.get("intent", "其他")
    answer_advice = INTENT_TEMPLATES.get(intent, INTENT_TEMPLATES.get("其他", ""))
    refs = state.get("tool_results", [])
    external = _format_executor_reference_info(state, refs)
    file_context = (state.get("file_context") or "").strip() or "无"
    personal_context = _format_executor_personal_context(state)
    history_context = _format_executor_history_context(state)
    visual_names = state.get("visual_file_names") or []
    visual_note = "、".join(str(name) for name in visual_names) if visual_names else "无"
    return (
        f"【用户个性化内容】\n{personal_context}\n\n"
        f"【历史对话信息】\n{history_context}\n\n"
        f"【用户问题】\n{state['user_question']}\n\n"
        f"【识别意图】\n{intent}\n\n"
        f"【回答建议】\n{answer_advice}\n\n"
        f"【附件/文件内容】\n{file_context}\n\n"
        f"【视觉附件文件名】\n{visual_note}\n\n"
        f"【外部参考信息（RAG/网络检索/工具整理）】\n{external}"
    )

def llm_generator(state: HealthState) -> HealthState:
    prompt = _build_executor_prompt(state)
    state["executor_prompt"] = prompt
    raw = executor_client.chat([
        _executor_user_message(prompt, state),
    ], temperature=EXECUTOR_TEMPERATURE, max_tokens=EXECUTOR_MAX_TOKENS)
    think, visible = _extract_visible_answer(raw)
    state["answer_raw"] = raw
    state["answer_think"] = think
    state["answer_visible"] = visible or "抱歉，本轮生成结果未能形成可展示的正式回答，请再试一次。"
    return state

def stream_llm_generator(state: HealthState) -> Iterator[str]:
    """Stream executor answer deltas and populate final answer fields in state."""
    prompt = _build_executor_prompt(state)
    state["executor_prompt"] = prompt
    raw_parts: List[str] = []
    for delta in executor_client.chat_stream([
        _executor_user_message(prompt, state),
    ], temperature=EXECUTOR_TEMPERATURE, max_tokens=EXECUTOR_MAX_TOKENS):
        raw_parts.append(delta)
        yield delta
    raw = "".join(raw_parts)
    think, visible = _extract_visible_answer(raw)
    state["answer_raw"] = raw
    state["answer_think"] = think
    state["answer_visible"] = visible or raw.strip() or "抱歉，本轮生成结果未能形成可展示的正式回答，请再试一次。"

def feedback_reviewer(state: HealthState) -> HealthState:
    payload = _reviewer_payload(state)
    try:
        raw = reviewer_client.chat([
            {"role": "system", "content": REVIEWER_PROMPT},
            {"role": "user", "content": payload},
        ], temperature=REVIEWER_TEMPERATURE, max_tokens=REVIEWER_MAX_TOKENS)
        review = extract_json_object(raw) or {"approved": True, "reason": "审核通过", "issues": []}
    except Exception as exc:
        review = {"approved": True, "reason": f"审核服务不可用，默认通过：{exc}", "issues": []}
    state["review"] = review
    if not review.get("approved", True):
        db = state["db"]
        db.add(FeedbackRecord(
            user_id=state["user_id"], session_id=state["session_id"], feedback_type="compliance_fail",
            reason=review.get("reason", ""), question=state["user_question"], answer=state.get("answer_visible", ""), metadata_json=safe_json_dumps(review)
        ))
        db.commit()
    return state

def should_regenerate(state: HealthState) -> str:
    if state.get("review", {}).get("approved", True):
        return "memory_updater"
    if state.get("regeneration_count", 0) >= 1:
        return "memory_updater"
    return "regenerate"

def regenerate(state: HealthState) -> HealthState:
    state["regeneration_count"] = state.get("regeneration_count", 0) + 1
    guidance = state.get("review", {}).get("rewrite_guidance") or state.get("review", {}).get("reason", "请更谨慎合规")
    state["executor_prompt"] = state.get("executor_prompt", "") + f"\n\n【反馈审核要求】\n上一版不合规：{guidance}\n请重新生成。"
    raw = executor_client.chat([
        _executor_user_message(state["executor_prompt"], state),
    ], temperature=REGENERATION_TEMPERATURE, max_tokens=EXECUTOR_MAX_TOKENS)
    think, visible = _extract_visible_answer(raw)
    state["answer_raw"] = raw
    state["answer_think"] = think
    state["answer_visible"] = visible or "抱歉，本轮重新生成结果未能形成可展示的正式回答，请再试一次。"
    return state

def stream_regenerate(state: HealthState) -> Iterator[str]:
    """Stream a reviewer-guided regeneration and update state with final answer."""
    state["regeneration_count"] = state.get("regeneration_count", 0) + 1
    guidance = state.get("review", {}).get("rewrite_guidance") or state.get("review", {}).get("reason", "请更谨慎合规")
    base_prompt = state.get("executor_prompt") or _build_executor_prompt(state)
    state["executor_prompt"] = base_prompt + f"\n\n【反馈审核要求】\n上一版不合规：{guidance}\n请重新生成。"
    raw_parts: List[str] = []
    for delta in executor_client.chat_stream([
        _executor_user_message(state["executor_prompt"], state),
    ], temperature=REGENERATION_TEMPERATURE, max_tokens=EXECUTOR_MAX_TOKENS):
        raw_parts.append(delta)
        yield delta
    raw = "".join(raw_parts)
    think, visible = _extract_visible_answer(raw)
    state["answer_raw"] = raw
    state["answer_think"] = think
    state["answer_visible"] = visible or raw.strip() or "抱歉，本轮重新生成结果未能形成可展示的正式回答，请再试一次。"

def memory_updater(state: HealthState) -> HealthState:
    log_agent_event(state["db"], state["user_id"], state["session_id"], "memory_updater", "工作流完成，记忆已按需更新", {"review": state.get("review", {})})
    return state

def build_graph():
    graph = StateGraph(HealthState)
    graph.add_node("load_context", load_context)
    graph.add_node("context_manager", context_manager)
    graph.add_node("intent_analyzer", intent_analyzer)
    graph.add_node("tool_dispatcher", tool_dispatcher)
    graph.add_node("info_refiner", info_refiner)
    graph.add_node("llm_generator", llm_generator)
    graph.add_node("feedback_reviewer", feedback_reviewer)
    graph.add_node("regenerate", regenerate)
    graph.add_node("memory_updater", memory_updater)
    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "context_manager")
    graph.add_edge("context_manager", "intent_analyzer")
    graph.add_edge("intent_analyzer", "tool_dispatcher")
    graph.add_edge("tool_dispatcher", "info_refiner")
    graph.add_edge("info_refiner", "llm_generator")
    graph.add_edge("llm_generator", "feedback_reviewer")
    graph.add_conditional_edges("feedback_reviewer", should_regenerate, {"regenerate": "regenerate", "memory_updater": "memory_updater"})
    graph.add_edge("regenerate", "feedback_reviewer")
    graph.add_edge("memory_updater", END)
    return graph.compile()

health_graph = build_graph()

def run_health_workflow(
    db: Session,
    user_id: int,
    session_id: str,
    user_question: str,
    file_context: str = "",
    visual_content: List[Dict[str, Any]] | None = None,
    visual_file_names: List[str] | None = None,
) -> HealthState:
    initial: HealthState = {
        "db": db,
        "user_id": user_id,
        "session_id": session_id,
        "user_question": user_question,
        "file_context": file_context,
        "visual_content": visual_content or [],
        "visual_file_names": visual_file_names or [],
        "regeneration_count": 0,
    }
    return health_graph.invoke(initial)

def _clean_planner_stream_text(raw: str) -> str:
    if not raw:
        return ""
    text = raw.replace("<|im_end|>", "")
    text = text.replace("<think>", "").replace("</think>", "")
    text = text.split("<tool_call>", 1)[0]
    marker = '{"intent"'
    if marker in text:
        text = text.split(marker, 1)[0]
    return text.strip()

def stream_health_workflow_events(
    db: Session,
    user_id: int,
    session_id: str,
    user_question: str,
    file_context: str = "",
    enable_search: bool = True,
    enable_rag: bool = True,
    tools_enabled: bool | None = None,
    visual_content: List[Dict[str, Any]] | None = None,
    visual_file_names: List[str] | None = None,
    context_before_message_id: int | None = None,
    stop_after_execution: bool = False,
    stop_before_execution: bool = False,
) -> Iterator[Tuple[Dict[str, Any], HealthState]]:
    """Run the health workflow step-by-step and emit UI-friendly events.

    The compiled LangGraph workflow remains available via ``run_health_workflow``.
    This generator mirrors the graph order so the HTTP stream can render each
    agent stage as it happens instead of waiting for ``graph.invoke`` to finish.
    """
    state: HealthState = {
        "db": db,
        "user_id": user_id,
        "session_id": session_id,
        "user_question": user_question,
        "file_context": file_context,
        "enable_search": enable_search,
        "enable_rag": enable_rag,
        "tools_enabled": tools_enabled if tools_enabled is not None else (enable_search or enable_rag),
        "visual_content": visual_content or [],
        "visual_file_names": visual_file_names or [],
        "context_before_message_id": context_before_message_id,
        "regeneration_count": 0,
    }

    yield {"type": "context_start", "title": "上下文与记忆", "text": "正在加载用户画像、个性化记忆和近期对话上下文。"}, state
    state = load_context(state)
    yield {
        "type": "context_done",
        "title": "上下文与记忆",
        "text": "已加载用户画像、个性化记忆和近期对话上下文。\n\n"
                f"【用户个人信息】\n{_short_text(state.get('user_context', '无'), TRACE_CONTEXT_CHAR_LIMIT)}\n\n"
                f"【压缩历史记忆】\n{_short_text(state.get('compressed_context', '无'), TRACE_CONTEXT_CHAR_LIMIT)}\n\n"
                f"【近期对话上下文】\n{_short_text(state.get('history_text', '无'), TRACE_CONTEXT_CHAR_LIMIT)}",
    }, state

    yield {"type": "memory_start", "title": "信息管理", "text": "正在抽取可沉淀的个性化信息并检查上下文压缩。"}, state
    state = context_manager(state)
    if state.get("extracted_memories"):
        memory_text = "个性化信息管理完成。\n\n" + _format_extracted_memories(state.get("extracted_memories", {}))
    elif state.get("extraction_error"):
        memory_text = f"个性化信息抽取失败：{state.get('extraction_error')}"
    else:
        memory_text = "个性化信息管理完成。\n\n本轮未抽取到可沉淀的个性化信息。"
    yield {"type": "memory_done", "title": "信息管理", "text": memory_text}, state

    yield {"type": "planning_start", "title": "规划阶段", "text": "规划智能体正在判断意图、工具需求和执行路线。"}, state
    tool_results: List[Dict[str, str]] = []
    planner_trace: List[Dict[str, Any]] = []
    intent = "其他"
    available_tools = _available_openai_tools(enable_search, enable_rag, state.get("tools_enabled"))
    _attach_global_execution_plan(state, available_tools)
    if state.get("global_execution_plan"):
        planner_trace.append({"step": 0, "action": "global_execution_plan", "plan": state["global_execution_plan"]})
        yield {
            "type": "planning_delta",
            "title": "全局执行计划",
            "text": "已生成全局多智能体执行计划，并附加统一 Agent prompt trace。",
        }, state
    extra_body = _planner_extra_body(available_tools)
    planner_messages: List[Dict[str, Any]] = [_planner_user_message(state)]
    intent_emitted = False
    for step in range(PLANNER_MAX_STEPS):
        message = planner_client.chat_message(planner_messages, temperature=PLANNER_TEMPERATURE, max_tokens=PLANNER_MAX_TOKENS, extra_body=extra_body)
        raw = message.get("content") or ""
        assistant_segment = _planner_assistant_segment(raw)
        action = extract_json_object(assistant_segment)
        if action.get("intent") and (not intent_emitted or action.get("intent") != intent):
            intent = action.get("intent") or intent
            state["intent"] = intent
            intent_emitted = True
            yield {"type": "intent", "title": "意图识别", "intent": intent, "text": f"识别意图：{intent}"}, state

        openai_tool_calls = []
        tool_calls_for_history = []
        for tool_call in message.get("tool_calls") or []:
            fn = tool_call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {"query": fn.get("arguments") or ""}
            if not isinstance(args, dict):
                args = {"query": str(args)}
            name = fn.get("name", "")
            openai_tool_calls.append({"id": tool_call.get("id"), "name": name, "query": args.get("query", ""), "args": args})
            tool_calls_for_history.append({
                "id": tool_call.get("id"),
                "type": tool_call.get("type", "function"),
                "function": {"name": name, "arguments": args},
            })
        calls = openai_tool_calls or parse_tool_calls(assistant_segment)
        if not calls:
            calls = _fallback_tool_calls_from_text(action, assistant_segment, state["user_question"], available_tools)
        trace = {"step": step + 1, "raw": assistant_segment, "action": action, "tool_calls": calls}
        planner_trace.append(trace)
        cleaned = _clean_planner_stream_text(assistant_segment)
        if cleaned:
            yield {"type": "planning_delta", "title": "规划阶段", "text": cleaned}, state
        if action.get("action") == "planning_finish":
            yield {"type": "planning_done", "title": "规划完成", "text": action.get("finish_reason") or "信息足够，进入执行阶段。"}, state
            break
        if not calls:
            break
        outputs = []
        for call in calls:
            yield {"type": "tool_start", "title": "工具调用", "name": call.get("name", ""), "query": call.get("query", "")}, state
            output = run_tool_calls([call], user_id=state.get("user_id"))[0]
            outputs.append(output)
            tool_results.append(output)
            state["tool_results"] = tool_results
            yield {
                "type": "tool_done",
                "title": "工具返回",
                "name": output.get("tool", call.get("name", "")),
                "query": output.get("query", call.get("query", "")),
                "text": (output.get("content", "") or "")[:TOOL_TRACE_CHAR_LIMIT],
            }, state
        if openai_tool_calls:
            planner_messages.append({"role": "assistant", "content": raw or "", "tool_calls": tool_calls_for_history})
            for call, output in zip(openai_tool_calls, outputs):
                planner_messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": call.get("name"), "content": output.get("content", "")})
        else:
            planner_messages.append({"role": "assistant", "content": assistant_segment})
            planner_messages.append({"role": "user", "content": "<tool_response>\n" + "\n".join(f"【{x['tool']}】\n{x['content']}" for x in outputs) + "\n</tool_response>"})
    state["intent"] = intent
    state["planner_trace"] = planner_trace
    state["tool_results"] = tool_results
    log_agent_event(state["db"], state["user_id"], state["session_id"], "intent_analyzer", f"意图：{intent}", {"trace": planner_trace})

    yield {"type": "refine_start", "title": "信息整理", "text": "信息管理智能体正在整理工具返回内容。"}, state
    state = info_refiner(state)
    yield {
        "type": "refine_done",
        "title": "信息整理",
        "text": "参考信息整理完成。\n\n" + _short_text(state.get("refined_info", "无"), 1400),
    }, state

    yield {"type": "execution_start", "title": "执行阶段", "text": "执行智能体正在生成回答。"}, state
    if stop_before_execution:
        return
    state = llm_generator(state)
    if state.get("answer_think"):
        yield {"type": "execution_delta", "title": "执行阶段", "text": state.get("answer_think")}, state
    yield {"type": "execution_done", "title": "执行阶段", "text": "执行阶段完成，开始输出正式回答。"}, state
    if stop_after_execution:
        return

    yield {"type": "feedback_start", "title": "反馈审核", "text": "反馈智能体正在后台检查回答质量与合规性。"}, state
    state = feedback_reviewer(state)
    while not state.get("review", {}).get("approved", True) and state.get("regeneration_count", 0) < 1:
        yield {"type": "feedback_regenerate", "title": "反馈审核", "text": state.get("review", {}).get("reason", "需要重新生成。")}, state
        state = regenerate(state)
        state = feedback_reviewer(state)
    yield {"type": "feedback_done", "title": "反馈审核", "text": state.get("review", {}).get("reason", "审核通过。"), "approved": state.get("review", {}).get("approved", True)}, state

    state = memory_updater(state)
    yield {"type": "workflow_done", "title": "工作流完成", "text": "所有智能体阶段已完成。"}, state