# -*- coding: utf-8 -*-
"""组装幻觉率评测极简交付包。

只交付：528 条总评测数据集（含判定与评价输出）+ 测试方案 + 字段说明 + 分类统计 + README。
其他任何数据（题库、判定记录、复现代码等）不打包。
输出：deliverables/hallucination-evaluation-20260813/

用法：python build_delivery_package.py
"""
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # tiaozhanbei/
EVAL = ROOT / "evaluation_delivery"
OUT = EVAL / "outputs"
PKG = ROOT / "deliverables" / "hallucination-evaluation-20260813"

BUCKETS = [
    (OUT / "cmb_分类_02_正确且无幻觉.jsonl", "正确且无幻觉"),
    (OUT / "cmb_分类_03_正确且有幻觉.jsonl", "正确且有幻觉"),
    (OUT / "cmb_分类_04_错误且无幻觉.jsonl", "错误且无幻觉"),
    (OUT / "cmb_分类_05_错误且有幻觉.jsonl", "错误且有幻觉"),
]
DISPUTE_BUCKET = OUT / "cmb_分类_01_争议题.jsonl"


def load_dispute_ids() -> set[str]:
    ids = set()
    for line in DISPUTE_BUCKET.read_text(encoding="utf-8").splitlines():
        line = line.strip().lstrip("/")
        if not line:
            continue
        try:
            ids.add(json.loads(line)["case_id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return ids


def main() -> None:
    if PKG.exists():
        shutil.rmtree(PKG)
    PKG.mkdir(parents=True)

    dispute_ids = load_dispute_ids()

    # 合并四个桶 → 528 条总评测数据集，按 case_id 排序
    total = []
    for src, bucket in BUCKETS:
        for line in src.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            rec["bucket"] = bucket
            total.append(rec)
    total.sort(key=lambda r: r.get("case_id", ""))

    # 硬校验：528 条、case_id 唯一、零争议题
    n = len(total)
    assert n == 528, f"总评测数据集应为 528 条，实际 {n}"
    assert len({r["case_id"] for r in total}) == n, "case_id 存在重复"
    leak = [r["case_id"] for r in total if r["case_id"] in dispute_ids]
    assert not leak, f"总评测数据集中混入争议题: {leak}"

    (PKG / "CMB总评测数据集.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in total) + "\n",
        encoding="utf-8",
    )

    # 统计
    bcnt = Counter(r["bucket"] for r in total)
    correct = sum(1 for r in total if r["is_correct"])
    halu = sum(1 for r in total if r["halu"])
    acc = correct / n * 100
    halu_rate = halu / n * 100

    # README
    readme = README_TEMPLATE.format(
        date=datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d"),
        n=n,
        dispute=len(dispute_ids),
        correct=correct,
        halu=halu,
        acc=acc,
        halu_rate=halu_rate,
        b2=bcnt.get("正确且无幻觉", 0),
        b3=bcnt.get("正确且有幻觉", 0),
        b4=bcnt.get("错误且无幻觉", 0),
        b5=bcnt.get("错误且有幻觉", 0),
    )
    (PKG / "README.md").write_text(readme, encoding="utf-8")

    # 字段说明与分类统计（评价输出汇总）
    shutil.copy2(OUT / "字段说明.md", PKG / "字段说明.md")
    shutil.copy2(OUT / "cmb_分类统计.md", PKG / "cmb_分类统计.md")

    # 测试方案
    shutil.copy2(ROOT / "docs" / "01_幻觉率测试方案_修订版.md", PKG / "测试方案_幻觉率.md")
    shutil.copy2(OUT / "判定口径与人工复审流程.md", PKG / "测试方案_判定口径与人工复审流程.md")

    # 校验
    files = sorted(str(p.relative_to(PKG)).replace("\\", "/") for p in PKG.rglob("*") if p.is_file())
    sha_lines = []
    for rel in files:
        h = hashlib.sha256((PKG / rel).read_bytes()).hexdigest()
        sha_lines.append(f"{h}  {rel}")
    (PKG / "SHA256SUMS").write_text("\n".join(sha_lines) + "\n", encoding="utf-8")

    print(f"交付包已生成: {PKG}")
    print(f"文件数: {len(files)}")
    for rel in files:
        print(f"  {rel}")
    print(f"硬校验通过: {n} 条 | case_id 唯一 | 零争议题 | 答对 {correct} 幻觉 {halu}")


README_TEMPLATE = """# 幻觉率评测交付包（极简版）

交付日期：{date}

## 本包内容

| 文件 | 说明 |
|---|---|
| `CMB总评测数据集.jsonl` | **{n} 条有效作答测试数据**（已剔除争议题与未作答题），每条含题干、标准答案、系统回答、判定结果、幻觉类型与专家讲解（评价输出） |
| `测试方案_幻觉率.md` | 幻觉率测试方案（评测目的、双通道判定、幻觉定义） |
| `测试方案_判定口径与人工复审流程.md` | 判定口径与三轮人工复审流程 |
| `字段说明.md` | 数据集字段字典 |
| `cmb_分类统计.md` | 最终指标与分布汇总 |
| `SHA256SUMS` | 文件校验值 |

## 数据口径

- 评测共执行 570 次（550 集 550 + 新采样 20），判定记录去重合并后 569 行（1 次重复执行合并）
- 剔除争议题 {dispute} 条 → 534
- 剔除未作答 6 条 → **有效作答 {n} 条（本包数据）**
- 本包数据经硬校验：case_id 唯一、与争议题清单零交集

## 最终指标（{n} 条有效作答）

- 客观题准确率：{correct}/{n} = {acc:.1f}%（95% CI 87.3%~92.4%）
- 模型级幻觉率：{halu}/{n} = {halu_rate:.1f}%（95% CI 2.6%~6.0%，达标 ≤5%）

## 分组分布

| 分组 | 数量 |
|---|---|
| 正确且无幻觉 | {b2} |
| 正确且有幻觉 | {b3} |
| 错误且无幻觉 | {b4} |
| 错误且有幻觉 | {b5} |

## 校验

```bash
sha256sum -c SHA256SUMS
```
"""


if __name__ == "__main__":
    main()
