# -*- coding: utf-8 -*-
"""按 case_id 从 cmb_batch500.json 生成 3 条 waiting 重跑输入（CMB 原始格式）"""
import json
import sys

sys.path.insert(0, "/root/autodl-tmp/tiaozhanbei/evaluation_delivery")
sys.path.insert(0, "/root/autodl-tmp/tiaozhanbei/backend")

from cmb_adapter import load_cmb_cases  # noqa: E402

OUT = "/root/autodl-tmp/tiaozhanbei/evaluation_delivery/outputs"
targets = {"cmb_0475_系统解剖学", "cmb_0491_内科主治医师", "cmb_0495_外科学"}

cases = load_cmb_cases(OUT + "/cmb_batch500.json")
raw = json.load(open(OUT + "/cmb_batch500.json", encoding="utf-8"))


def build(q):
    lines = [str(q.get("question", "")).strip()]
    for key in sorted(q.get("option", {}), key=lambda k: (len(str(k)) != 1, str(k))):
        lines.append(f"{key}. {str(q['option'][key]).strip()}")
    lines.append("请给出答案选项字母，并简要说明依据。")
    return "\n".join(lines)


wanted = []
for c in cases:
    if c["case_id"] in targets:
        matched = False
        for q in raw:
            if build(q) == c["user_request"]:
                qc = dict(q)
                qc["_cmb_index"] = int(c["case_id"].split("_")[1])
                wanted.append(qc)
                matched = True
                break
        if not matched:
            print("未匹配到:", c["case_id"])
print("匹配到:", len(wanted), [w["_cmb_index"] for w in wanted])
for w in wanted:
    print(" -", w["_cmb_index"], w.get("exam_subject"), "answer=", w.get("answer"))

json.dump(wanted, open(OUT + "/cmb_batch500_waiting3_rerun.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("saved:", OUT + "/cmb_batch500_waiting3_rerun.json")
