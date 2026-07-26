import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DashboardPage from './DashboardPage';
import { loadAtlasNodes } from './knowledge-atlas/knowledgeAtlasApi';
import {
  loadClassicLearningRoute,
  loadClassicLearningRoutes,
  loadPlannedLearningPath,
} from './learning-tree/learningPathApi';
import { loadExamTracks, loadLearningTarget } from './exam-atlas/examAtlasApi';

vi.mock('./knowledge-atlas/knowledgeAtlasApi', () => ({ loadAtlasNodes: vi.fn() }));
vi.mock('./knowledge-atlas/knowledgeAtlasFeature', () => ({ resolveKnowledgeAtlasEnabled: vi.fn() }));
vi.mock('./exam-atlas/examAtlasApi', () => ({
  loadExamNodes: vi.fn(),
  loadExamTracks: vi.fn(),
  loadLearningTarget: vi.fn(),
  loadNodeLearnerSummary: vi.fn(),
}));
vi.mock('./learning-tree/learningPathApi', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    loadClassicLearningRoute: vi.fn(),
    loadClassicLearningRoutes: vi.fn(),
    loadPlannedLearningPath: vi.fn(),
  };
});

const stage = {
  node_id: 'stage-1',
  node_type: 'stage',
  parent_id: null,
  title: '基础阶段',
  order: 1,
  status: 'in_progress',
  description: '先完成中医基础学习。',
};

const book = {
  node_id: 'book-1',
  node_type: 'book',
  parent_id: 'stage-1',
  title: '《中医学基础》',
  order: 1,
  status: 'in_progress',
  progress: 0.2,
  navigation: { route_id: 'textbook_14_5', book: '中医学基础' },
};

function pathPage(nodes) {
  return {
    schema_version: '1.0',
    plan_ref: { plan_id: 'LP_1', plan_version: 1, route_id: 'textbook_14_5' },
    nodes,
    total: nodes.length,
  };
}

describe('DashboardPage replacement learning workshop', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    loadAtlasNodes.mockResolvedValue({
      route: 'textbook_14_5',
      nodes: [{ id: 'atlas-book-1', name: '中医学基础' }],
    });
    loadLearningTarget.mockResolvedValue({ target: { exam_track_id: 'track-1', exam_name: '中医考试' } });
    loadExamTracks.mockResolvedValue({ items: [] });
    loadClassicLearningRoutes.mockResolvedValue({ items: [] });
    loadClassicLearningRoute.mockResolvedValue({ route: { stages: [] }, navigation: {} });
    loadPlannedLearningPath.mockImplementation((parentId) => Promise.resolve(
      parentId ? pathPage([book]) : pathPage([stage]),
    ));
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        current_learning_task: {
          title: '继续基础学习',
          duration: '30 分钟',
          learning_chapter: { book: '中医学基础', title: '阴阳学说' },
        },
      }),
    })));
  });

  it('uses the textbook library as the complete learning-workshop surface', async () => {
    render(<DashboardPage onNavigate={vi.fn()} />);

    const plan = await screen.findByRole('region', { name: '当前学习计划' });
    expect(within(plan).getByText('该继续学习《中医学基础》')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '教材学习列表' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '学习《中医学基础》' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '今日学习工作区' })).not.toBeInTheDocument();
  });

  it('shows a terminal error instead of leaving the catalogue loading forever', async () => {
    loadAtlasNodes.mockRejectedValueOnce(new Error('章节目录不可用'));

    render(<DashboardPage onNavigate={vi.fn()} />);

    expect(await screen.findByText('章节目录不可用')).toBeInTheDocument();
    expect(screen.queryByText('教材目录正在准备中')).not.toBeInTheDocument();
  });
});
