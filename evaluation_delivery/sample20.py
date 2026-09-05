# -*- coding: utf-8 -*-
"""从 CMB 全题库中抽 20 道与已测 550 题完全不重复的题目：
1. 按题干文本（选项前部分）与已测 550 题去重
2. 按 exam_subject 分层，每科最多抽 1 道（20 道覆盖尽可能多科目）
3. 若科目数不够 20，从剩余题目补足
"""
import json
import random
import re
from collections import Counter, defaultdict

random.seed(20260813)

BASE = "/mnt/d/code/AI/deeplearning/tiaozhanbei"
SRC = BASE + "/docs/CMB-val-merge.json"
JUDGED = BASE + "/evaluation_delivery/outputs/cmb_550_rejudged_v12_final.jsonl"
DST = BASE + "/evaluation_delivery/outputs/cmb_sample20.json"


def pure_question(text: str) -> str:
    q = re.split(r"\n[A-E]\.", text)[0]
    return re.sub(r"请给出答案选项字母.*$", "", q).strip()


# 1) 已测纯题干
tested = set()
for line in open(JUDGED, encoding="utf-8"):
    r = json.loads(line)
    tested.add(pure_question(r["input"].get("user_request", "")))

qs = json.load(open(SRC, encoding="utf-8"))
fresh = [q for q in qs if pure_question(q.get("question", "")) not in tested]
print(f"题库总数: {len(qs)} | 已测: {len(tested)} | 未测可抽: {len(fresh)}")

# 2) 按科目分层，每科最多 1 道
by_subject = defaultdict(list)
for q in fresh:
    by_subject[q.get("exam_subject", "unknown")].append(q)

picked = []
for sub in sorted(by_subject, key=lambda s: -len(by_subject[s])):
    if len(picked) >= 20:
        break
    picked.append(random.choice(by_subject[sub]))

# 3) 不足 20 从剩余补
if len(picked) < 20:
    picked_keys = {pure_question(q["question"]) for q in picked}
    rest = [q for q in fresh if pure_question(q["question"]) not in picked_keys]
    random.shuffle(rest)
    picked.extend(rest[: 20 - len(picked)])

# 4) 验证
keys = [pure_question(q["question"]) for q in picked]
assert len(keys) == len(set(keys)), "存在重复题目！"
assert len(picked) == 20, len(picked)
assert not (set(keys) & tested), "与已测 550 题有重合！"

json.dump(picked, open(DST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print(f"写出: {DST} | 20 道，与已测 550 题 0 重复")
print("覆盖科目数:", len(set(q.get("exam_subject") for q in picked)))
print("题型:", dict(Counter(q.get("question_type") for q in picked)))
print("考试类别:", dict(Counter(q.get("exam_class") for q in picked)))
print("科目列表:", sorted(set(q.get("exam_subject") for q in picked)))
