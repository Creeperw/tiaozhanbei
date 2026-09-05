"""Extract CMB records that require manual review into reusable review/rerun files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--judged", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True, help="可直接重跑的CMB JSON")
    parser.add_argument("--jsonl", type=Path, required=True, help="完整待复核判定记录")
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()

    waiting = [row for row in load_jsonl(args.judged) if row.get("status") == "waiting_human_review"]
    source = json.loads(args.source.read_text(encoding="utf-8"))
    rerun: list[dict] = []
    sections = [
        "# CMB 等待人工复核题目",
        "",
        f"- 来源：`{args.judged.name}`",
        f"- 共计：{len(waiting)}题",
        "- 当前共同原因：Agent运行结束但未产生可判定的最终回答。",
        "",
    ]

    for number, row in enumerate(waiting, start=1):
        case_input = row.get("input") or {}
        meta = case_input.get("meta") or {}
        index = int(meta.get("cmb_index"))
        if index < 0 or index >= len(source):
            raise IndexError(f"{row.get('case_id')}: cmb_index={index} 超出源数据范围")
        question = dict(source[index])
        question["_cmb_index"] = index
        rerun.append(question)

        sections.extend(
            [
                f"## {number}. `{row.get('case_id')}`",
                "",
                str(case_input.get("user_request") or "（题干缺失）"),
                "",
                f"- 标准答案：**{case_input.get('expected') or question.get('answer') or '-'}**",
                f"- Judge说明：{row.get('reason') or '无系统最终回答，无法判定'}",
                f"- 运行耗时：{float(row.get('duration_seconds') or 0):.1f}秒",
                "- 快照：无",
                "",
            ]
        )

    args.json.write_text(json.dumps(rerun, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.jsonl.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in waiting),
        encoding="utf-8",
    )
    args.markdown.write_text("\n".join(sections), encoding="utf-8")
    print(f"extracted={len(waiting)} json={args.json} jsonl={args.jsonl} markdown={args.markdown}")
    for row in waiting:
        case_input = row.get("input") or {}
        print(row.get("case_id"), "expected=" + str(case_input.get("expected")))


if __name__ == "__main__":
    main()
