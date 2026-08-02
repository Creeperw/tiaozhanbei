from __future__ import annotations

import json
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


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


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
            rows.append(f"**{label}**：{rendered}")
        return "\n\n".join(rows)
    return json.dumps(value, ensure_ascii=False, default=str)


def _human_review_draft(agent_outputs: Any) -> str:
    """Extract the expert-generated resource draft for human review.

    The waiting_human_review receipt carries ``agent_outputs`` including the
    expert's ResourceDraft. Rendering its content lets the user actually
    review what was generated, instead of only seeing the audit findings.
    """
    outputs = _plain(agent_outputs) or []
    if not isinstance(outputs, list):
        outputs = [outputs]
    for output in outputs:
        output = _plain(output) or {}
        if output.get("producer") != "expert_agent":
            continue
        payload = _plain(output.get("payload")) or {}
        content = payload.get("content")
        if isinstance(content, dict):
            rendered = _markdown_value(content)
        elif isinstance(content, str) and content.strip():
            rendered = str(content).strip()
        else:
            continue
        if not rendered:
            continue
        title = str(payload.get("title") or "待复核内容").strip()
        return f"「{title}」\n\n{rendered}"
    return ""


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


def workflow_result_to_markdown(result: Any) -> str:
    """Build the natural-language chat projection; structured data stays in the result."""

    body = _plain(result) or {}
    if body.get("status") == "waiting_human_review":
        review = _plain(body.get("review")) or {}
        audit_report = str(review.get("audit_report") or "").strip()
        findings = [
            str(item).strip()
            for item in review.get("findings", [])
            if str(item).strip()
        ]
        review_content = _human_review_draft(body.get("agent_outputs") or [])
        parts = ["本次内容已进入人工复核，复核完成前不会发布。"]
        if review_content:
            parts.append(f"### 待复核内容\n\n{review_content}")
        if audit_report:
            parts.append(f"### 审核意见\n\n{audit_report}")
        if findings:
            parts.append(
                "### 需要确认的问题\n\n"
                + "\n".join(f"- {item}" for item in findings)
            )
        parts.append(
            "请核对待复核内容与审核意见：若内容无误，确认后我会继续发布；"
            "如需调整，可以直接告诉我修改意见。"
        )
        return "\n\n".join(part for part in parts if part)

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
        return str(
            body.get("direct_response")
            or "你好！我是时珍智训智能助教。有什么想学习或练习的内容，可以直接告诉我。"
        ).strip()

    if body.get("task_type") == "learner_data_query":
        return str(
            body.get("direct_response")
            or "暂时没有查到可用于回答的学习记录。"
        ).strip()

    if body.get("task_type") == "paper_generation":
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
                    f"预计用时：{learning_task['estimated_minutes']} 分钟"
                    if learning_task.get("estimated_minutes")
                    else None
                ),
            ]
            parts.append("\n\n".join(str(item) for item in task_parts if item))
        if plan.get("force_replan_prompt"):
            parts.append(str(plan["force_replan_prompt"]))
        return "\n\n".join(part for part in parts if part)

    resource = _plain(body.get("resource")) or {}
    if resource:
        title = resource.get("title") or "学习内容"
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
                f"下面是为你整理的「{title}」。",
                content,
                reminder_text,
                action_hint.strip(),
            )
            if part
        )

    return "本次处理已经完成。你可以继续补充目标或提出下一步需求。"
