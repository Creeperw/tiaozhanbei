# -*- coding: utf-8 -*-
"""人工审核后错题分类与统计。

读取 cmb_wrong65_精简.jsonl（// 前缀 = 人工标注的争议题），
结合 v1.2 最终判定数据（含 halu 全票口径），输出：
  1. cmb_争议题.jsonl            —— 争议题单独放好（不计入总题目数）
  2. cmb_确认错题_分类.jsonl      —— 确认错题，按错误类型分类
  3. cmb_审核统计汇总.md          —— 重算后的正确率/幻觉率

用法：python review_classify.py
"""
import json
import math
import re
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


def classify_error(r: dict) -> str:
    """确认错题的分类：漏选 / 幻觉 / 知识错误。"""
    halu = bool(r.get("halu"))
    kind = ((r.get("input", {}).get("meta") or {}).get("kind")) or "unknown"
    halu_types = r.get("halu_types") or []
    if halu:
        return "幻觉：" + "/".join(halu_types) + ("（多选）" if kind == "multi" else "（单选）")
    if kind == "multi":
        return "多选漏选/多选（无幻觉）"
    return "单选知识错误（无幻觉）"


def main() -> None:
    # 1. 读人工审核标注（// = 争议）
    disputed, confirmed = [], []
    for line in WRONG65.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        marked = s.startswith("//")
        obj = json.loads(s[2:] if marked else s)
        (disputed if marked else confirmed).append(obj)
    print(f"审核状态：争议 {len(disputed)} 题 | 确认错题 {len(confirmed)} 题")

    # 2. 读 v1.2 最终判定
    judged = {}
    for line in JUDGED.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        judged[r["case_id"]] = r

    # 3. 争议题单独落盘
    disputed_ids = {d["case_id"] for d in disputed}
    with open(OUT / "cmb_争议题.jsonl", "w", encoding="utf-8") as f:
        for d in disputed:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    # 4. 确认错题分类落盘
    classified = []
    for c in confirmed:
        j = judged.get(c["case_id"], {})
        error_type = classify_error(j)
        row = {
            **c,
            "error_type": error_type,
            "got": j.get("got"),
            "want": j.get("want"),
            "question_type": (j.get("input", {}).get("meta") or {}).get("question_type"),
            "halu": j.get("halu"),
            "halu_types": j.get("halu_types") or [],
        }
        classified.append(row)
    classified.sort(key=lambda r: r["error_type"])
    with open(OUT / "cmb_确认错题_分类.jsonl", "w", encoding="utf-8") as f:
        for row in classified:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 5. 分类分布
    from collections import Counter
    dist = Counter(r["error_type"] for r in classified)

    # 6. 重算正确率/幻觉率（争议题剔除）
    total = len(judged)
    success = [r for r in judged.values() if r.get("status") == "success"]
    answered = [r for r in success if r.get("answered")]
    correct = [r for r in answered if r.get("is_correct")]
    halu_all = [r for r in answered if r.get("halu")]
    halu_in_disputed = [r for r in halu_all if r["case_id"] in disputed_ids]
    wrong_in_disputed = [r for r in answered if not r.get("is_correct") and r["case_id"] in disputed_ids]

    answered2 = [r for r in answered if r["case_id"] not in disputed_ids]
    correct2 = [r for r in answered2 if r.get("is_correct")]
    halu2 = [r for r in answered2 if r.get("halu")]

    acc_p, acc_lo, acc_hi = wilson_ci(len(correct2), len(answered2))
    halu_p, halu_lo, halu_hi = wilson_ci(len(halu2), len(answered2))

    # 6.5 幻觉在答对/答错中的分布（全部有效作答，剔除争议前）
    halu_in_correct = [r for r in answered if r.get("halu") and r.get("is_correct")]
    halu_in_wrong = [r for r in answered if r.get("halu") and not r.get("is_correct")]

    # 6.6 旧错题清单之外的新错题（v1.2 重判翻转，人工未审核）
    wrong7_ids = set()
    p7 = OUT / "cmb_sample50_wrong7_精简.jsonl"
    if p7.exists():
        for line in p7.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            wrong7_ids.add(json.loads(line)["case_id"])
    old_wrong_ids = {d["case_id"] for d in disputed} | {c["case_id"] for c in confirmed} | wrong7_ids
    wrong_now = {r["case_id"] for r in answered if not r.get("is_correct")}
    new_wrong = sorted(wrong_now - old_wrong_ids)

    # 7. 汇总报告
    lines = [
        "# CMB 错题人工审核统计（争议题剔除口径）",
        "",
        f"## 审核进度",
        f"- 人工审核错题总数: {len(disputed) + len(confirmed)}",
        f"- 争议题（// 标注，单独存放，不计入总题目数）: {len(disputed)}",
        f"- 确认错题: {len(confirmed)}",
        "",
        f"## 重算指标（剔除 {len(disputed)} 道争议题）",
        f"- 总判定用例: {total}（其中成功 {len(success)}）",
        f"- 有效作答（剔除争议后）: {len(answered2)}",
        f"- 客观题准确率: {len(correct2)}/{len(answered2)} = {acc_p:.1%} (95% CI {acc_lo:.1%}~{acc_hi:.1%})",
        f"- 模型级幻觉率: {len(halu2)}/{len(answered2)} = {halu_p:.1%} (95% CI {halu_lo:.1%}~{halu_hi:.1%})",
        f"- 剔除的争议题中判幻觉: {len(halu_in_disputed)} 题",
        "",
        f"## 幻觉分布（剔除争议前，全部 {len(answered)} 有效作答）",
        f"- 总幻觉 {len(halu_in_correct) + len(halu_in_wrong)} 题",
        f"- 其中答对但讲解有幻觉: {len(halu_in_correct)} 题（{len(halu_in_correct)/max(1,len(halu_in_correct)+len(halu_in_wrong)):.0%}）",
        f"- 其中答错且讲解有幻觉: {len(halu_in_wrong)} 题",
        "",
        "## 确认错题分类分布",
    ]
    for k, v in dist.most_common():
        lines.append(f"- {k}: {v} 题")
    lines += [
        "",
        f"## 人工审核范围外的新错题（{len(new_wrong)} 道，v1.2 重判翻转，建议补审）",
        "| case_id | 题型 | 系统给出 | 标准答案 | 判定幻觉 | 备注 |",
        "|---|---|---|---|---|---|",
    ]
    for cid in new_wrong:
        r = judged[cid]
        got = ",".join(r.get("got") or []) or "?"
        want = ",".join(r.get("want") or []) or "?"
        halu_mark = "是（" + "/".join(r.get("halu_types") or []) + "）" if r.get("halu") else "否"
        note = "双通道冲突（字母判对但LLM判错）" if r.get("conflict") else ""
        lines.append(f"| {cid} | {(r['input'].get('meta') or {}).get('question_type')} | {got} | {want} | {halu_mark} | {note} |")
    lines += [
        "",
        "## 争议题清单（不计入统计）",
        "| case_id | 标准答案 | 系统给出 | 判定幻觉 |",
        "|---|---|---|---|",
    ]
    for d in disputed:
        j = judged.get(d["case_id"], {})
        got = ",".join(j.get("got") or []) or "?"
        want = ",".join(j.get("want") or []) or d.get("standard_answer", "?")
        halu_mark = "是（" + "/".join(j.get("halu_types") or []) + "）" if j.get("halu") else "否"
        lines.append(f"| {d['case_id']} | {want} | {got} | {halu_mark} |")
    (OUT / "cmb_审核统计汇总.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"\n=== 重算结果 ===")
    print(f"准确率: {len(correct2)}/{len(answered2)} = {acc_p:.1%} (CI {acc_lo:.1%}~{acc_hi:.1%})")
    print(f"幻觉率: {len(halu2)}/{len(answered2)} = {halu_p:.1%} (CI {halu_lo:.1%}~{halu_hi:.1%})")
    print(f"争议题 {len(disputed)} 题（其中判幻觉 {len(halu_in_disputed)} 题）")
    print("分类分布:", dict(dist))


if __name__ == "__main__":
    main()
