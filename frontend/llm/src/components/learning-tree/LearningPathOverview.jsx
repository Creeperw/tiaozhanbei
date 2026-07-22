import React, { useMemo } from 'react';
import {
  CheckCircle2,
  Circle,
  Compass,
  Flame,
  Lock,
  PlayCircle,
} from 'lucide-react';
import { clampSemanticScale } from './learningTreeModel';

const ORBIT_WIDTH = 960;
const ORBIT_HEIGHT = 600;

const statusMeta = {
  completed: { label: '已完成', Icon: CheckCircle2, tone: 'completed' },
  in_progress: { label: '学习中', Icon: Flame, tone: 'in-progress' },
  next: { label: '下一阶段', Icon: PlayCircle, tone: 'next' },
  locked: { label: '待解锁', Icon: Lock, tone: 'locked' },
  unassessed: { label: '尚未评估', Icon: Circle, tone: 'unassessed' },
};

function nodeId(node) {
  return String(node?.membership_id || node?.node_id || node?.id || '');
}

function sequenceRank(node, index) {
  const order = Number(node?.order);
  return Number.isFinite(order) && order > 0 ? order : index + 1;
}

function orderLearningNodes(nodes, edges) {
  const indexedNodes = nodes.map((node, index) => ({ node, index, id: nodeId(node) }));
  const byId = new Map(indexedNodes.map((item) => [item.id, item]));
  const compareItems = (left, right) => (
    sequenceRank(left.node, left.index) - sequenceRank(right.node, right.index)
    || left.index - right.index
  );
  const sequenceEdges = edges.filter((edge) => (
    edge?.kind !== 'rib' && byId.has(String(edge?.from || '')) && byId.has(String(edge?.to || ''))
  ));
  const targets = new Set(sequenceEdges.map((edge) => String(edge.to)));
  const outgoing = new Map();
  sequenceEdges.forEach((edge) => {
    const from = String(edge.from);
    const to = String(edge.to);
    outgoing.set(from, [...(outgoing.get(from) || []), to]);
  });
  outgoing.forEach((ids, from) => {
    outgoing.set(from, ids.sort((left, right) => compareItems(byId.get(left), byId.get(right))));
  });

  const roots = indexedNodes.filter((item) => !targets.has(item.id)).sort(compareItems);
  const ordered = [];
  const visited = new Set();
  let current = roots[0] || indexedNodes.slice().sort(compareItems)[0];
  while (current && !visited.has(current.id)) {
    ordered.push(current.node);
    visited.add(current.id);
    const nextId = (outgoing.get(current.id) || []).find((id) => !visited.has(id));
    current = nextId ? byId.get(nextId) : null;
  }

  indexedNodes
    .filter((item) => !visited.has(item.id))
    .sort(compareItems)
    .forEach((item) => ordered.push(item.node));
  return ordered;
}

function getOrbitMetrics(nodeCount) {
  const compact = nodeCount >= 9;
  return {
    width: ORBIT_WIDTH,
    height: ORBIT_HEIGHT,
    centerX: ORBIT_WIDTH / 2,
    centerY: ORBIT_HEIGHT / 2 + 10,
    radiusX: compact ? 330 : nodeCount <= 4 ? 246 : 286,
    radiusY: compact ? 202 : nodeCount <= 4 ? 174 : 196,
    nodeWidth: compact ? 136 : nodeCount >= 7 ? 150 : 168,
    nodeHeight: compact ? 70 : 78,
  };
}

function getOrbitPosition(index, total, metrics) {
  const angle = total <= 1 ? -Math.PI / 2 : -Math.PI / 2 + (Math.PI * 2 * index) / total;
  return {
    x: metrics.centerX + Math.cos(angle) * metrics.radiusX,
    y: metrics.centerY + Math.sin(angle) * metrics.radiusY,
    angle,
  };
}

function orbitArc(from, to, metrics) {
  return `M ${from.x} ${from.y} A ${metrics.radiusX} ${metrics.radiusY} 0 0 1 ${to.x} ${to.y}`;
}

function connectorEnd(position, metrics) {
  const nodeHalfWidth = metrics.nodeWidth / 2;
  const nodeHalfHeight = metrics.nodeHeight / 2;
  const directionX = position.x - metrics.centerX;
  const directionY = position.y - metrics.centerY;
  const distance = Math.max(1, Math.hypot(directionX, directionY));
  const horizontalRatio = Math.abs(directionX) / distance;
  const verticalRatio = Math.abs(directionY) / distance;
  const inset = Math.max(nodeHalfWidth * horizontalRatio, nodeHalfHeight * verticalRatio) + 16;
  return {
    x: position.x - (directionX / distance) * inset,
    y: position.y - (directionY / distance) * inset,
  };
}

function stageFor(index, currentIndex) {
  if (index === currentIndex) return 'current';
  return index < currentIndex ? 'past' : 'future';
}

function progressFor(nodes, currentIndex) {
  if (!nodes.length) return 0;
  const completed = nodes.filter((node) => node.status === 'completed').length;
  const activeBonus = nodes.some((node) => node.status === 'in_progress') ? 0.45 : 0;
  const nextBonus = !activeBonus && currentIndex > completed ? 0.12 : 0;
  return Math.max(0, Math.min(100, Math.round(((completed + activeBonus + nextBonus) / nodes.length) * 100)));
}

export default function LearningPathOverview({
  nodes,
  edges,
  selectedId,
  onSelect,
  onDrill,
  onClearSelection,
  directDrill = false,
}) {
  const orderedNodes = useMemo(() => orderLearningNodes(nodes, edges), [edges, nodes]);
  const metrics = useMemo(() => getOrbitMetrics(orderedNodes.length), [orderedNodes.length]);
  const positions = useMemo(() => Object.fromEntries(
    orderedNodes.map((node, index) => [nodeId(node), getOrbitPosition(index, orderedNodes.length, metrics)]),
  ), [metrics, orderedNodes]);
  const effectiveCurrentId = orderedNodes.find((node) => node.status === 'in_progress') && nodeId(orderedNodes.find((node) => node.status === 'in_progress'))
    || orderedNodes.find((node) => node.status === 'next') && nodeId(orderedNodes.find((node) => node.status === 'next'))
    || orderedNodes.find((node) => !['completed', 'locked'].includes(node.status)) && nodeId(orderedNodes.find((node) => !['completed', 'locked'].includes(node.status)))
    || nodeId(orderedNodes.at(-1));
  const currentIndex = Math.max(0, orderedNodes.findIndex((node) => nodeId(node) === effectiveCurrentId));
  const progress = progressFor(orderedNodes, currentIndex);
  const currentNode = orderedNodes[currentIndex];
  const displayScale = clampSemanticScale(1);

  return (
    <div
      className="learning-path-overview learning-path-orbit"
      aria-label="一级知识学习路径"
      data-scale={displayScale}
      data-layout="orbit"
      data-node-count={orderedNodes.length}
      data-current-order={currentIndex + 1}
    >
      <header className="learning-path-orbit__summary">
        <div>
          <span><Compass aria-hidden="true" size={14} />顺序学习路径</span>
          <strong>中医药知识体系</strong>
          <p>{directDrill ? '按阶段依次进入教材与知识点' : '沿导引环逐步掌握中医核心知识'}</p>
        </div>
        <div className="learning-path-orbit__current" aria-label={`当前学习阶段：${currentNode?.title || '待开始'}`}>
          <span>当前进度</span>
          <strong>{currentNode ? `第 ${String(currentIndex + 1).padStart(2, '0')} 阶段` : '待开始'}</strong>
        </div>
      </header>
      <div
        className="learning-path-overview__stage learning-path-orbit__stage"
        style={{
          '--orbit-node-width': `${metrics.nodeWidth}px`,
          '--orbit-node-height': `${metrics.nodeHeight}px`,
        }}
        onClick={(event) => {
          if (event.target.closest?.('.learning-path-orbit__node')) return;
          onClearSelection?.();
        }}
      >
        <svg
          aria-hidden="true"
          className="learning-path-orbit__canvas"
          viewBox={`0 0 ${metrics.width} ${metrics.height}`}
          preserveAspectRatio="none"
        >
          <defs>
            <radialGradient id="learning-path-orbit-core" cx="50%" cy="42%" r="65%">
              <stop offset="0%" stopColor="#f5fff9" />
              <stop offset="100%" stopColor="#dff6ea" />
            </radialGradient>
            <linearGradient id="learning-path-orbit-progress" x1="0%" x2="100%" y1="0%" y2="100%">
              <stop offset="0%" stopColor="#118b68" />
              <stop offset="100%" stopColor="#5ecaa5" />
            </linearGradient>
          </defs>
          <ellipse
            className="learning-path-orbit__halo"
            cx={metrics.centerX}
            cy={metrics.centerY}
            rx={metrics.radiusX + 52}
            ry={metrics.radiusY + 46}
          />
          <ellipse
            className="learning-path-orbit__track"
            cx={metrics.centerX}
            cy={metrics.centerY}
            rx={metrics.radiusX}
            ry={metrics.radiusY}
          />
          {orderedNodes.map((node) => {
            const position = positions[nodeId(node)];
            const end = connectorEnd(position, metrics);
            return (
              <line
                key={`spoke-${nodeId(node)}`}
                className={`learning-path-orbit__spoke is-${stageFor(orderedNodes.indexOf(node), currentIndex)}`}
                x1={metrics.centerX}
                x2={end.x}
                y1={metrics.centerY}
                y2={end.y}
              />
            );
          })}
          {orderedNodes.slice(1).map((node, index) => {
            const from = positions[nodeId(orderedNodes[index])];
            const to = positions[nodeId(node)];
            const state = index < currentIndex ? 'completed' : index === currentIndex ? 'current' : 'upcoming';
            return (
              <path
                key={`sequence-${nodeId(orderedNodes[index])}-${nodeId(node)}`}
                data-testid="learning-path-orbit-segment"
                data-state={state}
                className="learning-path-orbit__sequence"
                d={orbitArc(from, to, metrics)}
              />
            );
          })}
          {orderedNodes.length > 0 && (
            <circle
              className="learning-path-orbit__start-dot"
              cx={positions[nodeId(orderedNodes[0])]?.x}
              cy={positions[nodeId(orderedNodes[0])]?.y}
              r="6"
            />
          )}
        </svg>

        <div className="learning-path-orbit__core" aria-label={`总体学习进度 ${progress}%`}>
          <svg aria-hidden="true" viewBox="0 0 120 120">
            <circle className="learning-path-orbit__core-track" cx="60" cy="60" r="49" pathLength="100" />
            <circle
              className="learning-path-orbit__core-progress"
              cx="60"
              cy="60"
              r="49"
              pathLength="100"
              style={{ strokeDasharray: `${progress} ${100 - progress}` }}
            />
          </svg>
          <span>{progress}%</span>
          <small>总体进度</small>
          <em>{orderedNodes.filter((node) => node.status === 'completed').length} / {orderedNodes.length || 0} 阶段</em>
        </div>

        {orderedNodes.map((node, index) => {
          const id = nodeId(node);
          const position = positions[id];
          const selected = selectedId === id;
          const current = effectiveCurrentId === id;
          const stage = stageFor(index, currentIndex);
          const meta = statusMeta[node.status] || statusMeta.unassessed;
          const Icon = meta.Icon;
          const total = Number(node.total_count ?? node.child_count ?? 0);
          return (
            <button
              key={id}
              type="button"
              aria-label={directDrill ? `进入${node.title}（第 ${index + 1} 阶段）` : `选择${node.title}，第 ${index + 1} 阶段，双击进入知识星球`}
              aria-pressed={selected}
              data-current={String(current)}
              data-stage={stage}
              data-order={index + 1}
              className={`learning-path-orbit__node is-${meta.tone}${selected ? ' is-selected' : ''}`}
              style={{
                left: `${(position.x / metrics.width) * 100}%`,
                top: `${(position.y / metrics.height) * 100}%`,
              }}
              onClick={() => onSelect(node)}
              onDoubleClick={(event) => {
                event.preventDefault();
                onDrill(node);
              }}
              title={node.title}
            >
              <span className="learning-path-orbit__order">{String(index + 1).padStart(2, '0')}</span>
              <span className="learning-path-orbit__title"><Icon aria-hidden="true" size={15} /><b>{node.title}</b></span>
              <small>{meta.label}{total ? ` · ${total}项` : ''}</small>
              {node.status === 'in_progress' && node.average_mastery != null && (
                <span className="learning-path-orbit__progress" aria-label={`掌握度 ${node.average_mastery}%`}>
                  <i style={{ width: `${Math.max(0, Math.min(100, node.average_mastery))}%` }} />
                </span>
              )}
            </button>
          );
        })}
      </div>

      <footer className="learning-path-orbit__legend" aria-label="知识点状态说明">
        <span><i className="is-completed" />已完成</span>
        <span><i className="is-progress" />学习中</span>
        <span><i className="is-next" />下一阶段</span>
        <span><i className="is-locked" />待解锁</span>
        <em>{directDrill ? '按序选择阶段，进入下一层教材' : '单击查看规划 · 双击进入知识星球'}</em>
      </footer>
    </div>
  );
}
