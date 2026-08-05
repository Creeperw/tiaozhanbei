import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DashboardPage from './DashboardPage';
import { clearTextbookSnapshotCache, visibleWorkshopTextbooks } from './learningPlanDashboard';
import { clearTextbookCache } from './workshop-textbook/textbookCache';
import { clearTeachingResourcesPageCache } from './teachingResourcesPageCache';
import { loadAtlasNodes } from './knowledge-atlas/knowledgeAtlasApi';
import {
  loadClassicLearningRoute,
  loadClassicLearningRoutes,
  loadPlannedLearningPath,
} from './learning-tree/learningPathApi';
import { loadExamTracks, loadLearningTarget } from './exam-atlas/examAtlasApi';

vi.mock('./knowledge-atlas/knowledgeAtlasApi', () => ({ loadAtlasNodes: vi.fn() }));
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
    clearTextbookSnapshotCache();
    clearTextbookCache();
    clearTeachingResourcesPageCache();
    localStorage.clear();
    loadAtlasNodes.mockImplementation(({ level }) => Promise.resolve({
      route: 'textbook_14_5',
      nodes: level === 1
        ? [{ id: 'atlas-book-1', name: '中医学基础' }]
        : level === 2
          ? [{ id: 'chapter-1', name: '阴阳学说' }]
          : [{ id: 'section-1', name: '阴阳的基本概念' }, { id: 'section-2', name: '五行生克' }],
    }));
    loadLearningTarget.mockResolvedValue({ target: { exam_track_id: 'track-1', exam_name: '中医考试' } });
    loadExamTracks.mockResolvedValue({ items: [] });
    loadClassicLearningRoutes.mockResolvedValue({ items: [] });
    loadClassicLearningRoute.mockResolvedValue({ route: { stages: [] }, navigation: {} });
    loadPlannedLearningPath.mockImplementation((parentId) => Promise.resolve(
      parentId ? pathPage([book]) : pathPage([stage]),
    ));
    vi.stubGlobal('fetch', vi.fn((url) => {
      let payload = {};
      if (String(url).includes('/dashboard/home')) payload = {
        current_learning_task: {
          title: '继续基础学习',
          estimated_minutes: 30,
          learning_chapter: { book: '中医学基础', title: '阴阳学说' },
          items: [{ status: 'in_progress', kp_name: '五行生克关系' }],
        },
      };
      if (String(url).includes('/learning-statistics/overview')) payload = { lifetime: { focus_minutes: 5160 } };
      if (String(url).includes('/task-load-policy')) payload = { recommended_minutes: 25 };
      if (String(url).includes('/textbook-progress')) payload = { completed_section_ids: ['section-1'], last_section_id: 'section-1' };
      return Promise.resolve({ ok: true, status: 200, text: async () => JSON.stringify(payload) });
    }));
  });


  it('shows every textbook when no long-term plan exists', () => {
    const all = [{ name: 'book-a' }, { name: 'book-b' }];
    expect(visibleWorkshopTextbooks({
      allTextbooks: all,
      plannedBooks: [],
      remainingTextbooks: all,
      showAllTextbooks: false,
    })).toEqual(all);
  });

  it('always opens a textbook from the all-textbook route', async () => {
    const onNavigate = vi.fn();
    render(<DashboardPage onNavigate={onNavigate} />);

    const library = await screen.findByRole('region', { name: '教材学习列表' });
    fireEvent.click(within(library).getByRole('button', { name: '继续学习《中医学基础》' }));

    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        view: 'textbook-chapters',
        route: 'textbook_14_5',
        lv1: '中医学基础',
        bookId: '',
        uploaded: false,
        source: 'textbook-library',
      },
    });
  });
  it('uses the textbook library as the complete learning-workshop surface', async () => {
    render(<DashboardPage onNavigate={vi.fn()} />);

    const plan = await screen.findByRole('region', { name: '当前学习计划' });
    expect(within(plan).getByText('《中医学基础》')).toBeInTheDocument();
    expect(await within(plan).findByText('86 小时')).toBeInTheDocument();
    expect(within(plan).getByText('今日建议学习 25 分钟')).toBeInTheDocument();
    expect(within(plan).getByText('下一个知识点：五行生克关系')).toBeInTheDocument();
    expect(within(plan).getByText('50%')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '教材学习列表' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '继续学习《中医学基础》' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '今日学习工作区' })).not.toBeInTheDocument();
  });

  it('navigates to the current textbook and the complete learning path', async () => {
    const onNavigate = vi.fn();
    render(<DashboardPage onNavigate={onNavigate} />);
    const plan = await screen.findByRole('region', { name: '当前学习计划' });

    fireEvent.click(await within(plan).findByRole('button', { name: /继续学习/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        view: 'textbook-chapters',
        route: 'textbook_14_5',
        lv1: '中医学基础',
        source: 'learning-plan',
      },
    });

    fireEvent.click(within(plan).getByRole('button', { name: '查看完整计划' }));
    expect(onNavigate).toHaveBeenLastCalledWith({ page: 'learning-path', params: {} });
  });

  it('shows a terminal error instead of leaving the catalogue loading forever', async () => {
    loadAtlasNodes.mockRejectedValueOnce(new Error('章节目录不可用'));

    render(<DashboardPage onNavigate={vi.fn()} />);

    expect(await screen.findByText('章节目录不可用')).toBeInTheDocument();
    expect(screen.queryByText('教材目录正在准备中')).not.toBeInTheDocument();
  });

  it('restores the complete teaching-resources page immediately and refreshes it in the background', async () => {
    const firstRender = render(
      <DashboardPage currentUser={{ username: 'cache-user' }} onNavigate={vi.fn()} />,
    );
    expect(await screen.findByRole('region', { name: '教材学习列表' })).toBeInTheDocument();
    const firstPlan = await screen.findByRole('region', { name: '当前学习计划' });
    expect(await within(firstPlan).findByText('下一个知识点：五行生克关系')).toBeInTheDocument();
    expect(await within(firstPlan).findByText('50%')).toBeInTheDocument();
    expect(within(firstPlan).getByText('基础阶段')).toBeInTheDocument();
    firstRender.unmount();

    const firstFetch = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn((url, ...args) => (
      String(url).includes('/dashboard/home')
        ? new Promise(() => {})
        : firstFetch(url, ...args)
    )));
    loadAtlasNodes.mockImplementation(() => new Promise(() => {}));
    loadLearningTarget.mockImplementation(() => new Promise(() => {}));
    render(<DashboardPage currentUser={{ username: 'cache-user' }} onNavigate={vi.fn()} />);

    const cachedPlan = screen.getByRole('region', { name: '当前学习计划' });
    expect(screen.getByRole('region', { name: '教材学习列表' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '继续学习《中医学基础》' })).toBeInTheDocument();
    expect(within(cachedPlan).getByText('基础阶段')).toBeInTheDocument();
    expect(within(cachedPlan).getByText('下一个知识点：五行生克关系')).toBeInTheDocument();
    expect(cachedPlan.querySelector('.workshop-plan__focus')).toHaveAttribute('aria-busy', 'false');
    expect(within(cachedPlan).getByText('基础阶段').closest('article')).not.toHaveClass('is-loading');
    expect(screen.queryByRole('status', { name: '正在加载教材目录' })).not.toBeInTheDocument();
    expect(loadAtlasNodes).toHaveBeenCalledWith(expect.objectContaining({
      level: 1,
      route: 'textbook_14_5',
    }));
    expect(loadLearningTarget).toHaveBeenCalled();
  });
});
