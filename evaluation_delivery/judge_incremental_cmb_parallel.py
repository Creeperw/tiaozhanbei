"""Judge CMB records not yet in a prior judged JSONL (parallel workers).

用法（与 judge_incremental_cmb.py 相同，多一个 --workers）：
  python judge_incremental_cmb_parallel.py \\
      --raw outputs/cmb_batch500_server_w16_exa.jsonl \\
      --prior <已有判定合并文件> \\
      --env <env.local> \\
      --output <输出> \\
      --workers 4
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from cmb_adapter import judge_cmb_llm
from codex1_hallucination import _make_judge, _load_env_into_os


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def _judge_one(
    semaphore: asyncio.Semaphore,
    judge,
    record: dict,
    index: int,
    total: int,
) -> dict:
    async with semaphore:
        judgement = await judge_cmb_llm(judge, record)
        print(
            f"[{index}/{total}] {record['case_id']} "
            f"status={record.get('status')} answered={judgement.get('answered')} "
            f"correct={judgement.get('is_correct')} "
            f"judge_error={bool(judgement.get('judge_error'))}",
            flush=True,
        )
        return {**record, **judgement}


async def run(args: argparse.Namespace) -> None:
    _load_env_into_os(args.env)
    raw = load_jsonl(args.raw)
    prior = load_jsonl(args.prior)
    prior_ids = {row["case_id"] for row in prior}
    pending = [row for row in raw if row["case_id"] not in prior_ids]
    if not pending:
        print("no pending records to judge")
        return
    judge = _make_judge(max_concurrent=args.workers)
    semaphore = asyncio.Semaphore(args.workers)
    results = await asyncio.gather(
        *(
            _judge_one(semaphore, judge, record, index, len(pending))
            for index, record in enumerate(pending, start=1)
        )
    )
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results),
        encoding="utf-8",
    )
    print(f"wrote {len(results)} records to {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
