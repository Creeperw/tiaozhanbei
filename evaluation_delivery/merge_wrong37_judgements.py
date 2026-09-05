"""Merge the rejudged CMB wrong-37 subset back into the original judged batch."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from cmb_adapter import judge_cmb
from stats import wilson_ci


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--rerun", type=Path, required=True)
    parser.add_argument("--rerun-failures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    original = load_jsonl(args.original)
    rerun = load_jsonl(args.rerun)
    failure_text = args.rerun_failures.read_text(encoding="utf-8")
    wrong_ids = set(re.findall(r"\| (cmb_[^|]+?) \|", failure_text))
    if len(wrong_ids) != 20:
        raise RuntimeError(f"expected 20 rejudged wrong IDs, got {len(wrong_ids)}")

    rerun_ids = {row["case_id"] for row in rerun}
    original_ids = {row["case_id"] for row in original}
    if len(original) != 197 or len(rerun) != 37 or not rerun_ids <= original_ids:
        raise RuntimeError("unexpected original/rerun cardinality or case IDs")

    for row in rerun:
        rule = judge_cmb(row)
        success = row.get("status") == "success"
        judge_correct = success and row["case_id"] not in wrong_ids
        row.update(
            {
                **rule,
                "answered": success,
                "is_correct": judge_correct,
                "channel_a_correct": rule["is_correct"],
                "channel_a_answered": rule["answered"],
                "channel_b_correct": judge_correct if success else False,
                "channel_b_votes": 2 if success else 0,
                "conflict": success and rule["answered"] and rule["is_correct"] != judge_correct,
                "judge_error": None,
            }
        )

    replacements = {row["case_id"]: row for row in rerun}
    merged = [replacements.get(row["case_id"], row) for row in original]
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged),
        encoding="utf-8",
    )

    done = [row for row in merged if row.get("status") == "success"]
    answered = [row for row in done if row.get("answered")]
    correct = [row for row in answered if row.get("is_correct")]
    wrong = len(answered) - len(correct)
    conflicts = sum(bool(row.get("conflict")) for row in merged)
    judge_errors = sum(bool(row.get("judge_error")) for row in merged)
    accuracy, low, high = wilson_ci(len(correct), len(answered))

    report = f"""# CMB 验证集最终合并准确率报告（规则A + LLM Judge B）

## 合并口径

- 原始判定记录：197题
- 按 `case_id` 替换37道重跑题，未重复追加
- 重跑37题 Judge失败回退：0
- 重跑37题双通道冲突：1

## 执行概况

- 总用例：{len(merged)}
- 成功：{len(done)}
- 等待人工复核：{len(merged) - len(done)}
- 有效作答：{len(answered)}

## 最终准确率

- 答对：{len(correct)}/{len(answered)} = {accuracy:.1%}
- 95% CI：{low:.1%}～{high:.1%}
- 若以全部197题为分母：{len(correct)}/197 = {len(correct) / 197:.1%}

## 错误率

- 答错：{wrong}/{len(answered)} = {wrong / len(answered):.1%}

## 判定通道

- 原始记录与37题重跑均使用规则A + LLM Judge B
- Judge prompt：v1.0
- Judge失败回退：{judge_errors}
- 双通道冲突：{conflicts}
"""
    args.report.write_text(report, encoding="utf-8")
    print(
        f"merged={len(merged)} success={len(done)} answered={len(answered)} "
        f"correct={len(correct)} accuracy={accuracy:.4%} "
        f"judge_errors={judge_errors} conflicts={conflicts}"
    )


if __name__ == "__main__":
    main()
