# -*- coding: utf-8 -*-
"""提取 waiting_human_review 记录的题目+期望答案，输出可读Markdown"""
import json

root = "/root/autodl-tmp/tiaozhanbei"
p = root + "/evaluation_delivery/outputs/waiting17_current.jsonl"
rows = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
# 按 cmb_index 排序
rows.sort(key=lambda r: int(r["input"]["meta"]["cmb_index"]))

md = ["# 待人工复核 17 条\n"]
for i, r in enumerate(rows, 1):
    cid = r["case_id"]
    meta = r["input"]["meta"]
    q = r["input"]["user_request"]
    exp = r["input"]["expected"]
    md.append(f"## {i}. {cid}（{meta['exam_subject']}，{meta['exam_type']}）")
    md.append(f"- 期望答案：**{exp}**")
    md.append("")
    md.append("> " + q.replace("\n", "  \n> "))
    md.append("")

out = root + "/evaluation_delivery/outputs/waiting17_当前清单.md"
with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(md))
print("saved:", out)
print("count:", len(rows))
