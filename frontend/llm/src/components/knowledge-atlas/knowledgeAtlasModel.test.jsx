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
