from __future__ import annotations

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
            web_items = [
                *await self.exa_retriever.search_videos(query, limit=3),
                *await self.exa_retriever.search_references(query, limit=3),
                *await self.exa_retriever.search_questions(query, limit=3),
            ]
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

    async def search_question_candidates(
        self,
        query: str,
        kp_ids: list[str] | None = None,
        limit: int = 10,
        owner_id: str | None = None,
        scope: str = "all",
    ):
        if self.delivery_backend is not None:
            return await self.delivery_backend.search_questions(
                query,
                kp_ids,
                limit,
                owner_id=owner_id,
                scope=scope,
            )
        if self.question_retriever is None:
            raise RuntimeError("question retrieval is not configured")
        resolved_kp_ids = kp_ids or [match.kp_id for match in self.repository.resolve_topic(query)]
        if not resolved_kp_ids:
            raise LookupError(f"knowledge point could not be resolved for query: {query}")
        return await self.question_retriever.search(query, resolved_kp_ids, limit)

    async def get_question_with_content(
        self,
        query: str,
        kp_ids: list[str] | None = None,
        limit: int = 10,
        owner_id: str | None = None,
        scope: str = "all",
    ):
        """Retrieve question candidates with content for controlled downstream use."""
        return await self.search_question_candidates(
            query,
            kp_ids=kp_ids,
            limit=limit,
            owner_id=owner_id,
            scope=scope,
        )
