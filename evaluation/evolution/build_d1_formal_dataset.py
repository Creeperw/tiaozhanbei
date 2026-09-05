from __future__ import annotations

"""Freeze the formal 50-case A/B dataset for D1.

This creates evaluation fixtures only. The dataset contains no internal rule
instructions in learner prompts; the rule is toggled by the isolated runner.
"""

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "datasets" / "semantic_conflict_closure_ab50_20260819.jsonl"
CSV_DATASET = DATASET.with_suffix(".csv")
MANIFEST = DATASET.with_suffix(".manifest.json")
RULE_ID = "semantic_conflict_pair_closure_v1"

TARGET_TOPICS = [
    ("阴阳互根互用", "请系统整理阴阳互根互用的学习资料，比较教材与课程讲解的重点，并说明适合我的学习顺序。"),
    ("五行生克制化", "我想从基础到应用学习五行生克制化，请推荐配套教材和课程，并说明每项资料解决什么问题。"),
    ("心藏象", "请整理心藏象相关的正式教材和教学资源，帮助我建立概念、功能与常见辨析的完整框架。"),
    ("肺主宣发肃降", "请为肺主宣发肃降整理一份分层学习资源，重点说明如何区分两个功能及其病理表现。"),
    ("脾主运化", "我准备学习脾主运化，请推荐教材章节、课程和练习资源，并说明它们各自的学习用途。"),
    ("肝主疏泄", "请整理肝主疏泄的入门与进阶学习资料，分别标出概念理解、辨证和应用的重点。"),
    ("肾藏精", "请为肾藏精建立一份正式学习资源清单，说明每项资料适合解决哪些理解难点。"),
    ("气血津液", "我想系统学习气血津液的关系，请按基础、辨析和应用排列教材与课程资源。"),
    ("经络总论", "请整理经络总论的教材和课程资源，帮助我从基本概念过渡到循行和临床联系。"),
    ("八纲辨证", "请推荐八纲辨证的入门资料，并说明哪些资源适合掌握概念、鉴别和实际判断。"),
    ("气血辨证", "请整理气血辨证的学习资料，重点帮助我区分气虚、气滞、血虚和血瘀。"),
    ("脏腑辨证", "我想建立脏腑辨证的整体框架，请提供分层次的教材、课程和自测资源。"),
    ("六经辨证", "请整理六经辨证的学习资源，说明不同资料分别适合解决入门、辨析和综合应用问题。"),
    ("卫气营血辨证", "请为卫气营血辨证推荐正式教材和课程，并给出由浅入深的使用顺序。"),
    ("三焦辨证", "请整理三焦辨证的入门资料，帮助我理解三焦层次、证候特点和传变关系。"),
    ("心气虚与心阳虚", "请推荐用于辨析心气虚与心阳虚的教材和课程，标出每项资料的侧重点。"),
    ("肝气郁结", "请整理肝气郁结证的教材与视频课程，分别说明适合学习病机、证候和鉴别的内容。"),
    ("脾气虚", "我想学习脾气虚证，请提供入门资源清单并说明每项资料对应的学习目标。"),
    ("肾阴虚与肾阳虚", "请整理区分肾阴虚与肾阳虚的学习资料，按概念、证候和应用分类。"),
    ("痰湿证", "请为痰湿证整理正式学习资料，并说明哪些内容适合理解病机、哪些适合辨证训练。"),
    ("血瘀证", "请推荐血瘀证的学习资源，帮助我掌握病机、表现和辨证要点之间的联系。"),
    ("风寒感冒", "请整理风寒感冒辨证的教材和课程资源，并说明各项资料适合解决什么问题。"),
    ("风热感冒", "请给我一份风热感冒辨证的学习资源清单，突出与风寒感冒的鉴别重点。"),
    ("四君子汤", "我想系统学习四君子汤，请整理教材、方剂课程和相关练习资源，并说明使用顺序。"),
    ("四物汤", "请推荐学习四物汤组成、功用和应用辨析的正式资料，说明每项资料的作用。"),
    ("逍遥散", "请整理逍遥散的学习资源，帮助我理解方义、证候和常见鉴别。"),
    ("六味地黄丸", "请为六味地黄丸建立一份入门学习资料清单，并说明各项资源的适用阶段。"),
    ("麻黄汤与桂枝汤", "请推荐用于比较麻黄汤与桂枝汤的教材和课程资源，注明各自侧重点。"),
    ("原穴", "请整理原穴概念和临床应用的学习资源，并说明哪些内容适合先学。"),
    ("五输穴", "我想学习五输穴，请提供教材章节、视频和练习资源，并说明每项资料的用途。"),
]

NON_REGRESSION_PROMPTS = [
    "我今天只有二十分钟，请根据我的当前学习阶段安排一个可完成的复习任务和自测方法。",
    "我最近连续三天没有完成任务，应该怎样调整今天的学习量，避免计划再次超时？",
    "请根据我最近的错题表现，告诉我今天更适合复习旧知识还是学习新章节，并说明理由。",
    "我看完一节课但记不住，请给我一个适合零基础学习者的主动回忆练习流程。",
    "请把我今天要学习的内容拆成几个明确的小步骤，每一步都给出完成标准。",
    "我晚上只有四十五分钟，想复习一个已经学过的章节，请安排具体的复习顺序。",
    "请根据我的学习进度，帮我判断当前最需要补的是概念理解、题目训练还是错题复盘。",
    "我对一个知识点似懂非懂，请给我一个先自测、再查资料、最后复述的学习方法。",
    "请为我设计一次不超过三十分钟的章节回顾，并包含一个简单的验收方式。",
    "我最近错题主要集中在概念混淆，请给我一个不依赖死记硬背的复习安排。",
    "请结合我的可用时间和近期完成情况，给出今天最稳妥的一项学习任务。",
    "我希望把昨天未完成的任务接着做，请说明应该保留哪些内容、删减哪些内容。",
    "请帮我把一段学习材料转成适合主动回忆的几个问题，不要直接给答案。",
    "我学习时容易在一个难点上花太久，请给我一个控制单点耗时的执行策略。",
    "请根据当前阶段给我一个短时复习方案，并说明完成后如何判断是否需要再次复习。",
]

NEGATIVE_PROMPTS = [
    "你好，简单介绍一下你能怎样帮助我学习。",
    "谢谢你，刚才的讲解很清楚。",
    "我暂时没有具体问题，先和我聊聊如何保持学习习惯吧。",
    "请告诉我今天是星期几，并给我一句鼓励。",
    "我想了解一下这个学习系统有哪些主要功能。",
]


def _row(index: int, group: str, prompt: str, task_type: str, scenario: str) -> dict:
    return {
        "case_id": f"EVO-D1-AB50-{index:03d}",
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
    }


def build() -> list[dict]:
    rows: list[dict] = []
    for index, (_, prompt) in enumerate(TARGET_TOPICS, start=1):
        scenario = (
            "expert_conflict_pair_resolvable"
            if index <= 10
            else "expert_conflict_pair_residual_after_repair"
            if index <= 20
            else "expert_conflict_pair_expanded_rerun"
        )
        rows.append(_row(index, "target_fault", prompt, "knowledge_explanation", scenario))
    for index, prompt in enumerate(NON_REGRESSION_PROMPTS, start=31):
        rows.append(_row(index, "non_regression_control", prompt, "general_learning_support", "normal_dialogue_no_conflict"))
    for index, prompt in enumerate(NEGATIVE_PROMPTS, start=46):
        rows.append(_row(index, "targeting_negative_control", prompt, "general_learning_support", "no_conflict_normal_resource"))
    return rows


def write(rows: list[dict]) -> None:
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    DATASET.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    with CSV_DATASET.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=[
            "case_id", "case_group", "prompt", "expected_task_type", "pair_order",
            "target_rule", "target_agent", "owner_step_id", "scenario",
        ])
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in writer.fieldnames})
    manifest = {
        "dataset_id": "semantic_conflict_closure_ab50_20260819",
        "version": "2026-08-19.1",
        "case_count": len(rows),
        "paired_model_run_count": len(rows) * 2,
        "case_groups": dict(Counter(row["case_group"] for row in rows)),
        "pair_orders": dict(Counter(row["pair_order"] for row in rows)),
        "scenarios": dict(Counter(row["scenario"] for row in rows)),
        "target_rule": RULE_ID,
        "target_agent": "expert_agent",
        "formal_environment_write_allowed": False,
        "effect_claim_boundary": "formal_ab_ready_no_effect_claim_before_execution",
        "files": {
            DATASET.name: hashlib.sha256(DATASET.read_bytes()).hexdigest(),
            CSV_DATASET.name: hashlib.sha256(CSV_DATASET.read_bytes()).hexdigest(),
        },
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    rows = build()
    write(rows)
    print(json.dumps({"case_count": len(rows), "manifest": str(MANIFEST)}, ensure_ascii=False))
