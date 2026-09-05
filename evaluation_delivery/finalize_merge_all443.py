"""整理最终443条：
1. 以 merged_all443_judged 为底
2. 17条 waiting 用 waiting10 替换（success judged）
3. 2条 (0394/0428) 用重跑记录替换，并重新 LLM Judge
4. 写出最终 judged 文件 + 更新主批次 raw（续跑时全部跳过）
5. 生成最终报告
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from cmb_adapter import judge_cmb_llm
from codex1_hallucination import _make_judge, _load_env_into_os, _write_cmb_report

OUT = Path("outputs")
MAIN = OUT / "cmb_batch500_server_w16_exa.jsonl"
ALL443 = OUT / "cmb_batch500_server_w16_exa_merged_all443_judged.jsonl"
W10 = OUT / "cmb_batch500_server_w16_exa_merged_all368_waiting10_replaced_judged.jsonl"
RERUN2 = OUT / "cmb_waiting2_rerun_w2.jsonl"
FINAL = OUT / "cmb_batch500_server_w16_exa_merged_all443_final_judged.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def dump_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


async def main() -> None:
    _load_env_into_os(Path(__file__).resolve().parents[1] / "backend" / "competition_app" / ".env.local")

    all443 = load_jsonl(ALL443)
    w10 = load_jsonl(W10)
    rerun2 = load_jsonl(RERUN2)
    main_rows = load_jsonl(MAIN)
    main_map = {r["case_id"]: r for r in main_rows}
    merged = {r["case_id"]: r for r in all443}

    # 0) 主批次中已重试成功的 failed 记录，用主批次记录替换旧 failed（拿到新快照）
    recovered = []
    for cid, r in list(merged.items()):
        if r.get("status") != "success" and cid in main_map and main_map[cid].get("status") == "success":
            merged[cid] = main_map[cid]
            recovered.append(cid)

    # 1) waiting10 替换（17条）
    replaced_w10 = 0
    for r in w10:
        if r["case_id"] in merged and merged[r["case_id"]].get("status") != "success":
            merged[r["case_id"]] = r
            replaced_w10 += 1

    # 2) 重跑2条替换 + 重新判定
    judge = _make_judge()
    judged_rerun = {}
    for r in rerun2:
        j = await judge_cmb_llm(judge, r)
        merged[r["case_id"]] = {**r, **j}
        judged_rerun[r["case_id"]] = j
        print(f"[judge-rerun] {r['case_id']} answered={j.get('answered')} correct={j.get('is_correct')}", flush=True)

    # 2.5) 4条恢复的 failed 记录需要判定（主批次记录无 judged 字段）
    judged_recovered = {}
    for cid in recovered:
        if not merged[cid].get("is_correct"):
            j = await judge_cmb_llm(judge, merged[cid])
            merged[cid] = {**merged[cid], **j}
            judged_recovered[cid] = j
            print(f"[judge-recovered] {cid} answered={j.get('answered')} correct={j.get('is_correct')}", flush=True)

    # 3) 写最终 judged
    final_rows = list(merged.values())
    dump_jsonl(FINAL, final_rows)
    print(f"recovered={len(recovered)}, replaced_w10={replaced_w10}, rerun_judged={len(judged_rerun)}, "
          f"recovered_judged={len(judged_recovered)}, final={len(final_rows)}", flush=True)

    # 4) 更新主批次 raw：非 success 全部替换为 merged 中的 success 记录
    shutil.copy2(MAIN, OUT / f"cmb_batch500_server_w16_exa_before_finalize_v2.jsonl")
    updated = 0
    for i, r in enumerate(main_rows):
        if r.get("status") != "success" and r["case_id"] in merged:
            main_rows[i] = merged[r["case_id"]]
            updated += 1
    dump_jsonl(MAIN, main_rows)
    print(f"main updated={updated}, main rows={len(main_rows)}", flush=True)

    # 5) 最终报告
    args = SimpleNamespace(run_tag="batch500_server_w16_exa_merged_all443_final")
    _write_cmb_report(args, final_rows)
    print("report written", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
