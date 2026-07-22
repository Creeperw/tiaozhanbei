from __future__ import annotations

import json
from typing import Any, Callable


class StubChatModel:
    async def complete_json(
        self,
        role: str,
        payload: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        business_payload = payload.get("payload", payload)
        if role == "memory_agent":
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
            requires_compression = bool(
                business_payload.get("conversation_context", {}).get("requires_compression")
            )
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
            is_plan = not requests_resource and (
                plan_scope in {"long_term", "short_term", "daily_task", "unspecified"}
                or plan_scope_hint in {"long_term", "short_term", "daily_task", "unspecified"}
                or any(
                keyword in request_text
                for keyword in (
                    "制定计划", "学习计划", "复习计划", "长期计划", "短期计划",
                    "调整计划", "规划", "学习状态", "状态如何", "学情",
                )
                )
            )
            status_only = any(
                keyword in request_text for keyword in ("学习状态", "状态如何", "学情")
            ) and not any(
                keyword in request_text for keyword in ("制定", "调整", "修改", "计划", "规划")
            )
            return self._emit({
                "task_type": (
                    "paper_generation"
                    if requests_paper
                    else "knowledge_explanation" if requests_explanation
                    else "learning_plan" if is_plan else "personalized_review_card"
                ),
                "selected_agents": (
                    [
                        "knowledge_base_agent",
                        "expert_agent",
                        "audit_agent",
                    ]
                    if requests_paper
                    else [
                        *(["memory_agent"] if requires_compression else []),
                        "knowledge_base_agent",
                        "expert_agent",
                        "audit_agent",
                    ]
                    if requests_explanation
                    else [
                        *(["memory_agent"] if requires_compression else []),
                        *([] if status_only else ["knowledge_base_agent"]),
                        "diagnosis_agent",
                        "learning_plan_service",
                    ]
                    if is_plan
                    else [
                        *(["memory_agent"] if requires_compression else []),
                        "knowledge_base_agent",
                        "diagnosis_agent",
                        "learning_plan_service",
                        "review_scheduler",
                        "expert_agent",
                        "audit_agent",
                    ]
                ),
                "plan_scope": plan_scope or plan_scope_hint,
                "routing_reason": (
                    "用户要求生成试卷蓝图，需要知识检索、专家蓝图生成和审核。"
                    if requests_paper
                    else "用户要求知识讲解，需要教材检索、专家讲解和审核，不生成学习规划。"
                    if requests_explanation
                    else "用户只要求制定计划，无需生成教学资源。"
                    if is_plan
                    else "用户同时需要学习计划和可直接学习的资源，需要完成计划落地、专家生成和审核。"
                ),
                "risk_level": "low",
                "requires_audit": True,
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
                        f"| 1. 临时基础阶段 | 待确认教材 | 建立{topic}基础 | "
                        "提交学习笔记 | 完成核对后晋级 | 待确认 |"
                    )
                generated_long = (
                    f"## 目标契约\n最终目标是系统掌握{topic}；期限和稳定能力证据待用户确认。\n"
                    "## 能力图谱摘要\n围绕基础识记、理解辨析和应用反馈逐步推进。\n"
                    "## 长期阶段路径\n| 阶段 | 具体教材 | 阶段目标 | 验收证据 | 晋级条件 | 个性化状态 |\n"
                    "|---|---|---|---|---|---|\n"
                    + "\n".join(phase_rows)
                    + "\n## 长期维护与恢复\n中断时保留一次短时主动回忆，复盘后回到当前阶段。\n"
                    "## 长期重规划触发器\n目标、期限、路线教材或稳定能力证据持续变化时调整。"
                )
                weeks = "未来两周" if "两周" in request_text else "未来一周"
                cycle_plan = (
                    f"第1周完成{topic}的基础回忆和教材核对，形成遗漏清单；"
                    "第2周完成类项辨析与综合自测，以纠错记录验收。"
                    if "两周" in request_text
                    else f"周初完成{topic}的基础回忆，周中进行教材核对和错因订正，"
                    "周末完成闭卷复述与综合验收。"
                )
                generated_short = (
                    f"## 当前周期目标\n{weeks}在当前长期阶段推进{topic}，以回忆和核对记录验收。\n"
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
                                "book": list(phase.get("books", [])) or ["待确认教材"],
                                "goal": str(phase.get("objective") or "完成本阶段目标"),
                            }
                            for index, phase in enumerate(phases, start=1)
                        ]
                        or [
                            {
                                "stage": 1,
                                "book": ["待确认教材"],
                                "goal": "长期教材和阶段目标待用户确认",
                            }
                        ]
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
                        for key in ("long_term_plan_content", "long_term_plan_stages")
                    }
                elif plan_scope == "short_term":
                    response = {
                        key: response[key]
                        for key in (
                            "short_term_plan_content",
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
            route_context = business_payload.get("route_context", {})
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
                return self._emit({
                    "generated_items": [
                        {
                            "unit_id": unit_id,
                            "question_type": question_type,
                            "stem": f"{business_payload.get('knowledge_module', '当前主题')}补充练习题{index + 1}",
                            "options": ["A. 符合教学结论", "B. 不符合教学结论"] if "选择" in question_type else [],
                            "reference_answer": "A" if "选择" in question_type else "依据当前教学材料作答。",
                            "analysis": "用于补足用户明确题量，正式发布前由系统审核完整性。",
                            "selection_rationale": "按蓝图单元补足硬题量。",
                            "source_tier": "model_knowledge",
                        }
                        for index in range(count)
                    ]
                }, on_delta)
            if phase == "knowledge_explanation":
                topic = str(business_payload.get("topic", "当前主题"))
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
                total_score = constraints.get("total_score", 100)
                duration = constraints.get("duration_minutes")
                return self._emit({
                    "title": "四君子汤章节练习试卷",
                    "source_status": "user_provided_unverified",
                    "scope_summary": "围绕四君子汤组成、功效主治和配伍意义进行教学练习。",
                    "duration_minutes": duration,
                    "total_score": total_score,
                    "units": [
                        {
                            "knowledge_module": "组成与功效主治",
                            "learning_objective": "识别组成并理解功效主治。",
                            "retrieval_query": "四君子汤 组成 功效 主治",
                            "question_type_preferences": ["单项选择题", "简答题"],
                            "required_question_count": 2,
                            "score_total": float(total_score) * 0.5,
                            "candidate_limit": 8,
                            "selection_rules": ["优先选择直接考查核心概念的题目"],
                            "difficulty_preference": None,
                        },
                        {
                            "knowledge_module": "配伍意义与辨析",
                            "learning_objective": "说明君臣佐使并完成方剂辨析。",
                            "retrieval_query": "四君子汤 配伍意义 君臣佐使 辨析",
                            "question_type_preferences": [],
                            "required_question_count": 2,
                            "score_total": float(total_score) * 0.5,
                            "candidate_limit": 8,
                            "selection_rules": ["与上一单元全卷去重"],
                            "difficulty_preference": None,
                        },
                    ],
                    "assumptions": ["题型未完全指定，按候选题实际类型组卷。"],
                    "acceptance_criteria": ["题目全部来自候选池", "全卷题目ID不重复"],
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
                        "【蓝图矩阵】核心组成模块：识记层级，单选题，中等难度，建议2题，"
                        "建议20分；功效主治模块：理解与辨析层级，简答题，中等难度，"
                        "建议2题，建议40分；配伍应用模块：应用层级，病例题，较难，"
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
            return self._emit({"decision": "pass"}, on_delta)
        return self._emit({"producer": role, "status": "success"}, on_delta)

    @staticmethod
    def _emit(result: dict[str, Any], on_delta: Callable[[str], None] | None) -> dict[str, Any]:
        if on_delta:
            on_delta(json.dumps(result, ensure_ascii=False))
        return result
