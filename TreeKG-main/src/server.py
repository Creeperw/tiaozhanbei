"""
TreeKG 知识图谱服务器
纯 Python 标准库实现，零外部依赖。

启动:
  cd TreeKG-main/src
  python server.py

然后浏览器打开 http://localhost:8080
"""

import json
import os
import sys
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"

# ── 全局数据缓存 ─────────────────────────────────────────
# 结构: { "中医学基础": { toc_tree, chunks, search_index, entity_edges, meta, ... }, ... }
DB = {}


def load_all():
    """加载所有教材的拆分数据到内存"""
    global DB
    if not DATA_DIR.exists():
        print(f"[警告] 数据目录不存在: {DATA_DIR}")
        print("  请先运行: python scripts/split_kg.py --all")
        return

    for textbook_dir in sorted(DATA_DIR.iterdir()):
        if not textbook_dir.is_dir():
            continue
        # 必须有 meta.json 才认为是教材数据目录
        if not (textbook_dir / "meta.json").exists():
            continue
        name = textbook_dir.name
        print(f"  加载: {name} ...", end=" ", flush=True)

        try:
            textbook_data = {}

            # toc_tree
            toc_path = textbook_dir / "toc_tree.json"
            if toc_path.exists():
                textbook_data["toc_tree"] = json.loads(toc_path.read_text("utf-8"))

            # chunks 名称映射 + 延迟加载 chunk 内容
            chunks_dir = textbook_dir / "chunks"
            names_path = chunks_dir / "_names.json"
            if names_path.exists():
                textbook_data["chunk_names"] = json.loads(names_path.read_text("utf-8"))

            # search_index
            si_path = textbook_dir / "search_index.json"
            if si_path.exists():
                textbook_data["search_index"] = json.loads(si_path.read_text("utf-8"))

            # entity_edges
            ee_path = textbook_dir / "entity_edges.json"
            if ee_path.exists():
                textbook_data["entity_edges"] = json.loads(ee_path.read_text("utf-8"))

            # meta
            meta_path = textbook_dir / "meta.json"
            if meta_path.exists():
                textbook_data["meta"] = json.loads(meta_path.read_text("utf-8"))

            textbook_data["chunks_dir"] = str(chunks_dir)

            # 统计
            chunk_count = len(textbook_data.get("chunk_names", {}))
            node_count = textbook_data.get("meta", {}).get("totalNodes", 0)
            print(f"OK ({node_count} 节点, {chunk_count} chunks)")

            DB[name] = textbook_data
        except Exception as e:
            print(f"失败: {e}")

    total_nodes = sum(d.get("meta", {}).get("totalNodes", 0) for d in DB.values())
    print(f"  总计: {len(DB)} 本教材, {total_nodes} 节点, 内存 ~{estimate_memory():.0f} MB\n")


def estimate_memory() -> float:
    """估算内存占用"""
    total_bytes = 0
    for name, data in DB.items():
        for key in ("toc_tree", "search_index", "entity_edges", "meta"):
            if key in data:
                total_bytes += len(json.dumps(data[key], ensure_ascii=False))
    return total_bytes / (1024 * 1024)


def load_chunk(textbook: str, toc_name: str) -> dict:
    """按需加载单个 chunk 文件，无数据时返回空结构"""
    data = DB.get(textbook)
    if not data:
        return {"tocName": toc_name, "nodes": [], "edges": []}
    chunk_names = data.get("chunk_names", {})
    filename = chunk_names.get(toc_name)
    if not filename:
        # 该章节没有实体，返回 TOC 节点本身
        toc_node = _find_toc_node(data.get("toc_tree", []), toc_name)
        nodes = [toc_node] if toc_node else []
        return {"tocName": toc_name, "nodes": nodes, "edges": []}
    chunk_path = Path(data["chunks_dir"]) / filename
    if not chunk_path.exists():
        return {"tocName": toc_name, "nodes": [], "edges": []}
    return json.loads(chunk_path.read_text("utf-8"))


def _find_toc_node(tree: list, name: str) -> dict | None:
    """在 TOC 树中递归查找节点"""
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


# ── API 处理 ─────────────────────────────────────────────


def api_textbooks() -> list:
    """列出所有可用教材"""
    return [
        {
            "id": name,
            "name": name,
            "meta": data.get("meta", {}),
        }
        for name, data in DB.items()
    ]


def api_toc(textbook: str) -> dict | None:
    """返回 TOC 树"""
    data = DB.get(textbook)
    if not data:
        return None
    return data.get("toc_tree")


def api_section(textbook: str, toc_name: str) -> dict | None:
    """返回某个章节的 chunk 数据"""
    return load_chunk(textbook, toc_name)


def api_expand(textbook: str, entity_name: str) -> dict | None:
    """返回某个实体的邻居节点和边"""
    data = DB.get(textbook)
    if not data:
        return None

    entity_edges = data.get("entity_edges", {})
    neighbors = entity_edges.get(entity_name, [])
    search_index = data.get("search_index", [])
    toc_tree = data.get("toc_tree", [])

    def _find_info(name: str) -> dict:
        """从 search_index 或 toc_tree 查找节点信息"""
        for entry in search_index:
            if entry["name"] == name:
                return entry
        toc_info = _find_toc_node(toc_tree, name)
        if toc_info:
            return toc_info
        return {"name": name, "type": "unknown", "level": "noncore"}

    self_info = _find_info(entity_name)

    if not neighbors:
        return {
            "entity": entity_name,
            "nodes": [self_info],
            "edges": [],
        }

    # 收集邻居节点
    nodes = []
    seen = {entity_name}
    for nb in neighbors:
        tgt = nb["target"]
        if tgt not in seen:
            seen.add(tgt)
            nodes.append(_find_info(tgt))

    # 构建边
    edges = [
        {
            "source": entity_name,
            "target": nb["target"],
            "type": nb["type"],
            "description": nb.get("description", ""),
        }
        for nb in neighbors
    ]

    return {
        "entity": entity_name,
        "nodes": [self_info] + nodes,
        "edges": edges,
    }


def api_search(textbook: str, query: str, limit: int = 20) -> list:
    """模糊搜索实体"""
    data = DB.get(textbook)
    if not data:
        return []

    search_index = data.get("search_index", [])
    q = query.lower().strip()
    if not q:
        return []

    results = []
    for entry in search_index:
        name = entry["name"]
        if q in name.lower():
            results.append(entry)
            if len(results) >= limit:
                break

    return results


def api_meta(textbook: str) -> dict | None:
    """返回元信息"""
    data = DB.get(textbook)
    if not data:
        return None
    return data.get("meta")


# ── HTTP 服务器 ──────────────────────────────────────────


class APIHandler(SimpleHTTPRequestHandler):
    """自定义请求处理器：/api/* 走 API，其他走静态文件"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        # 首页
        if path == "/" or path == "":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            index_path = STATIC_DIR / "index.html"
            if index_path.exists():
                self.wfile.write(index_path.read_bytes())
            else:
                self.wfile.write(b"<h1>TreeKG Server Running</h1><p>index.html not found</p>")
            return

        # API 路由
        if path.startswith("/api/"):
            self._handle_api(path, params)
            return

        # 静态文件
        super().do_GET()

    def _handle_api(self, path: str, params: dict):
        try:
            result = None
            # GET /api/textbooks
            if path == "/api/textbooks":
                result = api_textbooks()

            # GET /api/{textbook}/toc
            elif path.endswith("/toc"):
                textbook = self._extract_textbook(path, "/toc")
                result = api_toc(textbook)

            # GET /api/{textbook}/meta
            elif path.endswith("/meta"):
                textbook = self._extract_textbook(path, "/meta")
                result = api_meta(textbook)

            # GET /api/{textbook}/search?q=xxx
            elif "/search" in path:
                textbook = self._extract_textbook(path, "/search")
                q = params.get("q", [""])[0]
                limit = int(params.get("limit", ["20"])[0])
                result = api_search(textbook, q, limit)

            # GET /api/{textbook}/section/{name}
            elif "/section/" in path:
                parts = path.split("/section/", 1)
                if len(parts) == 2:
                    textbook = urllib.parse.unquote(parts[0].replace("/api/", ""))
                    toc_name = urllib.parse.unquote(parts[1])
                    result = api_section(textbook, toc_name)

            # GET /api/{textbook}/expand/{name}
            elif "/expand/" in path:
                parts = path.split("/expand/", 1)
                if len(parts) == 2:
                    textbook = urllib.parse.unquote(parts[0].replace("/api/", ""))
                    entity_name = urllib.parse.unquote(parts[1])
                    result = api_expand(textbook, entity_name)

            if result is None:
                self._send_error(404, "Not Found")
                return

            self._send_json(result)
        except Exception as e:
            self._send_error(500, str(e))

    def _extract_textbook(self, path: str, suffix: str) -> str:
        """从 path 中提取教材名称（URL 解码）"""
        # /api/%E4%B8%AD%E5%8C%BB.../toc → 中医学基础
        prefix = "/api/"
        rest = path[len(prefix):]
        if suffix in rest:
            rest = rest[: rest.rindex(suffix)]
        return urllib.parse.unquote(rest.strip("/"))

    def _send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: int, msg: str):
        body = json.dumps({"error": msg}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # 简洁日志
        if "/api/" in str(args[0]):
            print(f"  {args[0]}")
        else:
            pass  # 忽略静态文件请求日志


def main():
    port = 8080
    if len(sys.argv) > 1:
        port = int(sys.argv[1])

    print("=" * 55)
    print("  TreeKG 知识图谱服务器")
    print("=" * 55)
    print("\n加载数据...")
    load_all()

    if not DB:
        print("\n[错误] 没有加载到任何数据，请先运行拆分脚本:")
        print("  python scripts/split_kg.py --all")
        sys.exit(1)

    print(f"启动服务器: http://localhost:{port}")
    print(f"按 Ctrl+C 停止\n")

    server = HTTPServer(("0.0.0.0", port), APIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        server.server_close()


if __name__ == "__main__":
    main()
