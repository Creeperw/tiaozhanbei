import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  adaptClassicRouteBooks,
  adaptClassicRouteStage,
  loadClassicLearningRoute,
  loadClassicLearningRoutes,
  loadPlannedLearningPath,
} from './learningPathApi';


describe('planned learning path API', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ schema_version: '1.0', nodes: [] }),
    })));
  });

  it('uses the main backend namespace and encodes parent IDs', async () => {
    await loadPlannedLearningPath('plan:stage/1');

    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/learning-path?parent_id=plan%3Astage%2F1',
      expect.objectContaining({ credentials: 'include' }),
    );
  });

  it('loads the five qualification targets and resolves their textbook route detail', async () => {
    fetch
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({
          schema_version: '1.0',
          target_kind: 'qualification_exam',
          items: [{
            target_id: 'tcm_physician',
            official_name: '中医执业医师资格考试',
            textbook_route_id: 'textbook_tcm_physician',
          }],
          total: 1,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ schema_version: '1.0', route: { route_id: 'route/a', stages: [] } }),
      });

    const catalog = await loadClassicLearningRoutes();
    await loadClassicLearningRoute('route/a');

    expect(catalog.items[0]).toMatchObject({
      route_id: 'tcm_physician',
      goal_name: '中医执业医师资格考试',
      textbook_route_id: 'textbook_tcm_physician',
    });
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/qualification-targets');
    expect(fetch.mock.calls[1][0]).toBe('/api/v1/learning-routes/route%2Fa');
  });

  it('adapts classic stages and books without pretending they are personalized', () => {
    const route = { route_id: 'route-1' };
    const stage = { stage_id: 'stage-1', order: 1, name: '基础阶段', objective: '建立基础', books: ['《中医学基础》'] };

    const stageNode = adaptClassicRouteStage(route, stage);
    const [bookNode] = adaptClassicRouteBooks(route, stage);

    expect(stageNode).toMatchObject({ node_type: 'stage', title: '基础阶段', status: 'unassessed', child_count: 1 });
    expect(bookNode).toMatchObject({ node_type: 'book', title: '《中医学基础》', status: 'unassessed' });
    expect(bookNode.navigation).toMatchObject({ action: 'open_knowledge_atlas', route_id: 'textbook_14_5', book: '中医学基础' });
  });
});
