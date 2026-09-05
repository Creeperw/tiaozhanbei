# -*- coding: utf-8 -*-
"""从快照提取：检索内容全文 + 专家Agent思考过程，生成Markdown"""
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

resolved_cases = []
for cid, r in merged.items():
    if r.get("status") == "success" and r.get("snapshot_path"):
        resolved_cases.append(r)
resolved_cases.sort(key=lambda r: int(r["case_id"].split("_")[1]))

print(f"可提取快照的success记录: {len(resolved_cases)}")

def load_snap(r):
    sp = r.get("snapshot_path")
    sp_abs = os.path.join(ROOT, sp) if sp and not os.path.isabs(sp) else sp
    if sp_abs and os.path.isfile(sp_abs):
        try:
            return json.load(open(sp_abs, encoding="utf-8"))
        except Exception:
            return None
    return None

def get_kb_retrieval(snap):
    """knowledge_base_agent 的检索内容（evidence_items 全文）"""
    for ao in snap.get("agent_outputs") or []:
        if ao.get("producer") == "knowledge_base_agent":
            p = ao.get("payload") or {}
            items = []
            for ev in p.get("evidence_items") or []:
                items.append({
                    "evidence_id": ev.get("evidence_id"),
                    "source_label": ev.get("source_label") or ev.get("source_id"),
                    "authority_level": ev.get("authority_level"),
                    "content": ev.get("content_summary") or "",
                })
            return {
                "query": p.get("query"),
                "retrieval_summary": p.get("retrieval_summary"),
                "items": items,
                "exa_evidence": [i for i in items if "E_EXA_" in str(i["evidence_id"])],
                "textbook_evidence": [i for i in items if "E_EXA_" not in str(i["evidence_id"])],
            }
    return None

def get_expert_reasoning(snap):
    """专家Agent的思考过程：model_trace 中 expert_agent 的 reasoning_text"""
    for t in snap.get("model_trace") or []:
        if isinstance(t, dict) and t.get("agent") == "expert_agent":
            rt = t.get("reasoning_text") or ""
            ot = t.get("raw_output_text") or ""
            return {"reasoning_text": rt, "raw_output_text": ot}
    return None

def get_expert_output(snap):
    """expert_agent 最终输出"""
    for ao in snap.get("agent_outputs") or []:
        if ao.get("producer") == "expert_agent":
            p = ao.get("payload") or {}
            return {
                "title": p.get("title"),
                "content": (p.get("content") or {}),
                "claims": p.get("claims"),
                "provenance": p.get("provenance"),
            }
    return None

# 生成 Markdown（全部记录，按题号）
md = ["# CMB 检索内容 + 专家思考过程全量导出\n",
      f"> 覆盖 {len(resolved_cases)} 条 success 记录（含快照）\n"]

for i, r in enumerate(resolved_cases, 1):
    cid = r["case_id"]
    snap = load_snap(r)
    if not snap:
        continue
    md.append(f"\n---\n\n## {i}. {cid}")
    md.append(f"- Judge: {'对' if r.get('is_correct') else '错'} ｜ got={r.get('got')} want={r.get('want')}")

    kb = get_kb_retrieval(snap)
    if kb:
        md.append(f"\n### 🔍 检索内容（query: {kb['query']}）")
        md.append(f"检索摘要：{kb['retrieval_summary']}")
        md.append(f"\n**EXA证据 {len(kb['exa_evidence'])}条 + 知识库证据 {len(kb['textbook_evidence'])}条：**\n")
        for ev in kb["items"]:
            md.append(f"- **[{ev['evidence_id']}]** ({ev['authority_level']} | {ev['source_label']})")
            content = ev["content"]
            md.append(f"  > {content[:400]}")
        md.append("")

    expert = get_expert_output(snap)
    if expert and expert.get("content"):
        c = expert["content"]
        if isinstance(c, dict):
            text = c.get("题目讲解") or c.get("讲解") or json.dumps(c, ensure_ascii=False)
        else:
            text = str(c)
        md.append(f"\n### 🧠 专家Agent最终讲解（{expert.get('title')}）")
        md.append(text[:1500])
        md.append("")

    ex = get_expert_reasoning(snap)
    if ex and ex.get("reasoning_text"):
        md.append(f"\n### 💭 专家Agent思考过程（reasoning_text）")
        md.append(ex["reasoning_text"][:4000])
        md.append("")

out_md = OUT + "/cmb_retrieval_and_expert_reasoning.md"
with open(out_md, "w", encoding="utf-8") as f:
    f.write("\n".join(md))
print("saved:", out_md, f"({os.path.getsize(out_md)} bytes)")
