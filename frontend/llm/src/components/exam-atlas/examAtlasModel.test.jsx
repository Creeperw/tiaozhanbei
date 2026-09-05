import { describe, expect, it } from 'vitest';
import {
  buildAssistantIntent,
  buildKnowledgeIntent,
  buildPracticeIntent,
  distributeSphere,
  filterAtlasNodes,
  groupKnowledgePoints,
  hitTestProjected,
  placeVisibleLabels,
  projectPoint,
  rotatePoint,
  stableHash,
  transitionValues,
} from './examAtlasModel';

const nodes = [
  { membership_id: 'm-a', title: '中医基础理论', child_count: 4 },
  { membership_id: 'm-b', title: '中药学', child_count: 3 },
  { membership_id: 'm-c', title: '方剂学', child_count: 2 },
];

describe('examAtlasModel', () => {
  it('distributes the same nodes to stable points on a unit sphere', () => {
    const first = distributeSphere(nodes);
    const second = distributeSphere([...nodes]);

    expect(first).toEqual(second);
    expect(stableHash('m-a')).toBe(stableHash('m-a'));
    first.forEach((node) => {
      expect(Math.hypot(node.px, node.py, node.pz)).toBeCloseTo(1, 5);
    });
  });

  it('rotates and projects a point with bounded perspective', () => {
    const rotated = rotatePoint({ px: 1, py: 0, pz: 0 }, { yaw: Math.PI / 2, pitch: 0 });
    expect(rotated.x).toBeCloseTo(0, 5);
    expect(rotated.z).toBeCloseTo(1, 5);

    const projected = projectPoint(rotated, {
      centerX: 320,
      centerY: 240,
      radius: 180,
      zoom: 1,
    });
    expect(projected.sx).toBeCloseTo(320, 5);
    expect(projected.sy).toBeCloseTo(240, 5);
    expect(projected.depth).toBe(1);
  });

  it('hit-tests front-facing nodes and ignores hidden back nodes', () => {
    const front = { id: 'front', sx: 100, sy: 100, z: 0.8, radius: 6 };
    const back = { id: 'back', sx: 100, sy: 100, z: -0.6, radius: 8 };

    expect(hitTestProjected([back, front], { x: 106, y: 103 })?.id).toBe('front');
    expect(hitTestProjected([back], { x: 100, y: 100 })).toBeNull();
    expect(hitTestProjected([front], { x: 180, y: 180 })).toBeNull();
  });

  it('calculates dive and back transition endpoints', () => {
    expect(transitionValues({ mode: 'dive-out', progress: 0 })).toMatchObject({ alpha: 1, scale: 1 });
    expect(transitionValues({ mode: 'dive-out', progress: 1 })).toMatchObject({ alpha: 0, scale: 5.5 });
    expect(transitionValues({ mode: 'back-out', progress: 1 })).toMatchObject({ alpha: 0, scale: 0.24 });
    expect(transitionValues({ mode: 'dive-in', progress: 1 })).toMatchObject({ alpha: 1, scale: 1 });
  });

  it('keeps higher-depth labels and removes colliding labels', () => {
    const labels = placeVisibleLabels([
      { id: 'rear', sx: 90, sy: 100, z: 0.2, radius: 5, label: '后方节点' },
      { id: 'front', sx: 90, sy: 100, z: 0.8, radius: 5, label: '前方节点' },
      { id: 'separate', sx: 240, sy: 100, z: 0.7, radius: 5, label: '独立节点' },
    ], { measureText: (text) => text.length * 12, threshold: 0 });

    expect(labels.map((item) => item.id)).toEqual(['front', 'separate']);
  });

  it('searches only the current layer by title and node path', () => {
    const result = filterAtlasNodes([
      ...nodes,
      { membership_id: 'm-d', title: '温病学', node: { path: ['中医经典', '温病学'] } },
    ], '经典');

    expect(result.map((item) => item.membership_id)).toEqual(['m-d']);
  });

  it('groups normalized KP names while preserving stable variants', () => {
    const grouped = groupKnowledgePoints([
      { kp_id: 'kp-1', name: '阴阳 学说', path: ['中医学', '基础', '阴阳学说'], accepted_count: 1 },
      { kp_id: 'kp-2', name: '陰陽 學說', path: ['中医学', '基础', '阴阳学说'], accepted_count: 2 },
      { kp_id: 'kp-3', name: '阴阳学说', path: ['中医学', '理论', '阴阳学说'], accepted_count: 1 },
    ]);

    expect(grouped).toHaveLength(2);
    expect(grouped.find((item) => item.name === '阴阳 学说').variants.map((item) => item.kp_id)).toEqual(['kp-1', 'kp-3']);
  });

  it('builds practice, knowledge, and assistant intents with readable context', () => {
    const context = {
      trackId: 'track-a',
      membershipId: 'membership-a',
      kpId: 'kp-a',
      kpName: '阴阳学说',
      path: ['中医学', '中医基础理论', '阴阳学说'],
    };

    expect(buildPracticeIntent(context)).toEqual({
      page: 'practice',
      params: { trackId: 'track-a', membershipId: 'membership-a', kpId: 'kp-a', kpName: '阴阳学说' },
    });
    expect(buildKnowledgeIntent(context)).toEqual({
      page: 'knowledge',
      params: { trackId: 'track-a', membershipId: 'membership-a', kpId: 'kp-a', query: '阴阳学说' },
    });
    expect(buildAssistantIntent(context)).toMatchObject({
      page: 'assistant',
      params: { trackId: 'track-a', membershipId: 'membership-a', kpId: 'kp-a' },
    });
    expect(buildAssistantIntent(context).params.context).toContain('中医学 / 中医基础理论 / 阴阳学说');
  });
});
