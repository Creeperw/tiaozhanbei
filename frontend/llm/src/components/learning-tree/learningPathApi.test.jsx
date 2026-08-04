import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  adaptClassicRouteBooks,
  adaptClassicRouteStage,
  loadClassicLearningRoute,
  loadClassicLearningRoutes,
  loadPlannedLearningPath,
  loadPlannedLearningPathForTarget,
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

describe('loadPlannedLearningPathForTarget target matching', () => {
  const plannedPayload = {
    schema_version: '1.0',
    nodes: [{ node_id: 'plan:a', node_type: 'stage', title: '中医基础与文化语言' }],
    total: 1,
    has_more: false,
  };
  const target = {
    target_id: 'tcm_physician',
    official_name: '中医执业医师资格考试',
    textbook_route_id: 'textbook_tcm_physician',
  };

  function stubResponses(planningRoute) {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify(plannedPayload),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({
          long_term_plan: planningRoute === null ? null : { planning_route: planningRoute },
        }),
      }));
  }

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  it('信任教材路线 ID：ID 一致时，即使 goal_name 与目标名互不包含也展示已发布路径', async () => {
    stubResponses({
      goal_name: '继续完成中医执业医师的学习规划',
      textbook_route: { route: { route_id: 'textbook_tcm_physician' } },
    });

    const payload = await loadPlannedLearningPathForTarget(target);

    expect(payload.availability).not.toBe('requires_target_plan');
    expect(payload.nodes).toHaveLength(1);
  });

  it('教材路线 ID 不一致时判定为当前考试还没有个性化路径', async () => {
    stubResponses({
      goal_name: '中医执业医师资格考试',
      textbook_route: { route: { route_id: 'textbook_integrated_clinical' } },
    });

    const payload = await loadPlannedLearningPathForTarget(target);

    expect(payload.availability).toBe('requires_target_plan');
    expect(payload.nodes).toHaveLength(0);
    expect(payload.current_node_id).toBeNull();
  });

  it('双方都缺失教材路线 ID 时，退化为目标名双向包含兜底并展示路径', async () => {
    stubResponses({
      goal_name: '中医执业医师资格考试（2025 版大纲）',
      textbook_route: null,
    });

    const payload = await loadPlannedLearningPathForTarget(target);

    expect(payload.availability).not.toBe('requires_target_plan');
    expect(payload.nodes).toHaveLength(1);
  });

  it('双方都缺失 ID 且目标名互不包含时，才判定为没有个性化路径', async () => {
    stubResponses({
      goal_name: '中西医结合执业助理医师资格考试',
      textbook_route: null,
    });

    const payload = await loadPlannedLearningPathForTarget(target);

    expect(payload.availability).toBe('requires_target_plan');
    expect(payload.nodes).toHaveLength(0);
  });

  it('context 没有已发布计划时不擅自置空（交给 /learning-path 自身数据决定）', async () => {
    stubResponses(null);

    const payload = await loadPlannedLearningPathForTarget(target);

    expect(payload.availability).not.toBe('requires_target_plan');
    expect(payload.nodes).toHaveLength(1);
  });

  it('context 加载失败时保持宽松（不阻塞已发布路径展示）', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify(plannedPayload),
      })
      .mockRejectedValueOnce(new Error('context 请求失败')));

    const payload = await loadPlannedLearningPathForTarget(target);

    expect(payload.availability).not.toBe('requires_target_plan');
    expect(payload.nodes).toHaveLength(1);
  });
});
