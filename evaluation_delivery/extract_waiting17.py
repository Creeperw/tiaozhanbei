# -*- coding: utf-8 -*-
"""提取主批次中所有 waiting_human_review 记录摘要 + 完整JSON备份"""
import json, collections, sys

root = "/root/autodl-tmp/tiaozhanbei"
p = root + "/evaluation_delivery/outputs/cmb_batch500_server_w16_exa.jsonl"
rows = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
unique = {str(r.get("case_id")): r for r in rows}
wait = [r for r in unique.values() if r.get("status") == "waiting_human_review"]
print("waiting count:", len(wait))

out = root + "/evaluation_delivery/outputs/waiting17_current.jsonl"
with open(out, "w", encoding="utf-8") as f:
    for r in wait:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print("saved:", out)

for r in wait:
    cid = str(r.get("case_id", ""))
    parts = cid.split("_", 2)
    idx = parts[1] if len(parts) > 1 else "?"
    q = (r.get("question") or "")[:60].replace("\n", " ")
    exp = str(r.get("expected") or r.get("expected_answer") or "")
    reason = str(r.get("wait_reason") or r.get("reason") or "")
    print(f"{idx} | {q} | expected={exp} | reason={reason[:40]}")
