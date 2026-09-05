from __future__ import annotations

"""Extend the frozen D1 AB50 fixture to a new 100-case evaluation dataset.

The original 50 rows remain byte-for-byte equivalent at the object level so
their completed model receipts stay reusable.  Only cases 051..100 are new.
This module creates evaluation fixtures only and never touches business state.
"""

from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parent
LEGACY_DATASET = ROOT / "datasets" / "semantic_conflict_closure_ab50_20260819.jsonl"
DATASET = ROOT / "datasets" / "semantic_conflict_closure_ab100_20260826.jsonl"
CSV_DATASET = DATASET.with_suffix(".csv")
MANIFEST = DATASET.with_suffix(".manifest.json")
RULE_ID = "semantic_conflict_pair_closure_v1"


# Thirty additional target challenges.  Topics span foundations, diagnostics,
# formulae, materia medica and acupuncture instead of paraphrasing the first
# 30 prompts.  Learner prompts contain no rule, schema or evidence identifiers.
TARGET_EXTENSION: list[tuple[str, str]] = [
    ("中医学整体观念", "请结合正式教材讲清中医学整体观念的基本内涵，并比较不同教学资料在人体整体与人与环境关系上的讲解重点。"),
    ("阴阳对立制约", "我想辨清阴阳对立和相互制约之间的关系，请依据教材组织一份由概念到例子的学习讲解。"),
    ("阴阳消长转化", "请系统说明阴阳消长与阴阳转化的区别和联系，并整理适合逐步理解这两个概念的学习材料。"),
    ("五行相乘相侮", "请讲解五行相乘、相侮的形成条件与区别，并说明教材和课程材料各自适合关注哪些要点。"),
    ("精气学说", "我正在学习精气学说，请按核心概念、基本关系和中医学应用三个层次梳理正式学习内容。"),
    ("神的概念", "请依据中医基础教材解释广义之神与狭义之神，并帮助我辨析学习资料中容易混在一起的表述。"),
    ("心主血脉", "请讲清心主血脉的含义、实现条件和常见辨析点，并给出教材与课程内容的阅读顺序。"),
    ("肺主气司呼吸", "我想系统理解肺主气、司呼吸，请整理核心教材内容并说明不同讲解材料的侧重点。"),
    ("脾主升清", "请围绕脾主升清组织一份完整学习讲解，区分它与脾主运化之间的联系及不同资料的解释重点。"),
    ("肝藏血", "请依据正式教材说明肝藏血的基本含义和主要作用，并整理适合入门和辨析阶段使用的资料。"),
    ("肾主水", "请系统讲解肾主水的生理含义以及与其他脏腑的关系，指出教材和课程中需要重点核对的表述。"),
    ("胆主决断", "我对胆主决断的概念不太清楚，请依据教材说明其内涵，并对照不同教学材料的常见解释。"),
    ("胃主受纳腐熟", "请讲解胃主受纳、腐熟水谷的含义以及二者关系，并按学习阶段整理教材和辅助材料。"),
    ("气机升降出入", "请从定义、脏腑联系和常见辨析三个方面讲清气机升降出入，并安排合理的材料学习顺序。"),
    ("津液输布", "请依据中医基础教材梳理津液生成、输布和排泄过程，并说明不同课程讲解的关注重点。"),
    ("十二经脉流注", "我想建立十二经脉流注次序的整体认识，请组织教材内容、理解方法和必要的辨析提醒。"),
    ("奇经八脉", "请系统介绍奇经八脉的共同特点和主要作用，并比较教材正文与辅助课程适合承担的学习任务。"),
    ("舌诊", "请根据中医诊断教材整理舌诊的观察内容和基本分析顺序，帮助我分清不同资料的表述层次。"),
    ("浮沉迟数脉", "请讲清浮、沉、迟、数四种基础脉象的判断要点，并设计一条从教材定义到辨析练习的学习路径。"),
    ("寒热辨证", "请依据教材比较寒证与热证的主要表现和鉴别依据，并指出课程补充内容应如何使用。"),
    ("虚实辨证", "我想系统学习虚实辨证，请从概念、形成机制和鉴别要点组织讲解，并标明不同材料的使用顺序。"),
    ("肝病辨证", "请为肝病常见证候建立一个教材化学习框架，说明概念学习、证候比较和练习材料各自解决什么问题。"),
    ("脾胃病辨证", "请整理脾胃病辨证的基础学习内容，重点说明相近证候如何比较以及不同资料的侧重点。"),
    ("卫分与气分", "请依据温病学教材比较卫分证和气分证，并组织一份能够逐步核对关键判断依据的学习讲解。"),
    ("中药四气五味", "请系统说明中药四气五味的含义和作用特点，并比较教材定义与课程示例应如何配合学习。"),
    ("中药升降浮沉", "我想辨清中药升降浮沉的基本规律，请依据教材整理核心内容并说明辅助资料的适用范围。"),
    ("君臣佐使", "请讲清方剂配伍中君臣佐使的含义和判断方法，并帮助我核对不同讲解材料中的角色划分依据。"),
    ("桂枝汤", "请依据方剂学教材整理桂枝汤的组成、功用和证治要点，并说明课程讲解可用于补充哪些理解。"),
    ("小柴胡汤", "请系统学习小柴胡汤的组成、功用和主治逻辑，按教材原文、方义讲解和辨析练习安排顺序。"),
    ("足三里", "请依据针灸教材讲解足三里的定位、归经和常见学习要点，并整理适合核对定位与理解作用的材料。"),
]


# The first ten controls stay close to the target domain but contain no
# injected conflict.  The final five broaden ordinary learning-support usage.
NON_REGRESSION_EXTENSION: list[tuple[str, str]] = [
    ("knowledge_explanation", "请用教材中的基本定义解释阴阳学说，并给出三个帮助自检理解程度的问题。"),
    ("knowledge_explanation", "请概括五脏的主要生理功能，按适合初学者记忆的结构组织内容。"),
    ("knowledge_explanation", "请介绍望闻问切四诊分别收集什么信息，并说明学习时应先掌握什么。"),
    ("knowledge_explanation", "请解释气的推动、温煦、防御、固摄和气化作用，使用清楚的分点结构。"),
    ("knowledge_explanation", "请按照教材顺序介绍十二经脉的命名规律和基本分布特点。"),
    ("knowledge_explanation", "请帮助我复习中药性能的基本内容，先讲概念，再给出自测问题。"),
    ("knowledge_explanation", "请说明方剂组成原则的学习方法，并用一个简单例子帮助我理解角色分工。"),
    ("knowledge_explanation", "请介绍腧穴定位常用的骨度分寸法和同身寸法，提醒我学习时注意哪些边界。"),
    ("knowledge_explanation", "请把表里辨证的核心判断依据整理成适合考前回顾的简明提纲。"),
    ("knowledge_explanation", "请解释治未病的基本思想，并提出两个可以联系日常学习的开放问题。"),
    ("general_learning_support", "我对刚学完的一章掌握得不牢，请给我一个二十五分钟的回忆、核对和复述流程。"),
    ("general_learning_support", "今天的学习任务比预计多，请帮我确定保留项、可延后项和最低完成标准。"),
    ("general_learning_support", "我连续做题后注意力下降，请安排一次短休息以及后续二十分钟的学习节奏。"),
    ("general_learning_support", "请给我一个每周检查学习计划是否需要调整的简单清单，不要替我重新制定长期计划。"),
    ("general_learning_support", "我想复盘这周的学习情况，请告诉我应该记录哪些事实，避免只凭主观感觉判断。"),
]


# Harder targeting negatives: source descriptions differ in granularity or
# emphasis, but the learner explicitly says they are compatible.  They test
# near-boundary targeting without exposing internal evaluation terminology.
NEGATIVE_EXTENSION: list[tuple[str, str]] = [
    ("knowledge_explanation", "教材给出定义，课程补充了例子，两者结论一致。请帮我合并成一份学习笔记。"),
    ("knowledge_explanation", "两份资料对同一知识点分别讲概念和应用，内容可以互相补充，请整理它们的学习顺序。"),
    ("knowledge_explanation", "教材表述比较简洁，老师讲得更展开，但核心意思相同。请帮我提炼共同要点。"),
    ("knowledge_explanation", "我有一份章节提纲和一份课堂笔记，请在不改变原意的前提下合并并去除重复内容。"),
    ("knowledge_explanation", "两段材料使用的例子不同，但对概念的判断相同，请帮我整理成便于复习的对照表。"),
]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _new_row(
    index: int,
    group: str,
    prompt: str,
    task_type: str,
    scenario: str,
) -> dict[str, Any]:
    return {
        "case_id": f"EVO-D1-AB100-{index:03d}",
        "case_group": group,
        "prompt": prompt,
        "expected_task_type": task_type,
        "pair_order": "AB" if index % 2 else "BA",
        "target_rule": RULE_ID,
        "target_agent": "expert_agent",
        "owner_step_id": "expert",
        "expected_rerun_step_ids": ["expert", "audit"],
        "scenario": scenario,
        "formal_environment_write_allowed": False,
        "dataset_partition": "extension_20260826",
    }


def build() -> list[dict[str, Any]]:
    legacy = _load_jsonl(LEGACY_DATASET)
    if len(legacy) != 50:
        raise ValueError(f"expected 50 frozen legacy cases, got {len(legacy)}")

    extension: list[dict[str, Any]] = []
    for offset, (_, prompt) in enumerate(TARGET_EXTENSION):
        index = 51 + offset
        # Add ten fresh cases to each original target scenario.
        scenario = (
            "expert_conflict_pair_resolvable"
            if offset < 10
            else "expert_conflict_pair_residual_after_repair"
            if offset < 20
            else "expert_conflict_pair_expanded_rerun"
        )
        extension.append(
            _new_row(index, "target_fault", prompt, "knowledge_explanation", scenario)
        )

    for offset, (task_type, prompt) in enumerate(NON_REGRESSION_EXTENSION):
        extension.append(
            _new_row(
                81 + offset,
                "non_regression_control",
                prompt,
                task_type,
                "normal_dialogue_no_conflict",
            )
        )

    for offset, (task_type, prompt) in enumerate(NEGATIVE_EXTENSION):
        extension.append(
            _new_row(
                96 + offset,
                "targeting_negative_control",
                prompt,
                task_type,
                "compatible_sources_near_boundary",
            )
        )
    return legacy + extension


def validate(rows: list[dict[str, Any]], legacy: list[dict[str, Any]]) -> None:
    if len(rows) != 100:
        raise ValueError(f"expected 100 cases, got {len(rows)}")
    if rows[:50] != legacy:
        raise ValueError("the original frozen 50 cases must remain unchanged")
    ids = [str(row["case_id"]) for row in rows]
    if len(set(ids)) != 100:
        raise ValueError("case IDs must be unique")
    expected_extension_ids = [f"EVO-D1-AB100-{index:03d}" for index in range(51, 101)]
    if ids[50:] != expected_extension_ids:
        raise ValueError("extension IDs must be contiguous AB100-051..100")
    prompts = [re.sub(r"\s+", "", str(row["prompt"])) for row in rows]
    if len(set(prompts)) != 100:
        raise ValueError("all 100 learner prompts must be unique")
    if Counter(row["case_group"] for row in rows) != Counter(
        {"target_fault": 60, "non_regression_control": 30, "targeting_negative_control": 10}
    ):
        raise ValueError("unexpected AB100 group distribution")
    if Counter(row["pair_order"] for row in rows) != Counter({"AB": 50, "BA": 50}):
        raise ValueError("AB100 pair order must remain balanced")
    if any(row["target_rule"] != RULE_ID for row in rows):
        raise ValueError("every case must target the frozen D1 rule")
    if any(row["formal_environment_write_allowed"] is not False for row in rows):
        raise ValueError("formal environment writeback must remain disabled")
    forbidden = (RULE_ID, "evidence_id", "EvidencePack", "提示词", "Schema", "Trace")
    if any(any(token in str(row["prompt"]) for token in forbidden) for row in rows):
        raise ValueError("learner prompt contains an internal evaluation hint")


def write(rows: list[dict[str, Any]]) -> None:
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    DATASET.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    fieldnames = [
        "case_id",
        "case_group",
        "prompt",
        "expected_task_type",
        "pair_order",
        "target_rule",
        "target_agent",
        "owner_step_id",
        "scenario",
        "dataset_partition",
    ]
    with CSV_DATASET.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "legacy_ab50") for key in fieldnames})

    legacy_digest = hashlib.sha256(LEGACY_DATASET.read_bytes()).hexdigest()
    manifest = {
        "dataset_id": "semantic_conflict_closure_ab100_20260826",
        "version": "2026-08-26.1",
        "lineage": {
            "base_dataset": LEGACY_DATASET.name,
            "base_case_count": 50,
            "base_sha256": legacy_digest,
            "base_rows_unchanged": True,
            "extension_case_count": 50,
        },
        "case_count": len(rows),
        "paired_model_run_count": len(rows) * 2,
        "case_groups": dict(Counter(row["case_group"] for row in rows)),
        "pair_orders": dict(Counter(row["pair_order"] for row in rows)),
        "scenarios": dict(Counter(row["scenario"] for row in rows)),
        "task_types": dict(Counter(row["expected_task_type"] for row in rows)),
        "target_rule": RULE_ID,
        "target_agent": "expert_agent",
        "formal_environment_write_allowed": False,
        "effect_claim_boundary": "expanded_dataset_frozen_no_new_effect_claim_before_execution",
        "files": {
            DATASET.name: hashlib.sha256(DATASET.read_bytes()).hexdigest(),
            CSV_DATASET.name: hashlib.sha256(CSV_DATASET.read_bytes()).hexdigest(),
        },
    }
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    legacy = _load_jsonl(LEGACY_DATASET)
    rows = build()
    validate(rows, legacy)
    write(rows)
    print(
        json.dumps(
            {
                "case_count": len(rows),
                "new_case_count": len(rows) - len(legacy),
                "manifest": str(MANIFEST),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
