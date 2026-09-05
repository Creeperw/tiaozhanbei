"""诊断 v3：用真实 KnowledgeDeliveryBackend 复现检索，分类 50 道候选被拒原因。

直接调用 search_questions（与线上完全相同的链路），然后对返回的
QuestionDetail 逐条跑 _candidate_admission，统计拒绝原因。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/d/code/AI/deeplearning/tiaozhanbei/backend")

from competition_app.agents.knowledge_base import KnowledgeBaseAgent
from competition_app.tools.knowledge_delivery import (
    KnowledgeDeliveryBackend,
    KnowledgeDeliveryPaths,
)

RELEASE_ROOT = Path("/mnt/d/code/AI/deeplearning/tiaozhanbei/assets/knowledge/releases/2026-07-18")
RUNTIME_ROOT = Path("/mnt/d/code/AI/deeplearning/tiaozhanbei/runtime/knowledge")
VDB_STORE = Path("/mnt/d/code/AI/deeplearning/tiaozhanbei/assets/vectors/public/2026-07-18")

# 本次运行证据包 resolved_kp_ids
RESOLVED_KP_IDS = ["002463", "002812", "005390", "005391", "012252"]

QUERY = "考点标签同时包含“四君子汤组成”“四君子汤功效主治”“四君子汤配伍意义”；题型限定为单项选择题或多项选择题；排除类方对比题（如四君子汤与参苓白术散、补中益气汤等对比）和加减变化题（如四君子汤加味成异功散、六君子汤、香砂六君子汤等）。"


class FakeEvidencePack:
    resolved_kp_ids = RESOLVED_KP_IDS
    resolved_kp_names = {}
    evidence_items = []


class FakeUnit:
    unit_id = "UNIT_01"
    knowledge_module = "方剂学·补益剂·四君子汤"
    learning_objective = "四君子汤组成、功效主治与配伍意义"
    retrieval_query = QUERY
    question_type_preferences = ["单项选择题", "多项选择题"]
    required_question_count = 5
    candidate_limit = 10
    assessment_dimensions = []
    excluded_dimensions = []
    target_difficulty = None


def main():
    asyncio.run(run())


async def run():
    paths = KnowledgeDeliveryPaths.from_release_root(
        RELEASE_ROOT,
        runtime_root=RUNTIME_ROOT,
        public_vector_store=VDB_STORE,
    )
    backend = KnowledgeDeliveryBackend(
        paths,
        embedding_base_url="",
        embedding_model="",
        embedding_api_key=None,
    )
    print("backend 初始化完成")

    result = await backend.search_questions(
        QUERY,
        RESOLVED_KP_IDS,
        limit=50,
        owner_id=None,
        scope="all",
    )
    items = result.items
    print(f"检索到候选: {len(items)} 道")
    print(f"resolved_kp_ids: {result.resolved_kp_ids}")

    unit = FakeUnit()
    pack = FakeEvidencePack()

    stats = {"eligible": 0, "uncertain": 0, "rejected": 0}
    reasons = {}
    detail_fail = {}
    scope_detail = {}
    samples = []

    # 模拟放宽 analysis 后的结果
    relaxed_stats = {"eligible": 0, "uncertain": 0, "rejected": 0}
    relaxed_reasons = {}
    type_breakdown = {}

    # 模拟：放宽 analysis + scope 用全部 KP 比较
    fixed_stats = {"eligible": 0, "uncertain": 0, "rejected": 0}
    fixed_reasons = {}
    fixed_type = {}

    # 模拟：放宽 analysis + 主题锚点（knowledge_module 匹配 resolved_kp_names）
    anchor_stats = {"eligible": 0, "uncertain": 0, "rejected": 0}
    anchor_reasons = {}
    anchor_type = {}

    # 真实证据包（含 resolved_kp_names），用于主题锚点模拟
    real_pack = await backend.build_local_evidence_pack(QUERY, limit=8)
    print(f"真实证据包 resolved_kp_ids: {real_pack.resolved_kp_ids}")
    print(f"真实证据包 resolved_kp_names: {real_pack.resolved_kp_names}")

    # 用真实 _knowledge_module_anchor 提取锚点（模拟线上带括号后缀的 knowledge_module）
    anchor_unit = FakeUnit()
    anchor_unit.knowledge_module = "方剂学·补益剂·四君子汤（组成识记）"
    anchor = KnowledgeBaseAgent._knowledge_module_anchor(anchor_unit)
    print(f"knowledge_module='{anchor_unit.knowledge_module}' -> 锚点='{anchor}'")
    anchored_kp_ids = {
        kp_id
        for kp_id, name in real_pack.resolved_kp_names.items()
        if anchor in name or name in anchor
    }
    print(f"主题锚点 '{anchor}' 命中的 KP: {anchored_kp_ids}")

    # 统计候选池里挂四君子汤 KP 的题（真实题库检索 vs 专家生成）
    anchored_questions = [
        item
        for item in items
        if {b.kp_id for b in item.bridges} & anchored_kp_ids
    ]
    print(f"\n候选池中挂四君子汤 KP 的题: {len(anchored_questions)} 道")
    anchored_type = {}
    for item in anchored_questions:
        key = f"{item.question_type}->{'generated' if item.question_id.startswith('generated') else 'retrieved'}"
        anchored_type[key] = anchored_type.get(key, 0) + 1
    print(f"挂四君子汤 KP 的题题型分布: {anchored_type}")
    for item in anchored_questions:
        print(f"  [{item.question_type}] {item.question_id[:40]} | kp={[b.kp_id for b in item.bridges][:4]} | stem={item.stem[:30]}")

    for item in items:
        status, reason = KnowledgeBaseAgent._candidate_admission(item, unit, pack)
        stats[status] += 1
        reasons[reason] = reasons.get(reason, 0) + 1

        # 模拟：analysis 缺失不拒绝（放宽）
        relaxed_item = item.model_copy(update={"analysis": item.analysis or "（解析缺失，由专家补充）"})
        r_status, r_reason = KnowledgeBaseAgent._candidate_admission(relaxed_item, unit, pack)
        relaxed_stats[r_status] += 1
        relaxed_reasons[r_reason] = relaxed_reasons.get(r_reason, 0) + 1
        key = f"{item.question_type}->{r_status}"
        type_breakdown[key] = type_breakdown.get(key, 0) + 1

        # 模拟：放宽 analysis + scope 用全部 resolved_kp_ids
        fixed_item = relaxed_item
        # 手动重算 scope：用全部 KP
        candidate_kp_ids = {b.kp_id for b in fixed_item.bridges}
        all_kp = set(RESOLVED_KP_IDS)
        if candidate_kp_ids & all_kp:
            f_status = "eligible"
            f_reason = "topic_entity_match"
        elif not candidate_kp_ids:
            f_status = "uncertain"
            f_reason = "primary_kp_scope_unverified"
        else:
            f_status = "rejected"
            f_reason = "topic_entity_mismatch"
        # 再叠加维度检查（无维度时保持）
        fixed_stats[f_status] += 1
        fixed_reasons[f_reason] = fixed_reasons.get(f_reason, 0) + 1
        fkey = f"{item.question_type}->{f_status}"
        fixed_type[fkey] = fixed_type.get(fkey, 0) + 1

        # 模拟：放宽 analysis + 主题锚点（真实 resolved_kp_names）
        anchor_item = relaxed_item
        a_candidate_kp_ids = {b.kp_id for b in anchor_item.bridges}
        if anchored_kp_ids and a_candidate_kp_ids:
            if anchored_kp_ids & a_candidate_kp_ids:
                a_status = "eligible"
                a_reason = "topic_entity_match"
            else:
                a_status = "rejected"
                a_reason = "topic_entity_mismatch"
        elif not a_candidate_kp_ids:
            a_status = "uncertain"
            a_reason = "primary_kp_scope_unverified"
        else:
            # 锚点未命中任何 KP 时回退到首位
            if a_candidate_kp_ids & {RESOLVED_KP_IDS[0]}:
                a_status = "eligible"
                a_reason = "topic_entity_match"
            else:
                a_status = "rejected"
                a_reason = "topic_entity_mismatch"
        anchor_stats[a_status] += 1
        anchor_reasons[a_reason] = anchor_reasons.get(a_reason, 0) + 1
        akey = f"{item.question_type}->{a_status}"
        anchor_type[akey] = anchor_type.get(akey, 0) + 1

        if reason == "invalid_question_delivery":
            if not item.stem.strip():
                detail_fail["no_stem"] = detail_fail.get("no_stem", 0) + 1
            if not item.reference_answer.strip():
                detail_fail["no_answer"] = detail_fail.get("no_answer", 0) + 1
            if not (item.analysis or "").strip():
                detail_fail["no_analysis"] = detail_fail.get("no_analysis", 0) + 1
            parsed = KnowledgeBaseAgent._parsed_choice_options(item.options)
            labels = KnowledgeBaseAgent._choice_answer_labels(item.reference_answer, parsed)
            if len(parsed) < 2:
                detail_fail["few_options"] = detail_fail.get("few_options", 0) + 1
            if not labels:
                detail_fail["unparsable_answer"] = detail_fail.get("unparsable_answer", 0) + 1

        if reason == "topic_entity_mismatch":
            candidate_kp_ids = {b.kp_id for b in item.bridges}
            overlap_all = candidate_kp_ids & set(RESOLVED_KP_IDS)
            overlap_first = candidate_kp_ids & {RESOLVED_KP_IDS[0]}
            key = (
                f"命中非首KP={sorted(overlap_all - {RESOLVED_KP_IDS[0]})}"
                if overlap_all and not overlap_first
                else f"bridges={sorted(candidate_kp_ids)[:5]}"
            )
            scope_detail[key] = scope_detail.get(key, 0) + 1

        if len(samples) < 15:
            samples.append({
                "qid": item.question_id,
                "type": item.question_type,
                "stem": item.stem[:36],
                "status": status,
                "reason": reason,
                "analysis": (item.analysis or "")[:30] or "<空>",
                "options": len(item.options),
                "answer": item.reference_answer[:24],
                "kp_ids": [b.kp_id for b in item.bridges][:6],
                "channels": item.retrieval.channels,
            })

    print(f"\n=== 准入分类（{len(items)} 道候选）===")
    print(f"eligible={stats['eligible']} uncertain={stats['uncertain']} rejected={stats['rejected']}")
    print(f"拒绝原因: {reasons}")
    print(f"invalid_question_delivery 细分: {detail_fail}")
    print(f"topic_entity_mismatch 细分: {scope_detail}")

    print(f"\n=== 放宽 analysis 后 ===")
    print(f"eligible={relaxed_stats['eligible']} uncertain={relaxed_stats['uncertain']} rejected={relaxed_stats['rejected']}")
    print(f"拒绝原因: {relaxed_reasons}")
    print(f"题型->状态: {type_breakdown}")

    print(f"\n=== 放宽 analysis + scope 用全部 KP ===")
    print(f"eligible={fixed_stats['eligible']} uncertain={fixed_stats['uncertain']} rejected={fixed_stats['rejected']}")
    print(f"拒绝原因: {fixed_reasons}")
    print(f"题型->状态: {fixed_type}")

    print(f"\n=== 放宽 analysis + 主题锚点（knowledge_module 匹配 resolved_kp_names）===")
    print(f"eligible={anchor_stats['eligible']} uncertain={anchor_stats['uncertain']} rejected={anchor_stats['rejected']}")
    print(f"拒绝原因: {anchor_reasons}")
    print(f"题型->状态: {anchor_type}")

    print("\n=== 样本 ===")
    for s in samples:
        print(json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    main()