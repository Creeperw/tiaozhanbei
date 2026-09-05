# -*- coding: utf-8 -*-
"""探查快照结构：各Agent payload字段 + trace中的思考过程"""
import json

snap = "/root/autodl-tmp/tiaozhanbei/evaluation_delivery/outputs/snapshots/CASE_0bf4bf0d8157cb6867f0b609fbceb941/EXE_0bf4bf0d8157cb6867f0b609fbceb941.json"
d = json.load(open(snap, encoding="utf-8"))

print("===== 各 Agent payload 字段 =====")
for ao in d.get("agent_outputs") or []:
    pname = ao.get("producer")
    p = ao.get("payload") or {}
    print(f"--- {pname} ---")
    if isinstance(p, dict):
        for k, v in p.items():
            if isinstance(v, str):
                print(f"  [{k}] str len={len(v)}: {v[:120]}")
            else:
                n = len(v) if hasattr(v, "__len__") else "?"
                print(f"  [{k}] {type(v).__name__} len={n}")
    else:
        print(f"  payload type={type(p).__name__}")
    print()

print("===== trace 结构 =====")
for i, t in enumerate(d.get("trace") or []):
    if isinstance(t, dict):
        keys = list(t.keys())
        print(f"trace[{i}] keys={keys} :: {json.dumps(t, ensure_ascii=False)[:250]}")
        if i > 30:
            print("...(截断)")
            break
