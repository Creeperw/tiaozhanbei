"""知识点召回范围与题目桥接范围必须分开。

证据包同时服务两件事，两者的取舍标准相反：

* 证据要「最适合做教材证据」——描述性查询下正确知识点常被同名/通用知识点
  挤出前几位，所以只取头部命中；
* 题目桥接要「覆盖单元范围」——同章节里名字与查询字面不同的知识点永远进不了
  头部，只给头部命中会让单元候选池缺题。

线上实测（单元《伤寒论》太阳病篇，查询「伤寒论 太阳病 提纲 脉证」）：头部
10 个知识点只召回 4 道题，而单元需要 40 道；同一次检索放宽到 200 个知识点后
召回 297 道，其中 69 道的主知识点在单元范围内。
"""

from __future__ import annotations

from competition_app.agents.knowledge_base import KnowledgeBaseAgent
from competition_app.contracts.knowledge import EvidencePack
from competition_app.tools.knowledge_delivery import (
    _EVIDENCE_KP_LIMIT,
    _KP_BRIDGE_RECALL_LIMIT,
    KnowledgeDeliveryBackend,
)


class _FakeMap:
    """按请求上限返回等量知识点的假知识地图。"""

    def __init__(self) -> None:
        self.requested_limits: list[int] = []

    def resolve_topic(self, query: str, limit: int = 5):
        self.requested_limits.append(limit)
        return [
            {
                "kp_id": f"KP_{index:04d}",
                "name": f"知识点{index}",
                "score": 1.0 - index * 0.001,
                "kp": {"kp_id": f"KP_{index:04d}", "kp_lv1": "教材", "kp_lv2": "章节"},
            }
            for index in range(limit)
        ]

    def detail(self, kp_id: str, **kwargs):
        return {
            "chunks": [
                {
                    "chunk_uid": f"{kp_id}_c0",
                    "retrieval_text": f"{kp_id} 的教材正文",
                    "book": "伤寒论选读",
                    "kp_lv2": "太阳病辨证纲要",
                }
            ]
        }

    def targeted_videos(self, kp_id: str):
        return []


def _backend_with(fake_map: _FakeMap) -> KnowledgeDeliveryBackend:
    backend = KnowledgeDeliveryBackend.__new__(KnowledgeDeliveryBackend)
    backend.map = fake_map  # type: ignore[assignment]
    return backend


def test_evidence_pack_separates_evidence_hits_from_bridge_recall():
    fake_map = _FakeMap()
    pack = _backend_with(fake_map)._build_local_evidence_pack("测试查询", limit=8)

    # 一次检索：宽召回只跑一次，证据取头部。
    assert fake_map.requested_limits == [_KP_BRIDGE_RECALL_LIMIT]
    assert pack.resolved_kp_ids == [f"KP_{index:04d}" for index in range(_EVIDENCE_KP_LIMIT)]
    assert len(pack.bridge_kp_ids) == _KP_BRIDGE_RECALL_LIMIT
    # 桥接范围必须覆盖证据范围，否则桥接会比证据更窄。
    assert set(pack.resolved_kp_ids) <= set(pack.bridge_kp_ids)
    assert pack.bridge_kp_ids[:_EVIDENCE_KP_LIMIT] == pack.resolved_kp_ids


def test_bridge_scope_prefers_wide_recall_over_evidence_hits():
    pack = EvidencePack(
        evidence_pack_id="EP_test",
        query="伤寒论 太阳病 提纲 脉证",
        resolved_kp_ids=["KP_0001", "KP_0002"],
        bridge_kp_ids=["KP_0001", "KP_0002", "KP_0003", "KP_0004"],
    )
    assert KnowledgeBaseAgent._bridge_kp_ids(pack) == [
        "KP_0001",
        "KP_0002",
        "KP_0003",
        "KP_0004",
    ]


def test_bridge_scope_falls_back_to_evidence_hits_for_legacy_packs():
    """旧证据包不带宽召回字段时退回头部命中，不静默变成空范围。"""

    pack = EvidencePack(
        evidence_pack_id="EP_legacy",
        query="伤寒论 太阳病 提纲 脉证",
        resolved_kp_ids=["KP_0001", "KP_0002"],
    )
    assert KnowledgeBaseAgent._bridge_kp_ids(pack) == ["KP_0001", "KP_0002"]
