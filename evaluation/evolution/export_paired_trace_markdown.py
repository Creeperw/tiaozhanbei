"""Export existing paired evaluation evidence without rerunning or rescoring it."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fenced(text: str, language: str = "") -> str:
    fence = "````"
    while fence in text:
        fence += "`"
    return f"{fence}{language}\n{text}\n{fence}\n"


def encoded(value: object) -> str:
    return fenced(json.dumps(value, ensure_ascii=False, indent=2), "json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Read once: a concurrent evaluation may keep advancing its checkpoint.
    checkpoint_bytes = args.checkpoint.read_bytes()
    dataset_bytes = args.dataset.read_bytes()
    run = json.loads(checkpoint_bytes)
    cases = [json.loads(line) for line in dataset_bytes.decode().splitlines() if line.strip()]
    selected_cases = cases[:10]
    receipts = {item["case_id"]: item for item in run["final"]["receipts"]}
    assert len(selected_cases) == 10
    selected = [receipts[case["case_id"]] for case in selected_cases]
    for receipt in selected:
        assert len(receipt["arms"]) == 2
        assert {arm["arm"] for arm in receipt["arms"]} == {"A", "B"}
    evidence = {"cases": selected_cases, "receipts": selected}
    evidence_digest = digest(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode())
    lines = [
        "# 正式配对测评十条轨迹：T001—T010（现存证据完整导出）\n",
        "> 本文不是新生成的模拟轨迹，也不是逐 token 全量日志。它按冻结样本顺序完整收录前十条已完成配对的现存输入、输出及判定；未保存的中间过程明确标注，不推测补造。\n",
        "## 1. 来源与阅读说明\n",
        f"- 导出时间（UTC）：{datetime.now(timezone.utc).isoformat()}",
        f"- 原测评轮次：`{run['run_id']}`",
        f"- 导出时检查点状态：`{run['status']}`；已保存完整配对 {len(receipts)} 条。此处是导出时快照，不代表最终测评结果。",
        "- 选样规则：冻结正式数据集前十条 T001—T010，不依据改善、退化或审核结果筛选。",
        "- A 组：未暴露候选进化规则；B 组：暴露同一候选规则。具体暴露事实见每组原始回执。",
        "- 材料甲/乙是输入证据，与实验 A/B 两组不是同一概念。",
        "- 最终正文按原文完整保留，包括可能的错误结论；仅供测评审阅，不作为医学事实或诊疗建议。",
        "- 本导出不发起模型请求、不重评分、不改变冻结规则、样本、检查点或生产数据。",
        f"- 检查点来源：`{args.checkpoint}`",
        f"- 检查点读取版本 SHA-256：`{digest(checkpoint_bytes)}`",
        f"- 数据集来源：`{args.dataset}`",
        f"- 数据集 SHA-256：`{digest(dataset_bytes)}`",
        f"- 所选 cases/receipts 的规范 JSON SHA-256：`{evidence_digest}`\n",
        "### 证据完整性的限制\n",
        "原执行器虽在执行中捕获模型调用轨迹，但配对检查点仅保存回执摘要和允许发布的最终正文。现存记录不含首轮草稿全文、逐轮审核意见全文、每轮修订版本、模型完整请求/响应、逐 token 日志和各步时间。因此不能据此还原每一句修改的原因。`actual_rerun_step_ids` 是去重后的节点集合，不是执行顺序。若 `release_allowed=false`，最终正文在回执中为 null，不能还原被拦截草稿。\n",
        "### 判定口径\n",
        "语义判定检查：完成问题、正确处理材料甲、正确处理材料乙、正确处理材料关系、不无依据消解冲突。五项全部为真才 acceptable。这里展示原判定，不另行复审。semantic_failure 应结合判定 status 阅读，无法判定不应冒充已确认的内容错误。首审通过与语义通过是两个不同概念。repair_attempt_count 是实际修复尝试数；repair_count 是回执中的计分成本，耗尽时可能含惩罚；不应混为实际次数。\n",
        "## 2. 共同候选规则与审核\n",
        f"- 候选摘要：`{run.get('candidate_digest', '')}`",
        "- 候选规则原文：\n",
        fenced(run.get("candidate", {}).get("strategy_text", "未保存")),
        "### 候选来源与适用范围\n",
        encoded({key: run.get("candidate", {}).get(key) for key in (
            "rule_id", "version", "source_case_ids", "target_agent", "target_step_id", "field_path", "task_type"
        )}),
        "### 审核与协议变更原记录\n",
        "以下审核不是独立人工审核；历史技术延期及已知结果可见性按原记录披露。\n",
        encoded({key: run.get(key) for key in (
            "review", "safety_replay", "protocol_amendments", "deployment_recoveries",
            "operator_full_blindness", "operator_blindness_note"
        )}),
        "## 3. 十条结果索引\n",
        "| 样本 | A 首审→末审 | B 首审→末审 | A/B 实际修复次数 | A/B 语义失败 |",
        "|---|---|---|---|---|",
    ]
    for receipt in selected:
        arms = {item["arm"]: item for item in receipt["arms"]}
        a, b = arms["A"], arms["B"]
        lines.append(f"| {receipt['case_id']} | {a['first_audit_decision']}→{a['final_audit_decision']} | {b['first_audit_decision']}→{b['final_audit_decision']} | {a.get('repair_attempt_count')}/{b.get('repair_attempt_count')} | {a.get('semantic_failure')}/{b.get('semantic_failure')} |")
    for index, (case, receipt) in enumerate(zip(selected_cases, selected), 1):
        lines += [
            f"\n## 案例 {index:02d}：{case['case_id']}\n",
            "### 任务与两份材料（完整冻结输入）\n",
            encoded(case),
            "### 配对执行与隔离证据\n",
            encoded({key: value for key, value in receipt.items() if key != "arms"}),
        ]
        for arm in receipt["arms"]:
            lines += [
                f"### {arm['arm']} 组轨迹\n",
                f"1. Expert 生成内容；候选规则暴露：`{arm.get('rule_exposed')}`。",
                f"2. 首次 Audit 结论：`{arm.get('first_audit_decision')}`。首轮审核详细意见未保存。",
                f"3. 实际修复尝试：`{arm.get('repair_attempt_count')}`；计分修复成本：`{arm.get('repair_count')}`；耗尽：`{arm.get('repair_exhausted')}`。",
                f"4. 涉及重跑节点（无序去重集合）：`{json.dumps(arm.get('actual_rerun_step_ids', []), ensure_ascii=False)}`。每轮草稿与修订差异未保存。",
                f"5. 最终 Audit 结论：`{arm.get('final_audit_decision')}`；执行状态：`{arm.get('execution_status')}`；允许发布：`{arm.get('release_allowed')}`。",
                f"6. 对最终候选正文进行语义判定，回执 semantic_failure：`{arm.get('semantic_failure')}`；完整判定见下。\n",
                "#### 最终学习者可见正文（不截断）\n",
                fenced(arm["learner_visible_body"]) if arm.get("learner_visible_body") is not None else "**原记录为 null：未发布，正文未持久化。**\n",
                "#### 原始组回执（除正文外的全部字段）\n",
                encoded({key: value for key, value in arm.items() if key != "learner_visible_body"}),
            ]
    lines += ["\n## 4. 导出核验\n", "- 10 个冻结输入、10 个配对回执、20 个组回执；所有已持久化非空正文原样纳入。", "- 缺失的审核细节和中间草稿未补造；没有因展示效果筛样或重新执行。", "- 本文十条样本不代表最终 100 条总体指标。\n"]
    document = "\n".join(lines)
    bodies = [arm["learner_visible_body"] for row in selected for arm in row["arms"] if arm.get("learner_visible_body") is not None]
    assert all(body in document for body in bodies)
    assert document.count("\n## 案例 ") == 10
    assert document.count("#### 原始组回执") == 20
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(document)
    assert args.output.read_text(encoding="utf-8") == document
    print(json.dumps({"output": str(args.output), "cases": 10, "arms": 20, "non_null_bodies": len(bodies), "bytes": len(document.encode()), "sha256": digest(document.encode())}, ensure_ascii=False))


if __name__ == "__main__":
    main()