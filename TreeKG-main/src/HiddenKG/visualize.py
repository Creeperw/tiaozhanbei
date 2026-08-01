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
        print(f"[注意] 节点过多（{total}），已裁切到 {len(kept)}（TOC+Core + {max(0,budget)} NonCore）")
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

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>Tree-KG 中医知识图谱</title>
<style>
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; background: #ffffff; font-family: "Microsoft YaHei", sans-serif; }
#mynetwork { width: 100%; height: 100vh; }
#toolbar {
  position: fixed; top: 10px; left: 50%; transform: translateX(-50%); z-index: 20;
  background: rgba(255,255,255,0.94); backdrop-filter: blur(10px);
  border: 1px solid #d0d0d0; border-radius: 10px; padding: 10px 14px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  display: flex; gap: 10px; align-items: center; flex-wrap: wrap; justify-content: center;
}
#search {
  width: 180px; padding: 6px 12px; border-radius: 6px; border: 1px solid #4a4a6a;
  background: #f5f5f5; color: #333; font-size: 14px; outline: none;
}
#search:focus { border-color: #e6a817; }
#search::placeholder { color: #999; }
.btn {
  padding: 6px 14px; border-radius: 6px; border: 1px solid #4a4a6a; cursor: pointer;
  font-size: 13px; color: #555; background: #f0f0f0; transition: all 0.12s; user-select: none;
}
.btn:hover { background: #e0e0e0; color: #333; }
.btn.on.toc { background: #3C7BE6; border-color: #3C7BE6; color: #fff; }
.btn.on.core { background: #e6a817; border-color: #e6a817; color: #fff; }
.btn.on.nc { background: #2BB673; border-color: #2BB673; color: #fff; }
#stats { color: #999; font-size: 12px; min-width: 120px; text-align: center; }
.no-match {
  display: none; position: fixed; top: 70px; left: 50%; transform: translateX(-50%);
  color: #c03030; font-size: 13px; background: rgba(255,240,240,0.95);
  padding: 6px 16px; border-radius: 6px; z-index: 21;
}
#loading {
  display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; z-index: 100;
  background: rgba(255,255,255,0.78); align-items: center; justify-content: center;
}
.loading-box {
  background: rgba(40,40,60,0.85); color: #fff; font-size: 15px;
  padding: 14px 28px; border-radius: 10px; box-shadow: 0 4px 20px rgba(0,0,0,0.2);
}
#hint {
  display: none; position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
  color: #fff; font-size: 13px; background: rgba(40,40,60,0.85);
  padding: 8px 16px; border-radius: 8px; z-index: 30;
}
</style>
<script src="./vendor/vis-network.min.js"></script>
</head>
<body>
<div id="toolbar">
  <input id="search" type="text" placeholder="🔍 搜索实体..." />
  <span id="btn-toc" class="btn on toc" onclick="toggle('toc')">📚 章节 __N_TOC__</span>
  <span id="btn-core" class="btn core" onclick="toggle('core')">⭐ 核心 __N_CORE__</span>
  <span id="btn-noncore" class="btn nc" onclick="toggle('noncore')">🔹 非核心 __N_NC__</span>
  <span id="btn-all" class="btn" onclick="toggleAll()">显示全部</span>
  <span class="btn" onclick="network.fit({animation:true})">🔄 重置</span>
  <span id="stats"></span>
</div>
<div id="no-match" class="no-match">未找到匹配节点</div>
<div id="loading"><div class="loading-box">正在布局计算… <span id="pct">0%</span></div></div>
<div id="hint" class="hint"></div>
<div id="mynetwork"></div>

<script>
// ======== 所有数据 ========
var ALL_N = __ALL_N__;
var ALL_E = __ALL_E__;
var ELABEL = __ELABEL__;

// ======== 索引 ========
var N_BY_GROUP = { toc: [], core: [], noncore: [] };
var NODE_BY_ID = {}, GROUP_OF = {};
ALL_N.forEach(function(n) {
  (N_BY_GROUP[n.group] = N_BY_GROUP[n.group] || []).push(n);
  NODE_BY_ID[n.id] = n;
  GROUP_OF[n.id] = n.group;
});
var E_BY_KEY = {};
ALL_E.forEach(function(e) { E_BY_KEY[e.id] = e; });

// ======== 可见状态：默认仅章节树（分层渲染） ========
var SHOW = { toc: true, core: false, noncore: false };

// ======== 章节 -> 直接关联知识点（toc<->core 边，双向） ========
var TOC_LINKS = {};
ALL_E.forEach(function(e) {
  var f = GROUP_OF[e.from] === 'toc', t = GROUP_OF[e.to] === 'toc';
  if (f === t) return;                       // 同为章节（树）或同为知识点
  var tocId = f ? e.from : e.to;
  (TOC_LINKS[tocId] = TOC_LINKS[tocId] || []).push(e);
});

// ======== 基础工具 ========
function activeNodes() {
  var arr = [];
  for (var g in SHOW) if (SHOW[g]) arr = arr.concat(N_BY_GROUP[g]);
  return arr;
}
function activeEdges(nset) {
  return ALL_E.filter(function(e) { return nset.has(e.from) && nset.has(e.to); });
}
function buildData() {
  var an = activeNodes();
  var nset = new Set(an.map(function(n){ return n.id; }));
  return { nodes: new vis.DataSet(an), edges: new vis.DataSet(activeEdges(nset)) };
}
function updateStats() {
  document.getElementById('stats').textContent =
    '可见 ' + network.body.data.nodes.length + ' 节点 / ' + network.body.data.edges.length + ' 边';
}
function showLoading() { document.getElementById('loading').style.display = 'flex'; }
function hideLoading() { document.getElementById('loading').style.display = 'none'; }
function hint(msg) {
  var h = document.getElementById('hint');
  h.textContent = msg;
  h.style.display = 'block';
  clearTimeout(hint._t);
  hint._t = setTimeout(function() { h.style.display = 'none'; }, 1800);
}
function toggleBtn() {
  ['toc','core','noncore'].forEach(function(g) {
    var b = document.getElementById('btn-'+g);
    if (SHOW[g]) b.classList.add('on'); else b.classList.remove('on');
  });
  var full = SHOW.toc && SHOW.core && SHOW.noncore;
  document.getElementById('btn-all').textContent = full ? '精简' : '显示全部';
}

// ======== 统一视图应用（初始化/分组开关/全图共用） ========
var expanded = {};                 // tocId -> {nodes:[ids], edges:[ids]}
var searchReveal = { nodes: [], edges: [] };
function applyView() {
  expanded = {};
  clearSearchReveal();
  var d = buildData();
  network.setOptions({ physics: { enabled: true, stabilization: { iterations: 40, updateInterval: 20 } } });
  network.setData(d);
  updateStats();
  network.stabilize(40);           // 显式稳定，保证 stabilizationProgress / stabilizationIterationsDone 必然触发
}

// ======== 初始化 ========
var container = document.getElementById('mynetwork');
var initData = buildData();
var options = {
  interaction: { hover: true, tooltipDelay: 80, zoomView: true, dragView: true, navigationButtons: true, keyboard: true },
  layout: { improvedLayout: false },
  physics: {
    enabled: true, stabilization: { iterations: 40, updateInterval: 20 },
    barnesHut: { gravitationalConstant: -30000, centralGravity: 0.10, springLength: 120, springConstant: 0.03, damping: 0.14 }
  },
  edges: { smooth: { type: 'dynamic', roundness: 0.3 }, color: { color:'#888888', opacity:0.4 }, width: 0.6, selectionWidth: 2, hoverWidth: 1.5, font: { size:10, align:'middle', color:'#666' } },
  nodes: { font: { size:12, color:'#333', strokeWidth:1, strokeColor:'#ffffff' } }
};
var network = new vis.Network(container, initData, options);
updateStats();
toggleBtn();

// ======== 布局进度（保留分支的物理动效：稳定后不冻结） ========
network.on('stabilizationProgress', function(p) {
  var total = p.total || 1;
  if (network.body.data.nodes.length > 400) {
    showLoading();
    document.getElementById('pct').textContent = Math.round(p.iterations / total * 100) + '%';
  }
});
network.on('stabilizationIterationsDone', function() {
  hideLoading();
  // 保持 physics.enabled = true：保留分支原有的布局动效（节点飞入/浮动、拖拽连锁响应）
  network.fit({ animation: true });
});

// ======== 环形坐标 ========
function ringPos(cx, cy, n, r) {
  var pts = [];
  for (var i = 0; i < n; i++) {
    var a = -Math.PI / 2 + (2 * Math.PI * i) / n;
    pts.push({ x: Math.round(cx + r * Math.cos(a)), y: Math.round(cy + r * Math.sin(a)) });
  }
  return pts;
}

// ======== 点击章节：展开/收起其知识点 ========
function expandToc(tocId) {
  var links = (TOC_LINKS[tocId] || []).slice(0, 40);
  if (!links.length) { hint('此章节暂无直接关联知识点'); return; }
  var p = network.getPosition(tocId), n = links.length;
  var r = 50 + 6 * n, pts = ringPos(p.x, p.y, n, r);
  var nodes = [], edges = [];
  links.forEach(function(e, i) {
    var kid = GROUP_OF[e.from] === 'toc' ? e.to : e.from;
    if (network.body.data.nodes.get(kid)) return;        // 已可见则跳过
    var nd = NODE_BY_ID[kid];
    if (!nd) return;
    nodes.push({ id: kid, label: nd.label, title: nd.title, color: nd.color, size: nd.size, group: nd.group, shape: 'dot', x: pts[i].x, y: pts[i].y, physics: false });
    edges.push(e);
  });
  network.body.data.nodes.add(nodes);
  network.body.data.edges.add(edges);
  expanded[tocId] = { nodes: nodes.map(function(x){ return x.id; }), edges: edges.map(function(x){ return x.id; }) };
  updateStats();
}
function collapseToc(tocId) {
  var st = expanded[tocId];
  if (!st) return;
  network.body.data.nodes.remove(st.nodes);
  network.body.data.edges.remove(st.edges);
  delete expanded[tocId];
  updateStats();
}
network.on('click', function(prm) {
  if (!prm.nodes || prm.nodes.length !== 1) return;
  var id = prm.nodes[0];
  if (GROUP_OF[id] !== 'toc') return;
  if (expanded[id]) collapseToc(id); else expandToc(id);
});

// ======== 分组切换 / 显示全部 ========
window.toggle = function(g) {
  SHOW[g] = !SHOW[g];
  toggleBtn();
  applyView();
};
window.toggleAll = function() {
  var full = SHOW.toc && SHOW.core && SHOW.noncore;
  if (full) { SHOW = { toc: true, core: false, noncore: false }; }   // 精简 -> 回到章节树
  else { SHOW = { toc: true, core: true, noncore: true }; }          // 显示全部
  toggleBtn();
  applyView();
};

// ======== 边交互 ========
network.on('selectEdge', function(p) {
  (p.edges || []).forEach(function(id) { network.clustering.updateEdge(id, { label: ELABEL[id] || '' }); });
});
network.on('deselectEdge', function(p) {
  var ids = (p.previousSelection && p.previousSelection.edges) ? p.previousSelection.edges : [];
  ids.forEach(function(id) { network.clustering.updateEdge(id, { label: '' }); });
});

// ======== 搜索（命中隐藏节点时临时环形展示） ========
function clearSearchReveal() {
  if (searchReveal.nodes.length) {
    network.body.data.nodes.remove(searchReveal.nodes);
    network.body.data.edges.remove(searchReveal.edges);
  }
  searchReveal = { nodes: [], edges: [] };
}
function revealSearch(ids) {
  clearSearchReveal();
  var toAdd = ids.filter(function(id) { return !network.body.data.nodes.get(id); });
  if (!toAdd.length) return;
  var vp = network.getViewPosition();
  var n = toAdd.length, r = 70 + 7 * n, pts = ringPos(vp.x, vp.y, n, r);
  var nodes = [];
  toAdd.forEach(function(id, i) {
    var nd = NODE_BY_ID[id];
    if (!nd) return;
    nodes.push({ id: id, label: nd.label, title: nd.title, color: nd.color, size: nd.size, group: nd.group, shape: 'dot', x: pts[i].x, y: pts[i].y, physics: false });
  });
  var visible = {};
  network.body.data.nodes.get().forEach(function(x) { visible[x.id] = true; });
  toAdd.forEach(function(id) { visible[id] = true; });
  var edges = [];
  ALL_E.forEach(function(e) {
    if (visible[e.from] && visible[e.to] && !(network.body.data.nodes.get(e.from) && network.body.data.nodes.get(e.to))) {
      edges.push(e);
    }
  });
  network.body.data.nodes.add(nodes);
  network.body.data.edges.add(edges);
  searchReveal = { nodes: toAdd, edges: edges.map(function(x){ return x.id; }) };
}
var si = document.getElementById('search');
var nm = document.getElementById('no-match');
si.addEventListener('input', function() {
  var q = this.value.trim().toLowerCase();
  if (!q) {
    nm.style.display = 'none';
    clearSearchReveal();
    network.fit({ animation: true });
    return;
  }
  // 先在可见节点中匹配
  var curNodes = network.body.data.nodes;
  var matched = curNodes.get({ filter: function(n) {
    return (n.label || '').toLowerCase().indexOf(q) >= 0 || (n.title || '').toLowerCase().indexOf(q) >= 0;
  } }).map(function(n) { return n.id; });
  if (matched.length === 0) {
    // 可见无命中：全局匹配隐藏节点，临时环形展示
    var global = ALL_N.filter(function(n) {
      return (n.label || '').toLowerCase().indexOf(q) >= 0 || (n.title || '').toLowerCase().indexOf(q) >= 0;
    }).map(function(n) { return n.id; });
    if (global.length) {
      revealSearch(global.slice(0, 20));
      matched = global.slice(0, 20);
    }
  }
  if (matched.length === 0) { nm.style.display = 'block'; return; }
  nm.style.display = 'none';
  network.selectNodes(matched, false);
  network.focus(matched[0], { scale: 1.6, animation: true, offset: { x: 0, y: 0 } });
});
</script>
</body>
</html>
"""
    html = (html
        .replace("__ALL_N__", json.dumps(nodes, ensure_ascii=False))
        .replace("__ALL_E__", json.dumps(edges, ensure_ascii=False))
        .replace("__ELABEL__", json.dumps(edge_label_map, ensure_ascii=False))
        .replace("__N_TOC__", str(n_toc))
        .replace("__N_CORE__", str(n_core))
        .replace("__N_NC__", str(n_nc)))
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html, encoding="utf-8")
    print(f"[OK] 可视化生成：{out_html.resolve()}")
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
