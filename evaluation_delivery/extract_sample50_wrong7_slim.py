# -*- coding: utf-8 -*-
"""把 sample50 的 7 道错题快照精简为：问题 + 标准答案 + 专家智能体思考 + 专家智能体输出。"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

OUT = Path("/mnt/d/code/AI/deeplearning/tiaozhanbei/evaluation_delivery/outputs")
SNAP_DIR = OUT / "snapshots_sample50_wrong7"
DST = OUT / "cmb_sample50_wrong7_精简.jsonl"
FAILURES_MD = OUT / "cmb_failures_sample50_w4.md"


def get_expert_reasoning(snap):
    for t in snap.get("model_trace") or []:
        if t.get("agent") == "expert_agent":
            return t.get("reasoning_text") or ""
    return ""


def get_expert_output(snap):
    for a in snap.get("agent_outputs") or []:
        if a.get("producer") == "expert_agent":
            p = a.get("payload") or {}
            c = p.get("content") or {}
            if isinstance(c, dict):
                return c.get("题目讲解") or ""
            return str(c or "")
    return ""


def main():
    from cmb_adapter import load_cmb_cases

    cases = {c["case_id"]: c for c in load_cmb_cases(str(OUT / "cmb_sample50.json"))}

    # 从错题清单解析 case_id → snapshot 文件名
    md = FAILURES_MD.read_text(encoding="utf-8")
    table_lines = [l for l in md.splitlines() if l.startswith("| cmb_")]
    mapping = []
    for line in table_lines:
        parts = [p.strip() for p in line.split("|") if p.strip()]
        case_id, snapshot_path = parts[0], parts[-1]
        mapping.append((case_id, Path(snapshot_path).name))

    results = []
    no_snap = 0
    no_reason = 0
    no_output = 0
    for case_id, snap_name in mapping:
        snap_file = SNAP_DIR / snap_name
        if not snap_file.exists():
            print("缺少快照:", case_id, snap_name)
            no_snap += 1
            continue
        snap = json.loads(snap_file.read_text(encoding="utf-8"))
        case = cases.get(case_id)
        if case is None:
            print("缺少用例:", case_id)
            continue
        reason = get_expert_reasoning(snap)
        output = get_expert_output(snap)
        if not reason:
            no_reason += 1
        if not output:
            no_output += 1
        results.append({
            "case_id": case_id,
            "question": case["user_request"],
            "standard_answer": case["expected"],
            "expert_reasoning": reason,
            "expert_output": output,
        })

    with open(DST, "w", encoding="utf-8") as f:
        for item in results:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print("写出:", DST, "| 行数:", len(results))
    print("缺快照:", no_snap, "| 缺思考:", no_reason, "| 缺输出:", no_output)


if __name__ == "__main__":
    main()
