import json
from collections import defaultdict

tg_path = "../output/02_hidden_kg/dedup_result.json"
kb_path = r"D:\2026summer holiday challenges\tiaozhanbei\backend\competition\知识星球视频知识库_前端交接包_2026-07-18\知识库管理组件\data\backend_delivery\04_knowledge_points\final_knowledge_points.json"

print("加载中...")

with open(tg_path, encoding="utf-8") as f:
    tg = json.load(f)
tg_ents = tg["entities"]
print(f"TreeKG实体: {len(tg_ents)}")

# 看一个实体的完整结构
e = next(iter(tg_ents.values()))
print(f"TreeKG字段: {list(e.keys())}")
print(f"样例: {e.get('name')} | type={e.get('type')} | role={e.get('role')}")
print(f"  aliases={e.get('alias',[])}")
print(f"  desc={e.get('original','')[:120]}")
print(f"  neighbors={len(e.get('neighbors',[]))}条")

with open(kb_path, encoding="utf-8") as f:
    kb = json.load(f)
print(f"\n知识库知识点: {len(kb)}")

# KB索引: 名/别名 -> kp条目
kb_idx = {}
for item in kb:
    kp = item["kp"]
    kb_idx[kp["kp_lv3"]] = kp
    alias = kp.get("other_name", "").strip()
    if alias:
        kb_idx[alias] = kp

print(f"KB名+别名索引: {len(kb_idx)}")

# 匹配
exact, alias_m, no_m = 0, 0, 0
no_m_samples = []
for name, ent in tg_ents.items():
    if name in kb_idx:
        exact += 1
    else:
        found = False
        for a in (ent.get("alias") or []):
            if a in kb_idx:
                alias_m += 1
                found = True
                break
        if not found:
            no_m += 1
            if len(no_m_samples) < 20:
                no_m_samples.append((name, ent.get("type", "?")))

print(f"\n=== 匹配分析 ===")
print(f"精确名匹配: {exact}")
print(f"别名匹配:   {alias_m}")
print(f"KG有但KB无: {no_m}")
print(f"KB独有:     {len(kb_idx) - exact - alias_m}")

print(f"\nKG有但KB无的样例:")
for name, typ in no_m_samples:
    print(f"  [{typ}] {name}")

# 反向: KB在KG中不存在
kb_names = set()
for item in kb:
    kb_names.add(item["kp"]["kp_lv3"])
kg_names = set(tg_ents.keys())
kb_only = kb_names - kg_names
print(f"\nKB独有(不在KG): {len(kb_only)}")
