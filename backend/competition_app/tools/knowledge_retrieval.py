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
    ) -> None:
        self.repository = repository
        self.embedding_model = embedding_model
        self.question_retriever = question_retriever
        self.textbook_retriever = textbook_retriever
        self.exa_retriever = exa_retriever
        self.delivery_backend = delivery_backend

    async def build_evidence_pack(self, query: str) -> EvidencePack:
        web_items = []
        if self.exa_retriever is not None:
            videos, references, questions = await asyncio.gather(
                self.exa_retriever.search_videos(query, limit=3),
                self.exa_retriever.search_references(query, limit=3),
                self.exa_retriever.search_questions(query, limit=3),
            )
            web_items = [*videos, *references, *questions]
        if self.delivery_backend is not None:
            pack = await self.delivery_backend.build_local_evidence_pack(query)
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
                hits = await self.textbook_retriever.search(query, limit=5)
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

    async def get_kp_with_content(self, query: str, limit: int = 8) -> EvidencePack:
        """Retrieve knowledge points together with their textbook content."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        pack = await self.build_evidence_pack(query)
        by_type: dict[str, list[EvidenceItem]] = {}
        for item in pack.evidence_items:
            by_type.setdefault(item.resource_type, []).append(item)
        selected: list[EvidenceItem] = []
        for resource_type in ("video", "reference", "question"):
            if by_type.get(resource_type) and len(selected) < limit:
                selected.append(by_type[resource_type][0])
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
        """Search current external facts such as official dates or weather."""
        if self.exa_retriever is None:
            return []
        return await self.exa_retriever.search_web(query, limit=limit)

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
