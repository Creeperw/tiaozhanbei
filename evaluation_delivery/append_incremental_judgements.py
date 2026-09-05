"""Append newly judged unique CMB records to an existing merged judged batch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from stats import wilson_ci


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--incremental", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    base = load_jsonl(args.base)
    incremental = load_jsonl(args.incremental)
    base_ids = {row["case_id"] for row in base}
    if base_ids & {row["case_id"] for row in incremental}:
        raise RuntimeError("incremental records overlap the merged base")
    merged = [*base, *incremental]
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged),
        encoding="utf-8",
    )

    done = [row for row in merged if row.get("status") == "success"]
    answered = [row for row in done if row.get("answered")]
    correct = [row for row in answered if row.get("is_correct")]
    wrong = len(answered) - len(correct)
    waiting = sum(row.get("status") == "waiting_human_review" for row in merged)
    failed = sum(row.get("status") == "failed" for row in merged)
    judge_errors = sum(bool(row.get("judge_error")) for row in merged)
    conflicts = sum(bool(row.get("conflict")) for row in merged)
    accuracy, low, high = wilson_ci(len(correct), len(answered))

    report = f"""# CMB 验证集最终合并准确率报告（规则A + LLM Judge B）

## 合并口径

- 原始批次与37道重跑题已按 `case_id` 替换合并
- 后续新增判定记录：{len(incremental)}题
- 当前总记录：{len(merged)}题

## 执行概况

- 成功：{len(done)}
- 等待人工复核：{waiting}
- 运行失败：{failed}
- 有效作答：{len(answered)}

## 最终准确率

- 答对：{len(correct)}/{len(answered)} = {accuracy:.1%}
- 95% CI：{low:.1%}～{high:.1%}
- 若以全部{len(merged)}题为分母：{len(correct)}/{len(merged)} = {len(correct) / len(merged):.1%}

## 错误率

- 答错：{wrong}/{len(answered)} = {wrong / len(answered):.1%}

## 判定通道

- 规则A + LLM Judge B（每题2次Judge调用）
- Judge prompt：v1.0
- Judge失败回退：{judge_errors}
- 双通道冲突：{conflicts}
"""
    args.report.write_text(report, encoding="utf-8")
    print(
        f"total={len(merged)} success={len(done)} waiting={waiting} failed={failed} "
        f"answered={len(answered)} correct={len(correct)} accuracy={accuracy:.4%} "
        f"judge_errors={judge_errors} conflicts={conflicts}"
    )


if __name__ == "__main__":
    main()
