# -*- coding: utf-8 -*-
"""最终分组整理：把全部判定记录按人工审核口径分成 5 类。

  1. 争议题（人工 // 标注，不计入统计）
  2. 正确且无幻觉
  3. 正确但有幻觉
  4. 错误但无幻觉
  5. 错误且有幻觉

输出 outputs/ 下 5 个 jsonl + 一份统计 md。可重跑。
用法：python organize_buckets.py
"""
import json
import math
from collections import Counter
from pathlib import Path

OUT = Path(__file__).parent / "outputs"
WRONG65 = OUT / "cmb_wrong65_精简.jsonl"
JUDGED = OUT / "cmb_550_rejudged_v12_final.jsonl"


def wilson_ci(success: int, total: int, z: float = 1.96):
    if total <= 0:
        return (0.0, 0.0, 0.0)
    p = success / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (p, max(0.0, center - margin), min(1.0, center + margin))


def slim(r: dict) -> dict:
    """精简输出字段：保留人工审核与答辩所需信息。"""
    meta = r["input"].get("meta") or {}
    content = (r.get("resource") or {}).get("content") or {}
    return {
        "case_id": r["case_id"],
        "exam_subject": meta.get("exam_subject"),
        "question_type": meta.get("question_type"),
        "question": r["input"].get("user_request", ""),
        "standard_answer": ",".join(r.get("want") or []),
        "got": ",".join(r.get("got") or []),
        "is_correct": r.get("is_correct"),
        "halu": r.get("halu"),
        "halu_types": r.get("halu_types") or [],
        "halu_reason": r.get("reason") if r.get("halu") else "",
        "expert_reasoning": content.get("题目讲解", ""),
    }


def main() -> None:
    # 1. 人工标注的争议题
    disputed_ids = set()
    reviewed_ids = set()  # 出现在 65 题清单里的都算"已人工审核"
    for line in WRONG65.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        marked = s.startswith("//")
        cid = json.loads(s[2:] if marked else s)["case_id"]
        reviewed_ids.add(cid)
        if marked:
            disputed_ids.add(cid)

    # 2. 全部判定记录
    judged = {}
    for line in JUDGED.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        judged[r["case_id"]] = r

    # 3. 分组
    buckets = {
        "争议题（人工标注，不计入统计）": [],
        "正确且无幻觉": [],
        "正确且有幻觉": [],
        "错误且无幻觉": [],
        "错误且有幻觉": [],
    }
    excluded = []  # 未作答/待人工复核（也不计入）
    for r in judged.values():
        if r["case_id"] in disputed_ids:
            row = slim(r)
            row["reviewed"] = True
            buckets["争议题（人工标注，不计入统计）"].append(row)
            continue
        if r.get("status") != "success" or not r.get("answered"):
            excluded.append(r)
            continue
        row = slim(r)
        row["reviewed"] = r["case_id"] in reviewed_ids
        key = ("正确" if r.get("is_correct") else "错误") + ("且有幻觉" if r.get("halu") else "且无幻觉")
        buckets[key].append(row)

    # 4. 落盘（jsonl 一行一条，ensure_ascii=False 可读性好）
    name_map = {
        "争议题（人工标注，不计入统计）": "01_争议题",
        "正确且无幻觉": "02_正确且无幻觉",
        "正确且有幻觉": "03_正确且有幻觉",
        "错误且无幻觉": "04_错误且无幻觉",
        "错误且有幻觉": "05_错误且有幻觉",
    }
    for label, rows in buckets.items():
        rows.sort(key=lambda x: x["case_id"])
        with open(OUT / f"cmb_分类_{name_map[label]}.jsonl", "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 5. 统计
    answered_all = [
        r for k, rows in buckets.items()
        if k != "争议题（人工标注，不计入统计）"
        for r in rows
    ]
    total = len(answered_all)
    correct = buckets["正确且无幻觉"] + buckets["正确且有幻觉"]
    halu = buckets["正确且有幻觉"] + buckets["错误且有幻觉"]
    n = {k: len(v) for k, v in buckets.items()}
    acc_p, acc_lo, acc_hi = wilson_ci(len(correct), total)
    halu_p, halu_lo, halu_hi = wilson_ci(len(halu), total)

    lines = [
        "# CMB 550 最终分组统计（争议题剔除口径）",
        "",
        f"## 一、总体",
        f"- 判定用例总数: {len(judged)}",
        f"- 争议题（人工标注）: {n['争议题（人工标注，不计入统计）']} 题 → 单独存放，不计入统计",
        f"- 未作答/待人工复核: {len(excluded)} 题 → 不计入统计",
        f"- 有效作答: {total} 题",
        f"- 客观题准确率: {len(correct)}/{total} = {acc_p:.1%} (95% CI {acc_lo:.1%}~{acc_hi:.1%})",
        f"- 模型级幻觉率: {len(halu)}/{total} = {halu_p:.1%} (95% CI {halu_lo:.1%}~{halu_hi:.1%})",
        "",
        "## 二、分组明细",
        "| 分组 | 数量 | 占有效作答比 |",
        "|---|---|---|",
        f"| ✅ 正确且无幻觉 | {n['正确且无幻觉']} | {n['正确且无幻觉']/total:.1%} |",
        f"| ⚠️ 正确且有幻觉 | {n['正确且有幻觉']} | {n['正确且有幻觉']/total:.1%} |",
        f"| ❌ 错误且无幻觉 | {n['错误且无幻觉']} | {n['错误且无幻觉']/total:.1%} |",
        f"| 💀 错误且有幻觉 | {n['错误且有幻觉']} | {n['错误且有幻觉']/total:.1%} |",
        f"| （争议题另计） | {n['争议题（人工标注，不计入统计）']} | — |",
        "",
        "## 三、幻觉分布",
        f"- 总幻觉 {len(halu)} 题 = 答对但幻觉 {n['正确且有幻觉']} + 答错且幻觉 {n['错误且有幻觉']}",
        f"- 幻觉题中答对的占比: {n['正确且有幻觉']/max(1,len(halu)):.0%}（说明幻觉与正确性相互独立，幻觉率必须单独考核）",
        "",
        "## 四、错误分布",
        f"- 总错题 {total-len(correct)} 题 = 无幻觉错误 {n['错误且无幻觉']} + 有幻觉错误 {n['错误且有幻觉']}",
        "",
        "## 五、幻觉类型分布（全部幻觉题）",
    ]
    type_counter = Counter()
    for r in buckets["正确且有幻觉"] + buckets["错误且有幻觉"]:
        for t in r["halu_types"]:
            type_counter[t] += 1
    for t, c in type_counter.most_common():
        lines.append(f"- {t}: {c} 题")
    (OUT / "cmb_分类统计.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"=== 分组结果 ===")
    print(f"争议题: {n['争议题（人工标注，不计入统计）']} | 未作答/待复核: {len(excluded)}")
    print(f"正确且无幻觉: {n['正确且无幻觉']}")
    print(f"正确且有幻觉: {n['正确且有幻觉']}")
    print(f"错误且无幻觉: {n['错误且无幻觉']}")
    print(f"错误且有幻觉: {n['错误且有幻觉']}")
    print(f"\n准确率: {len(correct)}/{total} = {acc_p:.1%} (CI {acc_lo:.1%}~{acc_hi:.1%})")
    print(f"幻觉率: {len(halu)}/{total} = {halu_p:.1%} (CI {halu_lo:.1%}~{halu_hi:.1%})")


if __name__ == "__main__":
    main()
