# -*- coding: utf-8 -*-
"""从500道题中按科目分层抽样50道不重复题目：
1. 用题干文本去重（保留第一道）
2. 按 exam_subject 分层，每科最多抽1道（50道覆盖尽可能多科目）
3. 若科目数不够50，从剩余题目补足
"""
import json
import random
from collections import defaultdict

random.seed(42)

OUT = "/mnt/d/code/AI/deeplearning/tiaozhanbei/evaluation_delivery/outputs"
SRC = OUT + "/cmb_batch500.json"
DST = OUT + "/cmb_sample50.json"

qs = json.load(open(SRC, encoding="utf-8"))

# 1) 去重（题干文本）
seen = set()
unique = []
for q in qs:
    key = str(q.get("question", "")).strip()
    if key and key not in seen:
        seen.add(key)
        unique.append(q)
print("去重后:", len(unique), "（去掉", len(qs) - len(unique), "道重复）")

# 2) 按科目分组
by_subject = defaultdict(list)
for q in unique:
    by_subject[q.get("exam_subject", "unknown")].append(q)

# 3) 每科抽1道，优先抽科目数多的
picked = []
for sub in sorted(by_subject, key=lambda s: -len(by_subject[s])):
    if len(picked) >= 50:
        break
    picked.append(random.choice(by_subject[sub]))

# 4) 若不足50，从剩余补（也保证不重复）
if len(picked) < 50:
    picked_ids = {id(q) for q in picked}
    rest = [q for q in unique if id(q) not in picked_ids]
    random.shuffle(rest)
    picked.extend(rest[: 50 - len(picked)])

# 5) 验证不重复
keys = [str(q.get("question", "")).strip() for q in picked]
assert len(keys) == len(set(keys)), "存在重复题目！"
assert len(picked) == 50, len(picked)

json.dump(picked, open(DST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# 统计
from collections import Counter
print("写出:", DST, "| 50道，0重复")
print("覆盖科目数:", len(set(q.get("exam_subject") for q in picked)))
print("题型:", dict(Counter(q.get("question_type") for q in picked)))
print("考试类别:", dict(Counter(q.get("exam_class") for q in picked)))
print("科目列表:", sorted(set(q.get("exam_subject") for q in picked)))
