import { describe, expect, it } from 'vitest';

import {
  arrangeAtlasNodes,
  filterAtlasNodes,
  getAtlasResourceKind,
  normalizeAtlasNode,
  projectAtlasNodes,
} from './knowledgeAtlasModel';

const nodes = [
  { id: 'both', name: '折返', count: 8, question_count: 3, video_count: 2 },
  { id: 'question', name: '动作电位', count: 6, question_count: 2, video_count: 0 },
  { id: 'video', name: '钠通道', count: 4, question_count: 0, video_count: 1 },
  { id: 'plain', name: '复极', count: 2, question_count: 0, video_count: 0 },
];

describe('knowledgeAtlasModel', () => {
  it('normalizes API nodes and preserves all four resource semantics', () => {
    expect(nodes.map((node) => getAtlasResourceKind(normalizeAtlasNode(node))))
      .toEqual(['both', 'question', 'video', 'plain']);
  });

  it('provides natural golden sphere, sequence, and semantic cluster arrangements', () => {
    for (const mode of ['sphere', 'sequence', 'semantic']) {
      const arranged = arrangeAtlasNodes(nodes, mode, 3);
      expect(arranged).toHaveLength(nodes.length);
      expect(arranged.every((node) => Number.isFinite(node.px) && Number.isFinite(node.py) && Number.isFinite(node.pz))).toBe(true);
    }
    expect(arrangeAtlasNodes(nodes, 'sequence', 3).map((node) => node.name))
      .toEqual(['动作电位', '复极', '钠通道', '折返']);
  });

  it('centers highly similar semantic nodes instead of pinning them below the stage', () => {
    const similarNodes = Array.from({ length: 12 }, (_, index) => ({
      id: `similar-${index}`,
      name: '相同知识点',
      count: 1,
    }));
    const arranged = arrangeAtlasNodes(similarNodes, 'semantic', 3);
    const averageY = arranged.reduce((sum, node) => sum + node.py, 0) / arranged.length;

    expect(Math.abs(averageY)).toBeLessThan(0.12);
    expect(Math.max(...arranged.map((node) => Math.abs(node.py)))).toBeLessThan(0.62);
  });

  it('keeps uneven semantic clusters inside the visible vertical stage', () => {
    const groupedNodes = [
      ...Array.from({ length: 7 }, (_, index) => ({ id: `heart-${index}`, name: `心脏电生理 ${index}` })),
      ...Array.from({ length: 4 }, (_, index) => ({ id: `liver-${index}`, name: `肝脏代谢 ${index}` })),
      ...Array.from({ length: 2 }, (_, index) => ({ id: `kidney-${index}`, name: `肾脏滤过 ${index}` })),
    ];
    const arranged = arrangeAtlasNodes(groupedNodes, 'semantic', 3);
    const projected = projectAtlasNodes(arranged, {
      width: 900,
      height: 600,
      yaw: 0,
      pitch: 0,
      zoom: 1,
    });

    expect(new Set(arranged.map((node) => node.cluster_id)).size).toBeGreaterThan(1);
    expect(projected.every((node) => node.y > 120 && node.y < 480)).toBe(true);
  });

  it('filters only the current level and projects visible hit targets', () => {
    expect(filterAtlasNodes(nodes, '返').map((node) => node.id)).toEqual(['both']);
    const projected = projectAtlasNodes(arrangeAtlasNodes(nodes, 'sphere', 3), {
      width: 900,
      height: 600,
      yaw: 0.2,
      pitch: -0.1,
      zoom: 1.1,
    });
    expect(projected).toHaveLength(nodes.length);
    expect(projected.every((node) => Number.isFinite(node.x) && Number.isFinite(node.y) && node.radius > 0)).toBe(true);
  });
});
