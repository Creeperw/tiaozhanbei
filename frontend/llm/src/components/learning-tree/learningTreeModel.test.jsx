import { describe, expect, it } from 'vitest';
import {
  buildTreeEdges,
  buildFishboneEdges,
  clampSemanticScale,
  centerViewportOnFocus,
  getSemanticFishboneMetrics,
  isDoubleActivation,
  layoutDependencyTree,
  layoutRadialTree,
  panViewport,
  resetViewport,
  zoomViewportAtPoint,
} from './learningTreeModel';

const nodes = [
  { membership_id: 'root', title: '方剂学', parent_membership_id: null },
  { membership_id: 'child-a', title: '解表剂', parent_membership_id: 'root' },
  { membership_id: 'child-b', title: '温里剂', parent_membership_id: 'root' },
  { membership_id: 'leaf', title: '四君子汤', parent_membership_id: 'child-a' },
];

describe('learningTreeModel', () => {
  it('builds one stable parent edge for every non-root node', () => {
    expect(buildTreeEdges(nodes)).toEqual([
      { from: 'root', to: 'child-a' },
      { from: 'root', to: 'child-b' },
      { from: 'child-a', to: 'leaf' },
    ]);
  });

  it('connects visible main nodes as a spine while preserving contained-node ribs', () => {
    const visible = [
      { membership_id: 'main-a', parent_membership_id: 'hidden-root' },
      { membership_id: 'rib', parent_membership_id: 'main-a' },
      { membership_id: 'main-b', parent_membership_id: 'hidden-root' },
      { membership_id: 'main-c', parent_membership_id: 'another-hidden-root' },
    ];

    expect(buildFishboneEdges(visible)).toEqual([
      { from: 'main-a', to: 'main-b', kind: 'spine' },
      { from: 'main-b', to: 'main-c', kind: 'spine' },
      { from: 'main-a', to: 'rib', kind: 'rib' },
    ]);
  });

  it('lays dependency nodes from left to right by graph depth', () => {
    const positions = layoutDependencyTree(nodes, buildTreeEdges(nodes), {
      width: 960,
      height: 520,
    });

    expect(positions.root.x).toBeLessThan(positions['child-a'].x);
    expect(positions['child-a'].x).toBeLessThan(positions.leaf.x);
    expect(positions['child-b'].y).not.toBe(positions.root.y);
    expect(positions['child-b'].x).toBeLessThan(positions['child-a'].x);
    expect(layoutDependencyTree([...nodes], buildTreeEdges(nodes), { width: 960, height: 520 }))
      .toEqual(positions);
  });

  it('keeps the selected first-level node in the center of a radial hierarchy', () => {
    const positions = layoutRadialTree('root', nodes, { width: 900, height: 620 });

    expect(positions.root).toMatchObject({ x: 450, y: 310, depth: 0 });
    expect(positions['child-a'].depth).toBe(1);
    expect(positions.leaf.depth).toBe(2);
    expect(Math.hypot(positions.leaf.x - 450, positions.leaf.y - 310))
      .toBeGreaterThan(Math.hypot(positions['child-a'].x - 450, positions['child-a'].y - 310));
  });

  it('recognizes a double activation only for the same node inside the threshold', () => {
    expect(isDoubleActivation({ id: 'root', at: 1000 }, 'root', 1250)).toBe(true);
    expect(isDoubleActivation({ id: 'root', at: 1000 }, 'child-a', 1200)).toBe(false);
    expect(isDoubleActivation({ id: 'root', at: 1000 }, 'root', 1500)).toBe(false);
  });

  it('places a sequential first-level learning path above and below one shared axis', () => {
    const sequence = Array.from({ length: 6 }, (_, index) => ({
      membership_id: `node-${index}`,
      parent_membership_id: index ? `node-${index - 1}` : null,
    }));
    const positions = layoutDependencyTree(sequence, buildTreeEdges(sequence), {
      width: 960,
      height: 640,
    });
    const yBands = new Set(Object.values(positions).map(({ y }) => Math.round(y)));
    const axisBands = new Set(Object.values(positions).map(({ axisY }) => Math.round(axisY)));

    expect(yBands).toEqual(new Set([208, 432]));
    expect(axisBands).toEqual(new Set([320]));
    expect(positions['node-0'].x).toBeLessThan(positions['node-5'].x);
    expect(positions['node-0'].lane).toBe('upper-spine');
    expect(positions['node-1'].lane).toBe('lower-spine');
  });

  it('extends contained nodes above and below their parent without breaking the main spine', () => {
    const fishboneNodes = [
      { membership_id: 'main-a', parent_membership_id: null },
      { membership_id: 'main-b', parent_membership_id: 'main-a' },
      { membership_id: 'main-c', parent_membership_id: 'main-b' },
      { membership_id: 'rib-up', parent_membership_id: 'main-a' },
      { membership_id: 'rib-down', parent_membership_id: 'main-a' },
    ];
    const positions = layoutDependencyTree(fishboneNodes, buildTreeEdges(fishboneNodes), {
      width: 960,
      height: 640,
    });

    expect(positions['main-a'].y).toBeLessThan(320);
    expect(positions['main-b'].y).toBeGreaterThan(320);
    expect(positions['main-c'].y).toBeLessThan(320);
    expect(positions['rib-up'].y).toBeLessThan(positions['main-a'].y);
    expect(positions['rib-down'].y).toBeGreaterThan(positions['main-a'].y);
    expect(positions['rib-up'].x).toBe(positions['main-a'].x);
    expect(positions['rib-down'].x).toBe(positions['main-a'].x);
  });

  it('fans out many contained siblings without collapsing them onto one boundary point', () => {
    const crowded = [
      { membership_id: 'main', parent_membership_id: null },
      { membership_id: 'next-main', parent_membership_id: 'main' },
      ...Array.from({ length: 6 }, (_, index) => ({
        membership_id: `rib-${index}`,
        parent_membership_id: 'main',
      })),
    ];
    const positions = layoutDependencyTree(crowded, buildTreeEdges(crowded), {
      width: 960,
      height: 640,
    });
    const ribPoints = Array.from({ length: 6 }, (_, index) => positions[`rib-${index}`]);
    const uniquePoints = new Set(ribPoints.map(({ x, y }) => `${Math.round(x)}:${Math.round(y)}`));

    expect(uniquePoints.size).toBe(6);
  });

  it('keeps explicit spine edges on the main axis when a rib chain is longer', () => {
    const mixedNodes = ['a', 'b', 'c', 'rib-1', 'rib-2', 'rib-3'].map((membership_id) => ({ membership_id }));
    const mixedEdges = [
      { from: 'a', to: 'b', kind: 'spine' },
      { from: 'b', to: 'c', kind: 'spine' },
      { from: 'a', to: 'rib-1', kind: 'rib' },
      { from: 'rib-1', to: 'rib-2', kind: 'rib' },
      { from: 'rib-2', to: 'rib-3', kind: 'rib' },
    ];
    const positions = layoutDependencyTree(mixedNodes, mixedEdges, { width: 960, height: 640 });

    expect(positions.a.depth).toBe(0);
    expect(positions.b.depth).toBe(1);
    expect(positions.c.depth).toBe(2);
    expect(positions['rib-1'].depth).toBe(0);
    expect(positions.c.x).toBeGreaterThan(positions.b.x);
  });

  it('zooms around the pointer and clamps the viewport scale', () => {
    const initial = { scale: 1, x: 20, y: 30 };
    const pointer = { x: 200, y: 160 };
    const zoomed = zoomViewportAtPoint(initial, 1.5, pointer);

    expect((pointer.x - zoomed.x) / zoomed.scale).toBeCloseTo((pointer.x - initial.x) / initial.scale);
    expect((pointer.y - zoomed.y) / zoomed.scale).toBeCloseTo((pointer.y - initial.y) / initial.scale);
    expect(zoomViewportAtPoint(initial, 99, pointer).scale).toBe(1.8);
    expect(zoomViewportAtPoint(initial, 0.01, pointer).scale).toBe(0.55);
  });

  it('pans and resets with finite viewport values', () => {
    expect(panViewport({ scale: 1.2, x: 5, y: -4 }, 12, 8)).toEqual({
      scale: 1.2,
      x: 17,
      y: 4,
    });
    expect(resetViewport()).toEqual({ scale: 1, x: 0, y: 0 });
  });

  it('uses semantic scale to tighten the fishbone while changing branch geometry', () => {
    const spacious = getSemanticFishboneMetrics(0.7);
    const compact = getSemanticFishboneMetrics(1.6);

    expect(compact.axisGap).toBeLessThan(spacious.axisGap);
    expect(compact.branchGap).toBeLessThan(spacious.branchGap);
    expect(compact.branchAngle).not.toBe(spacious.branchAngle);
    expect(clampSemanticScale(99)).toBe(1.8);
    expect(clampSemanticScale(0.01)).toBe(0.55);
  });

  it('centers the current and next node together in the visible fishbone area', () => {
    const viewport = centerViewportOnFocus(
      { scale: 1, x: 0, y: 0 },
      { current: { x: 420, y: 260 }, next: { x: 580, y: 380 } },
      { width: 960, height: 640 },
    );

    expect(viewport).toMatchObject({ scale: 1, x: -20, y: 0 });
  });
});
