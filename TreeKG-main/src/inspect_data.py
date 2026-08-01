import json, os, pickle
from pathlib import Path
from collections import Counter

OUT = Path("output")

def info(path, desc=""):
    size = os.path.getsize(path)
    sep = "=" * 50
    print(f"\n{sep}")
    print(f"{desc}")
    print(f"  文件: {path.name}  ({size/1024:.1f} KB)")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        keys = list(data.keys())
        print(f"  类型: dict, keys={keys[:12]}")
        for k, v in data.items():
            if isinstance(v, list):
                print(f"    {k}: list[{len(v)}]")
            elif isinstance(v, dict):
                print(f"    {k}: dict ({len(v)} entries)")
            elif isinstance(v, str):
                print(f"    {k}: str ({len(v)} chars)")
            elif isinstance(v, (int, float)):
                print(f"    {k}: {v}")
        if "nodes" in data and len(data["nodes"]) > 0:
            print(f"  nodes[0] 样例: {json.dumps(data['nodes'][0], ensure_ascii=False)[:150]}")
        if "edges" in data and len(data["edges"]) > 0:
            print(f"  edges[0] 样例: {json.dumps(data['edges'][0], ensure_ascii=False)[:150]}")
    elif isinstance(data, list):
        print(f"  类型: list[{len(data)}]")
        if len(data) > 0:
            s = json.dumps(data[0], ensure_ascii=False)
            print(f"  [0] 样例: {s[:300]}")
    return data

# ---- ① TOC 结构 ----
data = info(OUT / "01_explicit_kg" / "toc_structure.json", "① TextSegmentation → TOC目录结构")
print(f"  首章: {data[0].get('title','?')}  (共{len(data)}章)")

# ---- ② 摘要 ----
info(OUT / "01_explicit_kg" / "toc_with_summaries.json", "② Summarize → 带摘要的TOC")

# ---- ③ 实体+关系 ----
info(OUT / "01_explicit_kg" / "toc_with_entities_and_relations.json", "③ Extraction → TOC+实体+关系")

# ---- ④ 显式KG ----
info(OUT / "01_explicit_kg" / "toc_graph.json", "④ toc_graph → 显式知识图谱")

# ---- ⑤ conv ----
info(OUT / "02_hidden_kg" / "conv_entities.json", "⑤ Conv → 增强描述的实体")

# ---- ⑥ aggr ----
d6 = info(OUT / "02_hidden_kg" / "aggr_entities.json", "⑥ Aggr → 区分core/noncore")
if isinstance(d6, dict) and "entities" in d6:
    roles = Counter(e.get("role", "?") for e in d6["entities"].values())
    print(f"  角色分布: {dict(roles)}")

# ---- ⑦ dedup ----
info(OUT / "02_hidden_kg" / "dedup_result.json", "⑦ Dedup → 去重后实体")

# ---- ⑧ pred ----
info(OUT / "02_hidden_kg" / "pred_result.json", "⑧ Pred → 预测的新关系")

# ---- ⑨ final ----
info(OUT / "02_hidden_kg" / "final_kg.json", "⑨ FinalKG → 最终知识图谱")

# ---- 嵌入 ----
sep = "=" * 50
print(f"\n{sep}")
print("⑩ Embedding → BERT节点向量")
p = OUT / "02_hidden_kg" / "node_embeddings.pkl"
print(f"  文件: {p.name}  ({os.path.getsize(p)/1024/1024:.1f} MB)")
with open(p, "rb") as f:
    emb = pickle.load(f)
if isinstance(emb, dict):
    keys = list(emb.keys())
    print(f"  类型: dict ({len(emb)} entries)")
    print(f"  键样例: {keys[:5]}")
    if keys:
        v = emb[keys[0]]
        if hasattr(v, "shape"):
            print(f"  向量维度: {v.shape}")
elif hasattr(emb, "shape"):
    print(f"  类型: ndarray shape={emb.shape}")
else:
    print(f"  类型: {type(emb).__name__}")
