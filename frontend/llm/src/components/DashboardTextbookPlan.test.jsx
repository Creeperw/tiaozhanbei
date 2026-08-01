import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DashboardPage from './DashboardPage';
import { clearTextbookSnapshotCache } from './learningPlanDashboard';
import { loadAtlasNodes } from './knowledge-atlas/knowledgeAtlasApi';
import {
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
  node_id: 'stage-1', node_type: 'stage', parent_id: null, title: '基础学习阶段',
  order: 1, status: 'in_progress', progress: 0.25, child_count: 2,
  description: '先完成基础理论，再进入临床课程。', navigation: { action: 'expand' },
};
const plannedBooks = [
  {
    node_id: 'book-1', node_type: 'book', parent_id: 'stage-1', title: '《中医学基础》',
    order: 1, status: 'completed', progress: 1, child_count: 10,
    navigation: { route_id: 'textbook_14_5', book: '中医学基础' },
  },
  {
    node_id: 'book-2', node_type: 'book', parent_id: 'stage-1', title: '《方剂学》',
    order: 2, status: 'in_progress', progress: 0.35, child_count: 12,
    description: '掌握常用方剂。', navigation: { route_id: 'tcm_assistant', book: '方剂学' },
  },
];

function learningPathPage(nodes, currentNodeId) {
  return {
    schema_version: '1.0', learner_id: 'user-1',
    plan_ref: { plan_id: 'LP_1', plan_version: 1, route_id: 'textbook_14_5' },
    current_node_id: currentNodeId, nodes, offset: 0, limit: 100, total: nodes.length, has_more: false,
  };
}

describe('DashboardPage textbook plan library', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearTextbookSnapshotCache();
    localStorage.clear();
    loadAtlasNodes.mockImplementation(({ level }) => Promise.resolve({
      route: 'textbook_14_5',
      nodes: level === 1
        ? ['中医学基础', '方剂学', '针灸学', '中药学'].map((name, index) => ({ id: `atlas-${index}`, name }))
        : level === 2
          ? [{ id: 'chapter-1', name: '第一章' }]
          : ['s1', 's2', 's3', 's4'].map((id) => ({ id, name: id })),
    }));
    loadLearningTarget.mockResolvedValue({ target: { exam_track_id: 'track-1', exam_name: '中医考试' } });
    loadExamTracks.mockResolvedValue({ items: [] });
    loadClassicLearningRoutes.mockResolvedValue({ items: [], total: 0 });
    loadPlannedLearningPath.mockImplementation((parentId) => Promise.resolve(
      parentId ? learningPathPage(plannedBooks, 'book-2') : learningPathPage([stage], 'stage-1'),
    ));
    vi.stubGlobal('fetch', vi.fn((url) => {
      const payload = String(url).includes('/dashboard/home')
        ? { current_learning_task: { title: '继续完成方剂学习任务', duration: '25 分钟', learning_chapter: { book: '方剂学', title: '补益剂·补气' } } }
        : String(url).includes('/textbook-progress')
          ? { completed_section_ids: ['s1', 's2'], last_section_id: 's2' }
          : {};
      return Promise.resolve({ ok: true, status: 200, text: async () => JSON.stringify(payload) });
    }));
  });

  it('keeps planned books first and only appends remaining books after expansion', async () => {
    const onNavigate = vi.fn();
    render(<DashboardPage onNavigate={onNavigate} />);

    const plan = await screen.findByRole('region', { name: '当前学习计划' });
    expect(within(plan).getByText('《方剂学》')).toBeInTheDocument();
    expect(within(plan).getByText('补益剂·补气')).toBeInTheDocument();
    expect(await within(plan).findByText('50%')).toBeInTheDocument();

    const library = screen.getByRole('region', { name: '教材学习列表' });
    const firstPlannedCard = within(library).getByRole('button', { name: '继续学习《中医学基础》' });
    const secondPlannedCard = within(library).getByRole('button', { name: '继续学习《方剂学》' });
    expect(within(library).queryByRole('button', { name: '继续学习《针灸学》' })).not.toBeInTheDocument();
    expect(within(library).getByRole('button', { name: '展开全部教材' })).toBeInTheDocument();

    fireEvent.click(within(library).getByRole('button', { name: '展开全部教材' }));
    expect(within(library).getByRole('button', { name: '继续学习《针灸学》' })).toBeInTheDocument();
    expect(within(library).getByRole('button', { name: '继续学习《中药学》' })).toBeInTheDocument();
    expect(within(library).getByRole('button', { name: '继续学习《中医学基础》' })).toBe(firstPlannedCard);
    expect(within(library).getByRole('button', { name: '继续学习《方剂学》' })).toBe(secondPlannedCard);
    expect(within(library).queryByRole('button', { name: '展开全部教材' })).not.toBeInTheDocument();

    fireEvent.click(within(plan).getByRole('button', { name: /继续学习/ }));
    await waitFor(() => expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        view: 'textbook-chapters', route: 'textbook_14_5', lv1: '方剂学', source: 'learning-plan',
      },
    }));
  });
});
