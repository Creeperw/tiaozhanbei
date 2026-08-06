from __future__ import annotations

import re
from typing import Any
from collections import Counter

from competition_app.agents.common import envelope
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.knowledge import (
    EvidenceItem,
    EvidencePack,
    QuestionCandidateReference,
    QuestionDetail,
    QuestionSearchDecision,
    QuestionSearchResult,
)
from competition_app.contracts.paper import QuestionCandidatePool, UnitQuestionCandidates
from competition_app.tools.knowledge_retrieval import KnowledgeRetrievalTool
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel
from competition_app.llm.schemas import (
    KnowledgeModelOutput,
    KnowledgeRetrievalPlanModelOutput,
    validate_training_style_output,
)
from competition_app.runtime.event_stream import emit_runtime_event
from competition_app.services.conversation_history import (
    sanitize_compressed_dialogue_summary,
)


class KnowledgeBaseAgent:
    # 模型判定证据不足时，允许补充检索的最多轮数与每轮查询数。
    # 上限由系统强制，避免模型反复要求检索形成无限循环。
    MAX_SUPPLEMENT_ROUNDS = 2
    MAX_SUPPLEMENT_QUERIES = 3

    def __init__(
        self, retrieval_tool: KnowledgeRetrievalTool, chat_model: ChatModel | None = None
    ) -> None:
        self.retrieval_tool = retrieval_tool
        self.chat_model = chat_model or StubChatModel()

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[EvidencePack]:
        if (
            str(context.get("task_type")) == "paper_generation"
            and "paper_blueprint" in context.get("dependency_outputs", {})
        ):
            return await self._retrieve_questions_by_blueprint(context)
        user_request = str(context.get("user_request") or context.get("topic") or "").strip()
        if not user_request:
            raise ValueError("knowledge base agent requires user_request")
        external_request = bool(context.get("external_information_request")) or any(
            marker in user_request.lower()
            for marker in (
                "天气", "气温", "降雨", "下雨", "空气质量", "台风",
                "距离下次", "考试时间", "考试日期", "什么时候考试",
                "报名时间", "截止日期", "日程", "赛程", "最新消息",
                "当前时间", "今天几号", "现在几点",
            )
        )
        prompt_skill = prompt_skill_registry.load("knowledge_base_agent", "vector_retrieval")
        memory_output = context.get("dependency_outputs", {}).get("memory")
        memory_payload = getattr(memory_output, "payload", None)
        context_summary = getattr(memory_payload, "context_summary", None)
        compressed_summary = sanitize_compressed_dialogue_summary(
            getattr(context_summary, "summary", "")
            or context.get("compressed_conversation_summary")
            or ""
        )
        conversation_messages = list(context.get("messages", []))
        recent_messages = conversation_messages[-1:] if compressed_summary else conversation_messages[-8:]
        repair_instruction = dict(context.get("repair_instruction") or {})
        try:
            if external_request:
                # Current-fact retrieval has no textbook KP expression to
                # generate. Keep the user's wording intact and let the
                # approved web tool resolve it directly.
                raw_plan = {
                    "kp_query": user_request,
                    "question_query": user_request,
                    "retrieval_reason": "用户请求的是时效性外部事实，直接检索网络参考来源。",
                }
            else:
                raw_plan = await self.chat_model.complete_json(
                    "knowledge_base_agent",
                    build_model_context(
                        context,
                        target_agent="knowledge_base_agent",
                        prompt_skill=prompt_skill,
                        payload={
                            "phase": "plan_retrieval",
                            "user_request": user_request,
                            "recent_conversation": [
                                {
                                    "role": str(item.get("role", "")),
                                    "content": str(item.get("content", "")),
                                }
                                for item in recent_messages
                                if isinstance(item, dict) and str(item.get("content", "")).strip()
                            ],
                            "compressed_conversation_summary": compressed_summary,
                            "repair_request": (
                                {
                                    "issue_ids": repair_instruction.get("issue_ids", []),
                                    "locations": repair_instruction.get("locations", []),
                                    "instruction": repair_instruction.get(
                                        "repair_instruction", ""
                                    ),
                                    "previous_retrieval_digest": repair_instruction.get(
                                        "previous_output_digest"
                                    ),
                                }
                                if repair_instruction
                                else None
                            ),
                            "task_type": str(context.get("task_type", "personalized_review_card")),
                            "available_tools": {
                                "get_kp_with_content": "用模型生成的 kp_query 检索知识点及教材内容。",
                                "get_question_with_content": "按需用 question_query 检索题目及内容。",
                                "search_video_resources": "按知识主题检索公开教学视频；只返回视频链接和摘要，不把网页内容当作教材事实。",
                                "search_reference_resources": "检索外部参考内容、论文或原文；只作为补充来源，不替代教材证据。",
                                "search_question_resources": "检索外部练习题、考试题和解析；只作为题目候选参考，不直接写入正式题库。",
                                "search_web_resources": "检索天气、考试日期、报名时间等时效性事实；优先官方来源。",
                            },
                            "output_schema": KnowledgeRetrievalPlanModelOutput.model_json_schema(),
                        },
                        permission_note=(
                            "结合最近对话解析‘这些、它、上述内容’等指代，再生成可独立检索的两类检索语句和检索理由；"
                            "不得直接伪造检索结果、工具返回、知识点ID或题目ID。"
                        ),
                    ),
                )
            if not isinstance(raw_plan, dict):
                raw_plan = {}
            retrieval_plan = KnowledgeRetrievalPlanModelOutput.model_validate(
                {
                    "kp_query": raw_plan.get("kp_query") or raw_plan.get("knowledge_query"),
                    "question_query": raw_plan.get("question_query") or raw_plan.get("question_search"),
                    "retrieval_reason": raw_plan.get("retrieval_reason") or raw_plan.get("reason") or "根据用户请求检索相关知识和题目。",
                }
            )
        except ValueError as exc:
            if context.get("terminal_trace"):
                context["terminal_trace"].validation(
                    "knowledge_base_agent", valid=False, detail="KnowledgeRetrievalPlanModelOutput"
                )
            raise ValueError("knowledge retrieval plan validation failed") from exc
        try:
            if external_request:
                location = str(
                    context.get("user_profile", {}).get("location")
                    or context.get("user_profile", {}).get("user_area")
                    or context.get("user_profile", {}).get("city")
                    or ""
                )
                registry = context.get("tool_registry")
                if registry is not None:
                    pack = await registry.invoke(
                        "get_external_fact_evidence",
                        "knowledge_base_agent",
                        trace_recorder=context.get("trace_recorder"),
                        safe_input_summary={"query_length": len(user_request)},
                        safe_output_summary_factory=lambda value: {
                            "evidence_count": len(value.evidence_items)
                        },
                        query=user_request,
                        location=location,
                    )
                else:
                    pack = await self.retrieval_tool.build_external_evidence_pack(
                        user_request,
                        location=location,
                    )
            else:
                pack = await self._build_evidence_pack(retrieval_plan.kp_query, context)
        except LookupError:
            if external_request:
                raise
            # The retrieval planner occasionally replaces a concrete learner
            # topic (for example, “感冒”) with a generic label such as
            # “中医药基础知识点”.  That label cannot resolve to a catalog KP,
            # even though the original request can.  Treat the model query as
            # a retrieval hint, not as the authoritative business input.
            fallback_query = self._fallback_kp_query(user_request)
            if not fallback_query or fallback_query == retrieval_plan.kp_query.strip():
                raise
            pack = await self._build_evidence_pack(fallback_query, context)
            retrieval_plan = retrieval_plan.model_copy(
                update={
                    "kp_query": fallback_query,
                    "retrieval_reason": (
                        retrieval_plan.retrieval_reason
                        + " 模型检索词未命中正式知识点，已回退到用户原始主题。"
                    )[:500],
                }
            )
        query = pack.query
        question_retrieval_notes: list[str] = []
        if external_request:
            question_result = QuestionSearchResult(
                query=retrieval_plan.question_query,
                resolved_kp_ids=[],
                embedding_model="not_applicable",
                vector_index_path="",
                items=[],
            )
        else:
            try:
                question_result = await self._search_question_candidates(
                    retrieval_plan.question_query,
                    pack.resolved_kp_ids,
                    context,
                )
            except (LookupError, RuntimeError, TimeoutError) as exc:
                # Question candidates enrich a review card but are not a
                # prerequisite for explaining/recommending the successfully
                # retrieved textbook and video resources.  A slow embedding
                # provider used to abort and then repeat the complete Agent,
                # including its already-finished model and web searches.
                question_retrieval_notes.append(
                    "题目候选检索暂不可用，本次保留教材与资源推荐结果，不自动补造练习题。"
                )
                emit_runtime_event(
                    "question_retrieval_degraded",
                    agent="knowledge_base_agent",
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:300],
                )
                question_result = QuestionSearchResult(
                    query=retrieval_plan.question_query,
                    resolved_kp_ids=list(pack.resolved_kp_ids),
                    embedding_model="degraded",
                    vector_index_path="",
                    items=[],
                )
        if not pack.resolved_kp_ids:
            bridge_kp_ids = list(
                dict.fromkeys(
                    bridge.kp_id
                    for item in question_result.items
                    for bridge in item.bridges
                    if bridge.kp_id
                )
            )
            if bridge_kp_ids:
                pack = pack.model_copy(
                    update={
                        "resolved_kp_ids": bridge_kp_ids,
                        "risk_notes": [
                            *pack.risk_notes,
                            "教材向量切片未直接映射知识点；系统使用正式题库 Bridge 的知识点ID建立本次复习候选。",
                        ],
                    }
                )
        emit_runtime_event(
            "knowledge_retrieval",
            agent="knowledge_base_agent",
            kp_query=retrieval_plan.kp_query,
            question_query=retrieval_plan.question_query,
            evidence_items=[
                {
                    "source_id": item.source_id,
                    "content": item.content_summary,
                    "content_summary": item.content_summary,
                    "authority": item.authority_level,
                    "confidence": item.confidence,
                    "source_url": item.source_url,
                    "resource_type": item.resource_type,
                }
                for item in pack.evidence_items
            ],
            question_candidates=self._question_semantic_view(question_result),
        )
        semantic_facts = [
            {
                "text": item.content_summary,
                "authority": item.authority_level,
                "source_id": item.source_id,
                "resource_type": item.resource_type,
            }
            for item in pack.evidence_items
            if item.resource_type != "question"
        ]
        # 总结阶段支持按需补充检索：模型输出 need_more_retrieval=true 并给出
        # 聚焦缺口的补充查询时，系统自动再检索一次并合并证据，然后重新总结；
        # 最多 MAX_SUPPLEMENT_ROUNDS 轮，防止模型反复要求检索形成无限循环。
        previous_summary = ""
        model_output: KnowledgeModelOutput | None = None
        for retrieval_round in range(1, KnowledgeBaseAgent.MAX_SUPPLEMENT_ROUNDS + 2):
            model_output = await self._summarize_retrieved_content(
                context=context,
                prompt_skill=prompt_skill,
                query=query,
                pack=pack,
                user_request=user_request,
                semantic_facts=semantic_facts,
                retrieval_plan=retrieval_plan,
                repair_instruction=repair_instruction,
                retrieval_round=retrieval_round,
                previous_summary=previous_summary,
            )
            if (
                not model_output.need_more_retrieval
                or retrieval_round > KnowledgeBaseAgent.MAX_SUPPLEMENT_ROUNDS
            ):
                break
            supplement_queries = self._dedupe_supplement_queries(
                model_output.supplemental_queries,
                {retrieval_plan.kp_query.strip(), query.strip()},
            )
            if not supplement_queries:
                break
            existing_source_ids = {item.source_id for item in pack.evidence_items}
            extra_facts, extra_items, extra_kp_ids = await self._supplement_retrieval(
                supplement_queries, context, existing_source_ids
            )
            if not extra_items:
                break
            semantic_facts = [*semantic_facts, *extra_facts]
            pack = pack.model_copy(update={
                "evidence_items": [*pack.evidence_items, *extra_items],
                "resolved_kp_ids": list(
                    dict.fromkeys([*pack.resolved_kp_ids, *extra_kp_ids])
                ),
            })
            previous_summary = model_output.retrieval_summary
            emit_runtime_event(
                "knowledge_retrieval",
                agent="knowledge_base_agent",
                kp_query="；".join(supplement_queries),
                question_query="",
                retrieval_round=retrieval_round + 1,
                evidence_items=[
                    {
                        "source_id": item.source_id,
                        "content": item.content_summary,
                        "content_summary": item.content_summary,
                        "authority": item.authority_level,
                        "confidence": item.confidence,
                        "source_url": item.source_url,
                        "resource_type": item.resource_type,
                    }
                    for item in extra_items
                ],
                question_candidates=[],
            )
        assert model_output is not None
        final_needed = True
        candidates = [
            QuestionCandidateReference(
                question_id=item.question_id,
                channels=item.retrieval.channels,
                bridge_layers=sorted({bridge.bridge_layer for bridge in item.bridges}),
            )
            for item in question_result.items
        ]
        channel_summary = sorted({channel for item in candidates for channel in item.channels})
        decision = QuestionSearchDecision(
            rule_question_search_needed=False,
            rule_reasons=[],
            model_question_search_needed=True,
            model_question_search_reason=retrieval_plan.retrieval_reason,
            final_question_search_needed=final_needed,
            candidate_count=len(candidates),
            channel_summary=channel_summary,
        )
        pack = pack.model_copy(update={
            # quality_labels are an internal retrieval assessment, not learner-facing
            # safety notes. Only uncertainty is allowed to flow into downstream
            # resource safety metadata.
            "risk_notes": [
                *pack.risk_notes,
                *question_retrieval_notes,
                *model_output.uncertainty,
            ],
            "retrieval_summary": (
                model_output.retrieval_summary.strip()
                or self._fallback_retrieval_summary(semantic_facts)
            ),
            "summary_evidence_ids": [
                item.evidence_id
                for item in pack.evidence_items
                if item.resource_type != "question"
            ][:5],
            "question_search_decision": decision,
            "question_candidates": candidates,
        })
        pack._question_details = list(question_result.items)
        return envelope(context, "knowledge_base_agent", "evidence_pack", pack)

    async def _summarize_retrieved_content(
        self,
        *,
        context: dict[str, Any],
        prompt_skill: Any,
        query: str,
        pack: EvidencePack,
        user_request: str,
        semantic_facts: list[dict[str, Any]],
        retrieval_plan: KnowledgeRetrievalPlanModelOutput,
        repair_instruction: dict[str, Any],
        retrieval_round: int,
        previous_summary: str,
    ) -> KnowledgeModelOutput:
        """Run one summarize pass over the current evidence.

        The live model judges whether the evidence is sufficient; when it
        reports ``need_more_retrieval=true`` the caller runs supplemental
        retrieval and calls this method again with the merged evidence.
        """
        try:
            raw_quality = await self.chat_model.complete_json(
                "knowledge_base_agent",
                build_model_context(
                    context,
                    target_agent="knowledge_base_agent",
                    prompt_skill=prompt_skill,
                    payload={
                        "kp": {
                            "query": query,
                            "resolved_kp_ids": pack.resolved_kp_ids,
                        },
                        "phase": "process_retrieved_content",
                        "user_request": user_request,
                        "evidence": semantic_facts,
                        "retrieval_plan": retrieval_plan.model_dump(mode="json"),
                        "retrieval_round": retrieval_round,
                        "previous_retrieval_summary": previous_summary,
                        "repair_request": (
                            {
                                "issue_ids": repair_instruction.get("issue_ids", []),
                                "locations": repair_instruction.get("locations", []),
                                "instruction": repair_instruction.get(
                                    "repair_instruction", ""
                                ),
                            }
                            if repair_instruction
                            else None
                        ),
                        "task_type": str(context.get("task_type", "personalized_review_card")),
                        "expected_uncertainty": [],
                        "output_schema": KnowledgeModelOutput.model_json_schema(),
                    },
                    permission_note=(
                        "只处理已返回的教材、向量、BM25和可信网络参考，先筛掉无关内容，再用自然语言总结"
                        "可供下游使用的回答依据；题目候选不参与本次总结。若证据不足（召回冲突、覆盖不足、"
                        "无法映射正式知识点或用户具体问题点缺失），输出 need_more_retrieval=true 并给出"
                        "1-3 条聚焦缺口的补充检索语句 supplemental_queries，系统会自动执行补充检索后再次"
                        "调用本阶段重新总结；系统最多补充检索 2 轮，证据足够后 must 输出 need_more_retrieval=false。"
                        "不得再次规划工具、伪造检索结果或生成系统ID。"
                    ),
                ),
            )
            if not isinstance(raw_quality, dict):
                raw_quality = {}
            forbidden_quality_fields = {"items", "evidence", "question_id", "kp_id"}.intersection(raw_quality)
            if forbidden_quality_fields:
                raise ValueError(
                    "training output contract forbids objective fields: "
                    + ", ".join(sorted(forbidden_quality_fields))
                )
            # The live model often answers this review step in natural language
            # names instead of the internal field names. Normalize at the
            # boundary; downstream code still receives a small typed object.
            if "quality_labels" not in raw_quality:
                nested_pack = raw_quality.get("evidence_pack")
                nested_pack = nested_pack if isinstance(nested_pack, dict) else {}
                raw_quality["quality_labels"] = raw_quality.get("findings") or nested_pack.get("findings", [])
            if "uncertainty" not in raw_quality:
                nested_pack = raw_quality.get("evidence_pack")
                nested_pack = nested_pack if isinstance(nested_pack, dict) else {}
                raw_quality["uncertainty"] = nested_pack.get("uncertainty", [])
            if "retrieval_summary" not in raw_quality:
                raw_quality["retrieval_summary"] = (
                    raw_quality.get("summary")
                    or raw_quality.get("answer_basis")
                    or raw_quality.get("content")
                    or ""
                )
            if "need_more_retrieval" not in raw_quality:
                raw_quality["need_more_retrieval"] = False
            if "supplemental_queries" not in raw_quality:
                raw_quality["supplemental_queries"] = []
            # Some live responses repeat the retrieved evidence under an
            # `evidence_pack`/`knowledge_fragments` field. Those are not model
            # judgments and are already owned by the retrieval tool.
            raw_quality = {
                key: value
                for key, value in raw_quality.items()
                if key in {
                    "retrieval_summary",
                    "quality_labels",
                    "uncertainty",
                    "need_more_retrieval",
                    "supplemental_queries",
                }
            }
            if isinstance(raw_quality.get("quality_labels"), str):
                raw_quality["quality_labels"] = [raw_quality["quality_labels"]]
            if isinstance(raw_quality.get("uncertainty"), str):
                raw_quality["uncertainty"] = (
                    [raw_quality["uncertainty"]] if raw_quality["uncertainty"].strip() else []
                )
            if isinstance(raw_quality.get("need_more_retrieval"), str):
                raw_quality["need_more_retrieval"] = (
                    raw_quality["need_more_retrieval"].strip().lower()
                    in {"true", "yes", "1", "是", "需要", "不足"}
                )
            if not isinstance(raw_quality.get("need_more_retrieval"), bool):
                raw_quality["need_more_retrieval"] = False
            if isinstance(raw_quality.get("supplemental_queries"), str):
                raw_quality["supplemental_queries"] = [raw_quality["supplemental_queries"]]
            if not isinstance(raw_quality.get("supplemental_queries"), list):
                raw_quality["supplemental_queries"] = []
            model_output = validate_training_style_output(
                KnowledgeModelOutput, raw_quality, []
            )
        except ValueError as exc:
            if context.get("terminal_trace"):
                context["terminal_trace"].validation("knowledge_base_agent", valid=False, detail=str(exc))
            if "forbids objective fields" in str(exc):
                raise
            model_output = KnowledgeModelOutput(
                retrieval_summary=self._fallback_retrieval_summary(semantic_facts),
                quality_labels=["模型总结不可用，系统保留精简检索依据。"],
                uncertainty=["检索后总结未通过宽松校验。"],
            )
        if context.get("terminal_trace"):
            context["terminal_trace"].validation("knowledge_base_agent", valid=True, detail="KnowledgeModelOutput")
        return model_output

    @staticmethod
    def _dedupe_supplement_queries(
        queries: list[str], excluded: set[str]
    ) -> list[str]:
        """Normalize and dedupe supplemental retrieval queries.

        Queries are trimmed, whitespace-collapsed and compared case-sensitively
        against each other and against the queries already executed this run.
        Empty, oversized (>200 chars) or repeated queries are dropped; at most
        MAX_SUPPLEMENT_QUERIES are returned.
        """
        normalized_excluded = {"".join(str(item).split()) for item in excluded}
        seen: set[str] = set()
        result: list[str] = []
        for raw in queries:
            query_text = str(raw or "").strip().strip(" \t\n。，,；;：:")
            if not query_text or len(query_text) > 200:
                continue
            normalized = "".join(query_text.split())
            if normalized in seen or normalized in normalized_excluded:
                continue
            seen.add(normalized)
            result.append(query_text)
            if len(result) >= KnowledgeBaseAgent.MAX_SUPPLEMENT_QUERIES:
                break
        return result

    async def _supplement_retrieval(
        self,
        queries: list[str],
        context: dict[str, Any],
        existing_source_ids: set[str],
    ) -> tuple[list[dict[str, Any]], list[EvidenceItem], list[str]]:
        """Run one supplemental retrieval round over the given queries.

        Merges the newly retrieved evidence into semantic facts, EvidenceItems
        and resolved KP ids. Evidence already present in the pack is skipped so
        a broad supplemental query cannot duplicate the first-round content.
        Returns (facts, items, kp_ids); all three are empty when every query
        failed to resolve.
        """
        facts: list[dict[str, Any]] = []
        items: list[EvidenceItem] = []
        kp_ids: list[str] = []
        for query_text in queries:
            try:
                extra_pack = await self._build_evidence_pack(query_text, context)
            except LookupError:
                continue
            for item in extra_pack.evidence_items:
                if (
                    item.resource_type == "question"
                    or item.source_id in existing_source_ids
                ):
                    continue
                existing_source_ids.add(item.source_id)
                items.append(item)
                facts.append({
                    "text": item.content_summary,
                    "authority": item.authority_level,
                    "source_id": item.source_id,
                    "resource_type": item.resource_type,
                })
            kp_ids.extend(extra_pack.resolved_kp_ids)
        return facts, items, kp_ids

    @staticmethod
    def _fallback_retrieval_summary(evidence: list[dict[str, Any]]) -> str:
        summaries = [
            " ".join(str(item.get("text", "")).split())
            for item in evidence
            if str(item.get("text", "")).strip()
        ]
        return "\n".join(summaries[:3])[:8_000]

    async def _retrieve_questions_by_blueprint(
        self, context: dict[str, Any]
    ) -> AgentEnvelope[QuestionCandidatePool]:
        blueprint = context["dependency_outputs"]["paper_blueprint"].payload
        units: list[UnitQuestionCandidates] = []
        for unit in blueprint.units:
            warnings: list[str] = []
            try:
                evidence_pack = await self._build_evidence_pack(
                    unit.retrieval_query, context
                )
                retrieval_limit = min(
                    50,
                    max(
                        unit.candidate_limit * 5,
                        unit.required_question_count * 5,
                        20,
                    ),
                )
                target_difficulty = getattr(unit, "target_difficulty", None)
                difficulty_requested = target_difficulty is not None
                result = await self._search_question_candidates(
                    unit.retrieval_query,
                    evidence_pack.resolved_kp_ids,
                    context,
                    limit=retrieval_limit,
                    difficulty=target_difficulty if difficulty_requested else None,
                )
                eligible_items = self._eligible_unit_candidates(
                    result.items, unit, evidence_pack
                )
                if (
                    difficulty_requested
                    and len(eligible_items) < unit.required_question_count
                ):
                    # 指定难度且精确难度正式题不足时，放宽到“未标注难度正式题”
                    # 补足候选池；其他难度的正式题仍不进入候选池（严格匹配，
                    # 不伪装指定难度）。
                    try:
                        relaxed_result = await self._search_question_candidates(
                            unit.retrieval_query,
                            evidence_pack.resolved_kp_ids,
                            context,
                            limit=retrieval_limit,
                        )
                        result = QuestionSearchResult(
                            query=unit.retrieval_query,
                            resolved_kp_ids=list(dict.fromkeys(
                                [*result.resolved_kp_ids, *relaxed_result.resolved_kp_ids]
                            )),
                            embedding_model=relaxed_result.embedding_model,
                            vector_index_path=relaxed_result.vector_index_path,
                            items=[*result.items, *relaxed_result.items],
                        )
                        warnings.append(
                            f"难度{target_difficulty}正式题候选不足，已纳入未标注难度的"
                            "正式题补足；其他难度题目不会冒充指定难度。"
                        )
                    except (LookupError, RuntimeError, TimeoutError, ValueError) as exc:
                        warnings.append(
                            "难度补足检索暂不可用："
                            f"{type(exc).__name__}；已保留首轮难度候选继续组卷。"
                        )
                    eligible_items = self._eligible_unit_candidates(
                        result.items, unit, evidence_pack
                    )
                # A first formal-index pass is not enough evidence for a hard
                # paper count. Broaden the web/question query once before the
                # Expert is asked to create variants. External hits remain
                # references only; they are never promoted to formal items.
                if len(eligible_items) < unit.required_question_count:
                    expanded_query = (
                        f"{unit.retrieval_query} 变式题 练习题 考试真题 解析"
                    )
                    try:
                        expanded_pack = await self._build_evidence_pack(
                            expanded_query, context
                        )
                        expanded_result = await self._search_question_candidates(
                            expanded_query,
                            expanded_pack.resolved_kp_ids or evidence_pack.resolved_kp_ids,
                            context,
                            limit=retrieval_limit,
                        )
                        result = QuestionSearchResult(
                            query=unit.retrieval_query,
                            resolved_kp_ids=list(dict.fromkeys(
                                [*evidence_pack.resolved_kp_ids, *expanded_pack.resolved_kp_ids]
                            )),
                            embedding_model=expanded_result.embedding_model,
                            vector_index_path=expanded_result.vector_index_path,
                            items=[*result.items, *expanded_result.items],
                        )
                        evidence_pack = evidence_pack.model_copy(update={
                            "evidence_items": [
                                *evidence_pack.evidence_items,
                                *expanded_pack.evidence_items,
                            ]
                        })
                        warnings.append("首轮候选不足，已执行一次扩展题目/网络参考检索。")
                    except (LookupError, RuntimeError, TimeoutError, ValueError) as exc:
                        warnings.append(
                            "扩展题目/网络参考检索暂不可用："
                            f"{type(exc).__name__}；已保留首轮正式题库候选继续组卷。"
                        )
            except (LookupError, RuntimeError, TimeoutError, ValueError) as exc:
                warnings.append(
                    f"{unit.knowledge_module}检索失败：{type(exc).__name__}；待补充检索。"
                )
                units.append(
                    UnitQuestionCandidates(
                        unit_id=unit.unit_id,
                        retrieval_query=unit.retrieval_query,
                        resolved_kp_ids=[],
                        requested_limit=unit.candidate_limit,
                        required_question_count=unit.required_question_count,
                        items=[],
                        external_question_references=[],
                        warnings=warnings,
                        target_difficulty=getattr(unit, "target_difficulty", None),
                        difficulty_is_hard_constraint=bool(
                            getattr(unit, "difficulty_is_hard_constraint", False)
                        ),
                        unmet_required_count=unit.required_question_count,
                    )
                )
                emit_runtime_event(
                    "paper_unit_retrieval",
                    agent="knowledge_base_agent",
                    unit_id=unit.unit_id,
                    knowledge_module=unit.knowledge_module,
                    query=unit.retrieval_query,
                    required_count=unit.required_question_count,
                    candidate_count=0,
                    raw_candidate_count=0,
                    filtered_out_count=0,
                    channel_counts={},
                    question_type_preferences=unit.question_type_preferences,
                    fallback_applied=False,
                    external_question_references=[],
                    status="insufficient",
                )
                continue
            external_question_references = [
                item
                for item in evidence_pack.evidence_items
                if item.resource_type == "question"
            ]
            raw_candidates = []
            unit_seen: set[str] = set()
            for item in result.items:
                if item.question_id in unit_seen:
                    continue
                unit_seen.add(item.question_id)
                raw_candidates.append(item)
            if difficulty_requested:
                # 严格难度匹配：指定难度时只保留精确难度正式题与未标注难度正式题，
                # 其他难度正式题不进入候选池（不伪装、不近似）。
                raw_candidates = [
                    item
                    for item in raw_candidates
                    if item.difficulty == target_difficulty or item.difficulty is None
                ]
                raw_candidates.sort(
                    key=lambda item: item.difficulty != target_difficulty
                )
            scope_candidates = [
                item
                for item in raw_candidates
                if self._matches_question_scope(item, unit, evidence_pack)
            ]
            matching_candidates = [
                item
                for item in scope_candidates
                if not unit.question_type_preferences
                or self._matches_question_type(
                    item.question_type, unit.question_type_preferences
                )
            ]
            fallback_applied = False
            if matching_candidates:
                deduplicated = matching_candidates[: unit.candidate_limit]
            else:
                deduplicated = []
            scope_filtered_count = len(raw_candidates) - len(scope_candidates)
            if scope_filtered_count:
                warnings.append(
                    f"{unit.knowledge_module}已剔除{scope_filtered_count}道主题不一致候选；"
                    "这些题目不计入题量，也不会用于组卷。"
                )
            type_filtered_count = len(scope_candidates) - len(matching_candidates)
            if type_filtered_count:
                warnings.append(
                    f"{unit.knowledge_module}已剔除{type_filtered_count}道题型不一致候选；"
                    "缺口交由后续扩展检索或Expert生成变式题补足。"
                )
            if len(deduplicated) < unit.required_question_count:
                warnings.append(
                    f"{unit.knowledge_module}候选题不足：需要"
                    f"{unit.required_question_count}题，当前仅{len(deduplicated)}题。"
                )
            units.append(
                UnitQuestionCandidates(
                    unit_id=unit.unit_id,
                    retrieval_query=unit.retrieval_query,
                    resolved_kp_ids=result.resolved_kp_ids,
                    requested_limit=unit.candidate_limit,
                    required_question_count=unit.required_question_count,
                    items=deduplicated,
                    external_question_references=external_question_references,
                    warnings=warnings,
                    target_difficulty=target_difficulty,
                    difficulty_is_hard_constraint=bool(
                        getattr(unit, "difficulty_is_hard_constraint", False)
                    ),
                    exact_difficulty_count=(
                        sum(
                            1 for item in deduplicated
                            if item.difficulty == target_difficulty
                        )
                        if difficulty_requested
                        else 0
                    ),
                    unlabeled_official_count=(
                        sum(
                            1 for item in deduplicated
                            if item.difficulty is None
                        )
                        if difficulty_requested
                        else 0
                    ),
                    web_reference_count=len(external_question_references),
                    unmet_required_count=max(
                        0, unit.required_question_count - len(deduplicated)
                    ),
                )
            )
            emit_runtime_event(
                "paper_unit_retrieval",
                agent="knowledge_base_agent",
                unit_id=unit.unit_id,
                knowledge_module=unit.knowledge_module,
                query=unit.retrieval_query,
                required_count=unit.required_question_count,
                candidate_count=len(deduplicated),
                raw_candidate_count=len(raw_candidates),
                filtered_out_count=len(raw_candidates) - len(matching_candidates),
                scope_filtered_out_count=scope_filtered_count,
                channel_counts=dict(
                    Counter(
                        channel
                        for item in raw_candidates
                        for channel in item.retrieval.channels
                    )
                ),
                question_type_preferences=unit.question_type_preferences,
                fallback_applied=fallback_applied,
                external_question_references=[
                    {
                        "source_id": item.source_id,
                        "content": item.content_summary,
                        "source_url": item.source_url,
                        "confidence": item.confidence,
                    }
                    for item in external_question_references
                ],
                candidate_details=[
                    {
                        "question_id": item.question_id,
                        "question_type": item.question_type,
                        "stem": item.stem,
                        "channels": item.retrieval.channels,
                        "channel_scores": item.retrieval.channel_scores,
                        "fusion_score": item.retrieval.fusion_score,
                    }
                    for item in deduplicated
                ],
            )
        pool = QuestionCandidatePool(
            pool_id=f"POOL_{blueprint.blueprint_id}",
            blueprint_id=blueprint.blueprint_id,
            units=units,
            retrieval_summary=[
                f"按{len(units)}个蓝图单元完成题目检索；单元内已去重，"
                "跨单元候选保留给Expert做全卷唯一选择。"
            ],
        )
        return envelope(context, "knowledge_base_agent", "question_candidate_pool", pool)

    @staticmethod
    def _normalize_question_type(value: str) -> str:
        normalized = value.strip().replace(" ", "")
        aliases = {
            "单选题": "单项选择题",
            "单项选择": "单项选择题",
            "多选题": "多项选择题",
            "多项选择": "多项选择题",
            "选择题": "选择题",
            "简答": "简答题",
            "问答": "简答题",
            "问答题": "简答题",
            "临床案例问答": "简答题",
            "案例分析": "简答题",
            "案例分析题": "简答题",
            "病例分析": "简答题",
            "病例分析题": "简答题",
            "病例分析/实践技能": "简答题",
        }
        return aliases.get(normalized, normalized)

    @classmethod
    def _matches_question_type(cls, actual: str, preferences: list[str]) -> bool:
        actual_type = cls._normalize_question_type(actual)
        allowed = {cls._normalize_question_type(value) for value in preferences}
        if "选择题" in allowed:
            return actual_type in {"单项选择题", "多项选择题"}
        return actual_type in allowed

    @classmethod
    def _eligible_unit_candidates(
        cls,
        items: list[QuestionDetail],
        unit: Any,
        evidence_pack: EvidencePack,
    ) -> list[QuestionDetail]:
        return [
            item
            for item in items
            if cls._matches_question_scope(item, unit, evidence_pack)
            and (
                not unit.question_type_preferences
                or cls._matches_question_type(
                    item.question_type, unit.question_type_preferences
                )
            )
        ]

    @classmethod
    def _matches_question_scope(
        cls,
        item: QuestionDetail,
        unit: Any,
        evidence_pack: EvidencePack,
    ) -> bool:
        """Fail closed for narrow named topics before a candidate reaches Expert."""
        primary_kp_ids = set(evidence_pack.resolved_kp_ids[:1])
        candidate_kp_ids = {bridge.kp_id for bridge in item.bridges}
        if primary_kp_ids.intersection(candidate_kp_ids):
            return True

        anchors = cls._named_topic_anchors(
            evidence_pack.query,
            str(getattr(unit, "retrieval_query", "")),
        )
        searchable = cls._compact_topic_text(
            " ".join(
                [
                    item.stem,
                    item.reference_answer,
                    item.analysis or "",
                    *item.tags,
                ]
            )
        )
        if anchors:
            return any(cls._compact_topic_text(anchor) in searchable for anchor in anchors)

        # Broad stages and subjects do not always have a stable named entity.
        # When the retrieval layer supplies bridges, still require the primary
        # resolved KP; otherwise preserve the candidate for Expert/Audit review.
        if primary_kp_ids and candidate_kp_ids:
            return False
        return True

    @staticmethod
    def _named_topic_anchors(*values: str) -> list[str]:
        anchors: list[str] = []
        generic = {"方剂", "方剂学", "试题", "试卷", "练习题"}
        for value in values:
            for token in re.split(r"[\s,，、;；。:：/]+", str(value)):
                normalized = token.strip("《》()（）[]【】")
                if (
                    2 <= len(normalized) <= 12
                    and normalized not in generic
                    and normalized.endswith(("汤", "散", "丸", "饮", "方"))
                ):
                    anchors.append(normalized)
        return list(dict.fromkeys(anchors))

    @staticmethod
    def _compact_topic_text(value: str) -> str:
        return "".join(character.lower() for character in value if character.isalnum())

    @staticmethod
    def _fallback_kp_query(user_request: str) -> str:
        """Keep the learner's concrete topic while removing request boilerplate."""
        query = str(user_request).strip()
        prefixes = (
            "请结合教材证据",
            "请结合教材",
            "请根据教材",
            "请给我",
            "给我",
            "请",
        )
        for prefix in prefixes:
            if query.startswith(prefix):
                query = query[len(prefix):].strip(" ，,：:")
                break
        query = re.sub(r"^(讲解一下|讲解|讲讲|解释一下|解释|介绍一下|介绍)", "", query)
        query = re.sub(
            r"(的知识点|相关知识点|这个知识点|一个知识点|知识点)$",
            "",
            query,
        )
        query = re.split(r"[，,；;。\n]", query, maxsplit=1)[0]
        return query.strip(" ‘“”’《》()（）") or str(user_request).strip()

    async def _build_evidence_pack(self, topic: str, context: dict[str, Any]) -> EvidencePack:
        registry = context.get("tool_registry")
        if registry is not None:
            pack = await registry.invoke(
                "get_kp_with_content",
                "knowledge_base_agent",
                trace_recorder=context.get("trace_recorder"),
                safe_input_summary={"query_length": len(topic)},
                safe_output_summary_factory=lambda pack: {"evidence_count": len(pack.evidence_items)},
                query=topic,
            )
            self._emit_tool_event(
                context,
                {"tool_name": "get_kp_with_content", "evidence_count": len(pack.evidence_items)},
            )
            return pack
        if self.retrieval_tool is None:
            raise RuntimeError("knowledge retrieval tool is not configured")
        handler = getattr(
            self.retrieval_tool,
            "get_kp_with_content",
            self.retrieval_tool.build_evidence_pack,
        )
        return await handler(topic)

    async def _search_question_candidates(
        self,
        topic: str,
        kp_ids: list[str],
        context: dict[str, Any],
        limit: int = 10,
        *,
        difficulty: int | None = None,
        difficulty_min: int | None = None,
        difficulty_max: int | None = None,
    ) -> QuestionSearchResult:
        owner_id = str(
            context.get("learner_id")
            or context.get("user_profile", {}).get("user_id")
            or ""
        ).strip() or None
        registry = context.get("tool_registry")
        if registry is not None:
            invoke_kwargs: dict[str, Any] = {
                "query": topic,
                "kp_ids": kp_ids,
                "limit": limit,
                "owner_id": owner_id,
                "scope": "all",
            }
            if (
                difficulty is not None
                or difficulty_min is not None
                or difficulty_max is not None
            ):
                # Forward difficulty only when actually requested so tools
                # without difficulty support keep working unchanged.
                invoke_kwargs["difficulty"] = difficulty
                invoke_kwargs["difficulty_min"] = difficulty_min
                invoke_kwargs["difficulty_max"] = difficulty_max
            result = await registry.invoke(
                "get_question_with_content",
                "knowledge_base_agent",
                trace_recorder=context.get("trace_recorder"),
                safe_input_summary={"query_length": len(topic), "resolved_kp_count": len(kp_ids)},
                safe_output_summary_factory=lambda result: {
                    "candidate_count": len(result.items),
                    "channels": sorted({channel for item in result.items for channel in item.retrieval.channels}),
                },
                **invoke_kwargs,
            )
            if not isinstance(result, QuestionSearchResult):
                raise ValueError("question search result must be QuestionSearchResult")
            self._emit_tool_event(
                context,
                {
                    "tool_name": "get_question_with_content",
                    "candidate_count": len(result.items),
                    "channels": sorted(
                        {channel for item in result.items for channel in item.retrieval.channels}
                    ),
                },
            )
            return result
        if self.retrieval_tool is None:
            raise RuntimeError("knowledge retrieval tool is not configured")
        handler = getattr(self.retrieval_tool, "get_question_with_content", None)
        if handler is None:
            handler = getattr(self.retrieval_tool, "search_question_candidates", None)
        if handler is None:
            return QuestionSearchResult(
                query=topic,
                resolved_kp_ids=kp_ids,
                embedding_model="unconfigured-test-double",
                vector_index_path="",
                items=[],
            )
        result = await handler(
            topic,
            kp_ids or None,
            limit=limit,
            owner_id=owner_id,
            scope="all",
            difficulty=difficulty,
            difficulty_min=difficulty_min,
            difficulty_max=difficulty_max,
        )
        if not isinstance(result, QuestionSearchResult):
            raise ValueError("question search result must be QuestionSearchResult")
        return result

    @staticmethod
    def _question_semantic_view(result: QuestionSearchResult | None) -> list[dict[str, Any]]:
        if result is None:
            return []
        return [
            {
                "question_id": item.question_id,
                "question_type": item.question_type,
                "stem": item.stem,
                "tags": item.tags,
                "channels": item.retrieval.channels,
            }
            for item in result.items
        ]

    @staticmethod
    def _emit_tool_event(context: dict[str, Any], payload: dict[str, Any]) -> None:
        terminal_trace = context.get("terminal_trace")
        if terminal_trace:
            terminal_trace.tool_event("knowledge_base_agent", payload)

    @staticmethod
    def _question_search_reasons(context: dict[str, Any], topic: str) -> list[str]:
        task_type = str(context.get("task_type", "personalized_review_card"))
        reasons: list[str] = []
        if task_type in {"paper_generation", "grading_and_remediation", "variant_question_generation"}:
            reasons.append(f"task_type:{task_type}")
        if any(keyword in topic for keyword in ("出题", "题目", "练习题", "相似题", "批改", "错题", "试卷")):
            reasons.append("user_request:question_intent")
        for output in context.get("dependency_outputs", {}).values():
            payload = getattr(output, "payload", output)
            if getattr(payload, "question_search_required", False):
                reasons.append("upstream:question_search_required")
        return reasons
