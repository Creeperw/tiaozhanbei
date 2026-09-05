# -*- coding: utf-8 -*-
"""提取所有待复核(waiting_human_review)记录的完整数据，含详细模型输出。

- 已解决的17条（合并文件中为success）：从快照提取各Agent输出+最终回答+audit
- 真正待复核的2条：完整记录（无模型输出时注明）
输出：Markdown 文档 + 原始JSONL
"""
import json
import os

ROOT = "/root/autodl-tmp/tiaozhanbei"
OUT = ROOT + "/evaluation_delivery/outputs"
SNAP_ROOT = OUT + "/snapshots"

merged = {}
for line in open(OUT + "/cmb_batch500_server_w16_exa_merged_all443_judged.jsonl", encoding="utf-8"):
    line = line.strip()
    if line:
        r = json.loads(line)
        merged[str(r.get("case_id"))] = r

# raw 中全部 waiting（含 resolved 与 unresolved）
raw = {}
for line in open(OUT + "/cmb_batch500_server_w16_exa.jsonl", encoding="utf-8"):
    line = line.strip()
    if line:
        r = json.loads(line)
        raw[str(r.get("case_id"))] = r

waiting = [r for r in raw.values() if r.get("status") == "waiting_human_review"]
waiting.sort(key=lambda r: r["case_id"])

resolved = []   # 合并文件中已是 success（有快照输出）
unresolved = [] # 合并文件中仍 waiting（无输出）

for r in waiting:
    cid = r["case_id"]
    m = merged.get(cid)
    if m and m.get("status") == "success":
        resolved.append((r, m))
    else:
        unresolved.append(r)

print(f"raw waiting={len(waiting)} resolved={len(resolved)} unresolved={len(unresolved)}")

md = ["# CMB 待人工复核数据全量清单（含详细模型输出）\n"]
md.append(f"> 生成时间：{__import__('datetime').datetime.now().isoformat()}\n")
md.append(f"> 说明：raw 主批次中标记 waiting_human_review 共 {len(waiting)} 条；"
          f"其中 {len(resolved)} 条已由重跑解决（合并后为 success，含详细输出）；"
          f"{len(unresolved)} 条仍待复核（模型回答为空，无输出）。\n")

def get_final_answer(snap: dict) -> str:
    """从快照提取最终回答文本。"""
    resource = snap.get("resource") or {}
    content = resource.get("content") or {}
    if isinstance(content, dict):
        return content.get("题目讲解") or content.get("讲解") or json.dumps(content, ensure_ascii=False)[:2000]
    return str(content)[:2000]

def get_agent_summaries(snap: dict) -> list[tuple[str, str]]:
    """提取各Agent输出摘要。"""
    out = []
    for ao in snap.get("agent_outputs") or []:
        producer = ao.get("producer") or ao.get("artifact_type")
        payload = ao.get("payload")
        text = ""
        if isinstance(payload, dict):
            for key in ("content", "explanation", "answer", "summary", "text", "response"):
                if key in payload and isinstance(payload[key], str):
                    text = payload[key]
                    break
            if not text:
                text = json.dumps(payload, ensure_ascii=False)[:300]
        elif isinstance(payload, str):
            text = payload[:300]
        if text:
            out.append((str(producer), text[:600]))
    return out

# ---------- 已解决部分 ----------
md.append("\n---\n\n## 一、已由重跑解决（17条，合并后为 success，含详细模型输出）\n")

for idx, (raw_r, merged_r) in enumerate(resolved, 1):
    cid = raw_r["case_id"]
    sp = merged_r.get("snapshot_path")
    sp_abs = os.path.join(ROOT, sp) if sp and not os.path.isabs(sp) else sp
    snap = None
    if sp_abs and os.path.isfile(sp_abs):
        try:
            snap = json.load(open(sp_abs, encoding="utf-8"))
        except Exception as e:
            snap = None
    meta = (raw_r.get("input") or {}).get("meta") or {}
    q = (raw_r.get("input") or {}).get("user_request") or ""
    exp = (raw_r.get("input") or {}).get("expected") or "?"
    judge_ok = merged_r.get("answered")
    judge_correct = merged_r.get("is_correct")
    got = merged_r.get("got")
    want = merged_r.get("want")

    md.append(f"\n### {idx}. {cid}（{meta.get('exam_subject','')}）")
    md.append(f"- 期望答案：**{exp}**")
    md.append(f"- 合并后状态：success ｜ Judge判定：{'对' if judge_correct else ('错' if judge_ok else '未判定')} ｜ got={got} want={want}")
    md.append("")
    md.append("> **题目：**")
    md.append("> " + q.replace("\n", "  \n> "))
    md.append("")
    if snap:
        final = get_final_answer(snap)
        md.append("### 📄 模型最终回答")
        md.append(final)
        md.append("")
        audit = snap.get("audit") or {}
        md.append(f"### 🔍 审核结果（audit）：decision={audit.get('decision')}")
        if audit.get("audit_report"):
            md.append(audit["audit_report"])
        md.append("")
        ags = get_agent_summaries(snap)
        if ags:
            md.append("### 🤖 各Agent输出摘要")
            for producer, text in ags:
                md.append(f"- **{producer}**: {text[:300]}")
            md.append("")
    else:
        md.append("⚠️ 无快照（输出缺失）")
        md.append("")

# ---------- 未解决部分 ----------
md.append("\n---\n\n## 二、仍待人工复核（2条，模型回答为空，无输出）\n")

for idx, raw_r in enumerate(unresolved, 1):
    cid = raw_r["case_id"]
    meta = (raw_r.get("input") or {}).get("meta") or {}
    q = (raw_r.get("input") or {}).get("user_request") or ""
    exp = (raw_r.get("input") or {}).get("expected") or "?"
    m = merged.get(cid) or {}
    md.append(f"\n### {idx}. {cid}（{meta.get('exam_subject','')}）")
    md.append(f"- 期望答案：**{exp}**")
    md.append(f"- 状态：waiting_human_review ｜ duration={round(raw_r.get('duration_seconds') or 0)}s")
    md.append(f"- Judge reason：{m.get('reason')}")
    md.append("")
    md.append("> **题目：**")
    md.append("> " + q.replace("\n", "  \n> "))
    md.append("")
    md.append("⚠️ **无模型输出**（waiting 分支不导出快照，且系统回答为空）")
    md.append("")

out_md = OUT + "/waiting_all19_详细模型输出.md"
with open(out_md, "w", encoding="utf-8") as f:
    f.write("\n".join(md))
print("saved:", out_md)

# JSONL 全量备份
out_jsonl = OUT + "/waiting_all19_with_outputs.jsonl"
with open(out_jsonl, "w", encoding="utf-8") as f:
    for raw_r, merged_r in resolved:
        rec = {**merged_r, "detailed_output_extracted": True}
        sp = merged_r.get("snapshot_path")
        sp_abs = os.path.join(ROOT, sp) if sp and not os.path.isabs(sp) else sp
        if sp_abs and os.path.isfile(sp_abs):
            try:
                rec["snapshot_content"] = json.load(open(sp_abs, encoding="utf-8"))
            except Exception:
                pass
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    for raw_r in unresolved:
        f.write(json.dumps(raw_r, ensure_ascii=False) + "\n")
print("saved:", out_jsonl)
