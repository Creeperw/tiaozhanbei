import { describe, expect, it } from 'vitest';

import {
  buildKnowledgePlanetEdges,
  layoutKnowledgePlanet,
  mergeExpandedPlanetBranch,
} from './knowledgePlanetModel';

const nodes = [
  { membership_id: 'root', parent_membership_id: null, title: '方剂学' },
  { membership_id: 'recent', parent_membership_id: 'root', title: '桂枝汤证', display_order: 1 },
  { membership_id: 'old', parent_membership_id: 'root', title: '四君子汤', display_order: 2 },
  { membership_id: 'next', parent_membership_id: 'root', title: '方剂证型辨析', display_order: 3 },
  { membership_id: 'later', parent_membership_id: 'root', title: '理气剂', display_order: 4 },
];

const states = [
  { membership_id: 'recent', status: 'completed', last_assessed_at: '2026-07-17T08:00:00' },
  { membership_id: 'old', status: 'completed', last_assessed_at: '2026-05-01T08:00:00', review_due: true },
  { membership_id: 'next', status: 'unassessed', display_order: 3 },
  { membership_id: 'later', status: 'unassessed', display_order: 4 },
];

const distance = (position) => Math.hypot(position.x, position.y, position.z);

describe('knowledgePlanetModel', () => {
  it('lays learned history left/back and future learning right/front', () => {
    const positions = layoutKnowledgePlanet(nodes, states, { rootId: 'root', spiralGap: 0.8 });

    expect(positions.root).toMatchObject({ x: 0, y: 0, z: 0, material: 'current' });
    expect(positions.recent.x).toBeLessThan(0);
    expect(positions.recent.z).toBeLessThan(0);
    expect(positions.next.x).toBeGreaterThan(0);
    expect(positions.next.z).toBeGreaterThan(0);
  });

  it('keeps recent and next nodes closer than old and later nodes', () => {
    const positions = layoutKnowledgePlanet(nodes, states, { rootId: 'root', spiralGap: 0.8 });

    expect(distance(positions.recent)).toBeLessThan(distance(positions.old));
    expect(distance(positions.next)).toBeLessThan(distance(positions.later));
    expect(positions.old.material).toBe('review_due');
    expect(positions.next.material).toBe('next');
    expect(positions.later.material).toBe('unlearned');
  });

  it('falls back to official order without fabricating learning history', () => {
    const positions = layoutKnowledgePlanet(nodes, [], { rootId: 'root', spiralGap: 0.8 });

    expect(positions.recent.side).toBe('future');
    expect(distance(positions.recent)).toBeLessThan(distance(positions.later));
    expect(positions.recent.lastAssessedAt).toBeNull();
  });

  it('keeps a dense ordered path inside the readable sphere while preserving distance order', () => {
    const denseNodes = [
      nodes[0],
      ...Array.from({ length: 24 }, (_, index) => ({
        membership_id: `dense-${index}`,
        parent_membership_id: 'root',
        display_order: index + 1,
      })),
    ];
    const positions = layoutKnowledgePlanet(denseNodes, [], { rootId: 'root', spiralGap: 0.8 });
    const radii = denseNodes.slice(1).map((node) => positions[node.membership_id].radius);
    const verticalSpread = Math.max(...denseNodes.slice(1).map((node) => positions[node.membership_id].y))
      - Math.min(...denseNodes.slice(1).map((node) => positions[node.membership_id].y));
    const horizontalSpread = Math.max(...denseNodes.slice(1).map((node) => positions[node.membership_id].x))
      - Math.min(...denseNodes.slice(1).map((node) => positions[node.membership_id].x));

    expect(Math.max(...radii)).toBeLessThan(4.2);
    expect(radii.every((radius, index) => index === 0 || radius > radii[index - 1])).toBe(true);
    expect(verticalSpread).toBeGreaterThan(3.5);
    expect(horizontalSpread).toBeGreaterThan(2);
  });

  it('bounds very large paths inside the planet without losing monotonic time distance', () => {
    const manyNodes = [
      nodes[0],
      ...Array.from({ length: 160 }, (_, index) => ({
        membership_id: `many-${index}`,
        parent_membership_id: 'root',
        display_order: index + 1,
      })),
    ];
    const positions = layoutKnowledgePlanet(manyNodes, [], { rootId: 'root', spiralGap: 0.8 });
    const radii = manyNodes.slice(1).map((node) => positions[node.membership_id].radius);

    expect(Math.max(...radii)).toBeLessThan(3.9);
    expect(radii.every((radius, index) => index === 0 || radius > radii[index - 1])).toBe(true);
  });

  it('keeps history and future on their own side and orders timeline edges explicitly', () => {
    const orderedNodes = [
      { membership_id: 'center', parent_membership_id: null },
      ...Array.from({ length: 8 }, (_, index) => ({
        membership_id: `history-${index}`,
        parent_membership_id: 'center',
        display_order: index,
      })),
      ...Array.from({ length: 8 }, (_, index) => ({
        membership_id: `future-${index}`,
        parent_membership_id: 'center',
        display_order: index + 8,
      })),
    ];
    const orderedStates = Array.from({ length: 8 }, (_, index) => ({
      membership_id: `history-${index}`,
      status: 'completed',
      last_assessed_at: `2026-07-${String(18 - index).padStart(2, '0')}T08:00:00`,
    }));
    const positions = layoutKnowledgePlanet(orderedNodes, orderedStates, { rootId: 'center' });
    const timeline = buildKnowledgePlanetEdges(orderedNodes, [], positions)
      .filter((edge) => edge.kind === 'timeline');

    expect(Array.from({ length: 8 }, (_, index) => positions[`history-${index}`].x < 0)).not.toContain(false);
    expect(Array.from({ length: 8 }, (_, index) => positions[`future-${index}`].x > 0)).not.toContain(false);
    expect(positions['history-7'].timelineOrder).toBeLessThan(positions['history-0'].timelineOrder);
    expect(timeline[7]).toEqual({ from: 'history-0', to: 'center', kind: 'timeline' });
    expect(timeline[8]).toEqual({ from: 'center', to: 'future-0', kind: 'timeline' });
  });

  it('builds only hierarchy and explicitly supplied relation edges', () => {
    expect(buildKnowledgePlanetEdges(nodes)).toEqual([
      { from: 'root', to: 'recent', kind: 'hierarchy' },
      { from: 'root', to: 'old', kind: 'hierarchy' },
      { from: 'root', to: 'next', kind: 'hierarchy' },
      { from: 'root', to: 'later', kind: 'hierarchy' },
    ]);
    expect(buildKnowledgePlanetEdges(nodes, [{ from: 'recent', to: 'next' }])).toContainEqual({
      from: 'recent', to: 'next', kind: 'relation',
    });
  });

  it('adds thin timeline adjacency and node-declared relations for the reference-style web', () => {
    const relatedNodes = nodes.map((node) => (
      node.membership_id === 'recent' ? { ...node, related_membership_ids: ['later'] } : node
    ));
    const positions = layoutKnowledgePlanet(relatedNodes, states, { rootId: 'root', spiralGap: 0.8 });
    const edges = buildKnowledgePlanetEdges(relatedNodes, [], positions);

    expect(edges).toContainEqual({ from: 'recent', to: 'later', kind: 'relation' });
    expect(edges.filter((edge) => edge.kind === 'timeline')).toHaveLength(nodes.length - 1);
  });

  it('merges an expanded branch without moving or duplicating existing nodes', () => {
    const child = { membership_id: 'leaf', parent_membership_id: 'recent', title: '桂枝汤' };
    const merged = mergeExpandedPlanetBranch(nodes, nodes[1], [child, nodes[2]]);

    expect(merged).toHaveLength(nodes.length + 1);
    expect(merged.filter((node) => node.membership_id === 'old')).toHaveLength(1);
    expect(merged.at(-1)).toEqual(child);
  });
});
