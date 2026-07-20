import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';

const MATERIALS = {
  current: { color: 0x0b9e79, glow: 0x5be0be, opacity: 1, scale: 1.45, glowScale: 1.45 },
  mastered: { color: 0x29b693, glow: 0x8be5cb, opacity: 0.94, scale: 1, glowScale: 1 },
  review_due: { color: 0xfff8e2, glow: 0xe9c476, opacity: 1, scale: 1.12, glowScale: 1.3 },
  in_progress: { color: 0x13a884, glow: 0x62d9ba, opacity: 1, scale: 1.16, glowScale: 1.25 },
  next: { color: 0x26a9ad, glow: 0x7bded6, opacity: 1, scale: 1.12, glowScale: 1.18 },
  unlearned: { color: 0x83cfc0, glow: 0xa7dfd2, opacity: 0.42, scale: 0.78, glowScale: 0.7 },
};

const defaultRendererFactory = () => new THREE.WebGLRenderer({ antialias: true, alpha: true });
const getNodeId = (node) => node.membership_id || node.id;

function disposeObject(object) {
  object.traverse((child) => {
    child.geometry?.dispose?.();
    if (Array.isArray(child.material)) child.material.forEach((material) => material.dispose?.());
    else child.material?.dispose?.();
  });
}

function createLine(from, to, kind) {
  const geometry = new THREE.BufferGeometry().setFromPoints([from, to]);
  const opacity = kind === 'timeline' ? 0.18 : kind === 'relation' ? 0.065 : 0.1;
  const group = new THREE.Group();
  group.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({
    color: kind === 'timeline' ? 0x9edccb : 0x80cdb9,
    transparent: true,
    opacity,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  })));
  group.add(new THREE.Line(geometry.clone(), new THREE.LineBasicMaterial({
    color: 0xc8eee3,
    transparent: true,
    opacity: opacity * 0.22,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  })));
  return group;
}

function createTimeline(positions) {
  const ordered = Object.values(positions)
    .filter(Boolean)
    .sort((a, b) => (
      (a.timelineOrder ?? a.x) - (b.timelineOrder ?? b.x)
    ))
    .map(({ x, y, z }) => new THREE.Vector3(x, y, z));
  if (ordered.length < 2) return null;
  const curve = new THREE.CatmullRomCurve3(ordered);
  const group = new THREE.Group();
  const geometry = new THREE.TubeGeometry(curve, Math.max(24, ordered.length * 10), 0.007, 5, false);
  const material = new THREE.MeshBasicMaterial({
    color: 0xa7dfd0,
    transparent: true,
    opacity: 0.12,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  group.add(new THREE.Mesh(geometry, material));
  const particleCount = 18;
  const particlePositions = new Float32Array(particleCount * 3);
  const particleGeometry = new THREE.BufferGeometry();
  particleGeometry.setAttribute('position', new THREE.BufferAttribute(particlePositions, 3));
  const particles = new THREE.Points(
    particleGeometry,
    new THREE.PointsMaterial({
      color: 0xffffff,
      size: 0.035,
      transparent: true,
      opacity: 0.78,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    }),
  );
  group.add(particles);
  group.userData = { curve, particles, phase: 0 };
  return group;
}

function createGlowTexture() {
  const size = 64;
  const data = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const distance = Math.hypot(x - size / 2, y - size / 2) / (size / 2);
      const alpha = Math.max(0, 1 - distance) ** 2.2;
      const offset = (y * size + x) * 4;
      data[offset] = 255;
      data[offset + 1] = 255;
      data[offset + 2] = 255;
      data[offset + 3] = Math.round(alpha * 255);
    }
  }
  const texture = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  texture.needsUpdate = true;
  return texture;
}

export default function KnowledgePlanetScene({
  nodes = [],
  positions = {},
  edges = [],
  rendererFactory = defaultRendererFactory,
  onNodeClick,
  onNodeDoubleClick,
  onFallback,
  onResetView,
}) {
  const hostRef = useRef(null);
  const labelRefs = useRef(new Map());
  const sceneStateRef = useRef(null);
  const pausedRef = useRef(false);
  const dragRef = useRef(null);
  const [paused, setPaused] = useState(false);
  const [panMode, setPanMode] = useState(false);
  const [rendererMode, setRendererMode] = useState('webgl');

  const positionedNodes = useMemo(
    () => nodes.filter((node) => positions[getNodeId(node)]),
    [nodes, positions],
  );

  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;

    let renderer;
    let frameId;
    let resizeObserver;
    let scene;
    let root;
    let glowTexture;
    let fallbackTimer;
    let resizeHandler;
    let contextLostHandler;
    let motionQuery;
    let motionPreferenceHandler;
    let reduceMotion = false;
    let fallbackActivated = false;
    try {
      renderer = rendererFactory();
      renderer.setPixelRatio?.(Math.min(window.devicePixelRatio || 1, 2));
      renderer.domElement.className = 'knowledge-planet__canvas';
      renderer.domElement.setAttribute('aria-hidden', 'true');
      host.appendChild(renderer.domElement);

      scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(43, 1, 0.1, 100);
      camera.position.set(0, 0.25, 12.2);
      root = new THREE.Group();
      root.rotation.set(-0.08, -0.16, 0);
      scene.add(root);
      glowTexture = createGlowTexture();

      const surface = new THREE.Mesh(
        new THREE.SphereGeometry(4.05, 48, 32),
        new THREE.MeshBasicMaterial({
          color: 0x9fe4d2,
          transparent: true,
          opacity: 0.075,
          depthWrite: false,
          side: THREE.DoubleSide,
        }),
      );
      root.add(surface);

      const shell = new THREE.Mesh(
        new THREE.SphereGeometry(4.08, 28, 18),
        new THREE.MeshBasicMaterial({
          color: 0xb4ebd7,
          wireframe: true,
          transparent: true,
          opacity: 0.038,
          depthWrite: false,
        }),
      );
      root.add(shell);

      edges.forEach((edge) => {
        const from = positions[edge.from];
        const to = positions[edge.to];
        if (!from || !to) return;
        root.add(createLine(
          new THREE.Vector3(from.x, from.y, from.z),
          new THREE.Vector3(to.x, to.y, to.z),
          edge.kind,
        ));
      });

      const timeline = createTimeline(positions);
      if (timeline) root.add(timeline);

      positionedNodes.forEach((node) => {
        const id = getNodeId(node);
        const position = positions[id];
        const style = MATERIALS[position.material] || MATERIALS.unlearned;
        const geometry = new THREE.SphereGeometry(0.058 * style.scale, 14, 10);
        const material = new THREE.MeshBasicMaterial({
          color: style.color,
          transparent: true,
          opacity: style.opacity,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.set(position.x, position.y, position.z);
        mesh.userData.nodeId = id;
        root.add(mesh);

        const glow = new THREE.Sprite(new THREE.SpriteMaterial({
          map: glowTexture,
          color: style.glow,
          transparent: true,
          opacity: Math.max(0.18, style.opacity * 0.72),
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        }));
        glow.position.copy(mesh.position);
        const glowSize = 0.42 * style.glowScale;
        glow.scale.set(glowSize, glowSize, 1);
        root.add(glow);

        if (position.material === 'review_due' || position.material === 'next' || position.material === 'current') {
          const ring = new THREE.Mesh(
            new THREE.TorusGeometry(0.115 * style.scale, 0.006, 6, 36),
            new THREE.MeshBasicMaterial({
              color: position.material === 'review_due' ? 0xe9c476 : 0xe9fff7,
              transparent: true,
              opacity: 0.62,
              blending: THREE.AdditiveBlending,
              depthWrite: false,
            }),
          );
          ring.position.copy(mesh.position);
          ring.rotation.x = Math.PI / 2.5;
          root.add(ring);
        }
      });

      resizeHandler = () => {
        const rect = host.getBoundingClientRect();
        const width = Math.max(rect.width || 760, 1);
        const height = Math.max(rect.height || 560, 1);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
        renderer.setSize?.(width, height, false);
      };
      resizeHandler();
      if (typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(resizeHandler);
        resizeObserver.observe(host);
      } else {
        window.addEventListener('resize', resizeHandler);
      }

      const activateFallback = () => {
        if (fallbackActivated) return;
        fallbackActivated = true;
        if (frameId !== undefined) window.cancelAnimationFrame(frameId);
        frameId = undefined;
        resizeObserver?.disconnect();
        if (resizeHandler) window.removeEventListener('resize', resizeHandler);
        motionQuery?.removeEventListener?.('change', motionPreferenceHandler);
        renderer?.domElement?.removeEventListener?.('webglcontextlost', contextLostHandler);
        sceneStateRef.current = null;
        if (scene) disposeObject(scene);
        scene = null;
        glowTexture?.dispose?.();
        glowTexture = null;
        renderer?.dispose?.();
        renderer?.domElement?.remove?.();
        renderer = null;
        fallbackTimer = window.setTimeout(() => setRendererMode('fallback'), 0);
        onFallback?.();
      };
      contextLostHandler = (event) => {
        event.preventDefault();
        activateFallback();
      };
      renderer.domElement.addEventListener?.('webglcontextlost', contextLostHandler);
      motionQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)');
      reduceMotion = Boolean(motionQuery?.matches);
      motionPreferenceHandler = (event) => {
        reduceMotion = event.matches;
      };
      motionQuery?.addEventListener?.('change', motionPreferenceHandler);

      const animate = () => {
        if (!pausedRef.current && !reduceMotion) {
          if (!dragRef.current) root.rotation.y += 0.0016;
          if (timeline?.userData?.particles) {
            timeline.userData.phase = (timeline.userData.phase + 0.0028) % 1;
            const attribute = timeline.userData.particles.geometry.attributes.position;
            for (let index = 0; index < attribute.count; index += 1) {
              const point = timeline.userData.curve.getPoint((timeline.userData.phase + index / attribute.count) % 1);
              attribute.setXYZ(index, point.x, point.y, point.z);
            }
            attribute.needsUpdate = true;
          }
        }
        root.updateMatrixWorld(true);
        const rect = host.getBoundingClientRect();
        positionedNodes.forEach((node) => {
          const id = getNodeId(node);
          const label = labelRefs.current.get(id);
          const position = positions[id];
          if (!label || !position) return;
          const world = new THREE.Vector3(position.x, position.y, position.z).applyMatrix4(root.matrixWorld);
          const depth = world.z;
          const projected = world.clone().project(camera);
          label.style.left = `${(projected.x * 0.5 + 0.5) * (rect.width || 760)}px`;
          label.style.top = `${(-projected.y * 0.5 + 0.5) * (rect.height || 560)}px`;
          const visible = projected.z > -1 && projected.z < 1 && depth > -1.35;
          label.style.opacity = visible ? String(Math.min(1, Math.max(0.4, 0.58 + depth * 0.1))) : '0';
          label.style.pointerEvents = visible ? 'auto' : 'none';
        });
        try {
          renderer.render?.(scene, camera);
          frameId = window.requestAnimationFrame(animate);
        } catch {
          activateFallback();
        }
      };
      frameId = window.requestAnimationFrame(animate);
      sceneStateRef.current = {
        zoom(direction) {
          camera.position.z = Math.min(16, Math.max(5.8, camera.position.z + direction));
        },
        reset() {
          camera.position.set(0, 0.25, 12.2);
          root.position.set(0, 0, 0);
          root.rotation.set(-0.08, -0.16, 0);
        },
        move(dx, dy, shouldPan) {
          if (shouldPan) {
            root.position.x += dx * 0.008;
            root.position.y -= dy * 0.008;
          } else {
            root.rotation.y += dx * 0.008;
            root.rotation.x += dy * 0.006;
          }
        },
      };
      return () => {
        resizeObserver?.disconnect();
        if (resizeHandler) window.removeEventListener('resize', resizeHandler);
        motionQuery?.removeEventListener?.('change', motionPreferenceHandler);
        renderer?.domElement?.removeEventListener?.('webglcontextlost', contextLostHandler);
        if (fallbackTimer !== undefined) window.clearTimeout(fallbackTimer);
        if (frameId !== undefined) window.cancelAnimationFrame(frameId);
        sceneStateRef.current = null;
        if (scene) disposeObject(scene);
        glowTexture?.dispose?.();
        renderer?.dispose?.();
        renderer?.domElement?.remove?.();
      };
    } catch {
      if (frameId !== undefined) window.cancelAnimationFrame(frameId);
      resizeObserver?.disconnect();
      if (resizeHandler) window.removeEventListener('resize', resizeHandler);
      motionQuery?.removeEventListener?.('change', motionPreferenceHandler);
      renderer?.domElement?.removeEventListener?.('webglcontextlost', contextLostHandler);
      if (scene) disposeObject(scene);
      glowTexture?.dispose?.();
      renderer?.dispose?.();
      renderer?.domElement?.remove?.();
      sceneStateRef.current = null;
      fallbackTimer = window.setTimeout(() => setRendererMode('fallback'), 0);
      onFallback?.();
      return () => window.clearTimeout(fallbackTimer);
    }
  }, [edges, onFallback, positionedNodes, positions, rendererFactory]);

  const zoom = (direction) => {
    sceneStateRef.current?.zoom(direction);
  };

  const resetView = () => {
    sceneStateRef.current?.reset();
    onResetView?.();
  };

  const handlePointerDown = (event) => {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragRef.current = { x: event.clientX, y: event.clientY };
  };

  const handlePointerMove = (event) => {
    const start = dragRef.current;
    const state = sceneStateRef.current;
    if (!start || !state) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    state.move(dx, dy, panMode);
    dragRef.current = { x: event.clientX, y: event.clientY };
  };

  const stopDrag = () => {
    dragRef.current = null;
  };

  return (
    <section
      className="knowledge-planet"
      aria-label="三维知识星球"
      data-renderer={rendererMode}
      data-tone="light-mint-atlas"
      data-paused={String(paused)}
    >
      <div className="knowledge-planet__toolbar" aria-label="知识星球视图控制">
        <button type="button" aria-label="放大知识星球" onClick={() => zoom(-1)}>＋</button>
        <button type="button" aria-label="缩小知识星球" onClick={() => zoom(1)}>－</button>
        <button
          type="button"
          aria-label="平移知识星球"
          aria-pressed={panMode}
          onClick={() => setPanMode((value) => !value)}
        >移</button>
        <button
          type="button"
          aria-label={paused ? '继续星球旋转' : '暂停星球旋转'}
          onClick={() => setPaused((value) => !value)}
        >{paused ? '转' : '停'}</button>
        <button type="button" aria-label="回到时间视角" onClick={resetView}>归</button>
      </div>

      <div
        ref={hostRef}
        className={`knowledge-planet__stage${panMode ? ' is-pan-mode' : ''}`}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={stopDrag}
        onPointerCancel={stopDrag}
        onWheel={(event) => zoom(event.deltaY > 0 ? 0.6 : -0.6)}
      />

      <div className="knowledge-planet__labels">
        {positionedNodes.map((node) => {
          const id = getNodeId(node);
          const position = positions[id];
          const left = Math.min(91, Math.max(9, 50 + position.x * 6.3));
          const top = Math.min(86, Math.max(12, 49 - position.y * 8.5 - position.z * 1.2));
          return (
            <button
              key={id}
              type="button"
              className={`knowledge-planet__label is-${position.material}`}
              style={{ left: `${left}%`, top: `${top}%` }}
              ref={(element) => {
                if (element) labelRefs.current.set(id, element);
                else labelRefs.current.delete(id);
              }}
              data-visual="glow-point"
              data-anchor="point-origin"
              data-material={position.material}
              aria-label={`打开${node.title || node.name || '知识点'}知识卡片，按右方向键展开下级知识点`}
              onClick={() => onNodeClick?.(node)}
              onDoubleClick={() => onNodeDoubleClick?.(node)}
              onKeyDown={(event) => {
                if (event.key !== 'ArrowRight') return;
                event.preventDefault();
                event.stopPropagation();
                onNodeDoubleClick?.(node);
              }}
            >
              <span>{node.title || node.name}</span>
            </button>
          );
        })}
      </div>

      <div className="knowledge-planet__axis" aria-hidden="true">
        <span>更早学习</span><b>当前</b><span>后续路径</span>
      </div>
      <div className="knowledge-planet__legend" aria-label="知识星球状态图例">
        <span><i className="is-mastered" />已学习</span>
        <span><i className="is-review" />待复习</span>
        <span><i className="is-next" />下一步</span>
        <span><i className="is-unlearned" />未学习</span>
      </div>
      {rendererMode === 'fallback' && (
        <p className="knowledge-planet__fallback" role="status">三维渲染不可用，已切换二维知识路径</p>
      )}
    </section>
  );
}
