from __future__ import annotations

import json
import re
from typing import Any, Callable


class PlannerLikeCasualBoundary:
    """Offline model behavior only; production routing is decided by the LLM."""

    @staticmethod
    def matches(request: str) -> bool:
        normalized = "".join(
            character
            for character in request.strip().lower()
            if character not in "，。！？!?、,.；;：:~～ \t\r\n"
        )
        return normalized in {
            "你好", "你好啊", "您好", "您好啊", "嗨", "hi", "hello",
            "在吗", "早上好", "上午好", "下午好", "晚上好",
            "谢谢", "谢谢你", "感谢", "感谢你", "多谢", "不客气",
            "再见", "拜拜", "bye", "先这样", "下次再聊",
            "你是谁", "你能做什么", "你可以做什么", "你会什么",
        }


class StubChatModel:
    async def complete_text(
        self,
        role: str,
        payload: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
    ) -> str:
        """Offline compatibility path for business-agent prose calls."""
        result = await self.complete_json(role, payload, on_delta=None)
        if role == "diagnosis_agent" and isinstance(result, dict):
            text = self._diagnosis_plan_document(result)
        else:
            body = (
                result.get("explanation_content")
                or result.get("content")
                or result.get("body")
                or result.get("summary")
                or result.get("learning_tip")
                or ""
            )
            text = str(body)
        if on_delta and text:
            on_delta(text)
        return text

    @staticmethod
    def _diagnosis_plan_document(result: dict[str, Any]) -> str:
        """Render the stub's business result as prose, never as a contract JSON."""

        parts: list[str] = []
        if result.get("long_term_plan_content"):
            parts.extend([
                "## 长期规划正文",
                str(result["long_term_plan_content"]),
            ])
        if result.get("total_duration_days") is not None:
            parts.extend(["## 总周期", f"{result.get('total_duration_days')}天"])
        for stage in result.get("long_term_plan_stages") or []:
            if not isinstance(stage, dict):
                continue
            books = "；".join(str(item) for item in stage.get("book") or [])
            parts.extend([
                f"## 阶段{stage.get('stage', 1)}：{stage.get('stage_name', '')}",
                f"教材：{books}",
                f"目标：{stage.get('goal', '')}",
                f"阶段天数：{stage.get('duration_days', '')}天",
                f"安排：{stage.get('schedule_summary', '')}",
            ])
        if result.get("short_term_plan_content"):
            parts.extend([
                "## 短期规划正文",
                str(result["short_term_plan_content"]),
                "## 周期",
                f"{result.get('duration_days') or result.get('short_term_duration_days', '')}天",
                "## 推进节点",
                *[f"- {item}" for item in (result.get("progression_nodes") or result.get("short_term_progression_nodes") or [])],
                f"## 预期产出\n{result.get('expected_output', '')}",
                f"## 完成标准\n{result.get('completion_criteria', '')}",
                f"## 选用阶段\n{result.get('selected_stage_id') or ''}",
                f"## 选用教材\n{'；'.join(str(item) for item in result.get('selected_books') or [])}",
            ])
        if result.get("daily_task_content"):
            parts.extend([
                "## 当日任务正文",
                str(result["daily_task_content"]),
                f"## 学习章节\n{result.get('learning_chapter', '')}",
                "## 重点知识点",
                *[f"- {item}" for item in result.get("focus_knowledge_points") or []],
                f"## 预计分钟\n{result.get('estimated_minutes', '')}",
                f"## 当日产出\n{result.get('expected_output', '')}",
                f"## 完成标准\n{result.get('completion_criteria', '')}",
            ])
        return "\n".join(parts).strip()

    @staticmethod
    def _section(document: str, heading: str, next_headings: tuple[str, ...]) -> str:
        pattern = rf"(?ms)^## {re.escape(heading)}\s*\n(.*?)(?=^## (?:{'|'.join(map(re.escape, next_headings))})\s*$|\Z)"
        match = re.search(pattern, document)
        return match.group(1).strip() if match else ""

    @classmethod
    def _compile_plan_document(cls, scope: str, document: str) -> dict[str, Any]:
        """Parse only the stable prose markers emitted by this test double."""

        def anchor(path: str, quote: Any, field: str = "plan_document") -> tuple[str, list[dict[str, str]]]:
            return path, [{"source_field": field, "source_quote": cls._source_quote(quote)}]

        if scope == "long_term":
            content = cls._section(document, "长期规划正文", ("总周期", "阶段1：", "阶段2：", "阶段3："))
            total_match = re.search(r"(?m)^## 总周期\s*\n(\d+)天", document)
            stages: list[dict[str, Any]] = []
            stage_quotes: list[str] = []
            for match in re.finditer(
                r"(?ms)^## 阶段(\d+)：([^\n]*)\n教材：([^\n]*)\n目标：([^\n]*)\n阶段天数：(\d+)天\n安排：(.+?)(?=^## 阶段\d+：|\Z)",
                document,
            ):
                stage_quotes.append(match.group(0).strip())
                stages.append({
                    "stage": int(match.group(1)),
                    "stage_name": match.group(2).strip(),
                    "books": [item for item in match.group(3).split("；") if item],
                    "goal": match.group(4).strip(),
                    "duration_days": int(match.group(5)),
                    "schedule_summary": match.group(6).strip(),
                })
            if not content or not total_match or not stages:
                return {"status": "needs_revision", "contract_version": "1.0", "issues": [{"code": "missing_required_field", "category": "missing", "field_path": "/plan_document"}]}
            anchors = dict([
                anchor("/long_term_plan_content", content),
                anchor("/total_duration_days", total_match.group(1)),
                ("/stages", [
                    {"source_field": "plan_document", "source_quote": quote}
                    for quote in stage_quotes
                ]),
            ])
            return {"status": "compiled", "contract_version": "1.0", "contract": {"scope": scope, "long_term_plan_content": content, "total_duration_days": int(total_match.group(1)), "stages": stages, "field_anchors": anchors}}
        if scope == "short_term":
            content = cls._section(document, "短期规划正文", ("周期", "推进节点", "预期产出", "完成标准", "选用阶段", "选用教材"))
            duration = re.search(r"(?m)^## 周期\s*\n(\d+)天", document)
            nodes_block = cls._section(document, "推进节点", ("预期产出", "完成标准", "选用阶段", "选用教材"))
            nodes = [line[2:].strip() for line in nodes_block.splitlines() if line.strip().startswith("-")]
            expected = cls._section(document, "预期产出", ("完成标准", "选用阶段", "选用教材"))
            criteria = cls._section(document, "完成标准", ("选用阶段", "选用教材"))
            stage = cls._section(document, "选用阶段", ("选用教材",)) or None
            books = [item for item in cls._section(document, "选用教材", ()).split("；") if item]
            if not content or not duration or len(nodes) < 2 or not expected or not criteria or not books:
                return {"status": "needs_revision", "contract_version": "1.0", "issues": [{"code": "missing_required_field", "category": "missing", "field_path": "/plan_document"}]}
            anchors = dict([
                anchor("/short_term_plan_content", content),
                anchor("/duration_days", duration.group(1)),
                ("/progression_nodes", [
                    {"source_field": "plan_document", "source_quote": node}
                    for node in nodes
                ]),
                anchor("/expected_output", expected),
                anchor("/completion_criteria", criteria),
                ("/selected_books", [
                    {"source_field": "plan_document", "source_quote": book}
                    for book in books
                ]),
            ])
            return {"status": "compiled", "contract_version": "1.0", "contract": {"scope": scope, "short_term_plan_content": content, "duration_days": int(duration.group(1)), "progression_nodes": nodes, "expected_output": expected, "completion_criteria": criteria, "selected_stage_id": stage, "selected_books": books, "field_anchors": anchors}}
        content = cls._section(document, "当日任务正文", ("学习章节", "重点知识点", "预计分钟", "当日产出", "完成标准"))
        chapter = cls._section(document, "学习章节", ("重点知识点", "预计分钟", "当日产出", "完成标准"))
        points_block = cls._section(document, "重点知识点", ("预计分钟", "当日产出", "完成标准"))
        points = [line[2:].strip() for line in points_block.splitlines() if line.strip().startswith("-")]
        minutes = re.search(r"(?m)^## 预计分钟\s*\n(\d+)", document)
        expected = cls._section(document, "当日产出", ("完成标准",))
        criteria = cls._section(document, "完成标准", ())
        if not content or not chapter or not points or not minutes or not expected or not criteria:
            return {"status": "needs_revision", "contract_version": "1.0", "issues": [{"code": "missing_required_field", "category": "missing", "field_path": "/plan_document"}]}
        anchors = dict([
            anchor("/daily_task_content", content),
            anchor("/learning_chapter", chapter),
            ("/focus_knowledge_points", [
                {"source_field": "plan_document", "source_quote": point}
                for point in points
            ]),
            anchor("/estimated_minutes", minutes.group(1)),
            anchor("/expected_output", expected),
            anchor("/completion_criteria", criteria),
        ])
        return {"status": "compiled", "contract_version": "1.0", "contract": {"scope": scope, "daily_task_content": content, "learning_chapter": chapter, "focus_knowledge_points": points, "estimated_minutes": int(minutes.group(1)), "expected_output": expected, "completion_criteria": criteria, "field_anchors": anchors}}

    async def complete_json(
        self,
        role: str,
        payload: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        business_payload = payload.get("payload", payload)
        if role == "memory_agent":
            if "current_user_request" in business_payload:
                current_request = str(
                    business_payload.get("current_user_request") or ""
                )
                relevant_memories = list(
                    business_payload.get("relevant_memories") or []
                )
                conflict_answer = str(
                    business_payload.get("memory_conflict_answer") or ""
                ).strip()
                conflict = next(
                    (
                        item
                        for item in relevant_memories
                        if isinstance(item, dict)
                        and "每天最多学习二十分钟" in str(item.get("content") or "")
                        and "一小时" in current_request
                    ),
                    None,
                )
                resolution = "none"
                requires_clarification = False
                questions = []
                conflicts = []
                if conflict is not None:
                    conflicts = [
                        {
                            "memory_id": int(conflict["id"]),
                            "proposed_memory": "以后每天可以学习一小时。",
                            "reason": "新的稳定每日时长与现有上限不能同时成立。",
                        }
                    ]
                    if any(word in conflict_answer for word in ("仅本次", "这次")):
                        resolution = "use_current_once"
                    elif any(word in conflict_answer for word in ("替换", "更新")):
                        resolution = "replace_existing"
                    elif any(word in conflict_answer for word in ("保留", "原来")):
                        resolution = "keep_existing"
                    else:
                        resolution = "needs_clarification"
                        requires_clarification = True
                        questions = [
                            "当前一小时安排与原有每天最多二十分钟的记忆冲突。请确认：保留原记忆、仅本次采用一小时，还是用一小时替换原记忆？"
                        ]
                return self._emit(
                    {
                        "governance_notes": (
                            "发现一条需要用户确认的稳定时间约束冲突。"
                            if conflict is not None
                            else "本轮未发现与相关学习记忆不能同时成立的信息。"
                        ),
                        "memory_candidates": [],
                        "conflicts": conflicts,
                        "requires_clarification": requires_clarification,
                        "clarification_questions": questions,
                        "resolution": resolution,
                    },
                    on_delta,
                )
            messages = business_payload.get("messages", [])
            user_text = " ".join(
                str(item.get("content", "")) for item in messages if item.get("role") == "user"
            )
            result = {
                "summary": user_text or "当前会话暂无需要压缩的用户内容。",
                "preserved_facts": ["本次任务主题为四君子汤"] if "四君子汤" in user_text else [],
                "unresolved_questions": [],
                "temporary_constraints": [],
                "memory_candidates": ["用户可能偏好对比表式资源"] if "偏好对比表" in user_text else [],
            }
            return self._emit(result, on_delta)
        if role == "default_route_resolver":
            request_text = str(business_payload.get("user_request", ""))
            structured_goal = business_payload.get("structured_goal", {})
            goal_text = str(structured_goal.get("goal_name", ""))
            combined = f"{goal_text} {request_text}"
            catalog = list(business_payload.get("route_catalog", []))
            course_only = any(
                marker in combined
                for marker in ("不考试", "不考证", "只学课程", "课程学习", "单独学习", "单独掌握")
            )
            specific_intent = any(
                marker in combined
                for marker in ("执业医师", "执业药师", "职称", "专长", "师承", "保健艾灸师", "考研", "研究生")
            )
            learner_context = business_payload.get("learner_context", {})
            background = str(learner_context.get("learning_background", ""))
            physician_path_is_explicit = any(
                marker in combined
                for marker in ("规定学历", "中医（专长）", "中医(专长)", "师承", "确有专长")
            )
            if (
                "中医" in combined
                and "执业医师" in combined
                and "专业" in background
                and not any(marker in background for marker in ("中医", "中西医", "医学", "针灸推拿"))
                and not physician_path_is_explicit
            ):
                return self._emit({
                    "decision": "clarify",
                    "selected_route_id": None,
                    "confidence": 0.96,
                    "reason": "用户的专业背景不足以直接确认规定学历报考路径。",
                    "clarification_question": (
                        f"你提到自己是{background}。计划通过规定学历、中医（专长）医师考核，"
                        "还是传统医学师承/确有专长途径报考？"
                    ),
                }, on_delta)
            if "方剂学" in combined and not course_only and not specific_intent:
                return self._emit({
                    "decision": "clarify",
                    "selected_route_id": None,
                    "confidence": 0.95,
                    "reason": "仅凭方剂学无法区分独立课程学习、考试或升学目标。",
                    "clarification_question": "学习方剂学是单独课程学习，还是为了具体考试或升学目标？",
                }, on_delta)

            matches = []
            for route in catalog:
                labels = [route.get("goal_name", ""), *route.get("aliases", [])]
                matched = [str(label) for label in labels if label and str(label) in combined]
                if matched:
                    matches.append((max(len(label) for label in matched), route))
            if course_only and "方剂学" in combined:
                matches = [
                    (1000, route)
                    for route in catalog
                    if route.get("route_id") == "tcm_formula_course"
                ]
            # The live resolver model can infer the parent course from a named
            # formula. Keep the offline stub semantically equivalent so
            # composite planning/resource tests receive the approved route
            # instead of an artificial clarification caused by a weak stub.
            if "四君子汤" in combined and not specific_intent:
                matches = [
                    (1000, route)
                    for route in catalog
                    if route.get("route_id") == "tcm_formula_course"
                ]
            if matches:
                selected = max(matches, key=lambda item: item[0])[1]
                return self._emit({
                    "decision": "select",
                    "selected_route_id": selected.get("route_id"),
                    "confidence": 0.96,
                    "reason": "用户目标与已批准路线名称或别名明确对应。",
                    "clarification_question": None,
                }, on_delta)
            return self._emit({
                "decision": "clarify",
                "selected_route_id": None,
                "confidence": 0.9,
                "reason": "现有信息无法唯一对应已批准路线。",
                "clarification_question": "请说明具体考试、升学目标、专业方向，或确认仅进行课程学习。",
            }, on_delta)
        if role == "planner_agent":
            request_text = str(business_payload.get("user_request", ""))
            plan_scope = business_payload.get("plan_scope")
            plan_scope_hint = business_payload.get("plan_scope_hint")
            routing_correction = business_payload.get("routing_correction") or {}
            requests_resource = any(
                keyword in request_text
                for keyword in ("学习卡", "学习卡片", "复习卡", "学习资源", "直接学习")
            )
            requests_paper = any(
                keyword in request_text
                for keyword in ("组卷", "试卷", "模拟卷", "测试卷", "考试蓝图")
            )
            requests_explanation = any(
                keyword in request_text
                for keyword in ("讲一讲", "讲讲", "解释", "介绍", "是什么", "为什么", "原理", "区别")
            ) and not requests_resource and not requests_paper
            casual_request = PlannerLikeCasualBoundary.matches(request_text)
            # Stub mode is an offline fixture, so it cannot perform the live
            # Planner's semantic inference.  Still preserve the production
            # contract: asking to view an already persisted plan is a read-only
            # learner-data query, never a request to create/reuse another plan
            # layer.  Keep this boundary inside the stub rather than making it
            # an application router; live mode remains model-led.
            normalized_request = "".join(request_text.split())
            requests_existing_plan = (
                any(layer in normalized_request for layer in ("长期计划", "长期学习计划", "长期规划", "短期计划", "短期学习计划", "短期规划"))
                and any(intent in normalized_request for intent in ("看看", "查看", "看下", "是什么", "什么样", "内容", "进展", "进度"))
                and not any(intent in normalized_request for intent in ("制定", "生成", "安排", "修改", "调整", "重新", "更新"))
            )
            is_plan = not requests_resource and (
                plan_scope in {"long_term", "short_term", "daily_task", "unspecified"}
                or plan_scope_hint in {"long_term", "short_term", "daily_task", "unspecified"}
                or any(
                keyword in request_text
                for keyword in (
                    "制定计划", "学习计划", "复习计划", "长期计划", "短期计划",
                    "调整计划", "规划", "学习状态", "状态如何", "学情",
                    "我今天有哪些学习任务", "我今天要学习什么", "今天安排什么",
                    "今晚学习什么", "今天学什么",
                )
                )
            )
            status_only = any(
                keyword in request_text for keyword in ("学习状态", "状态如何", "学情")
            ) and not any(
                keyword in request_text for keyword in ("制定", "调整", "修改", "计划", "规划")
            )
            existing_state = business_payload.get("existing_plan_state") or {}
            effective_scope = plan_scope or plan_scope_hint
            # The offline planner mirrors the production semantic rule for a
            # generic “制定一份学习计划” request: if a current short-term
            # plan exists, it is the natural reusable layer; otherwise fall
            # back to the current long-term plan. This is fixture behaviour,
            # not an application keyword router.
            if not effective_scope and is_plan:
                if routing_correction and plan_scope_hint == "daily_task":
                    effective_scope = "daily_task"
                elif any(
                    phrase in request_text
                    for phrase in ("我今天有哪些学习任务", "我今天要学习什么", "今天安排什么", "今晚学习什么", "今天学什么")
                ):
                    effective_scope = "daily_task"
                elif existing_state.get("has_short_term_plan"):
                    effective_scope = "short_term"
                elif existing_state.get("has_long_term_plan"):
                    effective_scope = "long_term"
            has_existing_scope = bool(
                effective_scope
                and existing_state.get({
                    "long_term": "has_long_term_plan",
                    "short_term": "has_short_term_plan",
                    "daily_task": "has_daily_task",
                }.get(effective_scope, ""), False)
            )
            # The stub mirrors the production contract: Planner must emit a
            # semantic plan_action.  These branches are confined to the
            # offline model and exist only to exercise reuse/revision flows.
            explicit_revision_fixture = any(
                marker in request_text
                for marker in (
                    "强制修改",
                    "重新制定",
                    "重新规划",
                    "重新计划",
                    "调整",
                    "修改",
                    "不满意",
                    "学过",
                )
            )
            plan_action = (
                "create_or_update"
                if is_plan and explicit_revision_fixture
                else "reuse"
                if is_plan and has_existing_scope
                else "clarify"
                if is_plan and (plan_scope or plan_scope_hint) == "unspecified"
                else "create_or_update"
                if is_plan
                else None
            )
            emitted_scope = effective_scope or plan_scope or plan_scope_hint
            if is_plan and emitted_scope is None:
                emitted_scope = "unspecified"
            return self._emit({
                "task_type": (
                    "casual_conversation"
                    if casual_request
                    else "learner_data_query"
                    if requests_existing_plan
                    else "paper_generation"
                    if requests_paper
                    else "knowledge_explanation" if requests_explanation
                    else "learning_plan" if is_plan else "personalized_review_card"
                ),
                "selected_agents": (
                    []
                    if casual_request
                    else ["memory_agent", "diagnosis_agent"]
                    if requests_existing_plan
                    else [
                        "memory_agent",
                        "knowledge_base_agent",
                        "expert_agent",
                        "audit_agent",
                    ]
                    if requests_paper
                    else [
                        "memory_agent",
                        "knowledge_base_agent",
                        "expert_agent",
                        "audit_agent",
                    ]
                    if requests_explanation
                    else [
                        "memory_agent",
                        *([] if status_only else ["knowledge_base_agent"]),
                        "diagnosis_agent",
                        "learning_plan_service",
                    ]
                    if is_plan
                    else [
                        "memory_agent",
                        "knowledge_base_agent",
                        "diagnosis_agent",
                        "learning_plan_service",
                        "review_scheduler",
                        "expert_agent",
                        "audit_agent",
                    ]
                ),
                "plan_scope": None if requests_existing_plan else emitted_scope,
                "plan_action": None if requests_existing_plan else plan_action,
                "query_kind": "plan_progress" if requests_existing_plan else None,
                "casual_response": (
                    (
                        "我能理解你明天要考试时的焦虑，紧张并不代表你准备得不好。现在先不要试图把所有内容重学一遍："
                        "用10分钟列出最常考、最不稳的3个点，接着做一轮限时回忆或错题复盘，最后留出时间休息和准备考试用品。"
                        "如果你愿意，可以把考试科目或最担心的题型告诉我，我帮你把剩余时间拆成一个可执行的冲刺安排。"
                        if "焦虑" in request_text or "紧张" in request_text
                        else "你好！我是时珍智训智能助教。你想先聊聊当前学习情况，还是直接开始一项学习任务？"
                    )
                    if casual_request or "焦虑" in request_text or "紧张" in request_text
                    else None
                ),
                "routing_reason": (
                    "用户本轮是在进行日常交流，不需要启动学习业务流程。"
                    if casual_request
                    else "用户要求生成试卷蓝图，需要知识检索、专家蓝图生成和审核。"
                    if requests_paper
                    else "用户要求知识讲解，需要教材检索、专家讲解和审核，不生成学习规划。"
                    if requests_explanation
                    else (
                        "用户要的是当日任务，将已有规划和当前学情落地为今天可执行的学习安排。"
                        if emitted_scope == "daily_task"
                        else "用户只要求制定计划，无需生成教学资源。"
                    )
                    if is_plan
                    else "用户同时需要学习计划和可直接学习的资源，需要完成计划落地、专家生成和审核。"
                ),
                "risk_level": "low",
                "requires_audit": not casual_request,
                "fallback_policy": "fail_closed",
            }, on_delta)
        if role == "knowledge_base_agent":
            phase = str(business_payload.get("phase", "process_retrieved_content"))
            if phase == "plan_retrieval":
                request_text = str(business_payload.get("user_request", ""))
                retrieval_context = business_payload.get("retrieval_context", {})
                context_text = " ".join(
                    str(retrieval_context.get(name, ""))
                    for name in ("current_short_term_plan", "user_short_term_goal", "current_long_term_plan")
                )
                kp_query = next(
                    (
                        keyword
                        for keyword in ("四君子汤", "理中丸", "感冒")
                        if keyword in f"{request_text} {context_text}"
                    ),
                    "四君子汤",
                )
                return self._emit({
                    "kp_query": kp_query,
                    "question_query": f"{kp_query} 相关题目",
                    "retrieval_reason": "每次知识任务都同时检索知识点内容和相关题目内容。",
                }, on_delta)
            return self._emit({
                "retrieval_summary": "；".join(
                    str(item.get("text", ""))
                    for item in business_payload.get("evidence", [])[:3]
                    if item.get("text")
                ),
                "quality_labels": ["教材证据已覆盖"],
                "uncertainty": [],
            }, on_delta)
        if role == "plan_contract_compiler":
            scope = str(business_payload.get("plan_scope") or "")
            diagnosis = business_payload.get("diagnosis_output") or {}
            route = business_payload.get("trusted_route") or {}
            if isinstance(diagnosis.get("plan_document"), str):
                return self._emit(
                    self._compile_plan_document(
                        scope,
                        diagnosis["plan_document"],
                    ),
                    on_delta,
                )
            required_fields = {
                "long_term": (
                    "long_term_plan_content",
                    "total_duration_days",
                    "long_term_plan_stages",
                ),
                "short_term": (
                    "short_term_plan_content",
                    "duration_days",
                    "progression_nodes",
                    "expected_output",
                    "completion_criteria",
                    "selected_books",
                ),
                "daily_task": (
                    "daily_task_content",
                    "learning_chapter",
                    "focus_knowledge_points",
                    "estimated_minutes",
                    "expected_output",
                    "completion_criteria",
                ),
            }
            missing = [
                field
                for field in required_fields.get(scope, ())
                if diagnosis.get(field) in (None, "", [])
            ]
            if missing:
                return self._emit({
                    "status": "needs_revision",
                    "contract_version": "1.0",
                    "issues": [
                        {
                            "code": "missing_required_field",
                            "category": "missing",
                            "field_path": f"/{field}",
                            "source_refs": [field],
                        }
                        for field in missing
                    ],
                }, on_delta)
            if scope == "long_term":
                stages = list(diagnosis.get("long_term_plan_stages") or [])
                contract_stages = [
                    {
                        "stage": int(stage.get("stage", index)),
                        "stage_name": str(stage["stage_name"]),
                        "books": list(stage.get("book") or []),
                        "goal": str(stage["goal"]),
                        "duration_days": int(stage["duration_days"]),
                        "schedule_summary": str(stage["schedule_summary"]),
                    }
                    for index, stage in enumerate(stages, start=1)
                ]
                total = int(diagnosis["total_duration_days"])
                return self._emit({
                    "status": "compiled",
                    "contract_version": "1.0",
                    "contract": {
                        "scope": "long_term",
                        "long_term_plan_content": diagnosis["long_term_plan_content"],
                        "total_duration_days": total,
                        "stages": contract_stages,
                        "field_anchors": {
                            "/long_term_plan_content": [{"source_field": "long_term_plan_content", "source_quote": diagnosis["long_term_plan_content"]}],
                            "/total_duration_days": [{"source_field": "total_duration_days", "source_quote": str(diagnosis["total_duration_days"])}],
                            "/stages": [{"source_field": "long_term_plan_stages", "source_quote": json.dumps(stages, ensure_ascii=False, sort_keys=True)}],
                        },
                    },
                }, on_delta)
            if scope == "short_term":
                nodes = list(diagnosis.get("progression_nodes") or [])
                books = list(diagnosis.get("selected_books") or [])
                return self._emit({
                    "status": "compiled",
                    "contract_version": "1.0",
                    "contract": {
                        "scope": "short_term",
                        "short_term_plan_content": diagnosis["short_term_plan_content"],
                        "duration_days": int(diagnosis["duration_days"]),
                        "progression_nodes": nodes,
                        "expected_output": diagnosis["expected_output"],
                        "completion_criteria": diagnosis["completion_criteria"],
                        "selected_stage_id": diagnosis.get("selected_stage_id"),
                        "selected_books": books,
                        "field_anchors": {
                            "/short_term_plan_content": [{"source_field": "short_term_plan_content", "source_quote": diagnosis["short_term_plan_content"]}],
                            "/duration_days": [{"source_field": "duration_days", "source_quote": str(diagnosis["duration_days"])}],
                            "/progression_nodes": [{"source_field": "progression_nodes", "source_quote": json.dumps(nodes, ensure_ascii=False, sort_keys=True)}],
                            "/selected_books": [{"source_field": "selected_books", "source_quote": json.dumps(books, ensure_ascii=False, sort_keys=True)}],
                        },
                    },
                }, on_delta)
            return self._emit({
                "status": "compiled",
                "contract_version": "1.0",
                "contract": {
                    "scope": "daily_task",
                    **{key: diagnosis[key] for key in ("daily_task_content", "learning_chapter", "focus_knowledge_points", "estimated_minutes", "expected_output", "completion_criteria")},
                    "field_anchors": {
                        f"/{key}": [{"source_field": key, "source_quote": self._source_quote(diagnosis[key])}]
                        for key in ("daily_task_content", "learning_chapter", "focus_knowledge_points", "estimated_minutes", "expected_output", "completion_criteria")
                    },
                },
            }, on_delta)
        if role == "diagnosis_plan_change":
            change = business_payload.get("plan_change_context") or {}
            explicit = business_payload.get("explicit_flags") or {}
            request_text = str(business_payload.get("user_request") or "")
            has_change_context = bool(str(change.get("change_details") or "").strip())
            # Test-double semantics only. The production model receives the
            # full context and decides this; these phrases keep offline flows
            # representative without making application code depend on them.
            fixture_learning_change = any(
                marker in request_text
                for marker in ("重新规划", "重新计划", "调整计划", "修改计划", "不满意", "已经学过", "学过")
            )
            has_change_context = has_change_context or fixture_learning_change
            vague_replan_fixture = (
                not str(change.get("change_details") or "").strip()
                and any(marker in request_text for marker in ("重新规划", "重新计划", "不满意"))
                and not any(marker in request_text for marker in ("学过", "已经学过", "可用时间", "每天", "每周", "期限"))
            )
            if vague_replan_fixture:
                return self._emit(
                    {
                        "long_term_action": "reuse",
                        "short_term_action": "reuse",
                        "daily_task_action": "reuse",
                        "replan_requested": True,
                        "changed_facts": [],
                        "requires_clarification": True,
                        "clarification_questions": [
                            "你希望调整长期规划、短期计划，还是两者？请同时说明发生了什么变化。"
                        ],
                        "reason": "当前只表达了重规划意愿，尚未给出可确定影响范围的变化事实。",
                    },
                    on_delta,
                )
            long_update = bool(explicit.get("long_term"))
            short_update = bool(explicit.get("short_term"))
            scope = business_payload.get("plan_scope")
            # Offline model fixture: emulate a semantic Diagnosis decision for
            # a supplied learning-state change. Production uses the live model.
            if has_change_context and not (long_update or short_update):
                if scope == "short_term":
                    short_update = True
                elif scope == "daily_task":
                    short_update = False
                else:
                    long_update = True
                    short_update = True
            # A named completed textbook is sufficient evidence for a plan
            # revision; the model should not ask whether the whole book or a
            # particular chapter was completed before updating the path.
            if any(marker in request_text for marker in ("已经学习过", "已经学过", "学过")):
                long_update = True
                short_update = True
            return self._emit(
                {
                    "long_term_action": "update" if long_update else "reuse",
                    "short_term_action": "update" if short_update else "reuse",
                    "daily_task_action": "update",
                    "replan_requested": bool(
                        has_change_context or long_update or short_update
                    ),
                    "changed_facts": (
                        [str(change.get("change_details"))[:500]]
                        if has_change_context
                        else []
                    ),
                    "requires_clarification": False,
                    "clarification_questions": [],
                    "reason": "离线模型根据学习变化事实生成层级变更合同。",
                },
                on_delta,
            )
        if role == "diagnosis_agent":
            if "plan_actions" in business_payload:
                plan_scope = business_payload.get("plan_scope")
                route_context = business_payload.get("default_route", {})
                textbook_route = route_context.get("textbook_route") or {}
                phases = list(route_context.get("phases", [])) or list(
                    textbook_route.get("stages", [])
                )
                actions = business_payload.get("plan_actions", {})
                existing = business_payload.get("existing_plans", {})
                request_text = str(business_payload.get("user_request", ""))
                topic = next(
                    (
                        keyword
                        for keyword in ("四君子汤", "方剂学", "中医执业医师")
                        if keyword in request_text
                    ),
                    str(route_context.get("goal_name") or "当前学习主题"),
                )
                phase_rows = []
                for index, phase in enumerate(phases, start=1):
                    books = "、".join(phase.get("books", [])) or "经确认的当前学习材料"
                    evidence = "、".join(phase.get("exit_evidence", [])) or "阶段学习证据"
                    phase_rows.append(
                        f"| {index}. {phase.get('name', f'阶段{index}')} | {books} | "
                        f"{phase.get('objective', '完成阶段目标')} | {evidence} | "
                        f"提交{evidence}后晋级 | 正常学习 |"
                    )
                if not phase_rows:
                    phase_rows.append(
                        f"| 1. 路线解析失败 | 不可发布 | 建立{topic}基础 | "
                        "无 | 不可晋级 | 需要中断追问 |"
                    )
                duration_match = re.search(
                    r"(\d+(?:\.\d+)?|[一二两三四五六七八九十半]+)"
                    r"(年|个月|月|周|星期)",
                    request_text,
                )
                duration_text = "".join(duration_match.groups()) if duration_match else ""
                deadline_text = (
                    f"在{duration_text}内完成目标；第1个月建立基础，第2个月起按阶段验收推进。"
                    if duration_text
                    else "期限和稳定能力证据待用户确认。"
                )
                weekly_match = re.search(
                    r"每周[^。；，,\n]{0,16}?"
                    r"(\d+(?:\.\d+)?|[一二两三四五六七八九十半]+)"
                    r"(小时|分钟)",
                    request_text,
                )
                weekly_text = "".join(weekly_match.groups()) if weekly_match else ""
                budget_text = (
                    f"每周{weekly_text}为容量上限，建议保留反馈与机动缓冲。"
                    if weekly_text
                    else "每周最低学习投入和缓冲时间待用户确认。"
                )
                generated_long = (
                    f"## 目标契约\n最终目标是系统掌握{topic}；{deadline_text}\n"
                    "## 能力图谱摘要\n围绕基础识记、理解辨析和应用反馈逐步推进。\n"
                    "## 长期阶段路径\n| 阶段 | 具体教材 | 阶段目标 | 验收证据 | 晋级条件 | 个性化状态 |\n"
                    "|---|---|---|---|---|---|\n"
                    + "\n".join(phase_rows)
                    + "\n## 长期维护与恢复\n中断时保留一次短时主动回忆，复盘后回到当前阶段。\n"
                    f"## 资源预算\n{budget_text}\n"
                    "## 长期重规划触发器\n目标、期限、路线教材或稳定能力证据持续变化时调整。"
                )
                concrete_books = [
                    str(book)
                    for phase in phases
                    for book in phase.get("books", [])
                    if str(book).strip()
                ]
                current_books = concrete_books[:2]
                current_books_text = "、".join(current_books)
                weeks = "未来两周" if "两周" in request_text else "未来一周"
                cycle_plan = (
                    f"第1周使用{current_books_text}完成{topic}的基础回忆和教材核对，形成遗漏清单；"
                    f"第2周继续使用{current_books_text}完成类项辨析与综合自测，以纠错记录验收。"
                    if "两周" in request_text
                    else f"周初使用{current_books_text}完成{topic}的基础回忆，"
                    f"周中依据{current_books_text}进行教材核对和错因订正，"
                    "周末完成闭卷复述与综合验收。"
                )
                generated_short = (
                    f"## 当前周期目标\n{weeks}在当前长期阶段使用{current_books_text}推进{topic}，以回忆和核对记录验收。\n"
                    "## 本周期任务\n"
                    + cycle_plan
                    + "产出回忆与纠错记录；"
                    "完成标准为能够标出遗漏并完成订正。\n"
                    "## 复习与测评\n到期时闭卷复述，未通过则缩小范围后再次核对。\n"
                    "## 短期覆盖与恢复\n临时任务抢占时仍保留一次三分钟主线回忆。\n"
                    "## 短期重规划触发器\n连续未完成、正确率持续下降或可用时间稳定变化时调整。"
                )
                available = business_payload.get("time_constraints", {}).get(
                    "available_minutes_today"
                )
                task_minutes = min(10, int(available)) if available else 10
                generated_daily = (
                    f"## 今日目标\n完成{topic}的一次主动回忆并定位遗漏。\n"
                    f"## 与短期计划的对应任务\n对应第1周的{topic}主动回忆任务。\n"
                    "## 具体教材或材料\n使用短期计划已确认的路线教材或检索材料，不虚构章节。\n"
                    "## 分步动作\n先闭卷回忆，再核对教材并订正遗漏。\n"
                    "## 今日可见产出\n一份闭卷回忆与遗漏纠错记录。\n"
                    "## 客观完成标准\n完成回忆并逐项标记遗漏。\n"
                    "## 今日复习与降级动作\n时间不足时保留三分钟主线回忆并记录恢复检查点。"
                )
                long_action = str(actions.get("long_term_action", "update"))
                short_action = str(actions.get("short_term_action", "update"))
                daily_action = str(actions.get("daily_task_action", "update"))
                response = {
                    "long_term_plan_content": (
                        existing.get("long_term", {}).get("content", generated_long)
                        if long_action == "reuse"
                        else generated_long
                    ),
                    "short_term_plan_content": (
                        existing.get("short_term", {}).get("content", generated_short)
                        if short_action == "reuse"
                        else generated_short
                    ),
                    "daily_task_content": (
                        existing.get("daily_task", {}).get(
                            "task_content", generated_daily
                        )
                        if daily_action == "reuse"
                        else generated_daily
                    ),
                    "learning_chapter": "短期计划当前教材章节",
                    "focus_knowledge_points": [topic],
                    "estimated_minutes": task_minutes,
                    "expected_output": "一份闭卷回忆与遗漏纠错记录。",
                    "completion_criteria": "完成回忆并逐项标记遗漏。",
                    "long_term_plan_stages": (
                        [
                            {
                                "stage": index,
                                "stage_name": str(phase.get("name") or f"阶段{index}"),
                                "book": list(phase.get("books", []))
                                or ["路线解析失败（不可发布）"],
                                "goal": str(phase.get("objective") or "完成本阶段目标"),
                                "duration_days": 30,
                                "schedule_summary": (
                                    f"本阶段使用{'、'.join(phase.get('books', []))}，"
                                    f"围绕{phase.get('objective', '阶段目标')}推进，"
                                    f"以{'、'.join(phase.get('exit_evidence', [])) or '阶段学习证据'}验收。"
                                ),
                            }
                            for index, phase in enumerate(phases, start=1)
                        ]
                        or [
                            {
                                "stage": 1,
                                "book": ["路线解析失败（不可发布）"],
                                "goal": "必须先解析可信路线",
                            }
                        ]
                    ),
                    "total_duration_days": max(30, 30 * len(phases)),
                    "duration_days": 14 if "两周" in request_text else 7,
                    "progression_nodes": (
                        ["第1周完成教材学习与遗漏整理", "第2周完成辨析、自测与验收"]
                        if "两周" in request_text
                        else ["周初完成教材学习与框架整理", "周末完成闭卷复述与综合验收"]
                    ),
                }
                textbook_stages = list(textbook_route.get("stages", []))
                if textbook_route.get("route_id") and textbook_stages:
                    selected_stage = textbook_stages[0]
                    response.update(
                        selected_textbook_route_id=textbook_route["route_id"],
                        selected_stage_id=selected_stage["stage_id"],
                        selected_books=list(selected_stage.get("books", []))[:2],
                        selection_reason="当前没有更高阶段的稳定掌握证据，先从教材主线的基础阶段开始。",
                    )
                if plan_scope == "long_term":
                    response = {
                        key: response[key]
                        for key in ("long_term_plan_content", "total_duration_days", "long_term_plan_stages")
                    }
                elif plan_scope == "short_term":
                    response = {
                        key: response[key]
                        for key in (
                            "short_term_plan_content",
                            "duration_days",
                            "progression_nodes",
                            "expected_output",
                            "completion_criteria",
                            "selected_textbook_route_id",
                            "selected_stage_id",
                            "selected_books",
                            "selection_reason",
                        )
                        if key in response
                    }
                elif plan_scope == "daily_task":
                    response = {
                        key: response[key]
                        for key in (
                            "daily_task_content",
                            "learning_chapter",
                            "focus_knowledge_points",
                            "estimated_minutes",
                            "expected_output",
                            "completion_criteria",
                        )
                    }
                return self._emit(response, on_delta)
            current_long = business_payload.get("long_term_plan", {})
            current_short = business_payload.get("short_term_plan", {})
            route_context = business_payload.get("route_context") or business_payload.get("default_route") or {}
            planning_status = str(route_context.get("planning_status", "provisional"))
            route_is_approved = planning_status == "approved_route"
            route_goal_type = str(route_context.get("goal_type", "course"))
            route_goal_name = str(route_context.get("goal_name", "当前主题学习"))
            route_assumptions = list(route_context.get("assumptions", []))
            route_unknowns = list(route_context.get("unknowns_to_confirm", []))
            available_minutes = business_payload.get("learning_data", {}).get("available_minutes")
            task_minutes = min(10, int(available_minutes)) if available_minutes else 10
            time_description = (
                f"当前任务按输入的可用时间控制在{task_minutes}分钟。"
                if available_minutes
                else "当前可用时间待用户确认，任务时长采用 Stub 默认值。"
            )
            provisional_prefix = "【临时规划】" if not route_is_approved else ""
            assumptions = route_assumptions if route_is_approved else [
                *route_assumptions,
                "当前按一周短期学习包暂定，待反馈后调整。",
            ]
            unknowns = route_unknowns if route_is_approved else [
                *route_unknowns,
                "长期可用学习频次待用户确认。",
            ]
            request_text = str(business_payload.get("planning_reuse_policy", {}).get("user_request", ""))
            explicit_change = any(keyword in request_text for keyword in ("制定", "调整", "修改", "变更", "更新"))
            long_reuse = bool(current_long.get("content")) and not explicit_change
            short_reuse = bool(current_short.get("content")) and not explicit_change
            return self._emit({
                "summary": "当前为初始复习任务，建议先完成一次主动回忆。",
                "risk_flags": [],
                "recommendations": ["完成复习卡后提交反馈。"],
                "uncertainty": [],
                "long_term_plan_content": current_long.get("content") if long_reuse else provisional_prefix + (
                    "【最终目标】逐步建立当前主题的稳定知识结构。"
                    "【能力路径与阶段】基础概念→主动回忆→应用巩固。"
                    "【阶段里程碑】完成主题回忆并能依据教材解释核心概念；截止时间待用户确认。"
                    "【资源预算】每周最低学习投入和缓冲时间待用户确认。"
                    "【重规划条件】连续两次任务未达完成标准或可用时间明显变化时重规划。"
                    "【保温底线】短期事件打断时，每周至少完成一次错题或知识卡回忆。"
                ),
                "short_term_plan_content": current_short.get("content") if short_reuse else provisional_prefix + (
                    "【当前主目标】完成当前主题的一次主动回忆。"
                    "【长期目标保温】保留一次知识卡或错题回忆，具体时长待用户确认。"
                    "【具体任务块】围绕当前主题完成一次主动回忆，产出一份口述或书面回忆，"
                    "完成标准为对照证据标记遗漏。"
                    "【复习任务】本次完成后根据反馈安排到期复习。"
                    "【反馈指标】记录任务完成情况、回忆遗漏和实际耗时。"
                ),
                "long_term_plan_action": "reuse" if long_reuse else "update",
                "short_term_plan_action": "reuse" if short_reuse else "update",
                "priority_mode": "normal",
                "adjustment_reason": (
                    "当前诉求和已知目标没有变化，沿用已有长期与短期规划。"
                    if long_reuse and short_reuse
                    else "当前长期目标没有变化，沿用已有长期规划并更新短期规划。"
                    if long_reuse
                    else "当前短期诉求没有变化，沿用已有短期规划并更新长期规划。"
                    if short_reuse
                    else "当前证据适合从一次短时主动回忆开始。"
                ),
                "route_context": {
                    "goal_type": route_goal_type,
                    "goal_name": route_goal_name,
                    "planning_status": planning_status,
                    "match_reason": str(route_context.get("match_reason", "no_safe_match")),
                    "route_id": route_context.get("route_id"),
                    "route_version": route_context.get("route_version"),
                    "route_status": route_context.get("route_status"),
                    "phases": list(route_context.get("phases", [])),
                    "sources": list(route_context.get("sources", [])),
                    "assumptions": assumptions,
                    "unknowns_to_confirm": unknowns,
                    "runtime_checks": list(route_context.get("runtime_checks", [])),
                },
                "goal_contract": {
                    "goal_type": route_goal_type,
                    "goal_name": route_goal_name,
                    "observable_ability": "能够依据学习材料主动回忆并准确说明当前主题的核心知识。",
                    "acceptance_evidence": ["主动回忆记录与教材对照纠错记录"],
                },
                "milestones": [
                    {
                        "milestone_id": "M1",
                        "name": "完成当前主题基础回忆",
                        "success_criteria": "能够闭卷回忆核心内容并依据材料完成纠错。",
                        "evidence_required": ["闭卷回忆记录", "遗漏与纠错记录"],
                    }
                ],
                "short_term_learning_package": {
                    "time_window_weeks": 1,
                    "current_goal": "一周内完成当前主题的主动回忆与一次纠错复述。",
                    "task_blocks": ["主动回忆", "材料核对", "遗漏订正", "纠错复述"],
                    "expected_output": "主动回忆清单和遗漏纠错记录。",
                    "completion_criteria": "完成回忆、核对和纠错，并标记至少一个后续复习点。",
                },
                "recovery_policy": {
                    "trigger_conditions": ["连续两次任务未完成或回忆证据未达标"],
                    "recovery_actions": ["降低单次负荷，回到核心概念回忆，并在下次任务恢复长期主线"],
                },
                "recommendation_trace": {
                    "default_route": (
                        "遵循已批准路线的当前阶段与验收证据。"
                        if route_is_approved
                        else "按明确标记的临时路线推进，等待目标与频次确认。"
                    ),
                    "user_state": "当前证据适合先进行一次主动回忆并记录遗漏。",
                    "time_constraint": time_description,
                    "current_task": f"先完成{task_minutes}分钟主动回忆，再依据材料标记遗漏。",
                },
                "assumptions": assumptions,
                "unknowns_to_confirm": unknowns,
                "learning_task": {
                    "task_type": "active_recall",
                    "task_content": "围绕当前主题完成一次主动回忆。",
                    "learning_chapter": "短期计划当前教材章节",
                    "focus_knowledge_points": [str(business_payload.get("topic") or "当前主题")],
                    "estimated_minutes": task_minutes,
                    "expected_output": "一份不查看资料完成的口述或书面回忆。",
                    "completion_criteria": "完成回忆并对照证据标记遗漏。",
                },
            }, on_delta)
        if role == "expert_agent":
            phase = str(business_payload.get("phase", ""))
            if phase == "paper_gap_generation":
                count = int(business_payload.get("gap_count", 1))
                unit_id = str(business_payload.get("unit_id", "UNIT_01"))
                preferences = business_payload.get("question_type_preferences") or ["单项选择题"]
                question_type = "单项选择题" if any("选择" in str(item) for item in preferences) else str(preferences[0])
                if business_payload.get("assembly_output_mode") == "natural_language_document":
                    generated_lines = [
                        (
                            f"单元{unit_id}原创{question_type}："
                            f"题干：{business_payload.get('knowledge_module', '当前主题')}补充练习题{index + 1}；"
                            f"选项：{'A. 符合教学结论、B. 不符合教学结论' if '选择' in question_type else ''}；"
                            f"参考答案：{'A' if '选择' in question_type else '依据当前教学材料作答。'}；"
                            "解析：用于补足用户明确题量，正式发布前由系统审核完整性。"
                        )
                        for index in range(count)
                    ]
                    return self._emit(
                        {
                            "assembly_document": "\n".join(
                                ["【试卷标题】缺口题补充原稿", *generated_lines]
                            )
                        },
                        on_delta,
                    )
                return self._emit({
                    "generated_items": []
                }, on_delta)
            if phase in {"knowledge_explanation", "general_learning_support"}:
                topic = str(business_payload.get("topic", "当前主题"))
                if business_payload.get("external_information_request"):
                    evidence = str(business_payload.get("retrieval_summary") or "").strip()
                    return self._emit({
                        "title": f"{topic}查询结果",
                        "explanation_content": (
                            (evidence + "\n\n") if evidence else ""
                        )
                        + "以上为网络检索到的当前信息；考试日期、天气等内容可能变化，建议以相关官方发布页面为最终依据。",
                        "uncertainty": [],
                    }, on_delta)
                return self._emit({
                    "title": f"{topic}知识讲解",
                    "explanation_content": (
                        f"【先给结论】{topic}是当前需要理解的中医药教学主题。"
                        "【核心概念】依据教材证据说明其定义、范围和主要表现。"
                        "【关键机制或辨析】结合教材梳理病因病机、证候关系及容易混淆的边界。"
                        "【学习者易错点】不要把教学知识直接用于自我诊断，也不要混淆相近概念。"
                        "【小结】先记核心定义，再理解机制和辨析要点。"
                    ),
                    "uncertainty": [],
                }, on_delta)
            if phase == "paper_blueprint":
                constraints = business_payload.get("exam_constraints", {})
                total_score = constraints.get("total_score")
                duration = constraints.get("duration_minutes")
                if business_payload.get("blueprint_output_mode") != "natural_language_document":
                    numeric_score = (
                        float(total_score)
                        if isinstance(total_score, (int, float)) and total_score > 0
                        else 100.0
                    )
                    return self._emit({
                        "title": "四君子汤章节练习试卷",
                        "source_status": "user_provided_unverified",
                        "scope_summary": "围绕四君子汤组成、功效主治和配伍意义进行教学练习。",
                        "duration_minutes": duration,
                        "total_score": numeric_score,
                        "units": [
                            {
                                "knowledge_module": "组成与功效主治",
                                "learning_objective": "识别组成并理解功效主治。",
                                "retrieval_query": "四君子汤 组成 功效 主治",
                                "question_type_preferences": ["单项选择题", "简答题"],
                                "required_question_count": 2,
                                "score_total": numeric_score * 0.5,
                                "candidate_limit": 8,
                                "selection_rules": ["优先选择直接考查核心概念的题目"],
                            },
                            {
                                "knowledge_module": "配伍意义与辨析",
                                "learning_objective": "说明君臣佐使并完成方剂辨析。",
                                "retrieval_query": "四君子汤 配伍意义 君臣佐使 辨析",
                                "question_type_preferences": [],
                                "required_question_count": 2,
                                "score_total": numeric_score * 0.5,
                                "candidate_limit": 8,
                                "selection_rules": ["与上一单元全卷去重"],
                            },
                        ],
                        "assumptions": ["题型未完全指定，按候选题实际类型组卷。"],
                        "acceptance_criteria": ["题目全部来自候选池", "全卷题目ID不重复"],
                    }, on_delta)
                total_score_text = (
                    f"全卷总分：{total_score}分。"
                    if isinstance(total_score, (int, float)) and total_score > 0
                    else "用户未明确总分，蓝图不预设总分。"
                )
                duration_text = (
                    f"建议作答时长：{duration}分钟。"
                    if isinstance(duration, int) and duration > 0
                    else "用户未明确正式作答时长。"
                )
                return self._emit({
                    "blueprint_document": (
                        "【标题】四君子汤章节练习试卷\n"
                        "【范围】围绕四君子汤组成、功效主治和配伍意义进行教学练习。\n"
                        f"【时间与分值】{duration_text}{total_score_text}\n"
                        "【单元一：组成与功效主治】学习目标：识别组成并理解功效主治。"
                        "检索表达：四君子汤 组成 功效 主治。题型偏好：单项选择题、简答题。"
                        "目标题数：2题。选题规则：优先选择直接考查核心概念的题目。\n"
                        "【单元二：配伍意义与辨析】学习目标：说明君臣佐使并完成方剂辨析。"
                        "检索表达：四君子汤 配伍意义 君臣佐使 辨析。题型偏好：由候选题决定。"
                        "目标题数：2题。选题规则：与上一单元全卷去重。\n"
                        "【假设】题型未完全指定时，按候选题实际类型组卷。\n"
                        "【验收条件】题目全部来自候选池或经审核的原创补充题；全卷题目不得重复。"
                    ),
                }, on_delta)
            if phase == "paper_assembly":
                selected = []
                selected_ids: set[str] = set()
                sequence = 0
                for unit in business_payload.get("candidate_pool", []):
                    required = int(unit.get("required_question_count", 1))
                    selected_for_unit = 0
                    for item in unit.get("items", []):
                        if item["question_id"] in selected_ids:
                            continue
                        sequence += 1
                        selected_ids.add(item["question_id"])
                        selected_for_unit += 1
                        selected.append({
                            "unit_id": unit["unit_id"],
                            "question_id": item["question_id"],
                            "score": 25,
                            "selection_rationale": "符合当前蓝图单元且来自受控候选池。",
                        })
                        if selected_for_unit >= required:
                            break
                blueprint = business_payload.get("paper_blueprint", {})
                required_total = (
                    int(blueprint.get("required_total_question_count") or 0)
                    if blueprint.get("question_count_is_hard_constraint")
                    else 0
                )
                generated = []
                units = business_payload.get("candidate_pool", [])
                fallback_unit_id = units[0]["unit_id"] if units else "UNIT_01"
                for index in range(max(0, required_total - len(selected))):
                    generated.append({
                        "unit_id": fallback_unit_id,
                        "question_type": "单项选择题",
                        "stem": f"根据当前教学主题生成的补充练习题{index + 1}",
                        "options": ["A. 符合教材或通行教学结论", "B. 不符合教材或通行教学结论"],
                        "reference_answer": "A",
                        "analysis": "本题用于补足用户明确题量，正式使用时应结合当前主题证据审核。",
                        "selection_rationale": "正式候选去重后不足，按用户硬题量补充。",
                        "source_tier": "model_knowledge",
                    })
                if business_payload.get("assembly_output_mode") == "natural_language_document":
                    selected_lines = [
                        f"单元{item['unit_id']}选用候选题{item['question_id']}。"
                        for item in selected
                    ]
                    generated_lines = [
                        (
                            f"单元{item['unit_id']}原创{item['question_type']}："
                            f"题干：{item['stem']}；选项：{'、'.join(item['options'])}；"
                            f"参考答案：{item['reference_answer']}；解析：{item['analysis']}。"
                        )
                        for item in generated
                    ]
                    return self._emit(
                        {
                            "assembly_document": "\n".join(
                                [
                                    "【试卷标题】四君子汤章节练习试卷",
                                    "【正式候选题选择】",
                                    *selected_lines,
                                    "【原创缺口题】",
                                    *generated_lines,
                                ]
                            )
                        },
                        on_delta,
                    )
                return self._emit({
                    "title": "四君子汤章节练习试卷",
                    "instructions": "请按题目顺序作答；本卷仅用于教学练习。",
                    "selected_items": selected,
                    "generated_items": generated,
                    "coverage_summary": {"selected_count": len(selected)},
                    "unresolved_constraints": [],
                }, on_delta)
            learning_data = business_payload.get("learning_data", {})
            topic = learning_data.get("topic", business_payload.get("topic", ""))
            if business_payload.get("paper_generation", {}).get("enabled"):
                return self._emit({
                    "learning_tip": "后续落题应严格依据蓝图矩阵、教材证据和候选题边界执行。",
                    "blueprint_content": (
                        "【来源与假设】当前为教学练习蓝图，题量、分值、时长待用户确认。"
                        "【命题目标】围绕已检索教材证据考查识记、理解与辨析。"
                        "【蓝图矩阵】核心组成模块：识记层级，单选题，建议2题，"
                        "建议20分；功效主治模块：理解与辨析层级，简答题，"
                        "建议2题，建议40分；配伍应用模块：应用层级，病例题，"
                        "建议1题，建议40分。候选不足时待补充检索。"
                        "【题型与抽题规则】单项题保持唯一最佳答案，病例题每问只考一个判断层级。"
                        "【候选题使用策略】仅从当前候选目录中后续筛选，蓝图阶段不选具体题号。"
                        "【发布前验收】待 Audit 核对覆盖、算术、唯一性、来源和安全边界。"
                    ),
                    "use_question_candidates": bool(business_payload.get("question_candidate_catalog")),
                    "usage_reason": "候选题仅用于检查覆盖范围，蓝图阶段不选择具体题目。",
                    "selected_question_ids": [],
                    "resource_type": "practice",
                }, on_delta)
            return self._emit({
                "learning_tip": (
                    f"【知识点解释】{topic}是本次复习的核心对象。请先掌握其定义、组成或结构、"
                    "主要功用、适用范围和关键辨析；具体结论以本次教材证据为准。"
                    "【核心要点】围绕教材证据整理出知识对象、关键组成、功能/功效、适用条件和易混淆点。"
                    "【理解说明】说明各要点之间的关系，避免只背孤立名词。"
                    "【主动回忆】合上资料后，用自己的话复述核心要点。"
                    "【自测与反馈】完成练习资源后记录错误点，再回到对应知识要点纠正。"
                ),
                "use_question_candidates": bool(business_payload.get("question_candidate_catalog")),
                "usage_reason": "知识卡片解释后使用检索候选题进行巩固练习。" if business_payload.get("question_candidate_catalog") else "当前没有可用候选题。",
                "selected_question_ids": [
                    item.get("question_id")
                    for item in business_payload.get("question_candidate_catalog", [])[:3]
                    if item.get("question_id")
                ],
                "resource_type": "practice" if business_payload.get("question_candidate_catalog") else "none",
                "blueprint_content": None,
            }, on_delta)
        if role == "audit_agent":
            return self._emit(
                {
                    "decision": "pass",
                    "findings": ["已核验目标、期限、教材、负荷、来源与发布边界。"],
                    "audit_report": (
                        "审核已逐项核对业务正文、可信来源、时间与层级约束。"
                        "当前未发现阻断发布的问题，系统仍需执行确定性合同门禁。"
                    ),
                },
                on_delta,
            )
        if role == "paper_blueprint_compiler":
            document = str(business_payload.get("blueprint_document") or "")
            duration_match = re.search(r"作答时长：(\d+)分钟", document)
            score_match = re.search(r"全卷总分：(\d+(?:\.\d+)?)分", document)
            return self._emit(
                {
                    "status": "compiled",
                    "contract_version": "1.0",
                    "contract": {
                        "title": "四君子汤章节练习试卷",
                        "scope_summary": "围绕四君子汤组成、功效主治和配伍意义进行教学练习。",
                        "duration_minutes": (
                            int(duration_match.group(1)) if duration_match else None
                        ),
                        "total_score": (
                            float(score_match.group(1)) if score_match else None
                        ),
                        "units": [
                            {
                                "unit_key": "组成与功效主治",
                                "knowledge_module": "组成与功效主治",
                                "learning_objective": "识别组成并理解功效主治。",
                                "retrieval_query": "四君子汤 组成 功效 主治",
                                "question_type_preferences": ["单项选择题", "简答题"],
                                "required_question_count": 2,
                                "selection_rules": ["优先选择直接考查核心概念的题目"],
                            },
                            {
                                "unit_key": "配伍意义与辨析",
                                "knowledge_module": "配伍意义与辨析",
                                "learning_objective": "说明君臣佐使并完成方剂辨析。",
                                "retrieval_query": "四君子汤 配伍意义 君臣佐使 辨析",
                                "question_type_preferences": [],
                                "required_question_count": 2,
                                "selection_rules": ["与上一单元全卷去重"],
                            },
                        ],
                        "assumptions": ["题型未完全指定时，按候选题实际类型组卷。"],
                        "acceptance_criteria": [
                            "题目全部来自候选池或经审核的原创补充题",
                            "全卷题目不得重复",
                        ],
                        "field_anchors": {
                            "/title": [
                                {"source_field": "blueprint_document", "source_quote": "四君子汤章节练习试卷"}
                            ],
                            "/scope_summary": [
                                {"source_field": "blueprint_document", "source_quote": "围绕四君子汤组成、功效主治和配伍意义进行教学练习。"}
                            ],
                            "/units": [
                                {"source_field": "blueprint_document", "source_quote": "【单元一：组成与功效主治】"},
                                {"source_field": "blueprint_document", "source_quote": "【单元二：配伍意义与辨析】"},
                            ],
                        },
                    },
                },
                on_delta,
            )
        if role == "paper_assembly_compiler":
            document = str(business_payload.get("assembly_document") or "")
            catalog = business_payload.get("candidate_catalog", [])
            title_match = re.search(r"【试卷标题】([^\n]+)", document)
            title = title_match.group(1).strip() if title_match else "四君子汤章节练习试卷"
            selected_items = []
            for unit in catalog:
                unit_id = str(unit.get("unit_id") or "")
                for item in unit.get("items", []):
                    question_id = str(item.get("question_id") or "")
                    quote = f"单元{unit_id}选用候选题{question_id}。"
                    if quote in document:
                        selected_items.append(
                            {
                                "unit_id": unit_id,
                                "question_id": question_id,
                                "source_anchors": [
                                    {
                                        "source_field": "assembly_document",
                                        "source_quote": quote,
                                    }
                                ],
                            }
                        )
            generated_items = []
            generated_pattern = re.compile(
                r"单元(?P<unit>\S+)原创(?P<type>[^：:\n]+)："
                r"题干：(?P<stem>.*?)；选项：(?P<options>.*?)；"
                r"参考答案：(?P<answer>.*?)；解析：(?P<explanation>.*?)(?=\n|$)"
            )
            for match in generated_pattern.finditer(document):
                quote = match.group(0)
                generated_items.append(
                    {
                        "unit_id": match.group("unit"),
                        "question_type": match.group("type"),
                        "stem": match.group("stem"),
                        "options": [
                            item.strip()
                            for item in match.group("options").split("、")
                            if item.strip()
                        ],
                        "reference_answer": match.group("answer"),
                        "explanation": match.group("explanation").rstrip("。"),
                        "source_basis_refs": [],
                        "source_anchors": [
                            {
                                "source_field": "assembly_document",
                                "source_quote": quote,
                            }
                        ],
                    }
                )
            return self._emit(
                {
                    "status": "compiled",
                    "contract_version": "1.0",
                    "contract": {
                        "title": title,
                        "selected_items": selected_items,
                        "generated_items": generated_items,
                        "field_anchors": {
                            "/title": [
                                {
                                    "source_field": "assembly_document",
                                    "source_quote": title,
                                }
                            ]
                        },
                    },
                },
                on_delta,
            )
        if role == "paper_audit_findings_compiler":
            findings = [
                str(item).strip()
                for item in business_payload.get("findings", [])
                if str(item).strip()
            ]
            return self._emit(
                {
                    "status": "compiled",
                    "contract_version": "1.0",
                    "issues": [
                        {
                            "issue_type": "unresolved",
                            "message": finding,
                            "blocking": False,
                            "source_anchors": [
                                {
                                    "source_field": "findings",
                                    "source_quote": finding,
                                }
                            ],
                        }
                        for finding in findings
                    ],
                },
                on_delta,
            )
        return self._emit({"producer": role, "status": "success"}, on_delta)

    @staticmethod
    def _source_quote(value: Any) -> str:
        return value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, sort_keys=True
        )

    @staticmethod
    def _emit(result: dict[str, Any], on_delta: Callable[[str], None] | None) -> dict[str, Any]:
        if on_delta:
            on_delta(json.dumps(result, ensure_ascii=False))
        return result
