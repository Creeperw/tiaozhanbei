import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  getAtlasResourceKind,
  interpolateAtlasPositions,
  projectAtlasNodes,
} from './knowledgeAtlasModel';

const MORPH_DURATION = 1050;
const SPACE_OUT_DURATION = 480;
const SPACE_IN_DURATION = 600;
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const easeOut = (value) => 1 - ((1 - clamp(value, 0, 1)) ** 3);

function prefersReducedMotion() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function canvasSize(canvas) {
  const rect = canvas.getBoundingClientRect();
  return {
    width: Math.max(320, Math.round(rect.width || canvas.parentElement?.clientWidth || 960)),
    height: Math.max(420, Math.round(rect.height || canvas.parentElement?.clientHeight || 620)),
  };
}

function drawWireSphere(ctx, width, height, radius, alpha) {
  ctx.save();
  ctx.strokeStyle = `rgba(24, 49, 42, ${0.08 * alpha})`;
  ctx.lineWidth = 1;
  [1, 0.72, 0.38].forEach((scale) => {
    ctx.beginPath();
    ctx.arc(width / 2, height / 2, radius * scale, 0, Math.PI * 2);
    ctx.stroke();
  });
  [-0.55, 0, 0.55].forEach((offset) => {
    ctx.beginPath();
    ctx.moveTo(width / 2 - radius, height / 2 + radius * offset * 0.35);
    ctx.lineTo(width / 2 + radius, height / 2 - radius * offset * 0.35);
    ctx.stroke();
  });
  ctx.restore();
}

export function getAtlasNodeDrawKind(node, resourceStyles = false) {
  const kind = resourceStyles ? getAtlasResourceKind(node) : 'solid';
  return {
    kind,
    filled: true,
    marker: kind === 'both' ? 'ring-dot' : kind === 'question' ? 'ring' : kind === 'video' ? 'dot' : 'none',
  };
}

function drawNode(ctx, node, hovered, resourceStyles) {
  ctx.save();
  ctx.translate(node.x, node.y);
  ctx.globalAlpha = node.alpha;
  const radius = node.radius * (hovered ? 1.18 : 1);
  const gradient = ctx.createRadialGradient(-radius * 0.32, -radius * 0.38, 0, 0, 0, radius * 2.3);
  gradient.addColorStop(0, '#d8fff0');
  gradient.addColorStop(0.28, node.color?.solid || '#159b6b');
  gradient.addColorStop(1, node.color?.glow || 'rgba(21,155,107,.2)');
  ctx.shadowColor = node.color?.glow || 'rgba(21,155,107,.28)';
  ctx.shadowBlur = hovered ? 26 : 15;
  ctx.fillStyle = gradient;
  ctx.strokeStyle = node.color?.solid || '#159b6b';
  ctx.lineWidth = Math.max(1.6, radius * 0.26);
  ctx.setLineDash?.([]);
  const { kind, marker } = getAtlasNodeDrawKind(node, resourceStyles);
  ctx.beginPath();
  ctx.arc(0, 0, radius, 0, Math.PI * 2);
  ctx.fill();
  if (marker.includes('ring')) {
    ctx.strokeStyle = '#b57b1e';
    ctx.lineWidth = Math.max(1.1, radius * (kind === 'both' ? 0.14 : 0.13));
    ctx.beginPath();
    ctx.arc(0, 0, radius + (kind === 'both' ? 2.8 : 2.2), 0, Math.PI * 2);
    ctx.stroke();
  }
  if (marker.includes('dot')) {
    ctx.fillStyle = 'rgba(232,255,246,.92)';
    ctx.beginPath();
    ctx.arc(0, 0, Math.max(1.2, radius * 0.27), 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function drawClusterHalos(ctx, nodes, alpha) {
  const groups = new Map();
  nodes.forEach((node) => {
    if (node.cluster_id == null || node.z < -0.2) return;
    if (!groups.has(node.cluster_id)) groups.set(node.cluster_id, []);
    groups.get(node.cluster_id).push(node);
  });
  ctx.save();
  groups.forEach((group) => {
    if (!group.length) return;
    const centerX = group.reduce((sum, node) => sum + node.x, 0) / group.length;
    const centerY = group.reduce((sum, node) => sum + node.y, 0) / group.length;
    const radius = Math.max(24, ...group.map((node) => Math.hypot(node.x - centerX, node.y - centerY) + node.radius + 13));
    ctx.globalAlpha = 0.3 * alpha;
    ctx.fillStyle = group[0].color?.glow || 'rgba(21,155,107,.2)';
    ctx.strokeStyle = group[0].color?.solid || '#159b6b';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(centerX, centerY, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 0.13 * alpha;
    ctx.stroke();
  });
  ctx.restore();
}

function spaceTransitionValues(transition, now, width, height) {
  if (!transition || transition.mode.endsWith('-wait')) return { alpha: transition ? 0 : 1, scale: 1, ox: 0, oy: 0 };
  const progress = easeOut((now - transition.startedAt) / transition.duration);
  if (transition.mode === 'dive-out') {
    return {
      alpha: 1 - progress,
      scale: 1 + progress * 4.5,
      ox: transition.origin ? (width / 2 - transition.origin.sx) * progress : 0,
      oy: transition.origin ? (height / 2 - transition.origin.sy) * progress : 0,
    };
  }
  if (transition.mode === 'back-out') {
    return { alpha: 1 - progress, scale: 1 - progress * 0.76, ox: 0, oy: 0 };
  }
  return { alpha: progress, scale: 0.18 + progress * 0.82, ox: 0, oy: 0 };
}

function hitTest(nodes, point) {
  let match = null;
  for (const node of nodes) {
    const distance = Math.hypot(point.x - node.x, point.y - node.y);
    if (distance <= Math.max(13, node.radius + 7) && (!match || node.z > match.z)) match = node;
  }
  return match;
}

export function shouldContinueAtlasFrame({
  reducedMotion,
  hidden,
  loading,
  spaceAnimating,
  paused,
  autoRotate,
  inertiaActive,
  pointerActive,
  transitionActive,
}) {
  if (reducedMotion || hidden) return false;
  if (spaceAnimating) return true;
  return !loading && !paused && (autoRotate || inertiaActive || pointerActive || transitionActive);
}

export default function useKnowledgeAtlasCanvas({ nodes, autoRotate, paused, clustered = false, resourceStyles = false, loading = false, onNodeActivate }) {
  const canvasRef = useRef(null);
  const frameRef = useRef(0);
  const projectedRef = useRef([]);
  const previousNodesRef = useRef([]);
  const transitionRef = useRef({ previous: [], startedAt: 0 });
  const latestNodesRef = useRef(nodes);
  const spaceTransitionRef = useRef(null);
  const pointerRef = useRef(null);
  const viewRef = useRef({ yaw: -0.35, pitch: 0.12, zoom: 1, velocityX: 0, velocityY: 0 });
  const [hovered, setHovered] = useState(null);
  const [viewVersion, setViewVersion] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [spaceTransitionMode, setSpaceTransitionMode] = useState('');
  const reducedMotion = useMemo(() => prefersReducedMotion(), []);

  useEffect(() => {
    latestNodesRef.current = nodes;
    const spaceTransition = spaceTransitionRef.current;
    if (spaceTransition) {
      if (loading) return;
      spaceTransition.nextNodes = nodes;
      if (spaceTransition.mode.endsWith('-wait')) {
        spaceTransition.displayedNodes = nodes;
        spaceTransition.mode = `${spaceTransition.direction}-in`;
        spaceTransition.startedAt = typeof performance !== 'undefined' ? performance.now() : Date.now();
        spaceTransition.duration = SPACE_IN_DURATION;
        setSpaceTransitionMode(spaceTransition.mode);
        setViewVersion((value) => value + 1);
      }
      return;
    }
    if (loading) return;
    transitionRef.current = {
      previous: previousNodesRef.current,
      startedAt: typeof performance !== 'undefined' ? performance.now() : Date.now(),
    };
    previousNodesRef.current = nodes;
  }, [loading, nodes]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const context = canvas.getContext('2d');
    if (!context) return undefined;
    let disposed = false;
    let lastFrame = typeof performance !== 'undefined' ? performance.now() : Date.now();

    const draw = (now = lastFrame) => {
      if (disposed) return;
      const { width, height } = canvasSize(canvas);
      const dpr = clamp(window.devicePixelRatio || 1, 1, 2);
      if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
      }
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.clearRect(0, 0, width, height);

      const delta = clamp(now - lastFrame, 0, 40);
      lastFrame = now;
      const view = viewRef.current;
      if (!paused && !reducedMotion && !document.hidden) {
        if (!pointerRef.current) {
          view.yaw += view.velocityX * delta;
          view.pitch = clamp(view.pitch + view.velocityY * delta, -1.05, 1.05);
          const damping = Math.pow(0.93, delta / 16.7);
          view.velocityX *= damping;
          view.velocityY *= damping;
          if (autoRotate && Math.abs(view.velocityX) < 0.00003) view.yaw += delta * 0.000045;
        }
      }
      if (clustered) {
        view.yaw = clamp(view.yaw, -0.42, 0.42);
        view.pitch = clamp(view.pitch, -0.34, 0.34);
      }

      const transition = transitionRef.current;
      const progress = reducedMotion ? 1 : (now - transition.startedAt) / MORPH_DURATION;
      let renderedNodes = progress < 1
        ? interpolateAtlasPositions(transition.previous, nodes, progress)
        : nodes;
      const spaceTransition = spaceTransitionRef.current;
      if (spaceTransition?.displayedNodes) renderedNodes = spaceTransition.displayedNodes;
      const values = reducedMotion
        ? { alpha: 1, scale: 1, ox: 0, oy: 0 }
        : spaceTransitionValues(spaceTransition, now, width, height);
      const projected = projectAtlasNodes(renderedNodes, { width, height, ...view });
      const transformed = projected.map((node) => ({
        ...node,
        x: width / 2 + values.ox + (node.x - width / 2) * values.scale,
        y: height / 2 + values.oy + (node.y - height / 2) * values.scale,
        alpha: node.alpha * values.alpha,
      }));
      canvas.dataset.renderedNodeCount = String(transformed.length);
      canvas.dataset.maxNodeAlpha = String(transformed.reduce((maximum, node) => Math.max(maximum, Number(node.alpha) || 0), 0));
      projectedRef.current = transformed;
      drawWireSphere(
        context,
        width + values.ox * 2,
        height + values.oy * 2,
        Math.min(width, height) * 0.36 * view.zoom * values.scale,
        values.alpha,
      );
      drawClusterHalos(context, transformed, values.alpha);
      transformed.forEach((node) => drawNode(context, node, hovered?.id === node.id, resourceStyles));

      const labels = transformed
        .filter((node) => node.depth > 0.44 || hovered?.id === node.id)
        .sort((left, right) => right.depth - left.depth)
        .slice(0, width < 560 ? 12 : 28);
      context.save();
      context.font = '600 10.5px system-ui, sans-serif';
      context.textAlign = 'center';
      context.shadowColor = 'rgba(255,255,255,.96)';
      context.shadowBlur = 4;
      const occupied = [];
      let drawnLabelCount = 0;
      labels.forEach((node) => {
        const text = node.name.length > 18 ? `${node.name.slice(0, 17)}…` : node.name;
        const measured = context.measureText(text).width;
        const box = { x: node.x - measured / 2 - 4, y: node.y + node.radius + 5, width: measured + 8, height: 18 };
        const collides = hovered?.id !== node.id && occupied.some((other) => (
          box.x < other.x + other.width
          && box.x + box.width > other.x
          && box.y < other.y + other.height
          && box.y + box.height > other.y
        ));
        if (collides) return;
        occupied.push(box);
        drawnLabelCount += 1;
        context.fillStyle = `rgba(24,49,42,${Math.min(1, node.alpha + 0.22)})`;
        context.fillText(text, node.x, node.y + node.radius + 17);
      });
      canvas.dataset.drawnLabelCount = String(drawnLabelCount);
      context.restore();

      if (spaceTransition && now - spaceTransition.startedAt >= spaceTransition.duration) {
        if (spaceTransition.mode.endsWith('-out')) {
          if (spaceTransition.nextNodes !== undefined) {
            spaceTransition.displayedNodes = spaceTransition.nextNodes;
            spaceTransition.mode = `${spaceTransition.direction}-in`;
            spaceTransition.startedAt = now;
            spaceTransition.duration = SPACE_IN_DURATION;
          } else {
            spaceTransition.mode = `${spaceTransition.direction}-wait`;
          }
          setSpaceTransitionMode(spaceTransition.mode);
        } else if (spaceTransition.mode.endsWith('-in')) {
          spaceTransitionRef.current = null;
          previousNodesRef.current = latestNodesRef.current;
          setSpaceTransitionMode('');
        }
      }

      const transitionActive = progress < 1;
      const inertiaActive = Math.abs(view.velocityX) > 0.00002 || Math.abs(view.velocityY) > 0.00002;
      const spaceAnimating = Boolean(spaceTransitionRef.current && !spaceTransitionRef.current.mode.endsWith('-wait'));
      const shouldContinue = shouldContinueAtlasFrame({
        reducedMotion,
        hidden: document.hidden,
        loading,
        spaceAnimating,
        paused,
        autoRotate,
        inertiaActive,
        pointerActive: Boolean(pointerRef.current),
        transitionActive,
      });
      if (shouldContinue) frameRef.current = requestAnimationFrame(draw);
    };

    frameRef.current = requestAnimationFrame(draw);
    const redraw = () => {
      if (disposed || document.hidden) return;
      cancelAnimationFrame(frameRef.current);
      frameRef.current = requestAnimationFrame(draw);
    };
    window.addEventListener('resize', redraw);
    document.addEventListener('visibilitychange', redraw);
    return () => {
      disposed = true;
      window.removeEventListener('resize', redraw);
      document.removeEventListener('visibilitychange', redraw);
      cancelAnimationFrame(frameRef.current);
    };
  }, [autoRotate, clustered, hovered?.id, loading, nodes, paused, reducedMotion, resourceStyles, viewVersion]);

  useEffect(() => () => { spaceTransitionRef.current = null; }, []);

  const pointFromEvent = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const bindings = {
    onPointerDown: (event) => {
      if (event.button !== 0 || spaceTransitionRef.current) return;
      const point = pointFromEvent(event);
      pointerRef.current = { ...point, clientX: event.clientX, clientY: event.clientY, moved: false };
      event.currentTarget.setPointerCapture?.(event.pointerId);
      setViewVersion((value) => value + 1);
    },
    onPointerMove: (event) => {
      const point = pointFromEvent(event);
      const pointer = pointerRef.current;
      if (spaceTransitionRef.current) {
        setHovered(null);
        return;
      }
      if (!pointer) {
        setHovered(hitTest(projectedRef.current, point));
        return;
      }
      const dx = event.clientX - pointer.clientX;
      const dy = event.clientY - pointer.clientY;
      const view = viewRef.current;
      view.yaw += dx * 0.006;
      view.pitch = clamp(view.pitch + dy * 0.0048, -1.05, 1.05);
      if (clustered) {
        view.yaw = clamp(view.yaw, -0.42, 0.42);
        view.pitch = clamp(view.pitch, -0.34, 0.34);
      }
      view.velocityX = dx * 0.00018;
      view.velocityY = dy * 0.00014;
      pointerRef.current = { ...point, clientX: event.clientX, clientY: event.clientY, moved: pointer.moved || Math.hypot(dx, dy) > 2 };
    },
    onPointerUp: (event) => {
      const pointer = pointerRef.current;
      pointerRef.current = null;
      event.currentTarget.releasePointerCapture?.(event.pointerId);
      if (pointer && !pointer.moved) {
        const hit = hitTest(projectedRef.current, pointFromEvent(event));
        if (hit) onNodeActivate?.(hit);
      }
    },
    onPointerCancel: () => { pointerRef.current = null; },
    onPointerLeave: () => { if (!pointerRef.current) setHovered(null); },
    onWheel: (event) => {
      event.preventDefault();
      viewRef.current.zoom = clamp(viewRef.current.zoom * Math.exp(-event.deltaY * 0.001), 0.56, 2.1);
      setZoom(viewRef.current.zoom);
      setViewVersion((value) => value + 1);
    },
  };

  const setViewPreset = useCallback((preset = {}) => {
    const current = viewRef.current;
    const nextZoom = clamp(Number(preset.zoom ?? current.zoom), 0.56, 2.1);
    viewRef.current = {
      ...current,
      yaw: Number(preset.yaw ?? current.yaw),
      pitch: Number(preset.pitch ?? current.pitch),
      zoom: nextZoom,
      velocityX: 0,
      velocityY: 0,
    };
    setZoom(nextZoom);
    setViewVersion((value) => value + 1);
  }, []);

  const resetView = (preset = {}) => {
    setViewPreset({ yaw: -0.35, pitch: 0.12, zoom: 1, ...preset });
  };

  const zoomBy = (factor) => {
    viewRef.current.zoom = clamp(viewRef.current.zoom * factor, 0.56, 2.1);
    setZoom(viewRef.current.zoom);
    setViewVersion((value) => value + 1);
  };

  const startSpaceNavigation = (direction, origin = null) => {
    if (reducedMotion) {
      spaceTransitionRef.current = null;
      setSpaceTransitionMode('');
      return;
    }
    const normalizedDirection = direction === 'back' ? 'back' : 'dive';
    spaceTransitionRef.current = {
      direction: normalizedDirection,
      mode: `${normalizedDirection}-out`,
      origin: Number.isFinite(origin?.x) && Number.isFinite(origin?.y)
        ? { sx: origin.x, sy: origin.y }
        : null,
      displayedNodes: latestNodesRef.current,
      nextNodes: undefined,
      startedAt: typeof performance !== 'undefined' ? performance.now() : Date.now(),
      duration: SPACE_OUT_DURATION,
    };
    setSpaceTransitionMode(`${normalizedDirection}-out`);
    setHovered(null);
    setViewVersion((value) => value + 1);
  };

  return {
    canvasRef,
    bindings,
    hovered,
    resetView,
    setViewPreset,
    zoomBy,
    zoom,
    reducedMotion,
    morphDuration: MORPH_DURATION,
    startSpaceNavigation,
    spaceTransitionMode,
    spaceTransitionDuration: SPACE_OUT_DURATION + SPACE_IN_DURATION,
  };
}
