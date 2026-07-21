from pathlib import Path

import pytest

from competition_app.embeddings.stub import StubEmbeddingModel
from competition_app.tools.knowledge_assets import KnowledgeAssetPaths, KnowledgeAssetRepository
from competition_app.tools.knowledge_retrieval import KnowledgeRetrievalTool
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

    async def search(self, query: str, kp_ids: list[str], limit: int):
        self.arguments = (query, kp_ids, limit)
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