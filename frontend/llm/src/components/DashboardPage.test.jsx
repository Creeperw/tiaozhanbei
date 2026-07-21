import React from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { cwd } from 'node:process';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DashboardPage from './DashboardPage';
import {
  loadExamNodes,
  loadExamTracks,
  loadLearningTarget,
  loadNodeLearnerSummary,
} from './exam-atlas/examAtlasApi';
import { resolveKnowledgeAtlasEnabled } from './knowledge-atlas/knowledgeAtlasFeature';
import { loadTrainingWorkspaceModules } from '../pageDataLoaders';

vi.mock('./CompactAssistant', () => ({
  default: ({ onOpenFull, onCollapsedChange, onFloatingDockChange }) => (
    <aside aria-label="常驻智能助教">
      <button type="button" onClick={() => onOpenFull('session-home')}>打开完整智能助教</button>
      <button type="button" onClick={() => onCollapsedChange?.(true)}>折叠智能助教</button>
      <button type="button" onClick={() => onCollapsedChange?.(false)}>展开智能助教</button>
      <button type="button" onClick={() => onFloatingDockChange?.(false)}>移开智能助教</button>
      <button type="button" onClick={() => onFloatingDockChange?.(true)}>放回智能助教</button>
    </aside>
  ),
}));

vi.mock('./exam-atlas/examAtlasApi', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    loadExamNodes: vi.fn(),
    loadExamTracks: vi.fn(),
    loadLearningTarget: vi.fn(),
    loadNodeLearnerSummary: vi.fn(),
  };
});

vi.mock('./knowledge-atlas/knowledgeAtlasFeature', () => ({
  resolveKnowledgeAtlasEnabled: vi.fn(),
}));

vi.mock('../pageDataLoaders', () => ({
  loadTrainingWorkspaceModules: vi.fn(),
}));

vi.mock('./learning-tree/KnowledgeTreeDrilldown', () => ({
  default: ({ rootNode }) => <section aria-label="旧版知识树钻取">{rootNode.title}</section>,
}));

const root = { membership_id: 'root', parent_membership_id: null, title: '医学综合', child_count: 2 };
const practicalRoot = { membership_id: 'practical-root', parent_membership_id: null, title: '实践技能', child_count: 1 };
const basics = { membership_id: 'basics', parent_membership_id: 'root', title: '中医基础理论', child_count: 8, path: ['中医基础理论'] };
const formula = { membership_id: 'formula', parent_membership_id: 'root', title: '方剂学', child_count: 8, path: ['中医基础理论', '中药学', '方剂学'] };
const clinicalSkill = { membership_id: 'clinical-skill', parent_membership_id: 'practical-root', title: '中医操作技能', child_count: 4 };

describe('DashboardPage learning workspace', () => {
  beforeEach(() => {
    resolveKnowledgeAtlasEnabled.mockResolvedValue(true);
    vi.stubGlobal('fetch', vi.fn((url) => {
      const payload = url.endsWith('/training/onboarding/status')
        ? { needs_survey_popup: false }
        : {
          hero: { greeting: '你好，admin 🌿', goal: '今天继续完成方剂学训练', focus: '重点掌握方证对应' },
          status_cards: [{ key: 'accuracy', label: '正确率', value: '82%' }],
          yesterday_feedback: { metrics: [{ key: 'accuracy', label: '正确率', value: '82%' }] },
          checkin_status: { checked_in_today: false, streak: 12 },
          today_tasks: [
            { key: 'review', title: '回顾昨日错题', status: 'completed', duration: '8 分钟' },
            { key: 'formula', title: '方剂学第 3 章', reason: '重点掌握方证对应', duration: '25 分钟' },
          ],
          recommendations: [], continue_learning: [], announcements: [],
        };
      return Promise.resolve({ ok: true, status: 200, text: async () => JSON.stringify(payload) });
    }));
    loadLearningTarget.mockResolvedValue({ target: { exam_track_id: 'track-a', exam_name: '中医执业医师资格考试' } });
    loadExamTracks.mockResolvedValue({ items: [{ track_id: 'track-a', title_normalized: '中医执业医师资格考试' }] });
    loadExamNodes.mockImplementation(async (_trackId, parentId) => {
      if (!parentId) return { items: [practicalRoot, root] };
      if (parentId === 'practical-root') return { items: [clinicalSkill] };
      return { items: [basics, formula] };
    });
    loadNodeLearnerSummary.mockImplementation(async (_trackId, membershipId) => (
      membershipId === 'basics'
        ? { total_count: 8, completed_count: 8, incomplete_count: 0, average_mastery: 92, status: 'completed' }
        : { total_count: 8, completed_count: 3, incomplete_count: 5, average_mastery: 62, status: 'in_progress' }
    ));
    loadTrainingWorkspaceModules.mockResolvedValue({
      workspace: {
        modules: [{
          key: 'practice_grading',
          label: '练习批改',
          description: '提交练习并获得批改。',
          enabled: true,
          badge: '可用',
        }, {
          key: 'case_training',
          label: '案例训练',
          description: '完成案例问诊训练。',
          enabled: true,
          badge: '可用',
        }],
      },
      error: '',
    });
  });

  afterEach(() => vi.unstubAllGlobals());

  it('renders the greeting above a real dependency tree and a persistent assistant', async () => {
    const onNavigate = vi.fn();
    render(<DashboardPage currentUser={{ username: 'admin' }} onNavigate={onNavigate} />);

    expect(await screen.findByLabelText('一级知识学习路径')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '今日核心任务' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('常驻智能助教')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /选择中医操作技能/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /选择医学综合/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /选择方剂学/ }));
    const plan = await screen.findByLabelText('方剂学学习规划');
    expect(plan).toHaveAttribute('data-layout', 'overlay');
    expect(within(plan).getByText('第 3 / 8 个知识点')).toBeInTheDocument();
    expect(within(plan).getByText('章节进度')).toBeInTheDocument();
    expect(within(plan).getByText('5 个待完成')).toBeInTheDocument();
    expect(within(plan).getByText('学习路径')).toBeInTheDocument();
    expect(within(plan).getByRole('button', { name: '开始练习' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '打开完整智能助教' }));
    expect(onNavigate).toHaveBeenLastCalledWith('assistant', 'session-home');
  });

  it('uses the removed focus-banner space for the learning workspace', async () => {
    render(<DashboardPage currentUser={{ username: 'admin' }} onNavigate={vi.fn()} />);

    const workspace = await screen.findByRole('region', { name: '今日学习工作区' });
    expect(within(workspace).getByRole('complementary', { name: '今日安排' })).toBeInTheDocument();
    expect(within(workspace).getByRole('region', { name: '智能助教栏' })).toContainElement(screen.getByLabelText('常驻智能助教'));

    const feedback = screen.getByRole('region', { name: '昨日学习反馈' });
    expect(within(feedback).getByText('正确率')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '今日核心任务' })).not.toBeInTheDocument();
    expect(workspace).not.toContainElement(feedback);
  });

  it('lets today schedule reclaim the right column only after the assistant moves away', async () => {
    render(<DashboardPage currentUser={{ username: 'admin' }} onNavigate={vi.fn()} />);

    const workspace = await screen.findByRole('region', { name: '今日学习工作区' });
    expect(workspace).toHaveAttribute('data-assistant-collapsed', 'false');
    expect(workspace).toHaveAttribute('data-assistant-docked', 'true');
    expect(workspace).toHaveAttribute('data-right-column', 'stable');

    fireEvent.click(screen.getByRole('button', { name: '折叠智能助教' }));
    expect(workspace).toHaveAttribute('data-assistant-collapsed', 'true');
    expect(workspace).toHaveAttribute('data-assistant-docked', 'true');

    fireEvent.click(screen.getByRole('button', { name: '移开智能助教' }));
    expect(workspace).toHaveAttribute('data-assistant-docked', 'false');

    fireEvent.click(screen.getByRole('button', { name: '展开智能助教' }));
    expect(workspace).toHaveAttribute('data-assistant-collapsed', 'false');
    expect(workspace).toHaveAttribute('data-assistant-docked', 'false');

    fireEvent.click(screen.getByRole('button', { name: '放回智能助教' }));
    expect(workspace).toHaveAttribute('data-assistant-docked', 'true');
  });

  it('keeps the today column width unchanged when the docked assistant is collapsed', async () => {
    render(<DashboardPage currentUser={{ username: 'admin' }} onNavigate={vi.fn()} />);

    const workspace = await screen.findByRole('region', { name: '今日学习工作区' });
    fireEvent.click(screen.getByRole('button', { name: '折叠智能助教' }));

    const stylesheet = readFileSync(resolve(cwd(), 'src/index.css'), 'utf8');
    expect(workspace).toHaveAttribute('data-right-column', 'stable');
    expect(stylesheet).not.toMatch(/\.dashboard-daily__workspace:has\(\.compact-assistant\.is-collapsed\)/);
  });

  it('opens the matching training module from the learning path header', async () => {
    const onNavigate = vi.fn();
    render(<DashboardPage currentUser={{ username: 'admin' }} onNavigate={onNavigate} />);

    const moduleList = await screen.findByTestId('learning-path-training-modules');
    expect(moduleList).toBeInTheDocument();

    fireEvent.click(within(moduleList).getByRole('button', { name: /案例训练/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: { view: 'workspace', taskType: 'case_training', trackId: 'track-a' },
    });
  });

  it('uses the persisted long-term plan as stage to book navigation', async () => {
    const onNavigate = vi.fn();
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.includes('/learning-path')) {
        const isBooks = url.includes('parent_id=');
        const payload = {
          schema_version: '1.0',
          learner_id: 'user-a',
          plan_ref: { plan_id: 'LP_1', plan_version: 1, route_id: 'textbook_tcm_physician' },
          parent_id: isBooks ? 'stage-1' : null,
          parent_type: isBooks ? 'stage' : null,
          current_node_id: isBooks ? 'book-1' : 'stage-1',
          nodes: isBooks ? [{
            node_id: 'book-1', node_type: 'book', parent_id: 'stage-1', title: '《中医学基础》',
            order: 1, status: 'in_progress', progress: 0, mastery: null, has_children: true,
            child_count: 12, description: '建立基础', source_refs: [],
            navigation: { action: 'open_knowledge_atlas', route_id: 'tcm_assistant', book: '中医学基础' },
          }] : [{
            node_id: 'stage-1', node_type: 'stage', parent_id: null, title: '第一阶段',
            order: 1, status: 'in_progress', progress: 0, mastery: null, has_children: true,
            child_count: 2, description: '建立基础', source_refs: [],
            navigation: { action: 'expand', parent_id: 'stage-1' },
          }],
          offset: 0, limit: 100, total: 1, has_more: false,
        };
        return Promise.resolve({ ok: true, status: 200, text: async () => JSON.stringify(payload) });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({
          hero: { greeting: '你好', goal: '继续学习', focus: '长期主线' },
          today_tasks: [], yesterday_feedback: { metrics: [] },
        }),
      });
    }));

    render(<DashboardPage currentUser={{ username: 'admin' }} onNavigate={onNavigate} />);
    const stage = await screen.findByRole('button', { name: /进入第一阶段/ });
    fireEvent.click(stage);
    const book = await screen.findByRole('button', { name: /进入《中医学基础》/ });
    expect(screen.getByRole('button', { name: '返回阶段' })).toBeInTheDocument();
    fireEvent.click(book);

    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'knowledge',
      params: {
        view: 'atlas', route: 'tcm_assistant', lv1: '中医学基础', source: 'learning-plan',
      },
    });
  });

  it('shows an actionable empty path before a learner creates a long-term plan', async () => {
    const onNavigate = vi.fn();
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.includes('/learning-path')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          text: async () => JSON.stringify({
            schema_version: '1.0',
            learner_id: 'new-user',
            plan_ref: null,
            parent_id: null,
            parent_type: null,
            current_node_id: null,
            nodes: [],
            offset: 0,
            limit: 100,
            total: 0,
            has_more: false,
            availability: 'requires_long_term_plan',
            message: '请先完成长期学习规划，再生成阶段、教材和知识点路径。',
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({
          hero: { greeting: '你好', goal: '先完善学习目标', focus: '开始学习' },
          today_tasks: [],
          yesterday_feedback: { metrics: [] },
        }),
      });
    }));

    render(<DashboardPage currentUser={{ username: 'new-user' }} onNavigate={onNavigate} />);

    expect(await screen.findByText('请先完成长期学习规划，再生成阶段、教材和知识点路径。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '去制定长期规划' }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'assistant',
      params: { context: '请结合我的学习状态，给我制定一份长期学习规划。' },
    });
  });

  it('replaces loading copy with an honest fallback when dashboard data fails', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.endsWith('/training/onboarding/status')) {
        return Promise.resolve({ ok: true, status: 200, text: async () => JSON.stringify({ needs_survey_popup: false }) });
      }
      return Promise.resolve({ ok: false, status: 503, text: async () => JSON.stringify({ detail: '网络不可用' }) });
    }));

    render(<DashboardPage currentUser={{ username: 'admin' }} onNavigate={vi.fn()} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('网络不可用');
    expect(screen.getByRole('region', { name: '今日学习工作区' })).toBeInTheDocument();
  });

  it('keeps successful home data when the optional onboarding request rejects', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.endsWith('/training/onboarding/status')) return Promise.reject(new Error('引导接口不可用'));
      return Promise.resolve({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({
          hero: { greeting: '首页数据加载成功', goal: '继续今日计划', focus: '巩固方剂学' },
          today_tasks: [],
          status_cards: [],
          checkin_status: { checked_in_today: false, streak: 3 },
        }),
      });
    }));

    render(<DashboardPage currentUser={{ username: 'admin' }} onNavigate={vi.fn()} />);

    expect(await screen.findByRole('region', { name: '今日学习工作区' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('treats an empty successful home response as unavailable data', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.endsWith('/training/onboarding/status')) {
        return Promise.resolve({ ok: true, status: 200, text: async () => JSON.stringify({ needs_survey_popup: false }) });
      }
      return Promise.resolve({ ok: true, status: 200, text: async () => '' });
    }));

    render(<DashboardPage currentUser={{ username: 'admin' }} onNavigate={vi.fn()} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('首页数据解析失败');
    expect(screen.getByRole('region', { name: '今日学习工作区' })).toBeInTheDocument();
  });

  it('publishes the current learning target for primary knowledge navigation', async () => {
    const onKnowledgeContextChange = vi.fn();
    render(
      <DashboardPage
        currentUser={{ username: 'admin' }}
        onNavigate={vi.fn()}
        onKnowledgeContextChange={onKnowledgeContextChange}
      />,
    );

    await waitFor(() => expect(onKnowledgeContextChange).toHaveBeenCalledWith({ trackId: 'track-a' }));
  });

  it('keeps single-click preview and sends the unified Atlas intent on fishbone double-click', async () => {
    const onNavigate = vi.fn();
    render(<DashboardPage currentUser={{ username: 'admin' }} onNavigate={onNavigate} />);

    const node = await screen.findByRole('button', { name: /选择方剂学/ });
    fireEvent.click(node);
    expect(await screen.findByLabelText('方剂学学习规划')).toBeInTheDocument();

    fireEvent.doubleClick(node);
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith({
        page: 'knowledge',
        params: {
          view: 'atlas',
          trackId: 'track-a',
          membershipId: 'formula',
          source: 'dashboard',
        },
      }));
  });

  it('closes the selected learning plan when the path background is clicked', async () => {
    render(<DashboardPage currentUser={{ username: 'admin' }} onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: /选择方剂学/ }));
    expect(await screen.findByLabelText('方剂学学习规划')).toBeInTheDocument();

    fireEvent.click(document.querySelector('.learning-path-overview__stage'));
    expect(screen.queryByLabelText('方剂学学习规划')).not.toBeInTheDocument();
  });

  it('restores the legacy drilldown when the Atlas runtime switch is disabled', async () => {
    resolveKnowledgeAtlasEnabled.mockResolvedValue(false);
    render(<DashboardPage currentUser={{ username: 'admin' }} onNavigate={vi.fn()} />);

    fireEvent.doubleClick(await screen.findByRole('button', { name: /选择方剂学/ }));
    expect(await screen.findByLabelText('旧版知识树钻取')).toHaveTextContent('方剂学');
  });

  it('ignores a stale tree response after the requested track changes', async () => {
    const oldRoot = { membership_id: 'old-root', parent_membership_id: null, title: '旧轨道节点', child_count: 0 };
    const newRoot = { membership_id: 'new-root', parent_membership_id: null, title: '新轨道包装根', child_count: 1 };
    const newNode = { membership_id: 'new-node', parent_membership_id: 'new-root', title: '新轨道一级节点', child_count: 0 };
    let resolveOldRoots;
    loadExamTracks.mockResolvedValue({ items: [
      { track_id: 'track-a', title_normalized: '旧轨道' },
      { track_id: 'track-b', title_normalized: '新轨道' },
    ] });
    loadExamNodes.mockImplementation(async (requestedTrackId, parentId) => {
      if (requestedTrackId === 'track-a' && !parentId) {
        return new Promise((resolve) => { resolveOldRoots = resolve; });
      }
      if (requestedTrackId === 'track-b' && !parentId) return { items: [newRoot] };
      if (requestedTrackId === 'track-b' && parentId === 'new-root') return { items: [newNode] };
      return { items: [] };
    });

    const { rerender } = render(
      <DashboardPage currentUser={{ username: 'admin' }} navigationContext={{ trackId: 'track-a' }} onNavigate={vi.fn()} />,
    );
    await waitFor(() => expect(loadExamNodes).toHaveBeenCalledWith('track-a'));
    rerender(
      <DashboardPage currentUser={{ username: 'admin' }} navigationContext={{ trackId: 'track-b' }} onNavigate={vi.fn()} />,
    );
    expect(await screen.findByRole('button', { name: /选择新轨道一级节点/ })).toBeInTheDocument();

    resolveOldRoots({ items: [oldRoot] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByRole('button', { name: /选择旧轨道节点/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /选择新轨道一级节点/ })).toBeInTheDocument();
  });
});
