# -*- coding: utf-8 -*-
"""把分类桶文件中被人工 `//` 标记的行移入争议桶，并重算统计。

用户复审口径：在 cmb_分类_0X.jsonl 中，将认定为争议的题目行首加 `//` 注释。
本脚本扫描 5 个桶文件：
  1. 解析 `//` 行 → 移入 01_争议题 桶（标记 reviewed=True）
  2. 其余行保持原桶
  3. 重写 5 个桶文件 + 重算 cmb_分类统计.md

可重跑（01 桶内的行不再带 `//` 前缀，不会重复处理）。

用法：python reclassify_disputed.py
"""
import json
import math
from collections import Counter
from pathlib import Path

OUT = Path(__file__).parent / "outputs"

BUCKET_FILES = {
    "01_争议题": "cmb_分类_01_争议题.jsonl",
    "02_正确且无幻觉": "cmb_分类_02_正确且无幻觉.jsonl",
    "03_正确且有幻觉": "cmb_分类_03_正确且有幻觉.jsonl",
    "04_错误且无幻觉": "cmb_分类_04_错误且无幻觉.jsonl",
    "05_错误且有幻觉": "cmb_分类_05_错误且有幻觉.jsonl",
}

# 未作答题数（550 集 4 题 + sample20 2 题，均未入 5 桶）
TOTAL_EXCLUDED = 6


def wilson_ci(success: int, total: int, z: float = 1.96):
    if total <= 0:
        return (0.0, 0.0, 0.0)
    p = success / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (p, max(0.0, center - margin), min(1.0, center + margin))


def main() -> None:
    # 1. 读入各桶，拆出 `//` 争议行
    buckets: dict[str, list[dict]] = {}
    new_disputed: list[dict] = []
    moved = 0
    for key, fname in BUCKET_FILES.items():
        rows = []
        for line in (OUT / fname).read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s:
                continue
            marked = s.startswith("//")
            body = s[2:].strip() if marked else s
            try:
                r = json.loads(body)
            except json.JSONDecodeError:
                # 非 JSON 行原样保留
                rows.append({"raw": line})
                continue
            if marked:
                if key != "01_争议题":
                    r["reviewed"] = True
                    r["dispute_source_bucket"] = key
                    new_disputed.append(r)
                    moved += 1
                else:
                    rows.append(r)
            else:
                rows.append(r)
        buckets[key] = rows

    # 2. 新争议并入 01 桶并排序
    buckets["01_争议题"] = [r for r in buckets["01_争议题"] if isinstance(r, dict)]
    existing = {r["case_id"] for r in buckets["01_争议题"]}
    for r in new_disputed:
        if r["case_id"] in existing:
            buckets["01_争议题"] = [x for x in buckets["01_争议题"] if x["case_id"] != r["case_id"]]
        buckets["01_争议题"].append(r)

    print(f"新移入争议桶: {moved} 条")

    # 3. 写回各桶
    for key, fname in BUCKET_FILES.items():
        rows = buckets[key]
        rows.sort(key=lambda x: x.get("case_id", ""))
        with open(OUT / fname, "w", encoding="utf-8") as f:
            for row in rows:
                if "raw" in row:
                    f.write(row["raw"] + "\n")
                else:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 4. 重算统计
    disputed = buckets["01_争议题"]
    answered = [r for k in ("02_正确且无幻觉", "03_正确且有幻觉", "04_错误且无幻觉", "05_错误且有幻觉")
                for r in buckets[k] if isinstance(r, dict)]
    total_judged = len(answered) + len(disputed) + TOTAL_EXCLUDED
    correct = [r for r in answered if r.get("is_correct")]
    halu = [r for r in answered if r.get("halu")]
    acc = wilson_ci(len(correct), len(answered))
    halu_rate = wilson_ci(len(halu), len(answered))
    types = Counter()
    for r in halu:
        for t in r.get("halu_types") or []:
            types[t] += 1

    md = [
        "# CMB 最终分组统计（人工复审口径，争议题剔除）",
        "",
        "## 一、总体",
        f"- 判定用例总数: {total_judged}（550 集 549 + sample20 20）",
        f"- 争议题（人工标注）: {len(disputed)} 题 → 单独存放，不计入统计",
        f"- 未作答/待人工复核: {TOTAL_EXCLUDED} 题 → 不计入统计",
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
        md.append(f"| {k.split('_', 1)[1]} | {n} | {n/len(answered)*100:.1f}% |")
    md.append(f"| （争议题另计） | {len(disputed)} | — |")
    md += [
        "",
        "## 三、幻觉分布",
        f"- 总幻觉 {len(halu)} 题 = 答对但幻觉 {sum(1 for r in halu if r['is_correct'])} + 答错且幻觉 {sum(1 for r in halu if not r['is_correct'])}",
        f"- 幻觉题中答对的占比: {sum(1 for r in halu if r['is_correct'])/len(halu)*100:.0f}%（幻觉与正确性相互独立，幻觉率须单独考核）" if halu else "- 无幻觉题",
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
        "## 六、复审口径变更记录",
        "- 人工复审新增争议标记 8 条（原 03 桶 5 条、原 05 桶 3 条），已移入争议桶：",
        "  - 03→争议: cmb_0019_执业西药师、cmb_0031_放射学技术（师）、cmb_0180_考研西医综合、cmb_0258_超声科、cmb_0486_皮肤科",
        "  - 05→争议: cmb_0049_组织学与胚胎学、cmb_0473_中国医学史、cmb_0485_考研西医综合",
        "- 550 集人工标注争议 27 条 + sample20 复核改判 0 条，累计争议 35 条。",
    ]
    (OUT / "cmb_分类统计.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("统计已更新:", OUT / "cmb_分类统计.md")
    print(f"  争议题: {len(disputed)}")
    print(f"  正确且无幻觉: {len(buckets['02_正确且无幻觉'])}")
    print(f"  正确且有幻觉: {len(buckets['03_正确且有幻觉'])}")
    print(f"  错误且无幻觉: {len(buckets['04_错误且无幻觉'])}")
    print(f"  错误且有幻觉: {len(buckets['05_错误且有幻觉'])}")
    print(f"  准确率: {len(correct)}/{len(answered)} = {acc[0]*100:.1f}% (95% CI {acc[1]*100:.1f}%~{acc[2]*100:.1f}%)")
    print(f"  幻觉率: {len(halu)}/{len(answered)} = {halu_rate[0]*100:.1f}% (95% CI {halu_rate[1]*100:.1f}%~{halu_rate[2]*100:.1f}%)")


if __name__ == "__main__":
    main()
