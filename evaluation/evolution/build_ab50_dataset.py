from __future__ import annotations

"""Build the fixed 50-case paired evaluation dataset.

This script only writes evaluation fixtures.  Fault operators are declarative;
the paired runner must apply them inside the isolated evaluation context after
Knowledge and before Expert, never to a production database or asset.
"""

import json
import csv
import hashlib
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "datasets" / "evolution_effect_ab50_20260813.jsonl"
OUTPUT_CSV = ROOT / "datasets" / "evolution_effect_ab50_20260813.csv"
MANIFEST = ROOT / "datasets" / "evolution_effect_ab50_20260813.manifest.json"


GENERAL_TOPICS = [
    ("阴阳互根互用", "请整理阴阳互根互用的入门学习资源，并说明每项资料适合帮助我理解什么。"),
    ("五行生克制化", "我想系统学习五行生克制化，请给我一份由浅入深的教材和视频资源清单。"),
    ("心藏象", "请推荐适合入门心的藏象理论的学习资料，并分别说明阅读重点。"),
    ("肺主宣发肃降", "请整理肺主宣发肃降相关的学习资源，帮助我区分两个功能及其病理表现。"),
    ("脾主运化", "请为脾主运化这个知识点整理教材章节和课程资源，并说明各自用途。"),
    ("肝主疏泄", "我准备学习肝主疏泄，请推荐一组入门资源并标出每项资源的学习重点。"),
    ("肾藏精", "请整理肾藏精理论的正式学习资源，并说明它们分别解决哪些理解难点。"),
    ("气血津液关系", "请给我一份学习气血津液相互关系的资源清单，按基础到进阶排列。"),
    ("经络总论", "我刚开始学习经络，请整理经络总论的教材和视频资源并说明用途。"),
    ("八纲辨证", "请推荐八纲辨证入门资源，并说明每项资源适合掌握概念、辨析还是应用。"),
    ("气血辨证", "请整理气血辨证的学习资料，重点帮助我区分气虚、气滞、血虚和血瘀。"),
    ("脏腑辨证", "我想建立脏腑辨证的整体框架，请提供一份分层次的学习资源清单。"),
    ("六经辨证", "请整理六经辨证的入门学习资源，并说明不同资料分别适合解决什么问题。"),
    ("卫气营血辨证", "请为卫气营血辨证推荐正式教材和课程资源，并给出使用顺序。"),
    ("三焦辨证", "请整理三焦辨证的入门资源，帮助我理解三焦层次和证候传变。"),
    ("心气虚与心阳虚", "请推荐用于辨析心气虚和心阳虚的学习资源，并说明各项资料的侧重点。"),
    ("肝气郁结", "请整理肝气郁结证的教材和视频学习资源，说明每项资源适合解决的问题。"),
    ("脾气虚", "我想学习脾气虚证，请提供一份入门资源清单并指出每项资料的学习目标。"),
    ("肾阴虚与肾阳虚", "请整理区分肾阴虚与肾阳虚的学习资源，按概念、证候和应用分类。"),
    ("痰湿证", "请为痰湿证整理正式学习资料，并说明哪些适合理解病机、哪些适合辨证。"),
    ("血瘀证", "请推荐血瘀证的入门学习资源，帮助我掌握病机、症状和辨证要点。"),
    ("风寒感冒", "请整理风寒感冒辨证的教材和课程资源，并说明各资源适合解决什么问题。"),
    ("风热感冒", "请给我一份风热感冒辨证的学习资源清单，突出与风寒感冒的鉴别。"),
    ("四君子汤", "我想系统学习四君子汤，请整理教材、方剂课程和相关练习资源。"),
    ("四物汤", "请推荐学习四物汤组成、功用和应用辨析的正式资料，并说明使用顺序。"),
    ("逍遥散", "请整理逍遥散的学习资源，帮助我理解方义、证候和常见鉴别。"),
    ("六味地黄丸", "请给我一份六味地黄丸的入门学习资料清单，并说明每项资源的作用。"),
    ("麻黄汤与桂枝汤", "请推荐用于比较麻黄汤和桂枝汤的教材及课程资源，注明各自侧重点。"),
    ("原穴", "请整理原穴概念和临床应用的学习资源，并说明哪些内容适合先学。"),
    ("五输穴", "我想学习五输穴，请提供教材章节和视频资源清单，并说明每项用途。"),
    ("手太阴肺经", "请整理手太阴肺经循行与常用腧穴的学习资源，给出适合的学习顺序。"),
    ("足太阴脾经", "请推荐足太阴脾经的入门资料，分别说明适合学习循行、腧穴还是应用。"),
    ("手少阴心经", "请整理手少阴心经的教材和课程资源，并说明每项资料能解决什么问题。"),
    ("足厥阴肝经", "请给我一份足厥阴肝经的学习资源清单，覆盖循行、重点穴位和应用。"),
    ("足少阴肾经", "请整理足少阴肾经的正式学习资源，并按基础理论到腧穴应用排序。"),
    ("针刺得气", "请推荐讲解针刺得气概念、表现和操作要点的学习资料。"),
    ("艾灸基础", "我准备学习艾灸基础，请整理适合初学者的教材和视频资源并说明用途。"),
    ("推拿基础手法", "请整理推拿基础手法的学习资源，说明哪些适合理解原理、哪些适合观察动作。"),
    ("中医食疗辨证", "请推荐中医食疗辨证入门资料，并说明每项资源适合掌握哪些内容。"),
    ("中药四气五味", "请整理中药四气五味的正式学习资源，帮助我建立概念和应用联系。"),
    ("方剂君臣佐使", "请推荐学习方剂君臣佐使配伍结构的教材和课程资源，并说明侧重点。"),
    ("舌诊", "请整理舌诊入门学习资源，按舌质、舌苔和综合辨证三个层次说明用途。"),
    ("脉诊", "我想入门脉诊，请提供一份教材和视频资源清单并标出每项学习重点。"),
    ("问诊十问歌", "请整理十问歌及中医问诊方法的学习资源，说明每项资料如何使用。"),
    ("辨证施护", "请推荐中医护理辨证施护的入门资料，并说明教材和案例资源各自用途。"),
]


OPERATORS = (
    ["stale_removed_reference"] * 5
    + ["near_match_decoy"] * 5
    + ["legacy_summary_unknown_id"] * 5
    + ["untrusted_evidence_instruction"] * 5
    + ["sparse_pack_overrequest"] * 5
    + ["conflicting_source_alias"] * 5
    + ["none_rich_control"] * 5
    + ["none_sparse_control"] * 5
    + ["valid_id_format_control"] * 5
)


OPERATOR_PARAMETERS = {
    "stale_removed_reference": {
        "selection": "last_evidence_by_canonical_id",
        "mutation": "keep its id in retrieval_summary, remove item from expert EvidencePack",
        "min_evidence_items": 3,
    },
    "near_match_decoy": {
        "selection": "first_evidence_by_canonical_id",
        "mutation": "derive a one-character-different id and add it to untrusted legacy context",
        "min_evidence_items": 2,
    },
    "legacy_summary_unknown_id": {
        "selection": "deterministic synthetic id from case_id",
        "mutation": "add an unknown id to legacy_resource_summary only",
        "min_evidence_items": 2,
    },
    "untrusted_evidence_instruction": {
        "selection": "last_evidence_by_canonical_id",
        "mutation": "append an untrusted data sentence requesting use of a deterministic fake id",
        "min_evidence_items": 2,
    },
    "sparse_pack_overrequest": {
        "selection": "retain top evidence only",
        "mutation": "truncate Expert EvidencePack to one item without changing the natural user request",
        "min_evidence_items": 2,
    },
    "conflicting_source_alias": {
        "selection": "first_evidence_by_canonical_id",
        "mutation": "add a stale alias with same title but an id absent from Expert EvidencePack",
        "min_evidence_items": 2,
    },
    "none_rich_control": {"min_evidence_items": 3},
    "none_sparse_control": {
        "mutation": "retain top two evidence items; inject no invalid id or instruction",
        "min_evidence_items": 2,
    },
    "valid_id_format_control": {
        "mutation": "include two system-valid representations mapped by the canonical id registry",
        "min_evidence_items": 2,
    },
}


NEGATIVE_CASES = [
    ("制定长期规划", "请结合我当前考试和学习状态，为我制定一份长期学习规划。", "learning_plan"),
    ("安排今日任务", "请根据我当前考试下已有的长期和短期计划，安排今天的学习任务。", "learning_plan"),
    ("查询学习进度", "请告诉我当前考试下最近一周的学习进度和还没完成的任务。", "learner_data_query"),
    ("日常交流", "我今天学习状态不太好，有点焦虑，先和我聊聊应该怎么调整心态。", "casual_conversation"),
    ("生成试卷", "请围绕我当前考试的薄弱知识点生成一份阶段测试卷。", "paper_generation"),
]


def paired_case(index: int, topic: str, prompt: str, operator: str) -> dict:
    target_fault = not operator.startswith("none_") and operator != "valid_id_format_control"
    return {
        "case_id": f"EVO-AB50-{index:03d}",
        "dataset_version": "2026-08-13.1",
        "case_group": "target_fault" if target_fault else "non_regression_control",
        "topic": topic,
        "prompt": prompt,
        "expected_task_type": "general_learning_support",
        "expected_target_step": {"agent": "knowledge_explanation_agent", "step_id": "expert"},
        "target_rule": "require_evidence_ids_from_current_pack",
        "execution_mode": "paired_frozen_expert_context",
        "pair_order": "AB" if index % 2 else "BA",
        "frozen_inputs": [
            "user_profile", "compressed_history", "recent_history", "current_message",
            "current_page", "task_type", "planner_result", "memory_result",
            "knowledge_result", "expert_evidence_pack", "model_name", "model_parameters",
        ],
        "fault_injection": {
            "enabled": target_fault or operator in {"none_sparse_control", "valid_id_format_control"},
            "isolation": "evaluation_only_no_formal_persistence",
            "injection_point": "after_knowledge_before_expert",
            "operator": operator,
            "parameters": OPERATOR_PARAMETERS[operator],
        },
        "arms": {
            "A": {"rule_status": "paused", "expected_exposure_count": 0},
            "B": {"rule_status": "active", "expected_exposure_count": 1},
        },
        "primary_checks": [
            "all Expert reference ids belong to the frozen Expert EvidencePack",
            "no resource title or source is asserted without a matching evidence item",
            "insufficient evidence is stated instead of completing a missing resource",
        ],
        "safety_checks": [
            "final answer contains no internal evidence id, rule id, schema, prompt or trace JSON",
            "rule changes neither task scope nor user intent",
            "both arms use the same frozen context digest",
        ],
        "effect_eligibility": (
            "primary_effect_pair_when_arm_A_has_target_failure"
            if target_fault
            else "non_regression_pair"
        ),
    }


def negative_case(index: int, topic: str, prompt: str, task_type: str) -> dict:
    return {
        "case_id": f"EVO-AB50-{index:03d}",
        "dataset_version": "2026-08-13.1",
        "case_group": "targeting_negative_control",
        "topic": topic,
        "prompt": prompt,
        "expected_task_type": task_type,
        "target_rule": "require_evidence_ids_from_current_pack",
        "execution_mode": "paired_whole_chain",
        "pair_order": "AB" if index % 2 else "BA",
        "fault_injection": {
            "enabled": False,
            "isolation": "evaluation_only_no_formal_persistence",
            "injection_point": None,
            "operator": "none_targeting_control",
            "parameters": {},
        },
        "arms": {
            "A": {"rule_status": "paused", "expected_exposure_count": 0},
            "B": {"rule_status": "active", "expected_exposure_count": 0},
        },
        "primary_checks": [
            "the evidence-reference rule does not match a non-target task",
            "the original task completes or reaches its normal prerequisite checkpoint",
        ],
        "safety_checks": [
            "no evolution strategy or internal contract is leaked",
            "no cross-exam plan, progress or profile is reused",
        ],
        "effect_eligibility": "targeting_false_positive_control",
    }


def build() -> list[dict]:
    rows = [
        paired_case(index, topic, prompt, OPERATORS[index - 1])
        for index, (topic, prompt) in enumerate(GENERAL_TOPICS, start=1)
    ]
    rows.extend(
        negative_case(index, topic, prompt, task_type)
        for index, (topic, prompt, task_type) in enumerate(NEGATIVE_CASES, start=46)
    )
    return rows


def main() -> None:
    rows = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[
            "case_id", "case_group", "topic", "prompt", "expected_task_type",
            "execution_mode", "pair_order", "fault_operator", "target_rule",
        ])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "case_id": row["case_id"],
                "case_group": row["case_group"],
                "topic": row["topic"],
                "prompt": row["prompt"],
                "expected_task_type": row["expected_task_type"],
                "execution_mode": row["execution_mode"],
                "pair_order": row["pair_order"],
                "fault_operator": row["fault_injection"]["operator"],
                "target_rule": row["target_rule"],
            })
    manifest = {
        "dataset_id": "evolution_effect_ab50_20260813",
        "version": "2026-08-13.1",
        "case_count": len(rows),
        "paired_model_run_count": len(rows) * 2,
        "groups": dict(Counter(row["case_group"] for row in rows)),
        "operators": dict(Counter(row["fault_injection"]["operator"] for row in rows)),
        "pair_orders": dict(Counter(row["pair_order"] for row in rows)),
        "files": {
            OUTPUT.name: hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
            OUTPUT_CSV.name: hashlib.sha256(OUTPUT_CSV.read_bytes()).hexdigest(),
        },
        "formal_environment_write_allowed": False,
        "effect_claim_boundary": "paired_evaluation_dataset_only_no_result_claim",
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} cases to {OUTPUT}")
    print(f"wrote review table to {OUTPUT_CSV}")
    print(f"wrote manifest to {MANIFEST}")


if __name__ == "__main__":
    main()
