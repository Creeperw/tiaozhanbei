import { describe, expect, it } from 'vitest';
import {
  applyClassicRouteProgress,
  applyPlannedRouteProgress,
  classicBookNodesWithProgress,
} from './classicRouteProgress';
import { adaptClassicRouteStage } from './learningPathApi';

const route = {
  route_id: 'classic-tcm',
  stages: [
    { stage_id: 'stage-1', order: 1, name: '基础阶段', books: ['《中医学基础》'] },
    { stage_id: 'stage-2', order: 2, name: '方剂阶段', books: ['《方剂学》', '《中药学》'] },
    { stage_id: 'stage-3', order: 3, name: '临床阶段', books: ['《中医内科学》'] },
  ],
};

function baseState() {
  const nodes = route.stages.map((stage) => adaptClassicRouteStage(route, stage));
  return {
    nodes,
    stages: nodes.map((node) => ({ ...node, id: node.node_id, nodeId: node.node_id })),
    classicRoute: route,
    atlasRouteId: 'textbook_14_5',
  };
}

describe('classic route progress projection', () => {
  it('marks a fully learned stage completed and activates the next stage', () => {
    const result = applyClassicRouteProgress(baseState(), {
      '中医学基础': { progress: 1 },
      '方剂学': { progress: 0.4 },
      '中药学': { progress: 0 },
    });

    expect(result.nodes.map((node) => node.status)).toEqual(['completed', 'in_progress', 'next']);
    expect(result.nodes[0]).toMatchObject({ completed_count: 1, incomplete_count: 0, average_mastery: 100 });
    expect(result.nodes[1]).toMatchObject({ completed_count: 0, incomplete_count: 2, average_mastery: 20 });
    expect(result.stages.map((stage) => stage.level)).toEqual(['已完成', '当前阶段', '下一阶段']);
  });

  it('uses completed and in-progress states for books inside a classic stage', () => {
    const books = classicBookNodesWithProgress(route, route.stages[1], 'textbook_14_5', {
      '方剂学': { progress: 1 },
      '中药学': { progress: 0.35 },
    });

    expect(books.map((book) => book.status)).toEqual(['completed', 'in_progress']);
    expect(books[0].average_mastery).toBe(100);
    expect(books[1].average_mastery).toBe(35);
  });

  it('projects textbook progress onto personalized-route stages without moving its current stage', () => {
    const nodes = [
      { node_id: 'planned-1', node_type: 'stage', status: 'completed', title: '第一阶段' },
      { node_id: 'planned-2', node_type: 'stage', status: 'in_progress', title: '第二阶段' },
      { node_id: 'planned-3', node_type: 'stage', status: 'locked', title: '第三阶段' },
    ];
    const result = applyPlannedRouteProgress(
      {
        nodes,
        stages: nodes.map((node) => ({ ...node, id: node.node_id, nodeId: node.node_id })),
        classicRoute: null,
      },
      {
        'planned-1': [{ title: '《中医学基础》', navigation: { book: '中医学基础' } }],
        'planned-2': [{ title: '《方剂学》', navigation: { book: '方剂学' } }],
        'planned-3': [{ title: '《中药学》', navigation: { book: '中药学' } }],
      },
      {
        中医学基础: { progress: 1 },
        方剂学: { progress: 0.35 },
        中药学: { progress: 0 },
      },
    );

    expect(result.nodes.map((node) => node.status)).toEqual(['completed', 'in_progress', 'next']);
    expect(result.nodes[0]).toMatchObject({ completed_count: 1, average_mastery: 100 });
    expect(result.nodes[1]).toMatchObject({ completed_count: 0, average_mastery: 35 });
    expect(result.bookProgress.方剂学.progress).toBe(0.35);
  });
});
