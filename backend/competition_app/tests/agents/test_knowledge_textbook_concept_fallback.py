"""原始教材结果不丢失，但程序不得按概念关键词替模型选择证据。"""

import pytest

from competition_app.agents.knowledge_base import KnowledgeBaseAgent
from competition_app.contracts.knowledge import EvidenceItem, EvidencePack


class TextbookPackTool:
    """返回两条 textbook 证据：观察性研究定义（E_1）与 Meta分析定义（E_2）。"""

    async def build_evidence_pack(self, query: str) -> EvidencePack:
        return EvidencePack(
            evidence_pack_id="EP_TB",
            query=query,
            resolved_kp_ids=["KP_EPI_01"],
            evidence_items=[
                EvidenceItem(
                    evidence_id="E_CHUNK_预防医学_clean:00956",
                    source_id="预防医学:9",
                    content_summary=(
                        "观察性研究是在自然状态下，不给研究对象施加任何干预措施，"
                        "观察并记录其自然发展变化规律的研究方法。"
                    ),
                    authority_level="textbook",
                    confidence=0.95,
                    bridge_layer="strict",
                ),
                EvidenceItem(
                    evidence_id="E_CHUNK_预防医学_clean:01000",
                    source_id="预防医学:10",
                    content_summary=(
                        "Meta分析是系统评价中定量综合多个独立研究结果的统计方法。"
                    ),
                    authority_level="textbook",
                    confidence=0.95,
                    bridge_layer="strict",
                ),
            ],
        )


class MissingTextbookItemModel:
    """模型提取了 kp_concepts 但总结时只提取 Meta分析，漏掉观察性研究。"""

    async def complete_json(self, role, payload, on_delta=None):
        if payload["payload"].get("phase") == "plan_retrieval":
            return {
                "kp_query": (
                    "Meta分析是系统评价中定量综合多个独立研究结果的统计方法，"
                    "其前提是纳入的原始研究具有同质性，包括观察性研究等研究类型。"
                ),
                "kp_concepts": ["Meta分析", "观察性研究", "原始数据"],
                "question_query": "Meta分析 相关题目",
                "retrieval_reason": "检索教材依据和候选练习。",
            }
        return {
            "retrieval_summary": "Meta分析的定义及统计方法。",
            "summary_items": [
                {
                    "evidence_id": "E_CHUNK_预防医学_clean:01000",
                    "content": "Meta分析是系统评价中定量综合多个独立研究结果的统计方法。",
                },
            ],
            "quality_labels": ["教材依据相关"],
            "uncertainty": [],
        }


def context() -> dict[str, object]:
    return {
        "case_id": "CASE_TB_1",
        "trace_id": "TRACE_TB_1",
        "request_id": "REQ_TB_1",
        "execution_id": "EXE_TB_1",
        "step_id": "knowledge",
        "learner_id": "L1",
        "topic": "Meta分析",
        "available_minutes": 10,
        "dependency_outputs": {},
        "user_request": "Meta分析是什么？",
    }


@pytest.mark.asyncio
async def test_textbook_concept_match_does_not_override_model_selection() -> None:
    output = await KnowledgeBaseAgent(
        TextbookPackTool(), MissingTextbookItemModel()
    ).run(context())

    summary_ids = {item.evidence_id for item in output.payload.summary_items}

    # 模型提取的条目保留
    assert "E_CHUNK_预防医学_clean:01000" in summary_ids
    assert "E_CHUNK_预防医学_clean:00956" not in summary_ids
    preserved = next(
        item for item in output.payload.evidence_items
        if item.evidence_id == "E_CHUNK_预防医学_clean:00956"
    )
    assert "不给研究对象施加任何干预措施" in preserved.content_summary


@pytest.mark.asyncio
async def test_selected_evidence_ids_do_not_include_unselected_concept_hits() -> None:
    output = await KnowledgeBaseAgent(
        TextbookPackTool(), MissingTextbookItemModel()
    ).run(context())

    ids = [item.evidence_id for item in output.payload.summary_items]
    assert ids == ["E_CHUNK_预防医学_clean:01000"]
    # summary_evidence_ids 与 summary_items 一致
    assert set(output.payload.summary_evidence_ids) == set(ids)
