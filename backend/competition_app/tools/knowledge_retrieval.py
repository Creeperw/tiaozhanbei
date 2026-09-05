from __future__ import annotations

import asyncio
from uuid import uuid4

from competition_app.contracts.knowledge import EvidenceItem, EvidencePack
from competition_app.embeddings.base import EmbeddingModel
from competition_app.tools.knowledge_assets import KnowledgeAssetRepository
from competition_app.tools.question_retrieval import QuestionHybridRetriever
from competition_app.tools.textbook_vector_retrieval import TextbookVectorRetriever
from competition_app.tools.exa_retrieval import ExaVideoRetriever
from competition_app.tools.knowledge_delivery import KnowledgeDeliveryBackend


class KnowledgeRetrievalTool:
    def __init__(
        self,
        repository: KnowledgeAssetRepository,
        embedding_model: EmbeddingModel,
        question_retriever: QuestionHybridRetriever | None = None,
        textbook_retriever: TextbookVectorRetriever | None = None,
        exa_retriever: ExaVideoRetriever | None = None,
        delivery_backend: KnowledgeDeliveryBackend | None = None,
        *,
        exa_limit: int = 3,
        textbook_limit: int = 5,
    ) -> None:
        self.repository = repository
        self.embedding_model = embedding_model
        self.question_retriever = question_retriever
        self.textbook_retriever = textbook_retriever
        self.exa_retriever = exa_retriever
        self.delivery_backend = delivery_backend
        self.exa_limit = max(1, exa_limit)
        self.textbook_limit = max(1, textbook_limit)

    async def build_evidence_pack(self, query: str) -> EvidencePack:
        web_items = []
        if self.exa_retriever is not None:
            # 网络检索不限于题目：通用网络（概念定义/教材未覆盖知识点）、
            # 视频、参考、题目四路并行。search_web 无中医药后缀，适合概念
            # 定义等通用知识；其余三路带领域后缀。
            videos, references, questions, web = await asyncio.gather(
                self.exa_retriever.search_videos(query, limit=self.exa_limit),
                self.exa_retriever.search_references(query, limit=self.exa_limit),
                # question 路抓整页全文（text 模式），条数超出证据包实际用量
                # 会成倍放大 LLM 上下文导致超时；保底进包条数是
                # (exa_limit+1)//2，检索 5 条已够候选。
                self.exa_retriever.search_questions(
                    query, limit=min(self.exa_limit, 5)
                ),
                self.exa_retriever.search_web_knowledge(query, limit=self.exa_limit),
            )
            web_items = [*videos, *references, *questions, *web]
        if self.delivery_backend is not None:
            try:
                pack = await self.delivery_backend.build_local_evidence_pack(query)
            except LookupError:
                # 本地知识库未覆盖该主题（如法规类考点“儿童化妆品”）。
                # 若外部网络检索有结果，降级为纯外部证据包继续流程，
                # 避免整个复习卡因“知识检索未能完成”而失败；外部也无
                # 结果时保持抛出，交由上层回退逻辑决定。
                if not web_items:
                    raise
                return EvidencePack(
                    evidence_pack_id=f"EP_{uuid4().hex}",
                    query=query,
                    resolved_kp_ids=[],
                    evidence_items=[
                        EvidenceItem(
                            evidence_id=f"E_EXA_{item.resource_type.upper()}_{index}",
                            source_id=item.source_id,
                            content_summary=f"{item.title}\n{item.summary}",
                            authority_level=f"web_{item.resource_type}",
                            confidence=item.score,
                            bridge_layer="external",
                            source_url=item.url,
                            resource_type=item.resource_type,
                            source_label=str(item.title).strip() or None,
                        )
                        for index, item in enumerate(web_items, start=1)
                    ],
                    risk_notes=[
                        "本地知识库未覆盖该主题，本次证据全部来自外部网络检索，"
                        "未解析正式知识点，不写回知识状态。",
                        "仅用于中医药教学训练，不构成诊疗建议。",
                    ],
                )
            external = [
                EvidenceItem(
                    evidence_id=f"E_EXA_{item.resource_type.upper()}_{index}",
                    source_id=item.source_id,
                    content_summary=f"{item.title}\n{item.summary}",
                    authority_level=f"web_{item.resource_type}",
                    confidence=item.score,
                    bridge_layer="external",
                    source_url=item.url,
                    resource_type=item.resource_type,
                    source_label=str(item.title).strip() or None,
                )
                for index, item in enumerate(web_items, start=1)
            ]
            return pack.model_copy(
                update={"evidence_items": [*pack.evidence_items, *external]}
            )
        if self.textbook_retriever is not None:
            try:
                hits = await self.textbook_retriever.search(
                    query, limit=self.textbook_limit
                )
            except (LookupError, RuntimeError, ValueError):
                hits = []
            resolved_kp_ids = self.repository.kp_ids_for_chunks(
                [hit.source_id for hit in hits]
            )
            evidence = [
                EvidenceItem(
                    evidence_id=f"E_VECTOR_{index}",
                    source_id=hit.source_id,
                    content_summary=self.repository._teaching_excerpt(hit.content),
                    authority_level="textbook",
                    confidence=max(0.0, min(1.0, hit.score)),
                    bridge_layer="vector",
                    source_label=self.repository.chunk_label_for_uid(hit.source_id),
                )
                for index, hit in enumerate(hits, start=1)
            ]
            evidence.extend(
                EvidenceItem(
                    evidence_id=f"E_EXA_{item.resource_type.upper()}_{index}",
                    source_id=item.source_id,
                    content_summary=f"{item.title}\n{item.summary}",
                    authority_level=f"web_{item.resource_type}",
                    confidence=item.score,
                    bridge_layer="external",
                    source_url=item.url,
                    resource_type=item.resource_type,
                    source_label=str(item.title).strip() or None,
                )
                for index, item in enumerate(web_items, start=1)
            )
            if not evidence:
                raise LookupError(f"no textbook or web video evidence found for query: {query}")
            risk_notes = [
                "仅用于中医药教学训练，不构成诊疗建议。",
                "教材内容由向量相似度召回；相似度只表示相关性，结论仍需受证据原文约束。",
            ]
            if not resolved_kp_ids:
                risk_notes.append("向量切片未映射到正式知识点，后续不得生成知识状态写回。")
            return EvidencePack(
                evidence_pack_id=f"EP_{uuid4().hex}",
                query=query,
                resolved_kp_ids=resolved_kp_ids,
                evidence_items=evidence,
                risk_notes=risk_notes,
            )
        matches = self.repository.resolve_topic(query)
        if not matches:
            # Exercise the configured embedding provider while keeping unresolved
            # topics out of the formal knowledge graph until a candidate-KP flow exists.
            await self.embedding_model.embed([query])
            raise LookupError(f"knowledge point could not be resolved for query: {query}")
        match = matches[0]
        evidence = self.repository.get_chunk_evidence(match.kp_id)
        if not evidence:
            raise LookupError(f"no textbook evidence found for knowledge point: {match.kp_id}")
        weak_only = all(item.bridge_layer != "strict" for item in evidence)
        risk_notes = ["仅用于中医药教学训练，不构成诊疗建议。"]
        if weak_only:
            risk_notes.append("当前仅有 similarity 弱证据，不得单独支撑高风险专业声明。")
        return EvidencePack(
            evidence_pack_id=f"EP_{uuid4().hex}",
            query=match.name,
            resolved_kp_ids=[match.kp_id],
            evidence_items=evidence,
            risk_notes=risk_notes,
        )

    async def get_kp_with_content(
        self,
        query: str,
        limit: int | None = None,
        concepts: list[str] | None = None,
    ) -> EvidencePack:
        """Retrieve knowledge points together with their textbook content.

        外部网络证据（video/reference/question/web 合并计算）总量不超过
        exa_limit 条（即配置的 KNOWLEDGE_RETRIEVAL_EXA_LIMIT 是外部总上限，
        不是每类上限），教材按 textbook_limit 条保留；默认总预算 =
        exa_limit + textbook_limit。显式传入 limit 时按该上限截断。

        传入 concepts（检索规划模型输出的概念清单）时升级为多路分解检索
        （RQ-RAG 思想）：主查询照常完整检索，每个概念作为一路独立轻量
        检索（本地教材切片 + 网络概念定义）并行执行，全部证据合并去重后
        再统一截断，首轮就覆盖各选项辨析概念，而不是事后才发现缺概念。
        """
        if limit is None:
            limit = self.exa_limit + self.textbook_limit
        if limit <= 0:
            raise ValueError("limit must be positive")
        if concepts:
            return await self.build_multi_concept_pack(query, concepts, limit)
        pack = await self.build_evidence_pack(query)
        return self._select_evidence(pack, limit)

    async def build_multi_concept_pack(
        self,
        main_query: str,
        concepts: list[str],
        limit: int,
    ) -> EvidencePack:
        """多路分解检索：主查询完整检索 + 每个概念独立轻量检索，合并后统一截断。

        概念路只做本地教材切片与网络概念定义（search_web_knowledge），
        不重复主查询的 video/reference/question 四路，控制外部调用成本；
        每路证据按 source_id 去重后并入主证据包，最终仍受 exa_limit
        （外部总上限）与 textbook_limit（教材上限）约束。
        """
        main_pack = await self.build_evidence_pack(main_query)
        clean_concepts = [
            concept
            for concept in (str(c).strip() for c in (concepts or []))
            if concept and self._is_retrievable_concept(concept)
        ][:6]
        if not clean_concepts:
            return self._select_evidence(main_pack, limit)
        concept_results = await asyncio.gather(
            *[self._retrieve_concept(concept) for concept in clean_concepts],
            return_exceptions=True,
        )
        all_items = list(main_pack.evidence_items)
        seen_source_ids = {item.source_id for item in all_items}
        extra_kp_ids: list[str] = []
        for result in concept_results:
            if isinstance(result, BaseException) or result is None:
                continue
            for item in result["items"]:
                if item.source_id in seen_source_ids:
                    continue
                seen_source_ids.add(item.source_id)
                all_items.append(item)
            extra_kp_ids.extend(result["kp_ids"])
        merged = main_pack.model_copy(
            update={
                "evidence_items": all_items,
                "resolved_kp_ids": list(
                    dict.fromkeys([*main_pack.resolved_kp_ids, *extra_kp_ids])
                ),
            }
        )
        return self._select_evidence(merged, limit)

    @staticmethod
    def _is_retrievable_concept(concept: str) -> bool:
        """概念路只接受可独立检索的教材术语，过滤流程性/问句式描述。

        与检索规划 prompt 的 kp_concepts 规范一致：概念应是医学实体或术语
        （如"观察性研究""异质性"），"说法是否正确""下列说法正确的是"这类
        流程性描述无法独立检索，直接跳过，避免浪费一路外部调用。
        """
        if len(concept) < 2 or len(concept) > 40:
            return False
        process_markers = (
            "是否", "正确", "错误", "说法", "下列", "关于", "哪项",
            "说法正确", "下列哪", "为什么", "如何", "是否属于", "请",
        )
        return not any(marker in concept for marker in process_markers)

    async def _retrieve_concept(self, concept: str) -> dict[str, Any]:
        """单概念轻量检索：本地教材切片 + 网络概念定义，返回 (items, kp_ids)。"""
        items: list[EvidenceItem] = []
        kp_ids: list[str] = []
        local_pack: EvidencePack | None = None
        try:
            if self.delivery_backend is not None:
                local_pack = await self.delivery_backend.build_local_evidence_pack(
                    concept, limit=1
                )
            elif self.textbook_retriever is not None:
                hits = await self.textbook_retriever.search(concept, limit=1)
                if hits:
                    hit = hits[0]
                    local_pack = EvidencePack(
                        evidence_pack_id=f"EP_CONCEPT_{uuid4().hex}",
                        query=concept,
                        resolved_kp_ids=self.repository.kp_ids_for_chunks(
                            [hit.source_id]
                        ),
                        evidence_items=[
                            EvidenceItem(
                                evidence_id=f"E_CONCEPT_TXT_{uuid4().hex[:8]}",
                                source_id=hit.source_id,
                                content_summary=self.repository._teaching_excerpt(
                                    hit.content
                                ),
                                authority_level="textbook",
                                confidence=max(0.0, min(1.0, hit.score)),
                                bridge_layer="vector",
                                source_label=self.repository.chunk_label_for_uid(
                                    hit.source_id
                                ),
                            )
                        ],
                    )
        except (LookupError, RuntimeError, ValueError):
            local_pack = None
        if local_pack is not None:
            for item in [
                ev
                for ev in local_pack.evidence_items
                if ev.resource_type == "textbook"
            ]:
                # 概念路教材证据统一重写 evidence_id（uuid 保证多概念间全局
                # 唯一，避免与主查询 E_VECTOR_n/E_CHUNK_xxx 或彼此冲突）；
                # source_id 保留用于去重。
                items.append(
                    item.model_copy(
                        update={"evidence_id": f"E_CONCEPT_TXT_{uuid4().hex[:10]}"}
                    )
                )
            kp_ids.extend(local_pack.resolved_kp_ids)
        if self.exa_retriever is not None:
            try:
                # 概念路也并行检索网络题库原题（question）与概念定义（web）：
                # 网络题库原题常直接携带题目与标准答案，是考试口径（教材口径
                # 冲突时）的关键证据；_select_evidence 会优先保留 question 类型。
                question_hits, web_hits = await asyncio.gather(
                    self.exa_retriever.search_questions(concept, limit=2),
                    self.exa_retriever.search_web_knowledge(concept, limit=2),
                )
            except Exception:
                question_hits, web_hits = [], []
            for hit in question_hits:
                items.append(
                    EvidenceItem(
                        evidence_id=f"E_CONCEPT_Q_{uuid4().hex[:10]}",
                        source_id=hit.url,
                        content_summary=f"{hit.title}\n{hit.summary}",
                        authority_level="web_question",
                        confidence=hit.score,
                        bridge_layer="external",
                        source_url=hit.url,
                        resource_type="question",
                        source_label=str(hit.title).strip() or None,
                    )
                )
            for hit in web_hits:
                items.append(
                    EvidenceItem(
                        evidence_id=f"E_CONCEPT_WEB_{uuid4().hex[:10]}",
                        source_id=hit.url,
                        content_summary=f"{hit.title}\n{hit.summary}",
                        authority_level="web_reference",
                        confidence=hit.score,
                        bridge_layer="external",
                        source_url=hit.url,
                        resource_type="web",
                        source_label=str(hit.title).strip() or None,
                    )
                )
        return {"items": items, "kp_ids": kp_ids}

    def _select_evidence(self, pack: EvidencePack, limit: int) -> EvidencePack:
        """按 EXA 语义统一截断：外部合并按置信度取 exa_limit 条，教材 textbook_limit 条。

        question 类型（网络题库原题，常直接携带题目与标准答案）优先入选——
        它是教材口径与考试口径冲突时（如"Meta分析是否属于观察性研究"）最直接
        的考试口径证据，不能因置信度排序被 video/reference 挤出。
        """
        by_type: dict[str, list[EvidenceItem]] = {}
        for item in pack.evidence_items:
            by_type.setdefault(item.resource_type, []).append(item)
        # 外部资源合并：question 类型保底占预算一半（向上取整），
        # 其余按置信度跨类型补足；EXA_LIMIT 仍是外部总条数上限。
        external = [
            item
            for resource_type in ("video", "reference", "question", "web")
            for item in by_type.get(resource_type, [])
        ]
        question_items = [
            item for item in external if item.resource_type == "question"
        ]
        question_budget = min(
            len(question_items), max(1, (self.exa_limit + 1) // 2)
        )
        other = [item for item in external if item.resource_type != "question"]
        other.sort(key=lambda item: item.confidence, reverse=True)
        selected = [
            *question_items[:question_budget],
            *other[: max(0, self.exa_limit - question_budget)],
        ]
        textbook_limit = max(0, limit - len(selected))
        selected = [*by_type.get("textbook", [])[:textbook_limit], *selected]
        if len(selected) < limit:
            selected_ids = {item.evidence_id for item in selected}
            selected.extend(
                item
                for item in pack.evidence_items
                if item.evidence_id not in selected_ids
            )
        return pack.model_copy(update={"evidence_items": selected[:limit]})

    async def search_video_resources(self, query: str, limit: int = 5):
        """Search external video resources; only the knowledge agent may call it."""
        if self.exa_retriever is None:
            return []
        return await self.exa_retriever.search_videos(query, limit=limit)

    async def search_reference_resources(self, query: str, limit: int = 5):
        """Search external reference content; only the knowledge agent may call it."""
        if self.exa_retriever is None:
            return []
        return await self.exa_retriever.search_references(query, limit=limit)

    async def search_question_resources(self, query: str, limit: int = 5):
        """Search external question resources; these remain candidate references."""
        if self.exa_retriever is None:
            return []
        return await self.exa_retriever.search_questions(query, limit=limit)

    async def search_web_resources(self, query: str, limit: int = 5):
        """Search general web content for knowledge or current facts.

        Keeps a light medicine context suffix so concept lookups stay on-topic
        (textbook gaps are covered here); time-sensitive facts like official
        dates/weather also flow through this tool.
        """
        if self.exa_retriever is None:
            return []
        return await self.exa_retriever.search_web_knowledge(query, limit=limit)

    async def livecrawl_web_resources(self, urls: list[str], max_chars: int = 3000):
        """Livecrawl specific URLs to re-fetch fresh content (bypass cache).

        Use when a hit from search_question_resources / search_web_resources
        looks incomplete (answer/parse hidden behind a click or JS render);
        only the knowledge agent may call it. Returns full-page text (truncated
        to max_chars) as web-type evidence hits.
        """
        if self.exa_retriever is None:
            return []
        return await self.exa_retriever.livecrawl_urls(urls, max_chars=max_chars)

    async def build_external_evidence_pack(
        self, query: str, *, location: str = ""
    ) -> EvidencePack:
        """Build current-fact evidence without resolving a textbook knowledge point."""
        if self.exa_retriever is None:
            return EvidencePack(
                evidence_pack_id=f"EP_{uuid4().hex}",
                query=query,
                evidence_items=[
                    EvidenceItem(
                        evidence_id="E_WEB_UNAVAILABLE",
                        source_id="system:web-search-unconfigured",
                        content_summary=(
                            "当前运行环境未配置网络搜索服务，无法核验会随日期或地点变化的实时信息。"
                            "请以考试主管部门、气象服务或其他官方发布页面为准。"
                        ),
                        authority_level="system_notice",
                        confidence=1.0,
                        bridge_layer="system",
                        resource_type="web",
                    )
                ],
                risk_notes=["网络搜索服务未配置，当前不能把实时信息写成确定结论。"],
            )
        search_query = " ".join(
            part
            for part in (
                query.strip(),
                location.strip() if self._requires_location(query) else "",
            )
            if part
        )
        hits = await self.exa_retriever.search_web(search_query, limit=5)
        if not hits:
            return self._external_evidence_unavailable_pack(query)
        return EvidencePack(
            evidence_pack_id=f"EP_{uuid4().hex}",
            query=query,
            evidence_items=[
                EvidenceItem(
                    evidence_id=f"E_WEB_{index}",
                    source_id=item.source_id,
                    content_summary=f"{item.title}\n{item.summary}",
                    authority_level="web_current_fact",
                    confidence=item.score,
                    bridge_layer="external",
                    source_url=item.url,
                    resource_type="web",
                )
                for index, item in enumerate(hits, start=1)
            ],
            risk_notes=["当前信息来自网络检索，请以官方发布页面为最终依据。"],
        )

    @staticmethod
    def _requires_location(query: str) -> bool:
        text = str(query or "")
        return any(
            marker in text
            for marker in ("天气", "气温", "降雨", "下雨", "空气质量", "台风")
        )

    @staticmethod
    def _external_evidence_unavailable_pack(query: str) -> EvidencePack:
        return EvidencePack(
            evidence_pack_id=f"EP_{uuid4().hex}",
            query=query,
            evidence_items=[
                EvidenceItem(
                    evidence_id="E_WEB_UNAVAILABLE",
                    source_id="system:web-search-unavailable",
                    content_summary=(
                        "当前未检索到可用于核验的网络信息，无法确认会随日期或地点变化的实时事实。"
                        "请以主管部门、气象服务或其他官方发布页面为准。"
                    ),
                    authority_level="system_notice",
                    confidence=1.0,
                    bridge_layer="system",
                    resource_type="web",
                )
            ],
            risk_notes=["未获得可核验的网络证据，当前不能把实时信息写成确定结论。"],
        )

    async def search_question_candidates(
        self,
        query: str,
        kp_ids: list[str] | None = None,
        limit: int = 10,
        owner_id: str | None = None,
        scope: str = "all",
        *,
        difficulty: int | None = None,
        difficulty_min: int | None = None,
        difficulty_max: int | None = None,
    ):
        if self.delivery_backend is not None:
            return await self.delivery_backend.search_questions(
                query,
                kp_ids,
                limit,
                owner_id=owner_id,
                scope=scope,
                difficulty=difficulty,
                difficulty_min=difficulty_min,
                difficulty_max=difficulty_max,
            )
        if self.question_retriever is None:
            raise RuntimeError("question retrieval is not configured")
        resolved_kp_ids = kp_ids or [match.kp_id for match in self.repository.resolve_topic(query)]
        if not resolved_kp_ids:
            raise LookupError(f"knowledge point could not be resolved for query: {query}")
        return await self.question_retriever.search(
            query,
            resolved_kp_ids,
            limit,
            difficulty=difficulty,
            difficulty_min=difficulty_min,
            difficulty_max=difficulty_max,
        )

    async def get_question_with_content(
        self,
        query: str,
        kp_ids: list[str] | None = None,
        limit: int = 10,
        owner_id: str | None = None,
        scope: str = "all",
        *,
        difficulty: int | None = None,
        difficulty_min: int | None = None,
        difficulty_max: int | None = None,
    ):
        """Retrieve question candidates with content for controlled downstream use."""
        return await self.search_question_candidates(
            query,
            kp_ids=kp_ids,
            limit=limit,
            owner_id=owner_id,
            scope=scope,
            difficulty=difficulty,
            difficulty_min=difficulty_min,
            difficulty_max=difficulty_max,
        )
