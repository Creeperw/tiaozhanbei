# -*- coding: utf-8 -*-
"""把 sample20 评测结果（含人工复核修正）并入 cmb_分类_0X.jsonl 并重算统计。

输入：outputs/cmb_sample20_w10_judged.jsonl（服务器双通道判定落盘文件）。

复核结论（2026-08-13 人工复核 2 个双通道冲突题）：
  - cmb_0003_护师资格考试：got=D 与标准答案一致，改判正确；
    讲解瑕疵：把单选当双选讲解（称 D、E 均正确），但不构成 C-FAITH 7 类幻觉。
  - cmb_0008_中药学（师）：got=D 与标准答案一致，改判正确；
    讲解瑕疵：多列了 B、C 两项"绝对化表述错误"，但核心判定（D 为错误项）正确。
  - cmb_0013_考研中医综合：多选漏选 D（答 A,B,C），维持错误。
  - cmb_0019_执业西药师：答对但 4 类幻觉，维持正确且有幻觉。

用法：python merge_sample20.py
"""
import json
import math
from collections import Counter
from pathlib import Path

OUT = Path(__file__).parent / "outputs"
SAMPLE20 = OUT / "cmb_sample20_w10_judged.jsonl"

BUCKET_FILES = {
    "01_争议题": "cmb_分类_01_争议题.jsonl",
    "02_正确且无幻觉": "cmb_分类_02_正确且无幻觉.jsonl",
    "03_正确且有幻觉": "cmb_分类_03_正确且有幻觉.jsonl",
    "04_错误且无幻觉": "cmb_分类_04_错误且无幻觉.jsonl",
    "05_错误且有幻觉": "cmb_分类_05_错误且有幻觉.jsonl",
}

# 人工复核修正：case_id -> (is_correct, halu, halu_types, review_note)
REVIEW = {
    "cmb_0003_护师资格考试": (
        True, False, [],
        "人工复核：got=D 与标准答案一致，改判正确（规则A判对、LLM评委因讲解把单选讲成双选而判错）。"
        "讲解称'D和E均正确'，医学上MAS确为肺气肿+肺不张并存，但单选标准答案为D（两肺肺气肿为典型征象），不构成幻觉。",
    ),
    "cmb_0008_中药学（师）": (
        True, False, [],
        "人工复核：got=D 与标准答案一致，改判正确。系统将B/C/D均判为'错误表述'，"
        "其中D（师承/确有专长人员未提考试注册前置条件）是命题设定的错误项且辨析正确，"
        "B/C的'必须执业医师证'绝对化批评亦有法规依据，不构成幻觉。",
    ),
    "cmb_0013_考研中医综合": (
        False, False, [],
        "多选漏选：期望 A,B,C,D，系统答 A,B,C，漏选 D 项。",
    ),
    "cmb_0019_执业西药师": (
        True, True, ["attribute_error", "entity_error", "fabricated_fact", "fake_reference"],
        "答对但讲解含 4 类幻觉（含编造文献 fake_reference），审核裁判放行。",
    ),
}


def wilson_ci(success: int, total: int, z: float = 1.96):
    if total <= 0:
        return (0.0, 0.0, 0.0)
    p = success / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (p, max(0.0, center - margin), min(1.0, center + margin))


def slim(r: dict, note: str = "") -> dict:
    meta = (r.get("input") or {}).get("meta") or {}
    content = ((r.get("resource") or {}).get("content")) or {}
    if isinstance(content, dict):
        reasoning = content.get("题目讲解", "")
    else:
        reasoning = str(content or "")
    row = {
        "case_id": r["case_id"],
        "exam_subject": meta.get("exam_subject"),
        "question_type": meta.get("question_type"),
        "question": (r.get("input") or {}).get("user_request", ""),
        "standard_answer": ",".join(r.get("want") or []),
        "got": ",".join(r.get("got") or []),
        "is_correct": r.get("is_correct"),
        "halu": r.get("halu"),
        "halu_types": r.get("halu_types") or [],
        "halu_reason": r.get("reason") if r.get("halu") else "",
        "expert_reasoning": reasoning,
    }
    if note:
        row["review_note"] = note
    return row


def main() -> None:
    # 1. 读 judged 记录（want/got/is_correct/halu 已由双通道判定落盘）
    recs = {}
    for line in SAMPLE20.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        recs[r["case_id"]] = r

    # 2. 应用人工复核修正
    new_rows = []       # 进 5 桶的行
    excluded_rows = []  # 未作答/未成功
    for cid, r in recs.items():
        judged = dict(r)
        if cid in REVIEW:
            is_correct, halu, halu_types, note = REVIEW[cid]
            judged.update({
                "is_correct": is_correct, "halu": halu,
                "halu_types": halu_types, "reason": note,
            })
            if judged.get("status") == "success":
                new_rows.append(slim(judged, note))
            else:
                excluded_rows.append(judged)
            continue
        if judged.get("status") != "success" or not judged.get("answered"):
            excluded_rows.append(judged)
            continue
        new_rows.append(slim(judged))

    # 3. 追加到对应桶文件（去重防重跑）
    buckets = {"01_争议题": [], "02_正确且无幻觉": [], "03_正确且有幻觉": [],
               "04_错误且无幻觉": [], "05_错误且有幻觉": []}
    for key, fname in BUCKET_FILES.items():
        path = OUT / fname
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        buckets[key] = rows

    def bucket_of(row: dict) -> str:
        return ("02_正确且无幻觉" if row["is_correct"] and not row["halu"]
                else "03_正确且有幻觉" if row["is_correct"] and row["halu"]
                else "04_错误且无幻觉" if not row["is_correct"] and not row["halu"]
                else "05_错误且有幻觉")

    added = 0
    for row in new_rows:
        key = bucket_of(row)
        existing = {r["case_id"] for r in buckets[key]}
        if row["case_id"] in existing:
            # 覆盖更新（保持可重跑）
            buckets[key] = [r for r in buckets[key] if r["case_id"] != row["case_id"]]
        buckets[key].append(row)
        added += 1
    print(f"并入 5 桶: {added} 题; 未作答排除: {len(excluded_rows)} 题")

    for key, rows in buckets.items():
        rows.sort(key=lambda x: x["case_id"])
        with open(OUT / BUCKET_FILES[key], "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 4. 重算统计
    # 550 集口径：549 = 27 争议 + 518 有效 + 4 未作答（未入 5 桶，需单独补计）
    PREV_DISPUTED, PREV_VALID, PREV_EXCLUDED = 27, 518, 4
    disputed = buckets["01_争议题"]
    answered = [r for k in ("02_正确且无幻觉", "03_正确且有幻觉", "04_错误且无幻觉", "05_错误且有幻觉") for r in buckets[k]]
    total_judged = len(answered) + len(excluded_rows) + PREV_DISPUTED + PREV_EXCLUDED
    excluded_all = len(excluded_rows) + PREV_EXCLUDED
    correct = [r for r in answered if r["is_correct"]]
    halu = [r for r in answered if r["halu"]]
    acc = wilson_ci(len(correct), len(answered))
    halu_rate = wilson_ci(len(halu), len(answered))
    types = Counter()
    for r in halu:
        for t in r.get("halu_types") or []:
            types[t] += 1

    md = [
        "# CMB 最终分组统计（550 集 + sample20 并入，争议题剔除口径）",
        "",
        "## 一、总体",
        f"- 判定用例总数: {total_judged}（550 集 549 + sample20 20）",
        f"- 争议题（人工标注）: {len(disputed)} 题 → 单独存放，不计入统计",
        f"- 未作答/待人工复核: {excluded_all} 题 → 不计入统计",
        f"- 有效作答: {len(answered)} 题",
        f"- 客观题准确率: {len(correct)}/{len(answered)} = {acc[0]*100:.1f}% (95% CI {acc[1]*100:.1f}%~{acc[2]*100:.1f}%)",
        f"- 模型级幻觉率: {len(halu)}/{len(answered)} = {halu_rate[0]*100:.1f}% (95% CI {halu_rate[1]*100:.1f}%~{halu_rate[2]*100:.1f}%)",
        "",
        "## 二、分组明细",
        "| 分组 | 数量 | 占有效作答比 |",
        "|---|---|---|",
    ]
    for k in ("02_正确且无幻觉", "03_正确且有幻觉", "04_错误且无幻觉", "05_错误且有幻觉"):
        n = len(buckets[k])
        md.append(f"| {k.split('_',1)[1]} | {n} | {n/len(answered)*100:.1f}% |")
    md.append(f"| （争议题另计） | {len(disputed)} | — |")
    md += [
        "",
        "## 三、幻觉分布",
        f"- 总幻觉 {len(halu)} 题 = 答对但幻觉 {sum(1 for r in halu if r['is_correct'])} + 答错且幻觉 {sum(1 for r in halu if not r['is_correct'])}",
        f"- 幻觉题中答对的占比: {sum(1 for r in halu if r['is_correct'])/len(halu)*100:.0f}%（幻觉与正确性相互独立，幻觉率须单独考核）",
        "",
        "## 四、错误分布",
        f"- 总错题 {len(answered)-len(correct)} 题 = 无幻觉错误 {sum(1 for r in answered if not r['is_correct'] and not r['halu'])} + 有幻觉错误 {sum(1 for r in answered if not r['is_correct'] and r['halu'])}",
        "",
        "## 五、幻觉类型分布（全部幻觉题）",
    ]
    for t, n in types.most_common():
        md.append(f"- {t}: {n} 题")
    md += [
        "",
        "## 六、sample20 并入说明",
        "- 20 题全部来自 CMB 题库新采样科目（EXA 外部检索生效后的基准组）。",
        "- 人工复核 2 个双通道冲突题后均改判正确（详见各记录 review_note 字段）。",
        "- cmb_0005_主管中药师（waiting_human_review）与 cmb_0014_考研西医综合（Judge 判未作答）共 2 题未作答，计入未作答排除。",
    ]
    (OUT / "cmb_分类统计.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("统计已更新:", OUT / "cmb_分类统计.md")
    print(f"  正确且无幻觉: {len(buckets['02_正确且无幻觉'])}")
    print(f"  正确且有幻觉: {len(buckets['03_正确且有幻觉'])}")
    print(f"  错误且无幻觉: {len(buckets['04_错误且无幻觉'])}")
    print(f"  错误且有幻觉: {len(buckets['05_错误且有幻觉'])}")
    print(f"  准确率: {len(correct)}/{len(answered)} = {acc[0]*100:.1f}%  幻觉率: {len(halu)}/{len(answered)} = {halu_rate[0]*100:.1f}%")


if __name__ == "__main__":
    main()
