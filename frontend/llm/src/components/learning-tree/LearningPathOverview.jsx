import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  CheckCircle2,
  Circle,
  Flame,
  Lock,
  PlayCircle,
} from 'lucide-react';
import {
  clampSemanticScale,
  getSemanticFishboneMetrics,
  layoutDependencyTree,
  panViewport,
} from './learningTreeModel';

const WIDTH = 960;
const HEIGHT = 640;
const NODE_WIDTH = 152;
const NODE_HEIGHT = 62;
const SCALE_STORAGE_KEY = 'learning-path-semantic-scale';

function storedSemanticScale(fallback) {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(SCALE_STORAGE_KEY);
    if (raw == null) return fallback;
    const stored = Number(raw);
    return Number.isFinite(stored) ? clampSemanticScale(stored) : fallback;
  } catch {
    return fallback;
  }
}

function persistSemanticScale(scale) {
  try {
    window.localStorage.setItem(SCALE_STORAGE_KEY, String(scale));
  } catch {
    // Keep the canvas usable when browser storage is unavailable.
  }
}

const statusMeta = {
  completed: { label: '已完成', Icon: CheckCircle2 },
  in_progress: { label: '进行中', Icon: Flame },
  next: { label: '即将开始', Icon: PlayCircle },
  locked: { label: '待解锁', Icon: Lock },
  unassessed: { label: '尚未评估', Icon: Circle },
};

function edgePath(from, to, kind) {
  if (kind === 'rib') {
    const direction = to.y < from.y ? -1 : 1;
    return `M ${from.x} ${from.y + direction * NODE_HEIGHT / 2} L ${to.x} ${to.y - direction * NODE_HEIGHT / 2}`;
  }
  const startX = from.x;
  const endX = to.x;
  const axisY = from.axisY ?? to.axisY ?? HEIGHT / 2;
  const middleX = (startX + endX) / 2;
  return `M ${startX} ${axisY} C ${middleX} ${axisY - 3}, ${middleX} ${axisY + 3}, ${endX} ${axisY}`;
}

function ribbonPath(startX, endX, axisY, strand) {
  const span = Math.max(1, endX - startX);
  const amplitude = [13, 18, 10][strand] || 13;
  const phase = [-1, 1, 0.35][strand] || 0;
  return [
    `M ${startX} ${axisY + amplitude * phase}`,
    `C ${startX + span * 0.14} ${axisY - amplitude}`,
    `${startX + span * 0.27} ${axisY + amplitude} ${startX + span * 0.4} ${axisY}`,
    `S ${startX + span * 0.64} ${axisY - amplitude * phase} ${startX + span * 0.76} ${axisY}`,
    `S ${startX + span * 0.92} ${axisY + amplitude} ${endX} ${axisY - amplitude * phase}`,
  ].join(' ');
}

export default function LearningPathOverview({
  nodes,
  edges,
  selectedId,
  onSelect,
  onDrill,
  onClearSelection,
}) {
  const spineCount = Math.max(1, edges.filter((edge) => edge.kind !== 'rib').length + 1);
  const defaultSemanticScale = clampSemanticScale(Math.max(
    0.68,
    Math.min(1, WIDTH / (210 + (spineCount - 1) * 184)),
  ));
  const [semanticScale, setSemanticScale] = useState(() => storedSemanticScale(defaultSemanticScale));
  const [viewMode, setViewMode] = useState('start');
  const [viewport, setViewport] = useState({ scale: 1, x: 0, y: 0 });
  const [stageSize, setStageSize] = useState({ width: WIDTH, height: HEIGHT });
  const dragRef = useRef(null);
  const stageRef = useRef(null);
  const metrics = useMemo(() => getSemanticFishboneMetrics(semanticScale), [semanticScale]);
  const canvasWidth = Math.max(WIDTH, 210 + (spineCount - 1) * metrics.axisGap);
  const positions = useMemo(
    () => layoutDependencyTree(nodes, edges, {
      width: canvasWidth,
      height: HEIGHT,
      ...metrics,
    }),
    [canvasWidth, edges, metrics, nodes],
  );
  const nodeMap = useMemo(
    () => Object.fromEntries(nodes.map((node) => [node.membership_id, node])),
    [nodes],
  );
  const spineNodes = useMemo(() => {
    const ribTargets = new Set(edges.filter((edge) => edge.kind === 'rib').map((edge) => edge.to));
    return nodes.filter((node) => !ribTargets.has(node.membership_id));
  }, [edges, nodes]);
  const effectiveCurrentId = nodes.find((node) => node.status === 'in_progress')?.membership_id
    || nodes.find((node) => node.status === 'next')?.membership_id
    || nodes.find((node) => !['completed', 'locked'].includes(node.status))?.membership_id
    || nodes.find((node) => node.status === 'completed')?.membership_id
    || spineNodes[0]?.membership_id;
  const currentDepth = positions[effectiveCurrentId]?.depth ?? 0;
  const positionValues = Object.values(positions);
  const leftmostNodeEdge = positionValues.length
    ? Math.min(...positionValues.map((position) => position.x - NODE_WIDTH / 2))
    : 20;
  const startViewport = {
    scale: 1,
    x: 20 - leftmostNodeEdge,
    y: (stageSize.height - HEIGHT) / 2,
  };
  const activeViewport = viewMode === 'start' ? startViewport : viewport;
  const axisY = positions[spineNodes[0]?.membership_id]?.axisY ?? HEIGHT / 2;
  const axisStart = (positions[spineNodes[0]?.membership_id]?.x ?? canvasWidth / 2) - 84;
  const axisEnd = (positions[spineNodes.at(-1)?.membership_id]?.x ?? canvasWidth / 2) + 84;
  const stageFor = (position) => {
    if ((position?.depth ?? 0) === currentDepth) return 'current';
    return (position?.depth ?? 0) < currentDepth ? 'past' : 'future';
  };

  const changeSemanticScale = (nextScale, { persist = false } = {}) => {
    const scale = clampSemanticScale(nextScale);
    setSemanticScale(scale);
    setViewMode('start');
    if (persist) persistSemanticScale(scale);
  };

  useEffect(() => {
    const element = stageRef.current;
    if (!element || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.round(entry.contentRect.width);
      const height = Math.round(entry.contentRect.height);
      if (!width || !height) return;
      setStageSize((current) => (
        current.width === width && current.height === height ? current : { width, height }
      ));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const finishPan = (event) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };

  return (
    <div
      className="learning-path-overview"
      aria-label="一级知识学习路径"
      data-scale={Number(semanticScale.toFixed(2))}
      data-semantic-scale={Number(semanticScale.toFixed(2))}
      data-viewport-scale={Number(activeViewport.scale.toFixed(2))}
      data-layout="settled"
      data-view={viewMode}
    >
      <div
        className="learning-path-overview__stage"
        ref={stageRef}
        style={{ width: '100%', height: '100%' }}
        onClick={(event) => {
          if (event.target.closest?.('button')) return;
          onClearSelection?.();
        }}
        onWheel={(event) => {
          event.preventDefault();
          changeSemanticScale(semanticScale * Math.exp(-event.deltaY * 0.001), { persist: true });
        }}
        onPointerDown={(event) => {
          if (event.button !== 0 || event.target.closest?.('button')) return;
          setViewport(activeViewport);
          setViewMode('manual');
          dragRef.current = { x: event.clientX, y: event.clientY };
          event.currentTarget.setPointerCapture?.(event.pointerId);
        }}
        onPointerMove={(event) => {
          const previous = dragRef.current;
          if (!previous) return;
          setViewport((current) => panViewport(
            current,
            event.clientX - previous.x,
            event.clientY - previous.y,
          ));
          dragRef.current = { x: event.clientX, y: event.clientY };
        }}
        onPointerUp={finishPan}
        onPointerCancel={finishPan}
      >
        <div
          className="learning-path-overview__viewport"
          style={{
            width: canvasWidth,
            height: HEIGHT,
            transform: `translate3d(${activeViewport.x}px, ${activeViewport.y}px, 0) scale(${activeViewport.scale})`,
            transformOrigin: '0 0',
          }}
        >
        <svg
          aria-hidden="true"
          className="learning-path-overview__edges"
          viewBox={`0 0 ${canvasWidth} ${HEIGHT}`}
        >
          {[0, 1, 2].map((strand) => (
            <path
              key={`ribbon-${strand}`}
              className={`learning-path-overview__ribbon-line is-${strand + 1}`}
              d={ribbonPath(axisStart, axisEnd, axisY, strand)}
            />
          ))}
          {spineNodes.map((node) => {
            const position = positions[node.membership_id];
            if (!position) return null;
            const direction = position.y < axisY ? -1 : 1;
            return (
              <path
                key={`stem-${node.membership_id}`}
                data-testid="learning-path-stem"
                data-stage={stageFor(position)}
                className="learning-path-overview__stem"
                d={`M ${position.x} ${axisY} L ${position.x} ${position.y - direction * NODE_HEIGHT / 2}`}
              />
            );
          })}
          {edges.map((edge) => {
            const from = positions[edge.from];
            const to = positions[edge.to];
            if (!from || !to) return null;
            const active = ['completed', 'in_progress'].includes(nodeMap[edge.from]?.status);
            return (
              <path
                key={`${edge.from}-${edge.to}`}
                data-testid="learning-tree-edge"
                d={edgePath(from, to, edge.kind)}
                data-kind={edge.kind || 'spine'}
                className={`${edge.kind === 'rib' ? 'is-rib' : 'is-spine'}${active ? ' is-active' : ''}`}
              />
            );
          })}
        </svg>

        {nodes.map((node) => {
          const id = node.membership_id;
          const position = positions[id];
          const selected = selectedId === id;
          const current = effectiveCurrentId === id;
          const stage = stageFor(position);
          const meta = statusMeta[node.status] || statusMeta.unassessed;
          const Icon = meta.Icon;
          const total = Number(node.total_count ?? node.child_count ?? 0);
          return (
            <button
              key={id}
              type="button"
              aria-label={`选择${node.title}，双击进入知识星球`}
              aria-pressed={selected}
              data-current={String(current)}
              data-stage={stage}
              className={`learning-path-node learning-path-node--${node.status || 'unassessed'}`}
              style={{
                left: position.x - NODE_WIDTH / 2,
                top: position.y - NODE_HEIGHT / 2,
                width: NODE_WIDTH,
                height: NODE_HEIGHT,
              }}
              onClick={() => onSelect(node)}
              onDoubleClick={(event) => {
                event.preventDefault();
                onDrill(node);
              }}
              title={node.title}
            >
              <span className="learning-path-node__title"><Icon aria-hidden="true" size={13} /><b>{node.title}</b></span>
              <small>{meta.label}{total ? ` · ${total}项` : ''}</small>
              {node.status === 'in_progress' && node.average_mastery != null && (
                <span className="learning-path-node__progress" aria-label={`掌握度 ${node.average_mastery}%`}>
                  <i style={{ width: `${Math.max(0, Math.min(100, node.average_mastery))}%` }} />
                </span>
              )}
            </button>
          );
        })}
        </div>
      </div>

      <div className="learning-path-overview__legend" aria-label="知识点状态说明">
        <span><i className="is-completed" />已完成</span>
        <span><i className="is-progress" />进行中</span>
        <span><i />即将解锁</span>
        <span><i className="is-locked" />待解锁</span>
        <em>单击查看规划 · 双击进入知识星球</em>
      </div>
    </div>
  );
}
