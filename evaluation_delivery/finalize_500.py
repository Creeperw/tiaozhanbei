"""最终合并500题：
1. 443 旧判定 (merged_all443_final_judged) 为底
2. 57 条增量判定 (incremental57) 合并（新结果覆盖）
3. 3 条重跑 (waiting3) 替换并重新判定
4. 输出 500 题最终 judged + 报告
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from cmb_adapter import judge_cmb_llm
from codex1_hallucination import _make_judge, _load_env_into_os, _write_cmb_report

OUT = Path("outputs")
ALL443 = OUT / "cmb_batch500_server_w16_exa_merged_all443_final_judged.jsonl"
INC57 = OUT / "cmb_batch500_server_w16_exa_incremental57_judged.jsonl"
RERUN3 = OUT / "cmb_waiting3_rerun_w3.jsonl"
FINAL = OUT / "cmb_batch500_server_w16_exa_final_500_judged.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def dump_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


async def main() -> None:
    _load_env_into_os(Path(__file__).resolve().parents[1] / "backend" / "competition_app" / ".env.local")

    all443 = load_jsonl(ALL443)
    inc57 = load_jsonl(INC57)
    rerun3 = load_jsonl(RERUN3)

    merged = {r["case_id"]: r for r in all443}
    print(f"底: {len(merged)} 条", flush=True)

    # 1) 57 条增量判定合并（含之前443里没有的，覆盖旧的waiting记录）
    new_ids = set()
    for r in inc57:
        merged[r["case_id"]] = r
        new_ids.add(r["case_id"])
    print(f"57增量合并: {len(new_ids)} 条", flush=True)

    # 2) 3条重跑替换 + 重新判定
    judge = _make_judge()
    for r in rerun3:
        j = await judge_cmb_llm(judge, r)
        merged[r["case_id"]] = {**r, **j}
        print(f"[judge-rerun3] {r['case_id']} answered={j.get('answered')} correct={j.get('is_correct')}", flush=True)

    final_rows = list(merged.values())
    dump_jsonl(FINAL, final_rows)
    print(f"final 共 {len(final_rows)} 条 → {FINAL}", flush=True)

    # 3) 500 题最终报告
    args = SimpleNamespace(run_tag="batch500_server_w16_exa_final_500")
    _write_cmb_report(args, final_rows)
    print("report written", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
