import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import { IconButton } from '../ui';
import {
  clamp,
  distributeSphere,
  hitTestProjected,
  placeVisibleLabels,
  projectPoint,
  rotatePoint,
} from './examAtlasModel';

function nodeVisual(node, depth) {
  const childCount = Number(node.child_count || 0);
  const baseRadius = 4.5 + Math.min(4, Math.log10(childCount + 1) * 2);
  return {
    radius: baseRadius * (0.62 + depth * 0.72),
    fill: childCount > 0 ? '#059669' : '#0f766e',
    glow: childCount > 0 ? 'rgba(15,143,104,.26)' : 'rgba(13,148,136,.24)',
  };
}

function drawWireSphere(context, centerX, centerY, radius) {
  const glow = context.createRadialGradient(
    centerX,
    centerY,
    radius * 0.08,
    centerX,
    centerY,
    radius * 1.16,
  );
  glow.addColorStop(0, 'rgba(16,185,129,.09)');
  glow.addColorStop(0.68, 'rgba(13,148,136,.035)');
  glow.addColorStop(1, 'rgba(13,148,136,0)');
  context.fillStyle = glow;
  context.beginPath();
  context.arc(centerX, centerY, radius * 1.16, 0, Math.PI * 2);
  context.fill();

  context.strokeStyle = 'rgba(15,118,110,.2)';
  context.lineWidth = 1;
  context.beginPath();
  context.arc(centerX, centerY, radius, 0, Math.PI * 2);
  context.stroke();

  context.setLineDash([2, 7]);
  [-0.66, -0.33, 0, 0.33, 0.66].forEach((ratio) => {
    const y = centerY + ratio * radius;
    const width = radius * Math.sqrt(1 - ratio * ratio);
    context.beginPath();
    context.ellipse(centerX, y, width, width * 0.11, 0, 0, Math.PI * 2);
    context.stroke();
  });
  context.setLineDash([1, 8]);
  [-0.85, -0.42, 0, 0.42, 0.85].forEach((angle) => {
    context.beginPath();
    context.ellipse(
      centerX,
      centerY,
      radius * Math.abs(Math.cos(angle)) + 1,
      radius,
      0,
      0,
      Math.PI * 2,
    );
    context.stroke();
  });
  context.setLineDash([]);
}

export default function ExamAtlasCanvas({ nodes, onActivate }) {
  const canvasRef = useRef(null);
  const tooltipRef = useRef(null);
  const projectedRef = useRef([]);
  const frameRef = useRef(null);
  const interactionRef = useRef({
    yaw: -0.35,
    pitch: 0.12,
    zoom: 1,
    velocityX: 0,
    velocityY: 0,
    dragging: false,
    moved: false,
    lastPoint: null,
    lastFrame: 0,
  });
  const [autoRotate, setAutoRotate] = useState(true);
  const distributed = useMemo(() => distributeSphere(nodes), [nodes]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    let context;
    try {
      context = canvas.getContext('2d');
    } catch {
      return undefined;
    }
    if (!context) return undefined;

    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.round(rect.width * dpr);
      canvas.height = Math.round(rect.height * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const observer = typeof ResizeObserver === 'function'
      ? new ResizeObserver(resize)
      : null;
    observer?.observe(canvas);
    window.addEventListener('resize', resize);

    const draw = (now) => {
      const rect = canvas.getBoundingClientRect();
      const width = rect.width;
      const height = rect.height;
      const previous = interactionRef.current;
      const delta = Math.min(35, previous.lastFrame ? now - previous.lastFrame : 16.7);
      let next = { ...previous, lastFrame: now };
      if (!next.dragging && !reducedMotion) {
        if (Math.abs(next.velocityX) + Math.abs(next.velocityY) > 0.00002) {
          const damping = Math.pow(0.93, delta / 16.7);
          next = {
            ...next,
            yaw: next.yaw + next.velocityX * delta,
            pitch: clamp(next.pitch + next.velocityY * delta, -1.35, 1.35),
            velocityX: next.velocityX * damping,
            velocityY: next.velocityY * damping,
          };
        } else if (autoRotate) {
          next = { ...next, yaw: next.yaw + 0.000035 * delta };
        }
      }
      interactionRef.current = next;

      context.clearRect(0, 0, width, height);
      const centerX = width * 0.52;
      const centerY = height * 0.52;
      const sphereRadius = Math.max(110, Math.min(width, height) * 0.36) * next.zoom;
      drawWireSphere(context, centerX, centerY, sphereRadius);

      const projected = distributed.map((node) => {
        const rotated = rotatePoint(node, next);
        const point = projectPoint(rotated, {
          centerX,
          centerY,
          radius: sphereRadius,
        });
        const visual = nodeVisual(node, point.depth);
        return { ...point, ...visual, id: node.id, node, label: node.title };
      }).sort((first, second) => first.z - second.z);
      projectedRef.current = projected;

      projected.forEach((item) => {
        context.globalAlpha = 0.14 + item.depth * 0.84;
        context.fillStyle = item.fill;
        context.shadowColor = item.glow;
        context.shadowBlur = item.z > 0 ? 11 + item.depth * 11 : 3;
        context.beginPath();
        context.arc(item.sx, item.sy, item.radius, 0, Math.PI * 2);
        context.fill();
      });
      context.shadowBlur = 0;
      context.globalAlpha = 1;

      const threshold = nodes.length <= 24 ? -0.34 : nodes.length <= 80 ? 0.02 : 0.38;
      context.font = '500 12px "Noto Sans SC", "Microsoft YaHei UI", sans-serif';
      const labels = placeVisibleLabels(projected, {
        measureText: (text) => context.measureText(text).width,
        threshold,
        fontSize: 12,
      });
      labels.forEach((item) => {
        context.globalAlpha = 0.42 + item.depth * 0.58;
        context.fillStyle = '#315c51';
        const text = item.label.length > 18 ? `${item.label.slice(0, 18)}…` : item.label;
        context.fillText(text, item.box.x + 2, item.sy + 4);
      });
      context.globalAlpha = 1;
      frameRef.current = window.requestAnimationFrame(draw);
    };

    frameRef.current = window.requestAnimationFrame(draw);
    return () => {
      if (frameRef.current) window.cancelAnimationFrame(frameRef.current);
      observer?.disconnect();
      window.removeEventListener('resize', resize);
    };
  }, [autoRotate, distributed, nodes.length]);

  const canvasPoint = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const handlePointerDown = (event) => {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    if (tooltipRef.current) tooltipRef.current.hidden = true;
    interactionRef.current = {
      ...interactionRef.current,
      dragging: true,
      moved: false,
      lastPoint: canvasPoint(event),
      velocityX: 0,
      velocityY: 0,
    };
  };

  const updateTooltip = (event) => {
    const tooltip = tooltipRef.current;
    if (!tooltip || interactionRef.current.dragging) return;
    const hit = hitTestProjected(projectedRef.current, canvasPoint(event));
    if (!hit) {
      tooltip.hidden = true;
      return;
    }
    tooltip.textContent = `${hit.node.title} · ${Number(hit.node.child_count || 0) > 0 ? `${hit.node.child_count} 个下级节点` : '已确认知识点'}`;
    tooltip.style.left = `${event.clientX + 12}px`;
    tooltip.style.top = `${event.clientY + 12}px`;
    tooltip.hidden = false;
  };

  const handlePointerMove = (event) => {
    const current = interactionRef.current;
    if (!current.dragging || !current.lastPoint) {
      updateTooltip(event);
      return;
    }
    const point = canvasPoint(event);
    const dx = point.x - current.lastPoint.x;
    const dy = point.y - current.lastPoint.y;
    interactionRef.current = {
      ...current,
      yaw: current.yaw + dx * 0.006,
      pitch: clamp(current.pitch + dy * 0.006, -1.35, 1.35),
      velocityX: dx * 0.00036,
      velocityY: dy * 0.00036,
      moved: current.moved || Math.abs(dx) + Math.abs(dy) > 2,
      lastPoint: point,
    };
  };

  const handlePointerUp = (event) => {
    const current = interactionRef.current;
    const point = canvasPoint(event);
    interactionRef.current = { ...current, dragging: false, lastPoint: null };
    if (!current.moved) {
      const hit = hitTestProjected(projectedRef.current, point);
      if (hit) onActivate(hit.node);
    }
  };

  const handleWheel = (event) => {
    event.preventDefault();
    interactionRef.current = {
      ...interactionRef.current,
      zoom: clamp(
        interactionRef.current.zoom * Math.exp(-event.deltaY * 0.0007),
        0.7,
        1.6,
      ),
    };
  };

  return (
    <div className="exam-atlas-canvas-wrap">
      <canvas
        ref={canvasRef}
        className="exam-atlas-canvas"
        aria-hidden="true"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onPointerLeave={() => {
          if (tooltipRef.current) tooltipRef.current.hidden = true;
        }}
        onWheel={handleWheel}
      />
      <div ref={tooltipRef} className="exam-atlas-tooltip" role="tooltip" hidden />
      <IconButton
        label={autoRotate ? '暂停自动旋转' : '继续自动旋转'}
        className="exam-atlas-rotate"
        aria-pressed={!autoRotate}
        onClick={() => setAutoRotate((current) => !current)}
      >
        {autoRotate ? <Pause aria-hidden="true" size={17} /> : <Play aria-hidden="true" size={17} />}
      </IconButton>
    </div>
  );
}
