# -*- coding: utf-8 -*-
"""提取所有做错的题目，生成 Markdown 清单"""
import json

OUT = "/root/autodl-tmp/tiaozhanbei/evaluation_delivery/outputs"
src = OUT + "/cmb_batch500_server_w16_exa_merged_all443_judged.jsonl"

rows = [json.loads(x) for x in open(src, encoding="utf-8") if x.strip()]
answered = [r for r in rows if r.get("status") == "success" and r.get("answered")]
wrong = [r for r in answered if not r.get("is_correct")]
wrong.sort(key=lambda r: int(r["case_id"].split("_")[1]))
print("总443 成功:", len(answered), "答错:", len(wrong))

md = ["# CMB 错题清单（merged all443）\n",
      f"> 答错 {len(wrong)} / 有效作答 {len(answered)}\n"]
for i, r in enumerate(wrong, 1):
    cid = r["case_id"]
    q = (r.get("input") or {}).get("user_request", "")
    exp = (r.get("input") or {}).get("expected", "?")
    got = r.get("got")
    want = r.get("want")
    conflict = r.get("conflict")
    md.append(f"## {i}. {cid}")
    md.append(f"- 期望答案：**{exp}** ｜ 模型回答：**{got}** ｜ 期望集：{want} ｜ 双通道冲突：{conflict}")
    md.append("")
    md.append("> " + q.replace("\n", "  \n> "))
    md.append("")

out_md = OUT + "/cmb_wrong_list_all443.md"
with open(out_md, "w", encoding="utf-8") as f:
    f.write("\n".join(md))
print("saved:", out_md)
