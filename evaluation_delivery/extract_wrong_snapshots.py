# -*- coding: utf-8 -*-
"""提取全部错题的完整快照，拼接为一个 JSONL：
每行 = {case_id, expected, got, want, is_correct, conflict, question, snapshot}
snapshot 为快照文件完整内容。
"""
import json

OUT = "/root/autodl-tmp/tiaozhanbei/evaluation_delivery/outputs"
FINAL = OUT + "/cmb_batch500_server_w16_exa_final_500_judged.jsonl"

rows = [json.loads(l) for l in open(FINAL, encoding="utf-8") if l.strip()]
wrong = [r for r in rows if r.get("answered") and not r.get("is_correct")]
print("错题总数:", len(wrong))

results = []
missing = 0
for r in wrong:
    sp = r.get("snapshot_path") or ""
    snap = None
    if sp and __import__("os").path.exists(sp):
        try:
            snap = json.load(open(sp, encoding="utf-8"))
        except Exception as e:
            print("快照读取失败:", r["case_id"], e)
            snap = {"_error": str(e)}
    else:
        missing += 1
        snap = {"_error": "snapshot_not_found", "_path": sp}
    inp = r.get("input", {}) or {}
    meta = inp.get("meta", {}) or {}
    results.append({
        "case_id": r.get("case_id"),
        "status": r.get("status"),
        "question": inp.get("user_request", ""),
        "expected": inp.get("expected", ""),
        "want": r.get("want"),
        "got": r.get("got"),
        "is_correct": r.get("is_correct"),
        "conflict": r.get("conflict"),
        "subject": meta.get("exam_subject"),
        "question_type": meta.get("question_type"),
        "snapshot_path": sp,
        "snapshot": snap,
    })

out_path = OUT + "/cmb_wrong65_snapshots.jsonl"
with open(out_path, "w", encoding="utf-8") as f:
    for item in results:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
print("已写出:", out_path, "| 行数:", len(results), "| 缺快照:", missing)
