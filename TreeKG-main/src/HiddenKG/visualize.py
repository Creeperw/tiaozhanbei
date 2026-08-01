# 纯前端 vis-network 可视化 — 支持搜索 + 分类筛选
import argparse
import json
from pathlib import Path
from html import escape
from config_utils import get_output_dir

def load_final_kg(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def build_nodes_edges(kg: dict, toc_color: str, entity_color: str, core_color: str,
                      toc_size: int, core_size: int, noncore_size: int, max_nodes: int):
    nodes_out = []
    node_idx = {}

    for n in kg.get("nodes", []):
        name = (n.get("name") or "").strip()
        desc  = (n.get("description") or "").strip()
        level = (n.get("level") or "").strip().lower()
        if not name:
            continue

        is_toc = (level == "toc")
        is_core = (level == "core")
        if is_toc:
            color, size, group = toc_color, toc_size, "toc"
        elif is_core:
            color, size, group = core_color, core_size, "core"
        else:
            color, size, group = entity_color, noncore_size, "noncore"

        title = escape(desc) if desc else name

        if name in node_idx:
            i = node_idx[name]
            old = nodes_out[i]
            if old.get("group") != "toc" and group == "toc":
                old["color"], old["size"], old["group"] = toc_color, toc_size, "toc"
            old["title"] = title if len(title) > len(old.get("title", "")) else old["title"]
        else:
            node_idx[name] = len(nodes_out)
            nodes_out.append({
                "id": name, "label": name, "title": title,
                "color": color, "size": size, "group": group, "shape": "dot"
            })

    total = len(nodes_out)
    if max_nodes > 0 and total > max_nodes:
        core_toc = [n for n in nodes_out if n["group"] in ("toc", "core")]
        noncores = [n for n in nodes_out if n["group"] == "noncore"]
        budget = max_nodes - len(core_toc)
        kept = core_toc + noncores[:max(0, budget)]
        kept_names = {n["id"] for n in kept}
        print(f"⚠️ 节点过多（{total}），已裁切到 {len(kept)}（TOC+Core + {max(0,budget)} NonCore）")
        nodes_out = kept

    node_names = {n["id"] for n in nodes_out}
    edges_out = []
    edge_label_map = {}
    edge_length_map = {
        "toc->toc": 65, "toc->core": 60, "core->non-core": 60, "pred": 60, "_default": 40,
    }
    for idx, e in enumerate(kg.get("edges", [])):
        src = (e.get("source") or "").strip()
        tgt = (e.get("target") or "").strip()
        et  = (e.get("type") or "").strip()
        desc = (e.get("description") or "").strip()
        if not src or not tgt or src == tgt:
            continue
        if src not in node_names or tgt not in node_names:
            continue
        length = edge_length_map.get(et, edge_length_map["_default"])
        label_txt = f"{et} | {desc}" if desc else et
        eid = f"{src}|{tgt}|{et}|{idx}"
        edges_out.append({
            "id": eid, "from": src, "to": tgt,
            "title": escape(label_txt) if label_txt else "",
            "length": length,
            "arrows": "to" if et in ("core->non-core", "toc->core", "toc->toc") else "",
            "group": et,
        })
        edge_label_map[eid] = label_txt

    return nodes_out, edges_out, edge_label_map


def write_vis_html(nodes: list, edges: list, edge_label_map: dict, out_html: Path):
    n_toc = sum(1 for n in nodes if n["group"] == "toc")
    n_core = sum(1 for n in nodes if n["group"] == "core")
    n_nc  = sum(1 for n in nodes if n["group"] == "noncore")
    total = len(nodes)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>Tree-KG 中医知识图谱</title>
<style>
* {{ box-sizing: border-box; }}
html, body {{ height: 100%; margin: 0; background: #ffffff; font-family: "Microsoft YaHei", sans-serif; }}
#mynetwork {{ width: 100%; height: 100vh; }}
#toolbar {{
  position: fixed; top: 10px; left: 50%; transform: translateX(-50%); z-index: 20;
  background: rgba(255,255,255,0.94); backdrop-filter: blur(10px);
  border: 1px solid #d0d0d0; border-radius: 10px; padding: 10px 14px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  display: flex; gap: 10px; align-items: center; flex-wrap: wrap; justify-content: center;
}}
#search {{
  width: 180px; padding: 6px 12px; border-radius: 6px; border: 1px solid #4a4a6a;
  background: #f5f5f5; color: #333; font-size: 14px; outline: none;
}}
#search:focus {{ border-color: #e6a817; }}
#search::placeholder {{ color: #999; }}
.btn {{
  padding: 6px 14px; border-radius: 6px; border: 1px solid #4a4a6a; cursor: pointer;
  font-size: 13px; color: #555; background: #f0f0f0; transition: all 0.12s; user-select: none;
}}
.btn:hover {{ background: #e0e0e0; color: #333; }}
.btn.on.toc {{ background: #3C7BE6; border-color: #3C7BE6; color: #fff; }}
.btn.on.core {{ background: #e6a817; border-color: #e6a817; color: #fff; }}
.btn.on.nc {{ background: #2BB673; border-color: #2BB673; color: #fff; }}
#stats {{ color: #999; font-size: 12px; min-width: 120px; text-align: center; }}
.no-match {{
  display: none; position: fixed; top: 70px; left: 50%; transform: translateX(-50%);
  color: #c03030; font-size: 13px; background: rgba(255,240,240,0.95);
  padding: 6px 16px; border-radius: 6px; z-index: 21;
}}
</style>
<script src="https://cdn.jsdelivr.net/npm/vis-network@9.1.6/standalone/umd/vis-network.min.js"></script>
</head>
<body>
<div id="toolbar">
  <input id="search" type="text" placeholder="🔍 搜索实体..." />
  <span id="btn-toc" class="btn on toc" onclick="toggle('toc')">📚 章节 {n_toc}</span>
  <span id="btn-core" class="btn on core" onclick="toggle('core')">⭐ 核心 {n_core}</span>
  <span id="btn-nc" class="btn on nc" onclick="toggle('noncore')">🔹 非核心 {n_nc}</span>
  <span class="btn" onclick="network.fit({{animation:true}})">🔄 重置</span>
  <span id="stats"></span>
</div>
<div id="no-match" class="no-match">未找到匹配节点</div>
<div id="mynetwork"></div>

<script>
// ======== 所有数据 ========
var ALL_N = {json.dumps(nodes, ensure_ascii=False)};
var ALL_E = {json.dumps(edges, ensure_ascii=False)};
var ELABEL = {json.dumps(edge_label_map, ensure_ascii=False)};

// ======== 按 group 索引 ========
var N_BY_GROUP = {{ toc: [], core: [], noncore: [] }};
ALL_N.forEach(function(n) {{ (N_BY_GROUP[n.group] = N_BY_GROUP[n.group] || []).push(n); }});

var E_BY_KEY = {{}};
ALL_E.forEach(function(e) {{ E_BY_KEY[e.id] = e; }});

// ======== 可见状态 ========
var SHOW = {{ toc: true, core: true, noncore: true }};

function activeNodes() {{
  var arr = [];
  for (var g in SHOW) if (SHOW[g]) arr = arr.concat(N_BY_GROUP[g]);
  return arr;
}}

function activeEdges(nset) {{
  return ALL_E.filter(function(e) {{ return nset.has(e.from) && nset.has(e.to); }});
}}

function buildData() {{
  var an = activeNodes();
  var nset = new Set(an.map(function(n){{return n.id}}));
  var ae = activeEdges(nset);
  return {{ nodes: new vis.DataSet(an), edges: new vis.DataSet(ae) }};
}}

function rebuild() {{
  var d = buildData();
  network.setData(d);
  updateStats(d);
  document.getElementById('search').value = '';
  document.getElementById('no-match').style.display = 'none';
}}

function updateStats(d) {{
  d = d || buildData();
  document.getElementById('stats').textContent =
    '可见 ' + d.nodes.length + ' 节点 / ' + d.edges.length + ' 边';
}}

// ======== 初始化 ========
var container = document.getElementById('mynetwork');
var initData = buildData();
var options = {{
  interaction: {{ hover: true, tooltipDelay: 80, zoomView: true, dragView: true, navigationButtons: true, keyboard: true }},
  physics: {{
    enabled: true, stabilization: {{ iterations: 200, updateInterval: 20 }},
    barnesHut: {{ gravitationalConstant: -30000, centralGravity: 0.10, springLength: 120, springConstant: 0.03, damping: 0.14 }}
  }},
  edges: {{ smooth: true, color: {{ color:'#888888', opacity:0.4 }}, width: 0.6, selectionWidth: 2, hoverWidth: 1.5, font: {{ size:10, align:'middle', color:'#666' }} }},
  nodes: {{ font: {{ size:12, color:'#333', strokeWidth:1, strokeColor:'#ffffff' }} }}
}};
var network = new vis.Network(container, initData, options);

network.once("stabilizationIterationsDone", function() {{ network.fit({{animation:true}}); }});
updateStats(initData);

// ======== 边交互 ========
network.on('selectEdge', function(p) {{
  (p.edges||[]).forEach(function(id) {{ network.clustering.updateEdge(id, {{label: ELABEL[id]||''}}); }});
}});
network.on('deselectEdge', function(p) {{
  var ids = (p.previousSelection&&p.previousSelection.edges) ? p.previousSelection.edges : [];
  ids.forEach(function(id) {{ network.clustering.updateEdge(id, {{label:''}}); }});
}});

// ======== 分类切换 ========
window.toggle = function(g) {{
  SHOW[g] = !SHOW[g];
  var btn = document.getElementById('btn-'+g);
  if (SHOW[g]) btn.classList.add('on'); else btn.classList.remove('on');
  rebuild();
  network.fit({{animation:true}});
}};

// ======== 搜索 ========
var si = document.getElementById('search');
var nm = document.getElementById('no-match');
si.addEventListener('input', function() {{
  var q = this.value.trim().toLowerCase();
  if (!q) {{ nm.style.display='none'; network.fit({{animation:true}}); return; }}
  // 在所有可见节点中匹配
  var curNodes = network.body.data.nodes;
  var matched = curNodes.get({{ filter: function(n){{
    return (n.label||'').toLowerCase().indexOf(q)>=0 || (n.title||'').toLowerCase().indexOf(q)>=0;
  }}, returnType:'id' }});
  if (matched.length===0) {{ nm.style.display='block'; return; }}
  nm.style.display='none';
  network.selectNodes(matched, false);
  network.focus(matched[0], {{ scale:1.6, animation:true, offset:{{x:0,y:0}} }});
}});
</script>
</body>
</html>
"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html, encoding="utf-8")
    print(f"✅ 可视化生成：{out_html.resolve()}")
    print(f"   TOC={n_toc}  Core={n_core}  NonCore={n_nc}  (共{total}节点 / {len(edges)}边)")


def main():
    ap = argparse.ArgumentParser(description="可视化 final_kg.json（vis-network）")
    ap.add_argument("--kg", type=str,
                    default=str(get_output_dir("hidden") / "final_kg.json"),
                    help="final_kg.json 路径")
    ap.add_argument("--out", type=str,
                    default=str(get_output_dir("hidden") / "final_kg.html"),
                    help="输出 HTML 路径")
    ap.add_argument("--toc_color", type=str, default="#3C7BE6")
    ap.add_argument("--entity_color", type=str, default="#2BB673")
    ap.add_argument("--core_color", type=str, default="#e6a817")
    ap.add_argument("--toc_size", type=int, default=22)
    ap.add_argument("--core_size", type=int, default=18)
    ap.add_argument("--noncore_size", type=int, default=12)
    ap.add_argument("--max_nodes", type=int, default=0,
            help="最多保留节点数；0 表示使用 final_kg.json 中的全部节点")
    args = ap.parse_args()

    kg = load_final_kg(Path(args.kg))
    nodes, edges, edge_label_map = build_nodes_edges(
        kg,
        toc_color=args.toc_color, entity_color=args.entity_color,
        core_color=args.core_color,
        toc_size=args.toc_size, core_size=args.core_size,
        noncore_size=args.noncore_size, max_nodes=args.max_nodes
    )
    write_vis_html(nodes, edges, edge_label_map, Path(args.out))


if __name__ == "__main__":
    main()
