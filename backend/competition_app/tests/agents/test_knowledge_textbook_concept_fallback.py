"""textbook 概念兜底：模型漏提教材切片时，系统按 kp_concepts 自动补全。

对应 knowledge_base.py 中"教材概念兜底"逻辑：kp_concepts 中列出的每个
概念必须在总结中有对应条目；若某概念未被任何已覆盖条目命中，系统在
textbook 类型的证据中按概念关键词补全命中切片，保证专家能看到该概念的
教材原文（B：证据不丢失）。
"""

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
async def test_textbook_concept_fallback_restores_missed_evidence() -> None:
    output = await KnowledgeBaseAgent(
        TextbookPackTool(), MissingTextbookItemModel()
    ).run(context())

    summary_ids = {item.evidence_id for item in output.payload.summary_items}

    # 模型提取的条目保留
    assert "E_CHUNK_预防医学_clean:01000" in summary_ids
    # 模型漏提的观察性研究切片被概念兜底自动补全
    assert "E_CHUNK_预防医学_clean:00956" in summary_ids
    # 兜底条目的 content 是教材原文，不是模型改写
    restored = next(
        item for item in output.payload.summary_items
        if item.evidence_id == "E_CHUNK_预防医学_clean:00956"
    )
    assert "不给研究对象施加任何干预措施" in restored.content


@pytest.mark.asyncio
async def test_concept_coverage_adds_risk_note_free_fallback_without_duplication() -> None:
    output = await KnowledgeBaseAgent(
        TextbookPackTool(), MissingTextbookItemModel()
    ).run(context())

    # 兜底只补一次，不产生重复条目
    ids = [item.evidence_id for item in output.payload.summary_items]
    assert ids.count("E_CHUNK_预防医学_clean:00956") == 1
    # summary_evidence_ids 与 summary_items 一致
    assert set(output.payload.summary_evidence_ids) == set(ids)
