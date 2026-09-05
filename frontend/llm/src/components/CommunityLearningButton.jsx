import React, { useEffect, useRef, useState } from 'react';
import { Target } from 'lucide-react';

import './communityLearningButton.css';

const EFFECT_SIZE = 600;
const RINGS = [
  { radius: 140, count: 8 },
  { radius: 180, count: 12 },
  { radius: 220, count: 16 },
  { radius: 260, count: 20 },
];

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const easeOut = (value) => 1 - ((1 - clamp(value, 0, 1)) ** 3);

function prefersReducedMotion() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function supportsFineHover() {
  return typeof window === 'undefined'
    || typeof window.matchMedia !== 'function'
    || window.matchMedia('(hover: hover) and (pointer: fine)').matches;
}

function drawCursor(context, x, y, rotation, scale, alpha, color) {
  context.save();
  context.translate(x, y);
  context.rotate(rotation);
  context.scale(scale, scale);
  context.globalAlpha = alpha;
  context.fillStyle = color;
  context.strokeStyle = 'rgba(255,255,255,.88)';
  context.lineWidth = 0.9;
  context.shadowColor = color;
  context.shadowBlur = 7;
  context.beginPath();
  context.moveTo(-7.2, -7.4);
  context.lineTo(8.5, 0);
  context.lineTo(2.2, 2.1);
  context.lineTo(5.8, 8.2);
  context.lineTo(1.6, 10.4);
  context.lineTo(-1.8, 4.1);
  context.lineTo(-6.2, 7.1);
  context.closePath();
  context.fill();
  context.stroke();
  context.restore();
}

function drawCommunityEffect(canvas, elapsed) {
  const context = canvas.getContext('2d');
  if (!context) return;
  const dpr = clamp(window.devicePixelRatio || 1, 1, 2);
  const pixels = Math.round(EFFECT_SIZE * dpr);
  if (canvas.width !== pixels || canvas.height !== pixels) {
    canvas.width = pixels;
    canvas.height = pixels;
  }
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, EFFECT_SIZE, EFFECT_SIZE);
  const center = EFFECT_SIZE / 2;
  const pulse = 0.5 + Math.sin((elapsed / 2000) * Math.PI * 2) * 0.5;
  const glow = context.createRadialGradient(center, center, 20, center, center, 176 + pulse * 18);
  glow.addColorStop(0, `rgba(21,155,107,${0.16 + pulse * 0.08})`);
  glow.addColorStop(0.48, `rgba(181,123,30,${0.055 + pulse * 0.035})`);
  glow.addColorStop(1, 'rgba(247,250,248,0)');
  context.fillStyle = glow;
  context.beginPath();
  context.arc(center, center, 194 + pulse * 10, 0, Math.PI * 2);
  context.fill();

  const orbit = -(elapsed / 20000) * Math.PI * 2;
  RINGS.forEach((ring, ringIndex) => {
    context.save();
    context.strokeStyle = `rgba(21,155,107,${0.045 + ringIndex * 0.008})`;
    context.lineWidth = 0.7;
    context.beginPath();
    context.arc(center, center, ring.radius, 0, Math.PI * 2);
    context.stroke();
    context.restore();

    for (let index = 0; index < ring.count; index += 1) {
      const baseAngle = (index / ring.count) * Math.PI * 2 + orbit;
      const delay = (ringIndex * 10 + index * 2) + 8;
      const entry = easeOut((elapsed - delay) / 160);
      if (entry <= 0) continue;
      const shimmer = 0.5 + Math.sin(elapsed / 850 + index * 0.72 + ringIndex) * 0.5;
      const palette = ringIndex % 2 ? '#159b6b' : '#b57b1e';
      for (let trail = 2; trail >= 1; trail -= 1) {
        const trailAngle = baseAngle + trail * 0.016;
        const trailRadius = ring.radius * entry;
        drawCursor(
          context,
          center + Math.cos(trailAngle) * trailRadius,
          center + Math.sin(trailAngle) * trailRadius,
          trailAngle,
          entry * (1 - trail * 0.2),
          entry * (0.72 - trail * 0.24),
          palette,
        );
      }
      const radius = ring.radius * entry;
      drawCursor(
        context,
        center + Math.cos(baseAngle) * radius,
        center + Math.sin(baseAngle) * radius,
        baseAngle,
        entry * (0.96 + shimmer * 0.1),
        entry * (0.78 + shimmer * 0.2),
        palette,
      );
    }
  });
}

export default function CommunityLearningButton({ onClick }) {
  const canvasRef = useRef(null);
  const frameRef = useRef(0);
  const hasDrawnRef = useRef(false);
  const [active, setActive] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(prefersReducedMotion);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(query.matches);
    query.addEventListener?.('change', update);
    return () => query.removeEventListener?.('change', update);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    if (!active || reducedMotion || !supportsFineHover()) {
      cancelAnimationFrame(frameRef.current);
      if (hasDrawnRef.current) canvas.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height);
      return undefined;
    }
    let disposed = false;
    const startedAt = performance.now();
    const draw = (now) => {
      if (disposed || document.hidden) return;
      hasDrawnRef.current = true;
      drawCommunityEffect(canvas, now - startedAt);
      frameRef.current = requestAnimationFrame(draw);
    };
    const onVisibility = () => {
      cancelAnimationFrame(frameRef.current);
      if (!document.hidden) frameRef.current = requestAnimationFrame(draw);
    };
    frameRef.current = requestAnimationFrame(draw);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      disposed = true;
      document.removeEventListener('visibilitychange', onVisibility);
      cancelAnimationFrame(frameRef.current);
    };
  }, [active, reducedMotion]);

  return (
    <span className="community-learning-cta" data-active={active ? 'true' : 'false'} data-motion={reducedMotion ? 'reduced' : 'full'}>
      <canvas
        ref={canvasRef}
        aria-hidden="true"
        data-testid="community-learning-effect"
        data-particles="168"
      />
      <button
        type="button"
        data-effect="community-cursor-orbit"
        onClick={onClick}
        onMouseEnter={() => { if (supportsFineHover()) setActive(true); }}
        onMouseLeave={() => setActive(false)}
        onFocus={() => setActive(true)}
        onBlur={() => setActive(false)}
      >
        <span aria-hidden="true" className="community-learning-cta__shine" />
        <Target aria-hidden="true" size={14} />
        <span>开始今日学习</span>
      </button>
    </span>
  );
}
