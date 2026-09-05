"""CMB 结果重判定：对已有执行结果 jsonl 用 LLM Judge 双通道重新判定并出报告。

用法：
  python rejudge_cmb.py outputs/cmb_smoke1.jsonl --tag smoke1
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from codex1_hallucination import _default_env_file, _load_env_into_os, _make_judge, _write_cmb_report  # noqa: E402
from cmb_adapter import judge_cmb_llm  # noqa: E402
from stats import load_jsonl  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser(description="对已有 CMB 执行结果用 LLM Judge 双通道重判定")
    ap.add_argument("jsonl", help="执行结果 jsonl 路径（runner 产出）")
    ap.add_argument("--tag", default="rejudge", help="run_tag，用于报告文件名")
    ap.add_argument("--env", default=None, help="env 文件（默认 backend/competition_app/.env.local）")
    args = ap.parse_args()

    env_file = Path(args.env) if args.env else _default_env_file()
    _load_env_into_os(env_file)
    judge = _make_judge()
    print(f"Judge: {judge.base_url} {judge.model}", file=sys.stderr)

    records = [r for r in load_jsonl(Path(args.jsonl)) if not r.get("skipped")]
    judged: list[dict] = []
    for rec in records:
        judgement = await judge_cmb_llm(judge, rec)
        judged.append({**rec, **judgement})
    ns = argparse.Namespace(run_tag=args.tag, data=args.jsonl)
    _write_cmb_report(ns, judged)
    print(f"重判定完成: {len(judged)} 条 → outputs/cmb_report_{args.tag}.md")


if __name__ == "__main__":
    asyncio.run(main())
