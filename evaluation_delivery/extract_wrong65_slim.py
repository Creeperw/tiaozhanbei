# -*- coding: utf-8 -*-
"""把错题快照精简为：问题 + 标准答案 + 专家智能体思考 + 专家智能体输出。"""
import json

OUT = "/mnt/d/code/AI/deeplearning/tiaozhanbei/evaluation_delivery/outputs"
SRC = OUT + "/cmb_wrong65_snapshots.jsonl"
DST = OUT + "/cmb_wrong65_精简.jsonl"


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


rows = [json.loads(l) for l in open(SRC, encoding="utf-8") if l.strip()]
results = []
no_reason = 0
no_output = 0
for r in rows:
    snap = r.get("snapshot") or {}
    reason = get_expert_reasoning(snap)
    output = get_expert_output(snap)
    if not reason:
        no_reason += 1
    if not output:
        no_output += 1
    results.append({
        "case_id": r.get("case_id"),
        "question": r.get("question", ""),
        "standard_answer": r.get("expected", ""),
        "expert_reasoning": reason,
        "expert_output": output,
    })

with open(DST, "w", encoding="utf-8") as f:
    for item in results:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
print("写出:", DST, "| 行数:", len(results))
print("缺思考:", no_reason, "| 缺输出:", no_output)
