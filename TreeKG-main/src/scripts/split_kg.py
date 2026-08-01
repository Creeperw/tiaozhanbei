"""
TreeKG 数据拆分脚本
将 final_kg.json 拆分为按需加载的小文件：
  - toc_tree.json      TOC 层级树
  - chunks/{id}.json   每个章节的实体子图
  - search_index.json  全局搜索索引
  - entity_edges.json  实体邻接表（用于按需展开）
  - meta.json          统计元信息

用法:
  python split_kg.py --input output/中医学基础/02_hidden_kg/final_kg.json --out data/中医学基础/
  python split_kg.py --all   # 处理所有教材
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote


def load_kg(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_toc_tree(nodes: list, edges: list) -> dict:
    """从 toc->toc 边构建 TOC 层级树，返回根节点列表"""
    # 索引 TOC 节点
    toc_nodes = {n["name"]: n for n in nodes if n.get("level") == "toc"}

    # 构建父子关系
    children_map = {}  # parent_name → [child_name, ...]
    for e in edges:
        if e.get("type") == "toc->toc":
            src, tgt = e["source"], e["target"]
            if src in toc_nodes and tgt in toc_nodes:
                children_map.setdefault(src, []).append(tgt)

    # 找出所有被引用的子节点
    all_children = set()
    for children in children_map.values():
        all_children.update(children)

    # 根节点 = TOC 中未被任何节点作为子节点引用的
    roots = [n for n in toc_nodes if n not in all_children]

    seen = set()
    def build_subtree(name: str) -> dict:
        if name in seen:  # 防止循环引用
            return {"id": name, "name": name, "description": name, "children": []}
        seen.add(name)
        node = toc_nodes.get(name, {"name": name, "type": "toc", "description": name})
        children = children_map.get(name, [])
        return {
            "id": name,
            "name": name,
            "description": node.get("description", name),
            "children": [build_subtree(c) for c in children if c not in seen],
        }

    tree = [build_subtree(r) for r in roots if r not in seen]

    # 如果所有节点都是孤立的（没有 toc->toc 边），直接全部作为根
    if not tree:
        tree = [
            {"id": n, "name": n, "description": toc_nodes[n].get("description", n), "children": []}
            for n in toc_nodes
        ]

    return tree


def count_entities_in_subtree(tree_node: dict, toc_core_map: dict, counted: set) -> int:
    """递归统计子树下的实体数量（去重）"""
    total = 0
    for entity in toc_core_map.get(tree_node["id"], []):
        if entity not in counted:
            counted.add(entity)
            total += 1
    for child in tree_node.get("children", []):
        total += count_entities_in_subtree(child, toc_core_map, counted)
    tree_node["entityCount"] = total
    return total


def build_chunks(nodes: list, edges: list, toc_core_map: dict) -> dict:
    """
    为每个 TOC 节点构建 chunk：
    - 该 TOC 节点本身
    - 直接通过 toc->core 连接的实体
    - 这些实体的 1-hop 邻居
    - 所有相关边
    """
    # 快速索引
    node_map = {n["name"]: n for n in nodes}

    # 实体间的边（非 toc->toc, 非 toc->core）
    entity_edges = [e for e in edges if e["type"] not in ("toc->toc", "toc->core")]

    # 构建实体邻接表
    adjacency = {}
    for e in entity_edges:
        src, tgt = e["source"], e["target"]
        adjacency.setdefault(src, set()).add(tgt)
        adjacency.setdefault(tgt, set()).add(src)

    # 边索引：(source, target, type) → edge
    edge_index = {}
    for e in edges:
        key = (e["source"], e["target"], e.get("type", ""))
        edge_index[key] = e

    chunks = {}
    for toc_name, entity_names in toc_core_map.items():
        chunk_nodes = {}
        chunk_edges = {}

        # 添加 TOC 节点本身
        if toc_name in node_map:
            chunk_nodes[toc_name] = node_map[toc_name]

        # 添加直接实体
        direct_entities = set(entity_names)
        for ename in direct_entities:
            if ename in node_map:
                chunk_nodes[ename] = node_map[ename]

        # 收集 1-hop 邻居实体
        hop_neighbors = set()
        for ename in direct_entities:
            for neighbor in adjacency.get(ename, set()):
                if neighbor not in direct_entities and neighbor not in chunk_nodes:
                    hop_neighbors.add(neighbor)

        for ename in hop_neighbors:
            if ename in node_map:
                chunk_nodes[ename] = node_map[ename]

        all_entities = direct_entities | hop_neighbors

        # 收集边
        # 1) toc->core 边
        for ename in direct_entities:
            key = (toc_name, ename, "toc->core")
            if key in edge_index:
                e = edge_index[key]
                chunk_edges[f"{toc_name}|{ename}|toc->core"] = e

        # 2) 涉及这些实体的所有边
        for e in entity_edges:
            src, tgt = e["source"], e["target"]
            if src in all_entities or tgt in all_entities:
                if src in chunk_nodes or src in node_map:
                    if tgt in chunk_nodes or tgt in node_map:
                        eid = f"{src}|{tgt}|{e.get('type','')}"
                        chunk_edges[eid] = e

        chunks[toc_name] = {
            "tocName": toc_name,
            "nodes": list(chunk_nodes.values()),
            "edges": list(chunk_edges.values()),
        }

    return chunks


def build_search_index(nodes: list) -> list:
    """构建搜索索引：仅名称和类型，用于服务端搜索"""
    index = []
    for n in nodes:
        if n.get("level") == "toc":
            continue
        index.append({
            "name": n["name"],
            "type": n.get("type", ""),
            "level": n.get("level", "noncore"),
        })
    return index


def build_entity_adjacency(edges: list) -> dict:
    """构建实体间邻接表，用于按需展开"""
    entity_edges = [e for e in edges if e["type"] not in ("toc->toc", "toc->core")]
    adjacency = {}
    for e in entity_edges:
        src, tgt, etype = e["source"], e["target"], e.get("type", "related")
        adjacency.setdefault(src, []).append({"target": tgt, "type": etype, "description": e.get("description", "")})
        adjacency.setdefault(tgt, []).append({"target": src, "type": etype, "description": e.get("description", "")})
    # 去重
    for name in adjacency:
        seen = set()
        unique = []
        for entry in adjacency[name]:
            key = (entry["target"], entry["type"])
            if key not in seen:
                seen.add(key)
                unique.append(entry)
        adjacency[name] = unique
    return adjacency


def safe_filename(name: str) -> str:
    """将 TOC 名称转为安全文件名"""
    # 使用 URL 编码保留中文可读性，但替换特殊字符
    safe = quote(name, safe="")
    # 限制长度
    if len(safe) > 120:
        safe = safe[:120]
    return safe + ".json"


def process_kg(input_path: Path, output_dir: Path):
    """处理单个 final_kg.json"""
    print(f"\n{'='*60}")
    print(f"处理: {input_path}")
    kg = load_kg(input_path)
    nodes = kg.get("nodes", [])
    edges = kg.get("edges", [])

    toc_count = sum(1 for n in nodes if n.get("level") == "toc")
    entity_count = len(nodes) - toc_count
    print(f"节点: {len(nodes)} (TOC: {toc_count}, 实体: {entity_count})")
    print(f"边: {len(edges)}")

    # 0) toc->core 映射
    toc_core_map = {}
    for e in edges:
        if e.get("type") == "toc->core":
            toc_core_map.setdefault(e["source"], []).append(e["target"])

    linked = set()
    for v in toc_core_map.values():
        linked.update(v)
    orphan_count = entity_count - len(linked)
    print(f"直连实体: {len(linked)}, 孤儿实体: {orphan_count}")

    # 1) TOC 树
    print("构建 TOC 树...")
    toc_tree = build_toc_tree(nodes, edges)
    counted = set()
    for root in toc_tree:
        count_entities_in_subtree(root, toc_core_map, counted)

    toc_tree_path = output_dir / "toc_tree.json"
    toc_tree_path.parent.mkdir(parents=True, exist_ok=True)
    with open(toc_tree_path, "w", encoding="utf-8") as f:
        json.dump(toc_tree, f, ensure_ascii=False)
    print(f"  → {toc_tree_path} ({toc_tree_path.stat().st_size // 1024} KB)")

    # 2) Chunks
    print("构建章节 chunks...")
    chunks = build_chunks(nodes, edges, toc_core_map)
    chunks_dir = output_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    total_chunk_size = 0
    name_to_file = {}
    for toc_name, chunk in chunks.items():
        fname = safe_filename(toc_name)
        name_to_file[toc_name] = fname
        chunk_path = chunks_dir / fname
        with open(chunk_path, "w", encoding="utf-8") as f:
            json.dump(chunk, f, ensure_ascii=False)
        total_chunk_size += chunk_path.stat().st_size
    # 保存名称映射
    map_path = chunks_dir / "_names.json"
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(name_to_file, f, ensure_ascii=False)
    print(f"  → {len(chunks)} chunks ({total_chunk_size // 1024} KB total)")

    # 3) 搜索索引
    print("构建搜索索引...")
    search_index = build_search_index(nodes)
    si_path = output_dir / "search_index.json"
    with open(si_path, "w", encoding="utf-8") as f:
        json.dump(search_index, f, ensure_ascii=False)
    print(f"  → {si_path} ({si_path.stat().st_size // 1024} KB)")

    # 4) 实体邻接表
    print("构建实体邻接表...")
    entity_adj = build_entity_adjacency(edges)
    ea_path = output_dir / "entity_edges.json"
    with open(ea_path, "w", encoding="utf-8") as f:
        json.dump(entity_adj, f, ensure_ascii=False)
    print(f"  → {ea_path} ({ea_path.stat().st_size // 1024} KB)")

    # 5) 元信息
    meta = {
        "name": output_dir.name,
        "totalNodes": len(nodes),
        "tocNodes": toc_count,
        "coreEntities": sum(1 for n in nodes if n.get("level") == "core"),
        "noncoreEntities": sum(1 for n in nodes if n.get("level") == "noncore"),
        "totalEdges": len(edges),
        "sections": len(chunks),
        "orphanEntities": orphan_count,
    }
    meta_path = output_dir / "meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"  → {meta_path}")

    # 总结
    total_size = sum(
        p.stat().st_size
        for p in output_dir.rglob("*.json")
    )
    print(f"\n总数据大小: {total_size // 1024} KB (原始: {input_path.stat().st_size // 1024} KB)")
    print(f"拆分文件数: {len(chunks) + 4}")


def main():
    ap = argparse.ArgumentParser(description="拆分 final_kg.json 为按需加载的小文件")
    ap.add_argument("--input", "-i", type=str, help="final_kg.json 路径")
    ap.add_argument("--out", "-o", type=str, help="输出目录")
    ap.add_argument("--all", action="store_true", help="处理所有教材")
    args = ap.parse_args()

    if args.all:
        output_base = Path(__file__).parent.parent / "output"
        data_base = Path(__file__).parent.parent / "data"
        for kg_path in sorted(output_base.rglob("final_kg.json")):
            rel = kg_path.parent.parent  # 如 "中医学基础"
            textbook_name = rel.name
            out_dir = data_base / textbook_name
            process_kg(kg_path, out_dir)
    elif args.input and args.out:
        process_kg(Path(args.input), Path(args.out))
    else:
        # 默认：处理中医学基础
        src_root = Path(__file__).parent.parent
        default_input = src_root / "output" / "中医学基础" / "02_hidden_kg" / "final_kg.json"
        default_out = src_root / "data" / "中医学基础"
        if default_input.exists():
            process_kg(default_input, default_out)
        else:
            print("请指定 --input 和 --out，或使用 --all")
            sys.exit(1)


if __name__ == "__main__":
    main()
