"""TreeKG 知识图谱 viewer 后端接入（新版带左侧目录的图谱）。

数据源：TreeKG-main/src/data/{教材}/（toc_tree.json / entity_edges.json /
search_index.json / chunks/*.json / meta.json），由 TreeKG 流水线生成。

路由前缀 /api/treekg/*，viewer 页面挂载在 /treekg。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from competition_app.config import REPOSITORY_ROOT

TREEKG_DATA_ROOT = REPOSITORY_ROOT / "TreeKG-main" / "src" / "data"
TREEKG_STATIC_DIR = REPOSITORY_ROOT / "TreeKG-main" / "src" / "static"

router = APIRouter(prefix="/api/treekg", tags=["treekg"])


def _load_book_json(book: str, filename: str):
    """读取某本教材的数据文件；目录/文件缺失时抛 404。"""
    book_dir = TREEKG_DATA_ROOT / book
    if not book_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"教材不存在: {book}")
    path = book_dir / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"数据缺失: {book}/{filename}")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/textbooks")
def list_textbooks() -> list[dict]:
    """列出所有可用的教材（含 meta）。"""
    if not TREEKG_DATA_ROOT.is_dir():
        return []
    result = []
    for book_dir in sorted(TREEKG_DATA_ROOT.iterdir()):
        if not book_dir.is_dir():
            continue
        meta_path = book_dir / "meta.json"
        if not meta_path.is_file():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        result.append({
            "id": book_dir.name,
            "name": meta.get("title") or book_dir.name,
            "meta": meta,
        })
    return result


@router.get("/{book}/toc")
def book_toc(book: str) -> list:
    """教材目录树。"""
    return _load_book_json(book, "toc_tree.json")


@router.get("/{book}/meta")
def book_meta(book: str) -> dict:
    """教材元信息。"""
    return _load_book_json(book, "meta.json")


@router.get("/{book}/section/{toc_name}")
def book_section(book: str, toc_name: str) -> dict:
    """某个章节的知识图谱 chunk（节点 + 边）。"""
    data = _load_book_json(book, "chunks/_names.json")
    filename = data.get(toc_name)
    if not filename:
        toc_tree = _load_book_json(book, "toc_tree.json")
        toc_node = _find_toc_node(toc_tree, toc_name)
        nodes = [toc_node] if toc_node else []
        return {"tocName": toc_name, "nodes": nodes, "edges": []}
    chunk_path = TREEKG_DATA_ROOT / book / "chunks" / filename
    if not chunk_path.is_file():
        return {"tocName": toc_name, "nodes": [], "edges": []}
    return json.loads(chunk_path.read_text(encoding="utf-8"))


@router.get("/{book}/expand/{entity_name}")
def expand_entity(book: str, entity_name: str) -> dict:
    """展开某个实体的邻居节点和边。"""
    entity_edges = _load_book_json(book, "entity_edges.json")
    search_index = _load_book_json(book, "search_index.json")
    toc_tree = _load_book_json(book, "toc_tree.json")
    neighbors = entity_edges.get(entity_name, [])

    def _find_info(name: str) -> dict:
        for entry in search_index:
            if entry["name"] == name:
                return entry
        toc_info = _find_toc_node(toc_tree, name)
        if toc_info:
            return toc_info
        return {"name": name, "type": "unknown", "level": "noncore"}

    self_info = _find_info(entity_name)
    if not neighbors:
        return {"entity": entity_name, "nodes": [self_info], "edges": []}

    nodes, seen = [], {entity_name}
    for nb in neighbors:
        tgt = nb["target"]
        if tgt not in seen:
            seen.add(tgt)
            nodes.append(_find_info(tgt))

    edges = [
        {
            "source": entity_name,
            "target": nb["target"],
            "type": nb["type"],
            "description": nb.get("description", ""),
        }
        for nb in neighbors
    ]
    return {"entity": entity_name, "nodes": [self_info] + nodes, "edges": edges}


@router.get("/{book}/search")
def search_entities(book: str, q: str = Query("", max_length=100), limit: int = Query(20, ge=1, le=100)) -> list:
    """模糊搜索实体。"""
    search_index = _load_book_json(book, "search_index.json")
    query = q.strip().lower()
    if not query:
        return []
    results = []
    for entry in search_index:
        name = (entry.get("name") or "").lower()
        if query in name:
            results.append(entry)
            if len(results) >= limit:
                break
    return results


def _find_toc_node(tree: list, name: str) -> dict | None:
    """在 TOC 树中递归查找节点。"""
    for node in tree:
        if node.get("name") == name or node.get("id") == name:
            return {
                "name": name,
                "type": "toc",
                "level": "toc",
                "description": node.get("description", name),
            }
        children = node.get("children", [])
        if children:
            result = _find_toc_node(children, name)
            if result:
                return result
    return None


def mount_treekg(app) -> None:
    """挂载 TreeKG viewer 静态页面（/treekg）。"""
    if TREEKG_STATIC_DIR.is_dir():
        app.mount(
            "/treekg",
            StaticFiles(directory=TREEKG_STATIC_DIR, html=True),
            name="treekg_viewer",
        )
