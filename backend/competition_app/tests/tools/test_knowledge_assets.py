from pathlib import Path

import pytest

from competition_app.embeddings.stub import StubEmbeddingModel
from competition_app.tools.knowledge_assets import KnowledgeAssetPaths, KnowledgeAssetRepository
from competition_app.tools.knowledge_retrieval import KnowledgeRetrievalTool
from competition_app.tools.exa_retrieval import ExaVideoRetriever
from competition_app.contracts.knowledge import EvidenceItem, EvidencePack


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "knowledge_delivery"


def build_repository() -> KnowledgeAssetRepository:
    return KnowledgeAssetRepository(
        KnowledgeAssetPaths(
            knowledge_points=FIXTURE_ROOT / "knowledge_points.json",
            kp_chunk_links=FIXTURE_ROOT / "kp_chunk_links.jsonl",
            source_chunks=FIXTURE_ROOT / "source_chunks.jsonl",
        )
    )


def test_resolve_topic_matches_formal_knowledge_point() -> None:
    matches = build_repository().resolve_topic("复习四君子汤的组成")

    assert matches[0].kp_id == "KP_FJ_001"
    assert matches[0].name == "四君子汤"


def test_chunk_evidence_preserves_bridge_and_source_provenance() -> None:
    evidence = build_repository().get_chunk_evidence("KP_FJ_001")

    assert evidence[0].source_id == "方剂学:00001"
    assert evidence[0].authority_level == "textbook"
    assert evidence[0].bridge_layer == "strict"
    assert "人参" in evidence[0].content_summary


def test_teaching_excerpt_keeps_lead_content_and_bounds_mixed_source_chunks() -> None:
    source = "核心教学内容。" * 40 + "西班牙流感属于历史扩展。"

    excerpt = KnowledgeAssetRepository._teaching_excerpt(source, max_length=80)

    assert excerpt.startswith("核心教学内容。")
    assert len(excerpt) <= 81
    assert "西班牙流感" not in excerpt


def test_teaching_excerpt_never_exposes_a_truncated_sentence() -> None:
    source = "四君子汤由人参、白术、茯苓、甘草组成。" + "组方研究内容" * 80

    excerpt = KnowledgeAssetRepository._teaching_excerpt(source, max_length=80)

    assert excerpt == "四君子汤由人参、白术、茯苓、甘草组成。"
    assert excerpt.endswith("。")


@pytest.mark.asyncio
async def test_retrieval_tool_marks_similarity_only_evidence_as_risk() -> None:
    tool = KnowledgeRetrievalTool(build_repository(), StubEmbeddingModel())

    pack = await tool.build_evidence_pack("理中丸")

    assert pack.resolved_kp_ids == ["KP_FJ_018"]
    assert pack.evidence_items[0].bridge_layer == "similarity"
    assert any("弱证据" in note for note in pack.risk_notes)


@pytest.mark.asyncio
async def test_retrieval_tool_rejects_unresolved_topic() -> None:
    tool = KnowledgeRetrievalTool(build_repository(), StubEmbeddingModel())

    with pytest.raises(LookupError, match="knowledge point"):
        await tool.build_evidence_pack("不存在的知识点")


class RecordingQuestionRetriever:
    def __init__(self) -> None:
        self.arguments: tuple[str, list[str], int] | None = None
        self.difficulty_arguments: dict[str, object] | None = None

    async def search(
        self,
        query: str,
        kp_ids: list[str],
        limit: int,
        *,
        difficulty: int | None = None,
        difficulty_min: int | None = None,
        difficulty_max: int | None = None,
    ):
        self.arguments = (query, kp_ids, limit)
        self.difficulty_arguments = {
            "difficulty": difficulty,
            "difficulty_min": difficulty_min,
            "difficulty_max": difficulty_max,
        }
        return {"query": query, "kp_ids": kp_ids, "limit": limit}


@pytest.mark.asyncio
async def test_knowledge_tool_uses_resolved_kp_ids_for_question_search() -> None:
    retriever = RecordingQuestionRetriever()
    tool = KnowledgeRetrievalTool(build_repository(), StubEmbeddingModel(), question_retriever=retriever)

    result = await tool.search_question_candidates("四君子汤", limit=2)

    assert result == {"query": "四君子汤", "kp_ids": ["KP_FJ_001"], "limit": 2}
    assert retriever.arguments == ("四君子汤", ["KP_FJ_001"], 2)


@pytest.mark.asyncio
async def test_get_kp_with_content_returns_retrieved_textbook_content() -> None:
    tool = KnowledgeRetrievalTool(build_repository(), StubEmbeddingModel())

    pack = await tool.get_kp_with_content("四君子汤", limit=1)

    assert pack.query == "四君子汤"
    assert pack.resolved_kp_ids == ["KP_FJ_001"]
    assert len(pack.evidence_items) == 1
    assert "人参" in pack.evidence_items[0].content_summary


@pytest.mark.asyncio
async def test_get_question_with_content_forwards_model_query_and_kp_scope() -> None:
    retriever = RecordingQuestionRetriever()
    tool = KnowledgeRetrievalTool(
        build_repository(), StubEmbeddingModel(), question_retriever=retriever
    )

    result = await tool.get_question_with_content(
        "四君子汤组成练习题", kp_ids=["KP_FJ_001"], limit=3
    )

    assert result == {
        "query": "四君子汤组成练习题",
        "kp_ids": ["KP_FJ_001"],
        "limit": 3,
    }
    assert retriever.arguments == ("四君子汤组成练习题", ["KP_FJ_001"], 3)


@pytest.mark.asyncio
async def test_get_kp_with_content_reserves_space_for_external_resources() -> None:
    class MixedEvidenceTool(KnowledgeRetrievalTool):
        async def build_evidence_pack(self, query: str) -> EvidencePack:
            return EvidencePack(
                evidence_pack_id="EP_MIXED",
                query=query,
                resolved_kp_ids=["KP_FJ_001"],
                evidence_items=[
                    *[
                        EvidenceItem(
                            evidence_id=f"E_TEXT_{index}", source_id=f"教材:{index}",
                            content_summary=f"教材{index}", authority_level="textbook",
                            confidence=0.9, resource_type="textbook",
                        )
                        for index in range(1, 6)
                    ],
                    EvidenceItem(
                        evidence_id="E_VIDEO", source_id="EXA_VIDEO_1",
                        content_summary="视频", authority_level="web_video",
                        confidence=0.7, resource_type="video",
                        source_url="https://example.test/video",
                    ),
                    EvidenceItem(
                        evidence_id="E_REFERENCE", source_id="EXA_REFERENCE_1",
                        content_summary="参考", authority_level="web_reference",
                        confidence=0.7, resource_type="reference",
                        source_url="https://example.test/reference",
                    ),
                    EvidenceItem(
                        evidence_id="E_QUESTION", source_id="EXA_QUESTION_1",
                        content_summary="题目线索", authority_level="web_question",
                        confidence=0.7, resource_type="question",
                        source_url="https://example.test/question",
                    ),
                ],
            )

    tool = MixedEvidenceTool(build_repository(), StubEmbeddingModel())

    pack = await tool.get_kp_with_content("四君子汤", limit=5)

    assert {item.resource_type for item in pack.evidence_items} == {
        "textbook", "video", "reference", "question"
    }
    assert len(pack.evidence_items) == 5


@pytest.mark.asyncio
async def test_get_kp_with_content_keeps_exa_limit_as_external_total() -> None:
    """外部资源总量不超过 exa_limit（EXA_LIMIT 是外部总上限，不是每类上限）。"""

    class MultiExaTool(KnowledgeRetrievalTool):
        async def build_evidence_pack(self, query: str) -> EvidencePack:
            return EvidencePack(
                evidence_pack_id="EP_MULTI",
                query=query,
                resolved_kp_ids=["KP_FJ_001"],
                evidence_items=[
                    EvidenceItem(
                        evidence_id=f"E_VIDEO_{i}", source_id=f"EXA_VIDEO_{i}",
                        content_summary=f"视频{i}", authority_level="web_video",
                        confidence=0.7, resource_type="video",
                        source_url=f"https://example.test/video/{i}",
                    )
                    for i in range(1, 6)
                ]
                + [
                    EvidenceItem(
                        evidence_id=f"E_REF_{i}", source_id=f"EXA_REF_{i}",
                        content_summary=f"参考{i}", authority_level="web_reference",
                        confidence=0.7, resource_type="reference",
                        source_url=f"https://example.test/ref/{i}",
                    )
                    for i in range(1, 6)
                ]
                + [
                    EvidenceItem(
                        evidence_id=f"E_Q_{i}", source_id=f"EXA_Q_{i}",
                        content_summary=f"题目{i}", authority_level="web_question",
                        confidence=0.7, resource_type="question",
                        source_url=f"https://example.test/q/{i}",
                    )
                    for i in range(1, 6)
                ]
                + [
                    EvidenceItem(
                        evidence_id=f"E_WEB_{i}", source_id=f"EXA_WEB_{i}",
                        content_summary=f"网页{i}", authority_level="web_reference",
                        confidence=0.8, resource_type="web",
                        source_url=f"https://example.test/web/{i}",
                    )
                    for i in range(1, 6)
                ]
                + [
                    EvidenceItem(
                        evidence_id=f"E_TEXT_{i}", source_id=f"教材:{i}",
                        content_summary=f"教材{i}", authority_level="textbook",
                        confidence=0.9, resource_type="textbook",
                    )
                    for i in range(1, 4)
                ],
            )

    tool = MultiExaTool(build_repository(), StubEmbeddingModel(), exa_limit=5, textbook_limit=3)

    # 默认预算 = exa_limit + textbook_limit = 8；外部 4 类共 20 条 → 取前 5，
    # 教材 3 条。question 类型保底占一半（3 条），其余 2 条按置信度（web 0.8 最高）。
    pack = await tool.get_kp_with_content("四君子汤")
    by_type: dict[str, int] = {}
    for item in pack.evidence_items:
        by_type[item.resource_type] = by_type.get(item.resource_type, 0) + 1
    external_total = sum(v for k, v in by_type.items() if k != "textbook")
    assert external_total <= 5, f"外部证据总量 {external_total} 超过 exa_limit=5"
    assert by_type.get("question", 0) == 3, "question 类型保底入选 3 条（网络原题带标准答案）"
    assert by_type.get("web", 0) == 2, "剩余按置信度取 web（0.8 最高）2 条"
    assert by_type.get("textbook", 0) == 3
    assert len(pack.evidence_items) == 8

@pytest.mark.asyncio
async def test_external_evidence_pack_does_not_require_knowledge_point_mapping() -> None:
    class FakeExa:
        async def search(self, query, **kwargs):
            return {
                "results": [
                    {
                        "title": "上海天气",
                        "url": "https://example.test/weather",
                        "highlights": ["今日天气晴"],
                        "score": 0.8,
                    }
                ]
            }

    tool = KnowledgeRetrievalTool(
        build_repository(),
        StubEmbeddingModel(),
        exa_retriever=ExaVideoRetriever("exa-test-key", client=FakeExa()),
    )
    pack = await tool.build_external_evidence_pack("今天天气如何", location="上海")

    assert pack.resolved_kp_ids == []
    assert pack.evidence_items[0].resource_type == "web"


@pytest.mark.asyncio
async def test_external_evidence_pack_degrades_when_web_search_is_empty() -> None:
    class EmptyExa:
        async def search(self, query, **kwargs):
            return {"results": []}

    tool = KnowledgeRetrievalTool(
        build_repository(),
        StubEmbeddingModel(),
        exa_retriever=ExaVideoRetriever("exa-test-key", client=EmptyExa()),
    )

    pack = await tool.build_external_evidence_pack("距离下次执业医师资格考试还有多久")

    assert pack.evidence_items[0].authority_level == "system_notice"
    assert "不能把实时信息写成确定结论" in pack.risk_notes[0]


@pytest.mark.asyncio
async def test_non_local_current_fact_does_not_send_profile_location_to_search() -> None:
    class RecordingExa:
        def __init__(self) -> None:
            self.query = ""

        async def search(self, query, **kwargs):
            self.query = query
            return {"results": []}

    client = RecordingExa()
    tool = KnowledgeRetrievalTool(
        build_repository(),
        StubEmbeddingModel(),
        exa_retriever=ExaVideoRetriever("exa-test-key", client=client),
    )

    await tool.build_external_evidence_pack("距离下次执业医师资格考试还有多久", location="上海")

    assert "上海" not in client.query


@pytest.mark.asyncio
async def test_build_evidence_pack_degrades_to_external_when_local_unresolved() -> None:
    """本地知识库未覆盖（如法规类考点）时降级为外部网络证据，避免整个流程失败。"""

    class UnresolvedDeliveryBackend:
        async def build_local_evidence_pack(self, query: str) -> EvidencePack:
            raise LookupError(f"knowledge point could not be resolved for query: {query}")

    class FakeExa:
        async def search(self, query, **kwargs):
            return {
                "results": [
                    {
                        "title": "儿童化妆品管理法规",
                        "url": "https://example.test/cosmetics",
                        "highlights": ["儿童化妆品标志", "应当标注警示用语"],
                        "score": 0.85,
                    }
                ]
            }

    tool = KnowledgeRetrievalTool(
        build_repository(),
        StubEmbeddingModel(),
        exa_retriever=ExaVideoRetriever("exa-test-key", client=FakeExa()),
        delivery_backend=UnresolvedDeliveryBackend(),
    )

    pack = await tool.build_evidence_pack("关于儿童化妆品的说法")

    assert pack.resolved_kp_ids == []
    assert pack.evidence_items
    assert all(item.bridge_layer == "external" for item in pack.evidence_items)
    assert any("外部网络检索" in note for note in pack.risk_notes)
    assert any("不写回知识状态" in note for note in pack.risk_notes)


@pytest.mark.asyncio
async def test_build_evidence_pack_still_raises_when_external_is_empty() -> None:
    """本地与外部都无结果时保持抛错，交由上层回退逻辑处理。"""

    class UnresolvedDeliveryBackend:
        async def build_local_evidence_pack(self, query: str) -> EvidencePack:
            raise LookupError(f"knowledge point could not be resolved for query: {query}")

    class EmptyExa:
        async def search(self, query, **kwargs):
            return {"results": []}

    tool = KnowledgeRetrievalTool(
        build_repository(),
        StubEmbeddingModel(),
        exa_retriever=ExaVideoRetriever("exa-test-key", client=EmptyExa()),
        delivery_backend=UnresolvedDeliveryBackend(),
    )

    with pytest.raises(LookupError, match="knowledge point could not be resolved"):
        await tool.build_evidence_pack("关于儿童化妆品的说法")


class RecordingConceptExa:
    """记录每个查询的 web 定义检索，验证概念路被独立执行。"""

    def __init__(self) -> None:
        self.all_queries: list[str] = []
        self.concept_web_queries: list[str] = []

    async def search(self, query, **kwargs):
        self.all_queries.append(query)
        # 概念定义检索带"医学 概念 定义 讲解"后缀
        if "医学 概念 定义 讲解" in query:
            self.concept_web_queries.append(query)
            return {
                "results": [
                    {
                        "title": f"{query}的网络定义",
                        "url": f"https://example.test/web/{len(self.all_queries)}",
                        "highlights": ["该概念的通用定义与讲解"],
                        "score": 0.8,
                    }
                ]
            }
        return {"results": []}


class ConceptDeliveryBackend:
    """概念路本地教材：按概念返回一条教材切片，且保留 resolved_kp_ids。"""

    def __init__(self) -> None:
        self.concept_queries: list[str] = []

    async def build_local_evidence_pack(self, query: str, limit: int = 8) -> EvidencePack:
        self.concept_queries.append(query)
        if "异质性" in query or "观察性研究" in query:
            return EvidencePack(
                evidence_pack_id=f"EP_CONCEPT_{len(self.concept_queries)}",
                query=query,
                resolved_kp_ids=["KP_EPI_001"],
                evidence_items=[
                    EvidenceItem(
                        evidence_id=f"E_CHUNK_CONCEPT_{len(self.concept_queries)}",
                        source_id=f"流行病学:0000{len(self.concept_queries)}",
                        content_summary=f"{query}的教材定义：……（概念路命中）",
                        authority_level="textbook",
                        confidence=0.92,
                        bridge_layer="strict",
                        resource_type="textbook",
                        source_label=f"《流行病学》· 第{len(self.concept_queries)}节",
                    )
                ],
            )
        raise LookupError(f"knowledge point could not be resolved for query: {query}")


@pytest.mark.asyncio
async def test_get_kp_with_content_decomposes_concepts_into_parallel_retrieval() -> None:
    """多路分解检索：kp_concepts 每个概念独立执行教材+网络检索，证据合并去重。"""

    class MixedMainTool(KnowledgeRetrievalTool):
        async def build_evidence_pack(self, query: str) -> EvidencePack:
            return EvidencePack(
                evidence_pack_id="EP_MAIN",
                query=query,
                resolved_kp_ids=["KP_FJ_001"],
                evidence_items=[
                    EvidenceItem(
                        evidence_id="E_TEXT_1", source_id="教材:00001",
                        content_summary="主查询教材切片", authority_level="textbook",
                        confidence=0.9, resource_type="textbook",
                    ),
                    EvidenceItem(
                        evidence_id="E_WEB_1", source_id="EXA_WEB_1",
                        content_summary="主查询网络定义", authority_level="web_reference",
                        confidence=0.8, resource_type="web",
                        source_url="https://example.test/web",
                    ),
                ],
            )

    exa = RecordingConceptExa()
    delivery = ConceptDeliveryBackend()
    tool = MixedMainTool(
        build_repository(),
        StubEmbeddingModel(),
        exa_retriever=ExaVideoRetriever("exa-test-key", client=exa),
        delivery_backend=delivery,
        exa_limit=3,
        textbook_limit=3,
    )

    pack = await tool.get_kp_with_content(
        "关于异质性与观察性研究的说法",
        concepts=["异质性", "观察性研究", "说法是否正确"],
    )

    # 保留调用者选择的查询，不按自然语言词表删去“说法是否正确”。
    assert delivery.concept_queries == ["异质性", "观察性研究", "说法是否正确"]
    assert len(exa.concept_web_queries) == 3
    assert any("异质性" in q for q in exa.concept_web_queries)
    assert any("观察性研究" in q for q in exa.concept_web_queries)
    # 概念路教材证据与主查询证据合并，且证据 id 前缀不冲突
    concept_text_items = [i for i in pack.evidence_items if i.evidence_id.startswith("E_CONCEPT_TXT_")]
    concept_web_items = [i for i in pack.evidence_items if i.evidence_id.startswith("E_CONCEPT_WEB_")]
    assert len(concept_text_items) == 2
    assert len(concept_web_items) == 2
    assert concept_text_items[0].source_label == "《流行病学》· 第1节"
    # 概念路 resolved_kp_ids 合并进主包
    assert "KP_EPI_001" in pack.resolved_kp_ids
    # 概念路证据 id 与主查询不冲突
    all_ids = [i.evidence_id for i in pack.evidence_items]
    assert len(all_ids) == len(set(all_ids))
    # 外部总量仍受 exa_limit 约束（主查询 1 条 web + 概念路 2 条 web = 3）
    assert sum(1 for i in pack.evidence_items if i.resource_type != "textbook") <= 3


@pytest.mark.asyncio
async def test_get_kp_with_content_without_concepts_keeps_single_route() -> None:
    """不传 concepts 时保持原有单路行为，不触发概念分解。"""

    exa = RecordingConceptExa()
    tool = KnowledgeRetrievalTool(
        build_repository(),
        StubEmbeddingModel(),
        exa_retriever=ExaVideoRetriever("exa-test-key", client=exa),
    )

    pack = await tool.get_kp_with_content("四君子汤")

    # 无 concepts 时只保留主查询自身的 web 定义检索，没有独立的概念路
    assert exa.concept_web_queries == ["四君子汤 医学 概念 定义 讲解"]
    assert pack.query == "四君子汤"
    assert pack.resolved_kp_ids == ["KP_FJ_001"]
    assert any(item.source_id == "方剂学:00001" for item in pack.evidence_items)
