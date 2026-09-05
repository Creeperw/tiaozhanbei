"""Judge CMB records that are not yet present in a prior judged JSONL."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from cmb_adapter import judge_cmb_llm
from codex1_hallucination import _make_judge, _load_env_into_os


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def run(args: argparse.Namespace) -> None:
    _load_env_into_os(args.env)
    raw = load_jsonl(args.raw)
    prior = load_jsonl(args.prior)
    prior_ids = {row["case_id"] for row in prior}
    pending = [row for row in raw if row["case_id"] not in prior_ids]
    judge = _make_judge()
    judged: list[dict] = []
    for index, record in enumerate(pending, start=1):
        judgement = await judge_cmb_llm(judge, record)
        judged.append({**record, **judgement})
        print(
            f"[{index}/{len(pending)}] {record['case_id']} "
            f"status={record.get('status')} answered={judgement.get('answered')} "
            f"correct={judgement.get('is_correct')} "
            f"judge_error={bool(judgement.get('judge_error'))}",
            flush=True,
        )
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in judged),
        encoding="utf-8",
    )
    print(f"wrote {len(judged)} records to {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
