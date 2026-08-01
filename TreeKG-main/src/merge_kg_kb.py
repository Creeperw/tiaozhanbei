"""
增量合并：每次跑完一本书的 KG，把 KB 里没有的实体名补进去
第一本书从原始 KB 出发，后续从已有合并文件出发，全部写入同一个文件
"""

import json
from pathlib import Path

from config_utils import get_output_dir, get_book_name

# ===== 固定输出路径（始终写到这里）=====
MERGED_PATH = Path(r"D:\tree-KG\TreeKG-main\src\output\中医学基础\03_merged\final_knowledge_points_merged.json")

# 当前书的 KG 输入
BOOK = get_book_name()
TG_PATH = get_output_dir("hidden") / "aggr_entities.json"

# 原始 KB（仅首次合并时用）
KB_ORIG = Path(r"D:\2026summer holiday challenges\tiaozhanbei\backend\competition\知识星球视频知识库_前端交接包_2026-07-18\知识库管理组件\data\backend_delivery\04_knowledge_points\final_knowledge_points.json")

# ===== 判断从哪个文件出发 =====
if MERGED_PATH.exists():
    BASE_PATH = MERGED_PATH
    print(f"检测到已有合并文件，增量追加")
else:
    BASE_PATH = KB_ORIG
    print(f"首次合并，从原始 KB 出发")

# ===== 加载 KG =====
with open(TG_PATH, encoding="utf-8") as f:
    tg = json.load(f)
print(f"  当前书({BOOK}) KG 实体: {len(tg)}")

# ===== 加载基础 KB =====
with open(BASE_PATH, encoding="utf-8") as f:
    kb = json.load(f)
print(f"  基础 KB 条目: {len(kb)}")

# ===== 收集已有名字（含别名）=====
kb_names = set()
for item in kb:
    kp = item["kp"]
    kb_names.add(kp["kp_lv3"])
    alias = kp.get("other_name", "").strip()
    if alias:
        kb_names.add(alias)
print(f"  已有名字+别名: {len(kb_names)}")

# ===== 找 KG 有 KB 无的 =====
missing = []
for name, ent in tg.items():
    if name in kb_names:
        continue
    if any(a in kb_names for a in (ent.get("alias") or [])):
        continue
    missing.append((name, ent))

print(f"  本次新增: {len(missing)}")

if not missing:
    print("\n无新增，无需更新。")
    exit(0)

# ===== 分类推断 =====
known_lv1 = sorted(set(item["kp"]["kp_lv1"] for item in kb))

def infer_lv1(ent):
    occs = ent.get("occurrences") or []
    if occs:
        path = occs[0].get("path", "")
        for lv1 in known_lv1:
            if lv1 in path or any(seg.strip() in lv1 for seg in path.split(">")[:2]):
                return lv1
    return BOOK  # 兜底用当前书名

def infer_lv2(ent):
    occs = ent.get("occurrences") or []
    if occs:
        title = occs[0].get("title", "")
        if title:
            return title
        path = occs[0].get("path", "")
        parts = [p.strip() for p in path.split(">") if p.strip()]
        if len(parts) >= 2:
            return parts[1]
    return ""

# ===== 生成新条目 =====
max_id = max(int(item["kp"]["kp_id"]) for item in kb)

new_entries = []
for seq, (name, ent) in enumerate(missing, start=1):
    new_entries.append({
        "kp": {
            "kp_id": str(max_id + seq).zfill(6),
            "kp_lv1": infer_lv1(ent),
            "kp_lv2": infer_lv2(ent),
            "kp_lv3": name,
            "raw_content": [],
            "other_name": (ent.get("alias") or [None])[0] or "",
            "order": f"99{seq:08d}.0000",
            "knowledge_mastery": "",
            "answer_accuracy": "",
            "kp_review_status": "",
            "exam_bridges": [],
            "updated_at": "2026-07-30T00:00:00Z",
        }
    })

# ===== 合并写出 =====
merged = kb + new_entries
MERGED_PATH.parent.mkdir(parents=True, exist_ok=True)

with open(MERGED_PATH, "w", encoding="utf-8") as f:
    json.dump(merged, f, ensure_ascii=False, indent=2)

print(f"\n{'='*50}")
print(f"合并完成 ({BOOK})")
print(f"{'='*50}")
print(f"  基础条目:  {len(kb)}")
print(f"  新增条目:  {len(new_entries)}")
print(f"  合并总计:  {len(merged)}")

print(f"\n新增样例:")
for e in new_entries[:5]:
    kp = e["kp"]
    print(f"  [{kp['kp_lv1']}] {kp['kp_lv3']}")

print(f"\n输出: {MERGED_PATH.resolve()}")
