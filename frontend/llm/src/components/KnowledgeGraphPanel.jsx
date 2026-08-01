import React, { useEffect, useMemo, useRef, useState } from 'react';
import { LoaderCircle, Move, Network, RotateCcw } from 'lucide-react';
import { listKnowledgeGraphs, loadKnowledgeGraph } from './workshop-textbook/textbookPdfApi';
import './knowledgeGraphPanel.css';

const TYPE_COLORS = ['#2f9e77', '#e08a3c', '#5b7fd4', '#a05bbd', '#c2555f', '#3aa5b5', '#8a9b3f', '#b0793f'];
const VIEW_W = 960;
const VIEW_H = 640;
const MAX_NODES = 320;

const typeColor = (type) => {
  const key = String(type || '');
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return TYPE_COLORS[hash % TYPE_COLORS.length];
};

function pickSubgraph(nodes, edges) {
  const byName = new Map(nodes.map((node) => [String(node.name), node]));
  const adj = new Map();
  for (const edge of edges) {
    const a = String(edge.source);
    const b = String(edge.target);
    if (!byName.has(a) || !byName.has(b) || a === b) continue;
    if (!adj.has(a)) adj.set(a, []);
    if (!adj.has(b)) adj.set(b, []);
    adj.get(a).push(b);
    adj.get(b).push(a);
  }
  const degree = (name) => (adj.get(name) || []).length;
  const sorted = [...nodes].sort((x, y) => degree(String(y.name)) - degree(String(x.name)));
  const picked = new Set();
  const queue = [];
  if (sorted.length && degree(String(sorted[0].name)) > 0) {
    picked.add(String(sorted[0].name));
    queue.push(String(sorted[0].name));
  }
  while (queue.length && picked.size < MAX_NODES) {
    const current = queue.shift();
    for (const next of adj.get(current) || []) {
      if (!picked.has(next) && picked.size < MAX_NODES) {
        picked.add(next);
        queue.push(next);
      }
    }
  }
  // 补一些孤立节点（低度优先）
  for (const node of sorted) {
    if (picked.size >= MAX_NODES) break;
    const name = String(node.name);
    if (!picked.has(name)) picked.add(name);
  }
  const nameToIndex = new Map();
  const kept = [];
  for (const node of nodes) {
    if (picked.has(String(node.name))) {
      nameToIndex.set(String(node.name), kept.length);
      kept.push(node);
    }
  }
  const keptEdges = edges.filter((edge) => nameToIndex.has(String(edge.source)) && nameToIndex.has(String(edge.target)) && String(edge.source) !== String(edge.target));
  return { nodes: kept, edges: keptEdges, totalNodes: nodes.length, shownNodes: kept.length, nameToIndex };
}

function forceLayout(nodes, edges, nameToIndex) {
  const n = nodes.length;
  if (!n) return [];
  const adj = [];
  for (const edge of edges) {
    const a = nameToIndex.get(String(edge.source));
    const b = nameToIndex.get(String(edge.target));
    if (a !== undefined && b !== undefined && a !== b) adj.push([a, b]);
  }
  const pos = nodes.map(() => ({
    x: VIEW_W / 2 + (Math.random() - 0.5) * VIEW_W * 0.5,
    y: VIEW_H / 2 + (Math.random() - 0.5) * VIEW_H * 0.5,
  }));
  // Fruchterman-Reingold：温度冷却 + 位移上限，保证收敛且不产生 NaN
  const k = Math.sqrt((VIEW_W * VIEW_H) / Math.max(n, 1)) * 1.1;
  let temperature = Math.sqrt(VIEW_W * VIEW_H) / 12;
  const iterations = n > 200 ? 140 : 220;
  for (let iter = 0; iter < iterations; iter += 1) {
    const disp = nodes.map(() => ({ x: 0, y: 0 }));
    for (let i = 0; i < n; i += 1) {
      for (let j = i + 1; j < n; j += 1) {
        const dx = pos[i].x - pos[j].x;
        const dy = pos[i].y - pos[j].y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) d2 = 0.01;
        const d = Math.sqrt(d2);
        const f = (k * k) / d;
        const fx = (dx / d) * f;
        const fy = (dy / d) * f;
        disp[i].x += fx; disp[i].y += fy;
        disp[j].x -= fx; disp[j].y -= fy;
      }
    }
    for (const [a, b] of adj) {
      const dx = pos[b].x - pos[a].x;
      const dy = pos[b].y - pos[a].y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 0.01) d2 = 0.01;
      const d = Math.sqrt(d2);
      const f = (d * d) / k;
      const fx = (dx / d) * f;
      const fy = (dy / d) * f;
      disp[a].x += fx; disp[a].y += fy;
      disp[b].x -= fx; disp[b].y -= fy;
    }
    for (let i = 0; i < n; i += 1) {
      disp[i].x += (VIEW_W / 2 - pos[i].x) * 0.01;
      disp[i].y += (VIEW_H / 2 - pos[i].y) * 0.01;
    }
    for (let i = 0; i < n; i += 1) {
      const d = Math.sqrt(disp[i].x * disp[i].x + disp[i].y * disp[i].y) || 1;
      const move = Math.min(d, temperature);
      pos[i].x += (disp[i].x / d) * move;
      pos[i].y += (disp[i].y / d) * move;
      pos[i].x = Math.max(10, Math.min(VIEW_W - 10, pos[i].x));
      pos[i].y = Math.max(10, Math.min(VIEW_H - 10, pos[i].y));
    }
    temperature = Math.max(0.5, temperature * 0.92);
  }
  return pos;
}

export default function KnowledgeGraphPanel({ initialBookId = '' }) {
  const [books, setBooks] = useState([]);
  const [selected, setSelected] = useState('');
  const [graph, setGraph] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [hover, setHover] = useState(null);
  const [viewTransform, setViewTransform] = useState({ x: 0, y: 0, scale: 1 });
  const dragState = useRef(null);

  useEffect(() => {
    let alive = true;
    listKnowledgeGraphs()
      .then((payload) => {
        if (!alive) return null;
        const items = payload.items || [];
        setBooks(items);
        if (!items.length) return null;
        const preferred = items.find((item) => item.book_id === initialBookId) || items[0];
        setSelected(preferred.book_id);
        return loadKnowledgeGraph(preferred.book_id);
      })
      .then((payload) => { if (alive && payload) setGraph(payload); })
      .catch((reason) => { if (alive) setError(reason.message || '加载知识图谱失败'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const selectBook = (bookId) => {
    setSelected(bookId);
    setLoading(true);
    setError('');
    loadKnowledgeGraph(bookId)
      .then((payload) => setGraph(payload))
      .catch((reason) => setError(reason.message || '加载知识图谱失败'))
      .finally(() => setLoading(false));
  };

  const layout = useMemo(() => {
    if (!graph) return null;
    const sub = pickSubgraph(graph.nodes || [], graph.edges || []);
    const positions = forceLayout(sub.nodes, sub.edges, sub.nameToIndex);
    const degree = new Map();
    for (const edge of sub.edges) {
      degree.set(String(edge.source), (degree.get(String(edge.source)) || 0) + 1);
      degree.set(String(edge.target), (degree.get(String(edge.target)) || 0) + 1);
    }
    return { ...sub, positions, degree };
  }, [graph]);

  const labelNames = useMemo(() => {
    if (!layout) return new Set();
    const ranked = [...layout.nodes].sort((a, b) => (layout.degree.get(String(b.name)) || 0) - (layout.degree.get(String(a.name)) || 0));
    return new Set(ranked.slice(0, 36).map((node) => String(node.name)));
  }, [layout]);

  const edgeTypes = useMemo(() => {
    const types = new Set((graph?.edges || []).map((edge) => String(edge.type || 'related')));
    return [...types].slice(0, 8);
  }, [graph]);

  const onPointerDown = (event) => {
    dragState.current = {
      startX: event.clientX,
      startY: event.clientY,
      originX: viewTransform.x,
      originY: viewTransform.y,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const onPointerMove = (event) => {
    if (!dragState.current) return;
    const dx = event.clientX - dragState.current.startX;
    const dy = event.clientY - dragState.current.startY;
    setViewTransform((current) => ({
      ...current,
      x: dragState.current.originX + dx,
      y: dragState.current.originY + dy,
    }));
  };

  const onPointerUp = () => { dragState.current = null; };

  const onWheel = (event) => {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.12 : 0.89;
    setViewTransform((current) => {
      const scale = Math.min(4, Math.max(0.2, current.scale * factor));
      return { ...current, scale };
    });
  };

  const resetView = () => setViewTransform({ x: 0, y: 0, scale: 1 });

  if (loading) {
    return <section className="kg-panel" aria-label="知识图谱"><div className="kg-panel__state"><LoaderCircle className="is-spinning" />正在加载知识图谱…</div></section>;
  }
  if (!books.length) {
    return (
      <section className="kg-panel" aria-label="知识图谱">
        <header className="kg-panel__header"><div><span>Knowledge graph</span><h2>知识图谱</h2></div></header>
        <div className="kg-panel__state"><Network aria-hidden="true" size={26} /><h3>还没有可查看的知识图谱</h3><p>上传教材时勾选“是否匹配本地数据库”，完成后会自动构建教材知识图谱。</p></div>
      </section>
    );
  }
  if (error) {
    return <section className="kg-panel" aria-label="知识图谱"><div className="kg-panel__state" role="alert"><h3>加载失败</h3><p>{error}</p></div></section>;
  }

  return (
    <section className="kg-panel" aria-label="知识图谱">
      <header className="kg-panel__header">
        <div><span>Knowledge graph</span><h2>教材知识图谱</h2></div>
        <div className="kg-panel__books">
          {books.map((book) => (
            <button key={book.book_id} type="button" className={book.book_id === selected ? 'is-active' : ''} onClick={() => selectBook(book.book_id)}>
              <Network aria-hidden="true" size={15} />{book.title}
            </button>
          ))}
        </div>
      </header>
      {layout && (
        <div className="kg-panel__meta">
          <span>{graph.book_title}</span>
          <span>{graph.node_count} 节点</span>
          <span>{graph.edge_count} 条关系</span>
          {layout.shownNodes < layout.totalNodes && <span className="kg-panel__meta-warn">图谱较大，仅展示核心 {layout.shownNodes} 节点</span>}
          <span className="kg-panel__meta-hint"><Move aria-hidden="true" size={13} />拖动平移 · 滚轮缩放</span>
          <button type="button" className="kg-panel__reset" onClick={resetView}><RotateCcw aria-hidden="true" size={12} />复位</button>
        </div>
      )}
      <div className="kg-panel__canvas">
        {layout ? (
          <svg
            viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
            role="img"
            aria-label={`${graph.book_title}知识图谱`}
            className={dragState.current ? 'is-dragging' : ''}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onWheel={onWheel}
          >
            <g transform={`translate(${viewTransform.x}, ${viewTransform.y}) scale(${viewTransform.scale})`}>
            {layout.edges.map((edge, index) => {
              const a = layout.positions[layout.nameToIndex.get(String(edge.source))];
              const b = layout.positions[layout.nameToIndex.get(String(edge.target))];
              if (!a || !b) return null;
              return <line key={`e${index}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="kg-panel__edge" />;
            })}
            {layout.nodes.map((node, index) => {
              const name = String(node.name);
              const pos = layout.positions[index];
              const d = layout.degree.get(name) || 0;
              const radius = Math.min(11, 5 + Math.min(d, 6) * 1.1);
              const showLabel = labelNames.has(name);
              return (
                <g
                  key={name}
                  className="kg-panel__node"
                  transform={`translate(${pos.x}, ${pos.y})`}
                  onMouseEnter={() => setHover(node)}
                  onMouseLeave={() => setHover(null)}
                >
                  <circle r={radius} fill={typeColor(node.type)} opacity={showLabel || hover?.name === name ? 1 : 0.82} />
                  {showLabel && (
                    <text y={radius + 10} textAnchor="middle" className="kg-panel__label">{name.length > 12 ? `${name.slice(0, 12)}…` : name}</text>
                  )}
                </g>
              );
            })}
            </g>
          </svg>
        ) : <div className="kg-panel__state"><h3>图谱数据为空</h3></div>}
        {hover && (
          <div className="kg-panel__tooltip">
            <strong>{hover.name}</strong>
            {hover.type && <span>类型：{hover.type}</span>}
            {hover.description && hover.description !== hover.name && <p>{hover.description}</p>}
          </div>
        )}
      </div>
      {edgeTypes.length > 0 && (
        <footer className="kg-panel__legend">
          {edgeTypes.map((type) => <span key={type}>{type}</span>)}
        </footer>
      )}
    </section>
  );
}