"""Replace judged records by case_id and write a concise accuracy report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from stats import wilson_ci


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--replacement", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    base = load(args.base)
    replacement = load(args.replacement)
    replacements = {row["case_id"]: row for row in replacement}
    base_ids = {row["case_id"] for row in base}
    missing = set(replacements) - base_ids
    if missing:
        raise RuntimeError(f"replacement case_id not found: {sorted(missing)}")
    merged = [replacements.get(row["case_id"], row) for row in base]
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged), encoding="utf-8")

    success = [row for row in merged if row.get("status") == "success"]
    answered = [row for row in success if row.get("answered")]
    correct = [row for row in answered if row.get("is_correct")]
    wrong = len(answered) - len(correct)
    waiting = sum(row.get("status") == "waiting_human_review" for row in merged)
    failed = sum(row.get("status") == "failed" for row in merged)
    judge_errors = sum(bool(row.get("judge_error")) for row in merged)
    conflicts = sum(bool(row.get("conflict")) for row in merged)
    accuracy, low, high = wilson_ci(len(correct), len(answered))
    report = f"""# CMB 当前批次准确率报告（重跑替换后）

- 总记录：{len(merged)}
- 成功：{len(success)}
- 等待人工复核：{waiting}
- 运行失败：{failed}
- 有效作答：{len(answered)}
- 答对：{len(correct)}
- 答错：{wrong}
- 有效作答准确率：{len(correct)}/{len(answered)} = {accuracy:.1%}
- 95% CI：{low:.1%}～{high:.1%}
- 以全部{len(merged)}题为分母：{len(correct)}/{len(merged)} = {len(correct) / len(merged):.1%}
- Judge失败回退：{judge_errors}
- 双通道冲突：{conflicts}
- 本次按 `case_id` 替换：{len(replacement)}题
"""
    args.report.write_text(report, encoding="utf-8")
    print(
        f"total={len(merged)} replaced={len(replacement)} success={len(success)} "
        f"waiting={waiting} failed={failed} answered={len(answered)} correct={len(correct)} "
        f"wrong={wrong} accuracy={accuracy:.4%} judge_errors={judge_errors} conflicts={conflicts}"
    )


if __name__ == "__main__":
    main()
