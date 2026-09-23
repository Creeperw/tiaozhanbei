from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel


_INTERNAL_REASON_MARKERS = (
    "agent",
    "routing",
    "requires_compression",
    "系统已补全",
    "确定性依赖节点",
)

_INTERNAL_RESOURCE_FIELDS = {
    "kp_id",
    "kp_ids",
    "question_id",
    "resource_type",
    "source_id",
}

_RESOURCE_FIELD_LABELS = {
    "kp_name": "知识点",
    "exp": "讲解",
    "question_type": "题型",
    "stem": "题目",
    "options": "选项",
    "tags": "相关知识点",
    "title": "标题",
    "summary": "简介",
    "url": "链接",
}

# 失败原因码到学习者可见文案的唯一映射。
#
# 这里只放“原因码 → 一句可读中文”，不放任何内部异常、步骤名或 provider
# 细节。HTTP 入口与会话持久化入口共用同一份：此前两边各有一张表，内容已经
# 漂移——`audit_step_timeout` 只存在于会话消息表里，走 HTTP 失败路径的学习
# 者看到的是通用兜底句，同一故障在两个入口显示不同文案。
FAILURE_USER_MESSAGES: dict[str, str] = {
    "knowledge_timeout": "知识检索超时，已保存当前会话，请稍后重试。",
    "knowledge_step_failed": "知识检索未能完成，请稍后重试。",
    "paper_blueprint_timeout": "试卷蓝图生成超时，请稍后重试。",
    "model_timeout": "模型调用超时，请稍后重试。",
    "model_invalid_output": "模型输出格式不正确，请重新生成。",
    "invalid_json": "模型返回的 JSON 格式不完整，请重新生成。",
    "ambiguous_json": "模型返回了多个相互冲突的结果，请重新生成。",
    "schema_invalid": "模型结果字段不符合要求，请重新生成。",
    "business_schema_invalid": "模型结果字段不符合要求，请重新生成。",
    "business_validation_failed": "生成结果未满足本次业务约束，请调整要求后重试。",
    "provider_schema_unsupported": "当前模型通道不支持所需输出格式，请稍后重试。",
    "output_truncated": "模型结果未完整生成，请重新生成。",
    "workflow_timeout": "本次处理超时，已保存当前会话，请稍后重试。",
    "plan_compilation_failed": "学习规划未能通过结构化校验，请稍后重试。",
    "audit_step_timeout": "内容审核超时，已保存当前会话，请稍后重试。",
    "audit_step_failed": "内容审核未能完成，请稍后重试。",
    "daily_task_publication_failed": "今日任务发布未能完成，请稍后重试。",
    "paper_generation_failed": "试卷生成未能完成，请稍后重试。",
    "persistence_failed": "结果保存失败，请稍后重试。",
    "model_empty_response": "模型暂时没有返回内容，请再试一次。",
    "model_transport_error": "模型连接暂时不稳定，请稍后重试。",
    "exam_workspace_changed": (
        "考试目标已切换，旧任务不能继续；"
        "请在当前考试下重新发起该请求。"
    ),
}

FAILURE_USER_MESSAGE_FALLBACK = "这次处理没有成功完成，请稍后重试。"


def failure_user_message(error_code: object) -> str:
    """把内部失败原因码翻译成学习者可读的一句话。"""

    return FAILURE_USER_MESSAGES.get(
        str(error_code or ""),
        FAILURE_USER_MESSAGE_FALLBACK,
    )


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _format_minutes(value: Any) -> str:
    """分钟数展示：整数去掉小数尾零（15.0 → 15），小数保留（19.5 → 19.5）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _markdown_value(value: Any, depth: int = 0) -> str:
    value = _plain(value)
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        rows = []
        for item in value:
            rendered = _markdown_value(item, depth + 1).strip()
            if rendered:
                rows.append(f"- {rendered.replace(chr(10), chr(10) + '  ')}")
        return "\n".join(rows)
    if isinstance(value, dict):
        rows = []
        for key, item in value.items():
            if str(key) in _INTERNAL_RESOURCE_FIELDS:
                continue
            if str(key) == "summary" and isinstance(item, str):
                item = item[:240].rstrip() + ("…" if len(item) > 240 else "")
            rendered = _markdown_value(item, depth + 1).strip()
            if not rendered:
                continue
            label = _RESOURCE_FIELD_LABELS.get(
                str(key),
                str(key).replace("_", " "),
            )
            if re.match(r"^(?:#{1,6}\s+|[-*+]\s+|>\s+)", rendered):
                rows.append(f"**{label}**：\n\n{rendered}")
            else:
                rows.append(f"**{label}**：{rendered}")
        return "\n\n".join(rows)
    return json.dumps(value, ensure_ascii=False, default=str)


def _structured_stage_block(long_term: dict[str, Any]) -> str:
    stages = long_term.get("stages") or []
    if not isinstance(stages, list) or not stages:
        return ""
    compact_stages = []
    for item in stages:
        stage = _plain(item)
        if not isinstance(stage, dict):
            continue
        compact_stages.append(
            {
                "stage": stage.get("stage"),
                "book": list(stage.get("book") or []),
                "goal": stage.get("goal"),
            }
        )
    if not compact_stages:
        return ""
    payload = {"long_term_plan_stages": compact_stages}
    return "【阶段路线数据】\n```json\n" + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ) + "\n```"


def _normalize_learner_markdown(value: str) -> str:
    """Repair a common provider formatting defect without changing wording.

    Some models correctly generate Markdown emphasis but flatten every list
    marker onto one physical line (``说明：- **条目**``).  Markdown parsers
    must then treat the whole answer as a paragraph.  Only repair unmistakable
    bold-list markers; ordinary hyphens and prose remain untouched.
    """

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    inline_markers = re.findall(r"(?<!\n)-\s+(?=\S)", text)
    if len(inline_markers) >= 2:
        # Requiring at least two markers avoids treating a normal prose
        # hyphen as a list.  Providers may omit both Markdown emphasis and
        # punctuation, so bold-only repair is insufficient.
        repaired = re.sub(r"\s*-\s+(?=\S)", "\n- ", text)
        repaired = re.sub(r"([：:])\n- ", r"\1\n\n- ", repaired)
        repaired = re.sub(r"[；;]\n- ", "\n- ", repaired)
    else:
        repaired = re.sub(r"([：:])\s*-\s+(?=\*\*[^*]+\*\*)", r"\1\n\n- ", text)
        repaired = re.sub(r"[；;]\s*-\s+(?=\*\*[^*]+\*\*)", "\n- ", repaired)
    if repaired != text:
        repaired = re.sub(
            r"([。！？])(?=(?:您|你)(?:目前|当前|现在|可以|还可以|也可以)|请问|如果|接下来)",
            r"\1\n\n",
            repaired,
        )
        repaired = re.sub(
            r"(?<![。！？；;\n])(?=(?:您|你)(?:目前|当前|现在|可以)|请问|如果|接下来)",
            "。\n\n",
            repaired,
        )
    # Capability answers sometimes avoid list markers but still squeeze three
    # or more bold capability labels with parenthesised descriptions into one
    # paragraph.  This pattern is unambiguous enough to format safely without
    # inventing or rewriting any content.
    if "\n- " not in repaired and any(
        marker in repaired for marker in ("这些事", "这些帮助", "可以帮", "能够帮")
    ):
        capabilities = list(re.finditer(r"\*\*[^*]+\*\*（[^（）]*）", repaired))
        if len(capabilities) >= 3:
            prefix = repaired[: capabilities[0].start()]
            prefix = re.sub(r"选择性地列几条[：:]?\s*$", "", prefix).rstrip()
            items = [match.group(0).strip() for match in capabilities]
            tail = repaired[capabilities[-1].end() :]
            tail = re.sub(r"^[、，,；;\s]*(?:以及)?\s*", "", tail).lstrip("。 ")
            repaired = (
                f"{prefix}\n\n"
                + "\n".join(f"- {item}" for item in items)
                + (f"\n\n{tail}" if tail else "")
            )
    repaired = re.sub(
        r"(^-\s+[^\n]*?[。！？])(?=(?:您|你)(?:目前|当前|现在)|请问|如果|接下来)",
        r"\1\n\n",
        repaired,
        flags=re.MULTILINE,
    )
    return re.sub(r"\n{3,}", "\n\n", repaired).strip()


def workflow_result_to_markdown(result: Any) -> str:
    """Build the natural-language chat projection; structured data stays in the result."""

    body = _plain(result) or {}
    if body.get("status") == "waiting_human_review":
        # 兼容历史会话：该终态已不再产生（见 runtime/local_repair.py）。审核
        # 报告、findings 与待复核草稿属于内部审核信息，任何情况下都不得
        # 渲染到用户会话里；这里只给出中性的进度说明。
        return "本次内容已按审核意见完成修订，审核过程信息不对外展示。"

    if body.get("status") == "interrupted":
        interruption = body.get("interrupt") or {}
        questions = [
            str(question).strip()
            for question in interruption.get("questions", [])
            if str(question).strip()
        ]
        intro = str(interruption.get("reason") or "我还需要确认一些信息。").strip()
        if any(marker in intro.lower() for marker in _INTERNAL_REASON_MARKERS):
            intro = "为了继续为你安排合适的学习内容，我还需要确认一点信息。"
        continuation = "流程已在当前节点暂停；回答后会从检查点继续，不会重复已完成的步骤。"
        question_text = "\n".join(
            f"{index}. {question}" for index, question in enumerate(questions, 1)
        )
        return "\n\n".join(part for part in (intro, continuation, question_text) if part)

    if body.get("task_type") == "casual_conversation":
        return _normalize_learner_markdown(str(
            body.get("direct_response")
            or "你好！我是时珍智训智能助教。有什么想学习或练习的内容，可以直接告诉我。"
        ).strip())

    if body.get("task_type") == "learner_data_query":
        return str(
            body.get("direct_response")
            or "暂时没有查到可用于回答的学习记录。"
        ).strip()

    if body.get("task_type") == "review_task_adjustment":
        # 复习任务调整的正文由用例内联拼装（引用真实变更），直接透出。
        return str(
            body.get("direct_response")
            or "复习任务安排已按你的要求更新。"
        ).strip()

    if body.get("task_type") == "paper_generation":
        direct_response = str(body.get("direct_response") or "").strip()
        if direct_response:
            # 空态组卷（候选池无合规题目且未补足）直接展示面向用户的提示，
            # 不走“试卷已保存到学习工坊”的发布文案。
            return direct_response
        actions = body.get("ui_actions") or []
        has_answer_action = any(
            isinstance(item, dict) and item.get("destination") == "workshop.paper"
            for item in actions
        )
        if has_answer_action:
            return "试卷已经完成组卷并通过审核。试卷正文已保存到学习工坊，请点击下方“开始答题”进入计时答题界面。"
        return "试卷已经完成组卷并通过审核。试卷正文已保存到学习工坊，可前往试卷生成页面查看。"

    plan = _plain(body.get("learning_plan"))
    if not plan:
        outputs = body.get("agent_outputs") or []
        for output in outputs:
            output = _plain(output) or {}
            if output.get("producer") == "learning_plan_service":
                plan = _plain(output.get("payload"))
                break
    if plan:
        if plan.get("requires_clarification"):
            questions = plan.get("clarification_questions") or []
            intro = plan.get("reason") or "为了让接下来的安排真正适合你，我还需要确认一点信息。"
            return "\n\n".join(
                [str(intro), *[f"{index}. {question}" for index, question in enumerate(questions, 1)]]
            )
        generated_scope = plan.get("generated_scope")
        intros = {
            "long_term": "长期规划已经整理好。",
            "short_term": "短期计划已经整理好。",
            "daily_task": "当日任务已经结合当前短期计划安排好。",
        }
        reused_existing = bool(plan.get("reused_existing"))
        if reused_existing:
            reused_labels = {
                "long_term": "你已有有效的长期规划，我会继续沿用当前正式版本。",
                "short_term": "你已有有效的短期计划，我会继续沿用当前正式版本。",
                "daily_task": "今天已有有效任务，我会继续沿用，不重复生成。",
            }
            parts = [
                reused_labels.get(
                    generated_scope,
                    "当前已有有效学习计划，我会继续沿用正式版本。",
                )
            ]
        else:
            parts = [intros.get(generated_scope, "我已经结合你的目标和当前信息整理好了安排。")]
        long_term = _plain(plan.get("long_term_plan")) or {}
        short_term = _plain(plan.get("short_term_plan")) or {}
        learning_task = _plain(plan.get("learning_task")) or {}
        if long_term.get("content"):
            parts.append(str(long_term["content"]))
            stage_block = _structured_stage_block(long_term)
            if stage_block:
                parts.append(stage_block)
        if short_term.get("content"):
            parts.append(str(short_term["content"]))
        if learning_task:
            focus_points = learning_task.get("focus_knowledge_points") or []
            daily_schedule = _plain(
                learning_task.get("daily_task_schedule")
            ) or {}
            scheduling_explanation = str(
                daily_schedule.get("explanation") or ""
            ).strip()
            task_parts = [
                (
                    f"今日章节：{learning_task['learning_chapter']}"
                    if learning_task.get("learning_chapter")
                    else None
                ),
                (
                    f"重点知识点：{'、'.join(str(item) for item in focus_points)}"
                    if focus_points
                    else None
                ),
                learning_task.get("task_content"),
                (
                    f"验收标准：{learning_task['completion_criteria']}"
                    if learning_task.get("completion_criteria")
                    else None
                ),
                (
                    f"预计用时：{_format_minutes(learning_task['estimated_minutes'])} 分钟"
                    if learning_task.get("estimated_minutes")
                    else None
                ),
                (
                    f"安排说明：{scheduling_explanation}"
                    if scheduling_explanation
                    else None
                ),
            ]
            parts.append("\n\n".join(str(item) for item in task_parts if item))
        if plan.get("force_replan_prompt"):
            parts.append(str(plan["force_replan_prompt"]))
        return "\n\n".join(part for part in parts if part)

    resource = _plain(body.get("resource")) or {}
    if resource:
        content = _markdown_value(resource.get("content") or {})
        audit = _plain(body.get("audit")) or {}
        reminders = [
            str(item).removeprefix("实时信息提示：").strip()
            for item in audit.get("findings", [])
            if str(item).startswith("实时信息提示：")
        ]
        reminder_text = "\n".join(f"提示：{item}" for item in reminders)
        actions = body.get("ui_actions") or []
        action_hint = ""
        if actions:
            labels = [
                str(item.get("label") or "").strip()
                for item in actions
                if isinstance(item, dict) and str(item.get("label") or "").strip()
            ]
            if labels:
                action_hint = "\n\n你可以点击下方的“" + "”或“".join(labels) + "”继续。"
        return "\n\n".join(
            part
            for part in (
                content,
                reminder_text,
                action_hint.strip(),
            )
            if part
        )

    return "本次处理已经完成。你可以继续补充目标或提出下一步需求。"
