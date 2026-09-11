import React from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { cwd } from 'node:process';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import QualificationRoutePage from './QualificationRoutePage';
import { ASSISTANT_WORKFLOW_COMPLETED_EVENT } from '../assistantWorkflowEvents';
import { clearQualificationRoutePageCache } from './qualificationRoutePageCache';

vi.mock('./knowledge-atlas/knowledgeAtlasApi', () => ({ loadAtlasDetail: vi.fn() }));
import { loadAtlasDetail } from './knowledge-atlas/knowledgeAtlasApi';

vi.mock('../personalizedPathPlanner', () => ({
  buildPersonalizedLearningPath: vi.fn(() => Promise.resolve({ sessionId: 'CONV_TEST' })),
  PLANNING_STAGES: [
    { key: 'long_term', label: '正在制定长期规划' },
    { key: 'short_term', label: '正在生成短期计划' },
    { key: 'daily_task', label: '正在安排今日任务' },
  ],
}));
import { buildPersonalizedLearningPath } from '../personalizedPathPlanner';

vi.mock('./OnboardingSurveyPanel', () => ({
  __esModule: true,
  default: function MockOnboardingSurveyPanel({ onSaved }) {
    return (
      <div data-testid="mock-survey">
        <button type="button" onClick={() => onSaved?.({}, 'CUSTOM_TEST_0829：希望侧重方剂背诵。')}>
          模拟保存调研
        </button>
      </div>
    );
  },
}));

function response(payload, ok = true, status = 200) {
  return { ok, status, text: async () => JSON.stringify(payload) };
}

const routePayload = {
  schema_version: '1.0',
  nodes: [{
    node_id: 'stage-1',
    membership_id: 'stage-1',
    node_type: 'stage',
    title: '中医基础与文化语言',
    description: '建立中医基础概念。',
    order: 1,
    status: 'in_progress',
    child_count: 4,
  }],
};

function installHomeFetch(dashboardPayload = {}, options = {}) {
  const fetchMock = vi.fn((url, request = {}) => {
    const path = String(url);
    if (path.includes('/qualification-targets')) {
      return Promise.resolve(response({
        items: [options.qualificationTarget || {
          target_id: 'target-tcm',
          exam_track_id: 'track-tcm',
          official_name: '中医类别执业医师资格考试',
          exam_date: '2026-11-29T23:59:59+08:00',
        }],
      }));
    }
    if (path.endsWith('/personalization/learning-target')) {
      return Promise.resolve(response({
        target: options.currentTrackId === null
          ? null
          : {
            exam_track_id: options.currentTrackId
              || options.qualificationTarget?.exam_track_id
              || 'track-tcm',
          },
      }));
    }
    if (path.includes('/learning-path')) {
      if (options.learningPathPromise) return options.learningPathPromise;
      if (options.learningPathPayloadByParent) {
        const parentId = new URL(path, 'http://localhost').searchParams.get('parent_id') || '';
        return Promise.resolve(response(options.learningPathPayloadByParent(parentId)));
      }
      return Promise.resolve(response(options.learningPathPayload || routePayload));
    }
    if (path.includes('/learning-routes/textbook-integrated')) {
      return Promise.resolve(response(options.classicRoutePayload || {
        schema_version: '1.0',
        route: {
          route_id: 'textbook-integrated',
          stages: [{
            stage_id: 'stage-1',
            order: 1,
            name: '中西医结合基础阶段',
            objective: '建立中西医结合基础。',
            books: ['《中西医结合基础》'],
          }],
        },
        navigation: { atlas_route_id: 'textbook_14_5' },
      }));
    }
    if (path.includes('/learning-plans/current/context')) {
      const legacy = options.learningContext || {
        long_term_plan: { content: '【最终目标】通过中医执业医师资格考试。' },
        short_term_plan: { content: '【本周安排】完成中医基础理论复习。' },
      };
      return Promise.resolve(response({
        long_term_plan: legacy.long_term_plan || null,
        short_term_plan: legacy.short_term_plan || null,
      }));
    }
    if (path.includes('/learning-context')) {
      return Promise.resolve(response(options.learningContext || {
        long_term_plan: { content: '【最终目标】通过中医执业医师资格考试。' },
        short_term_plan: { content: '【本周安排】完成中医基础理论复习。' },
      }));
    }
    if (path.endsWith('/checkin') && request.method === 'POST') {
      return Promise.resolve(response({
        message: '今日签到成功',
        status: {
          checked_in_today: true,
          streak: 4,
          total_checkins: 8,
          calendar_days: [],
        },
      }));
    }
    if (path.includes('/dashboard/home')) {
      return Promise.resolve(response(dashboardPayload, options.dashboardOk ?? true, options.dashboardStatus || 200));
    }
    return Promise.resolve(response({}));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('QualificationRoutePage', () => {
  beforeEach(() => {
    clearQualificationRoutePageCache();
    loadAtlasDetail.mockReset();
    buildPersonalizedLearningPath.mockReset();
    buildPersonalizedLearningPath.mockResolvedValue({ sessionId: 'CONV_TEST' });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('keeps the history summary outside the horizontal route canvas and refreshes saved tasks', async () => {
    const dashboard = {
      current_learning_task: {
        task_id: 'TASK_OLD', title: '旧绪论任务',
        items: [{ task_item_id: 'ITEM_OLD', title: '旧绪论任务', estimated_minutes: 20 }],
      },
    };
    const fetchMock = installHomeFetch(dashboard);
    const mounted = render(<QualificationRoutePage currentUser={{ id: 'refresh-user' }} />);
    expect((await screen.findAllByText('旧绪论任务')).length).toBeGreaterThan(0);
    expect(document.querySelector('.home-portal__continuity-summary')).toBeNull();
    expect(screen.queryByLabelText('已有学习经历')).not.toBeInTheDocument();

    dashboard.current_learning_task = {
      task_id: 'TASK_NEW', title: '脾主运化新任务',
      items: [{ task_item_id: 'ITEM_NEW', title: '脾主运化新任务', estimated_minutes: 20 }],
    };
    fireEvent(window, new CustomEvent(ASSISTANT_WORKFLOW_COMPLETED_EVENT));
    expect((await screen.findAllByText('脾主运化新任务')).length).toBeGreaterThan(0);
    expect(screen.queryByText('旧绪论任务')).not.toBeInTheDocument();
    mounted.unmount();
    const count = fetchMock.mock.calls.length;
    fireEvent(window, new CustomEvent(ASSISTANT_WORKFLOW_COMPLETED_EVENT));
    expect(fetchMock).toHaveBeenCalledTimes(count);
  });

  it('asks for an exam selection instead of treating the first catalog item as current', async () => {
    installHomeFetch({}, { currentTrackId: null });

    render(<QualificationRoutePage currentUser={{ id: 'user-no-target' }} />);

    expect(await screen.findByRole('heading', { name: '请先选择资格考试' })).toBeInTheDocument();
    expect(screen.queryByLabelText('中医类别执业医师资格考试学习路径')).not.toBeInTheDocument();
  });

  it('keeps the welcome and route frames mounted while their data loads', async () => {
    installHomeFetch({});
    render(<QualificationRoutePage currentUser={{ display_name: '林同学' }} onNavigate={vi.fn()} />);

    const pageLoader = screen.getByRole('status', { name: '正在加载学习路径' });
    expect(pageLoader).toBeInTheDocument();
    expect(pageLoader).toHaveTextContent('');
    expect(pageLoader.querySelector('.page-loading-spinner__ring')).toBeInTheDocument();
    const heroFrame = document.querySelector('.home-portal__hero');
    const heroPrimary = document.querySelector('.home-portal__hero-primary');
    const routeFrame = screen.getByLabelText('当前考证学习路径');
    const planRail = screen.getByLabelText('今日学习计划');
    const calendarFrame = screen.getByLabelText('学习日历');
    const todayFrame = screen.getByLabelText('今日任务');
    expect(heroPrimary).toHaveAttribute('data-content-ready', 'false');
    expect(routeFrame).toHaveAttribute('data-content-ready', 'false');
    expect(planRail).toHaveAttribute('data-content-ready', 'false');
    expect(calendarFrame).toBeInTheDocument();
    expect(todayFrame).toBeInTheDocument();
    expect(within(routeFrame).getByRole('heading', { name: '当前考证' })).toBeInTheDocument();
    expect(within(routeFrame).queryByText('正在读取学习路径…')).not.toBeInTheDocument();

    expect((await within(routeFrame).findAllByText('中医基础与文化语言')).length).toBeGreaterThan(0);
    await waitFor(() => expect(heroPrimary).toHaveAttribute('data-content-ready', 'true'));
    expect(screen.queryByRole('status', { name: '正在加载学习路径' })).not.toBeInTheDocument();
    expect(routeFrame).toHaveAttribute('data-content-ready', 'true');
    expect(planRail).toHaveAttribute('data-content-ready', 'true');
    await waitFor(
      () => expect(document.querySelector('.home-portal__hero-caret')).toBeInTheDocument(),
      { timeout: 900 },
    );
    await waitFor(
      () => expect(document.querySelector('.home-portal__hero-caret')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );
    expect(screen.getByRole('heading', { name: /今天继续学习中医基础与文化语言/ })).toHaveTextContent('中医基础与文化语言');
    expect(document.querySelector('.home-portal__hero')).toBe(heroFrame);
    expect(screen.getByLabelText('中医类别执业医师资格考试学习路径')).toBe(routeFrame);

    const stylesheet = readFileSync(resolve(cwd(), 'src/index.css'), 'utf8');
    expect(stylesheet).toMatch(/@media \(min-width: 981px\) \{[\s\S]*?\.app-shell__main\[data-page="learning-path"\] \{[\s\S]*?height:\s*100%;/);
    expect(stylesheet).toMatch(/@media \(min-width: 981px\) \{[\s\S]*?\.home-portal__hero \{[\s\S]*?height:\s*auto;/);
    expect(stylesheet).toMatch(/data-page="learning-path"[^}]+\.home-portal__route\s*\{[^}]*contain:\s*layout paint;/s);
    expect(stylesheet).toMatch(/data-page="learning-path"[^}]+\.learning-path-orbit__legend\s*\{[^}]*margin:\s*-3px 18px 8px;/s);
    expect(stylesheet).toMatch(/home-portal__plan-rail \.home-study-calendar > \*[\s\S]*transition:\s*opacity 480ms ease;/);
    expect(stylesheet).toMatch(/\.learning-path-page__loading\s*\{[^}]*z-index:\s*10;/s);
  });

  it('waits for the qualification target before showing the exam countdown', async () => {
    installHomeFetch({});
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    expect(screen.queryByText(/距离.+还有 \d+ 天/)).not.toBeInTheDocument();
    const countdownText = await screen.findByText(/距离中医类别执业医师资格考试还有 \d+ 天/);
    await waitFor(() => expect(countdownText).toBeVisible());
  });

  it('restores a complete cached page immediately without showing the loading cover', async () => {
    installHomeFetch({
      current_learning_task: {
        task_id: 'TASK_CACHED',
        title: '缓存中的今日任务',
        items: [{ task_item_id: 'ITEM_CACHED', title: '缓存中的今日任务', estimated_minutes: 20 }],
      },
    });
    const firstRender = render(
      <QualificationRoutePage currentUser={{ username: 'cache-user' }} onNavigate={vi.fn()} />,
    );

    const firstRoute = await screen.findByLabelText('中医类别执业医师资格考试学习路径');
    expect((await within(firstRoute).findAllByText('中医基础与文化语言')).length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getByLabelText('今日学习计划')).toHaveAttribute('data-content-ready', 'true'));
    expect(screen.getByText('缓存中的今日任务')).toBeVisible();
    firstRender.unmount();

    const pendingRefresh = vi.fn(() => new Promise(() => {}));
    vi.stubGlobal('fetch', pendingRefresh);
    render(<QualificationRoutePage currentUser={{ username: 'cache-user' }} onNavigate={vi.fn()} />);

    expect(screen.queryByRole('status', { name: '正在加载学习路径' })).not.toBeInTheDocument();
    expect(document.querySelector('.home-portal')).toHaveAttribute('data-page-revealed', 'true');
    expect(screen.getByLabelText('今日学习计划')).toHaveAttribute('data-content-ready', 'true');
    expect(screen.getByText('缓存中的今日任务')).toBeVisible();
    expect(screen.getByLabelText('中医类别执业医师资格考试学习路径')).toHaveAttribute('data-content-ready', 'true');
    expect(pendingRefresh).toHaveBeenCalled();
  });

  it('keeps asynchronous hero notices fully visible without ellipsis clipping', async () => {
    installHomeFetch({ announcements: ['这是一条需要完整显示、不能被欢迎框裁切的学习安排提示。'] });
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    const notice = await screen.findByText('这是一条需要完整显示、不能被欢迎框裁切的学习安排提示。');
    expect(notice).toHaveAttribute('role', 'status');
    const stylesheet = readFileSync(resolve(cwd(), 'src/index.css'), 'utf8');
    const noticeRule = stylesheet.match(/data-page="learning-path"[^}]+\.home-portal__hero-notices \.home-portal__notice\s*\{([^}]+)\}/s)?.[1] || '';
    expect(noticeRule).toContain('overflow: visible;');
    expect(noticeRule).toContain('white-space: normal;');
    expect(noticeRule).not.toContain('text-overflow: ellipsis;');
  });

  it('keeps a long qualification title fully available in the route header', async () => {
    const officialName = '中西医结合执业助理医师资格考试（中医药基础与临床能力综合方向）';
    installHomeFetch({}, {
      qualificationTarget: {
        target_id: 'target-long-name',
        exam_track_id: 'track-long-name',
        official_name: officialName,
        textbook_route_id: 'textbook-integrated',
      },
    });
    render(<QualificationRoutePage currentUser={{ username: 'long-name-user' }} onNavigate={vi.fn()} />);

    expect(await screen.findByRole('heading', { name: officialName })).toBeInTheDocument();
    const stylesheet = readFileSync(resolve(cwd(), 'src/index.css'), 'utf8');
    const titleRule = stylesheet.match(/data-page="learning-path"[^}]+\.home-portal__route-kicker h2\s*\{([^}]+)\}/s)?.[1] || '';
    expect(titleRule).toContain('white-space: nowrap;');
    expect(titleRule).not.toContain('text-overflow: ellipsis;');
    expect(stylesheet).toMatch(/@media \(max-width: 1180px\) \{[\s\S]*?\.home-portal__route-header > div:first-child\s*\{[\s\S]*?flex:\s*1 0 100%;/);
  });

  it('renders the learning path by default with the current plan on the right', async () => {
    const learnedDate = new Date();
    learnedDate.setDate(learnedDate.getDate() - 1);
    const learnedDateKey = [
      learnedDate.getFullYear(),
      String(learnedDate.getMonth() + 1).padStart(2, '0'),
      String(learnedDate.getDate()).padStart(2, '0'),
    ].join('-');
    const learnedDateLabel = `${learnedDate.getFullYear()}年${learnedDate.getMonth() + 1}月${learnedDate.getDate()}日，已学习`;
    installHomeFetch({
      current_learning_task: {
        task_id: 'TASK_TODAY',
        title: '完成中医学基础第一章学习',
        completion_criteria: '完成章节视频并通过知识点训练',
        expected_output: '一份章节要点记录',
        scheduling: {
          policy_id: 'daily-task-multi-constraint-v1',
          summary: '系统按当前计划和到期复习安排阴阳学说，方剂配伍已顺延。',
          selected: [{ knowledge_point_name: '阴阳学说', task_kind: 'new_learning' }],
          deferred: [{ knowledge_point_name: '方剂配伍', task_kind: 'remediation' }],
        },
        learning_chapter: { book: '中医学基础', title: '第一章 中医学理论体系' },
        progress: { completed: 1, total: 2, rate: 0.5 },
        items: [
          {
            task_item_id: 'ITEM_VIDEO',
            item_type: 'video_section',
            title: '观看章节视频',
            estimated_minutes: 25,
            status: 'completed',
          },
          {
            task_item_id: 'ITEM_PRACTICE',
            item_type: 'knowledge_practice',
            title: '完成阴阳学说训练',
            kp_name: '阴阳学说',
            estimated_minutes: 15,
            status: 'pending',
          },
        ],
      },
      daily_task_timer: {
        available: true,
        refresh_due_at: '2026-07-28T08:00:00+08:00',
        server_time: '2026-07-27T08:00:00+08:00',
      },
      learning_activity: {
        trends: {
          series: [{ date: learnedDateKey, login_days: 1, focus_minutes: 12 }],
        },
        recent_activities: [],
      },
      review_queue: {
        entries: [
          {
            is_due: true,
            memory_unit: {
              memory_unit_id: 'memory-1',
              kp_id: 'KP_1',
              prompt_abstract: '四君子汤配伍',
              mastery_score: 45,
            },
            task: { review_task_id: 'review-1' },
          },
          {
            is_due: false,
            memory_unit: {
              memory_unit_id: 'memory-2',
              kp_id: 'KP_2',
              prompt_abstract: '君臣佐使原则',
            },
            task: { review_task_id: 'review-2', status: 'awaiting_attempt' },
          },
          {
            is_due: false,
            memory_unit: {
              memory_unit_id: 'memory-future',
              kp_id: 'KP_FUTURE',
              prompt_abstract: '未来复习知识点',
            },
            task: null,
          },
        ],
      },
    });

    render(<QualificationRoutePage currentUser={{ display_name: '林同学' }} onNavigate={vi.fn()} />);

    expect(await screen.findByRole('heading', { name: /(早上好|中午好|下午好|晚上好)，林同学\s+今天继续学习/ })).toBeInTheDocument();
    expect(screen.getByText(/距离中医类别执业医师资格考试还有/)).toBeInTheDocument();
    expect(screen.queryByLabelText('多智能体协作角色')).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '学习目标' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '中医类别执业医师资格考试' })).toBeInTheDocument();
    expect(screen.queryByText('阶段学习路径')).not.toBeInTheDocument();
    expect((await within(screen.getByLabelText('中医类别执业医师资格考试学习路径')).findAllByText('中医基础与文化语言')).length).toBeGreaterThan(0);
    expect(screen.getByRole('complementary', { name: '今日学习计划' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '今日任务' })).toHaveAttribute('data-task-count', '2');
    expect(screen.getByRole('complementary', { name: '学习日历' })).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: '学习日历' }).compareDocumentPosition(screen.getByRole('region', { name: '今日任务' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const calendar = screen.getByRole('complementary', { name: '学习日历' });
    expect(within(calendar).getByText('已签到')).toBeInTheDocument();
    expect(within(calendar).getByLabelText(/，今天，今日任务未完成$/)).toHaveAttribute('data-task-incomplete', 'true');
    expect(screen.queryByRole('button', { name: '学习与复习任务' })).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /学习任务/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: /复习任务/ })).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByLabelText('学习任务完成 1/2')).toBeInTheDocument();
    expect(screen.getByLabelText(learnedDateLabel)).toBeInTheDocument();
    expect(screen.getByText('完成阴阳学说训练')).toBeInTheDocument();
    expect(screen.getByText(/方剂配伍已顺延/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /添加新任务/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /复习任务/ }));
    expect(screen.getByRole('tab', { name: /复习任务/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('复习任务完成 0/2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /四君子汤配伍/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /君臣佐使原则/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /未来复习知识点/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /添加新任务/ })).not.toBeInTheDocument();

    expect(screen.getByRole('heading', { name: '中医类别执业医师资格考试' })).toBeInTheDocument();
  });

  it('shows stage cards only on request and opens the selected stage textbook path', async () => {
    const stages = [
      {
        node_id: 'stage-1',
        node_type: 'stage',
        title: '中医基础与文化语言',
        description: '建立中医基础概念。',
        order: 1,
        status: 'in_progress',
        child_count: 1,
      },
      {
        node_id: 'stage-2',
        node_type: 'stage',
        title: '经典与现代医学基础',
        description: '衔接经典理论与现代医学。',
        order: 2,
        status: 'next',
        child_count: 2,
      },
    ];
    installHomeFetch({}, {
      qualificationTarget: {
        target_id: 'target-tcm',
        exam_track_id: 'track-tcm',
        official_name: '中医类别执业医师资格考试',
        exam_date: '2026-11-29T23:59:59+08:00',
        textbook_route_id: 'textbook-integrated',
      },
      classicRoutePayload: {
        schema_version: '1.0',
        route: {
          route_id: 'textbook-integrated',
          stages: stages.map((stage, index) => ({
            stage_id: stage.node_id,
            order: stage.order,
            name: stage.title,
            objective: stage.description,
            books: index === 0 ? ['《中医学基础》'] : ['《伤寒论选读》', '《金匮要略》'],
          })),
        },
        navigation: { atlas_route_id: 'textbook_14_5' },
      },
    });

    render(<QualificationRoutePage currentUser={{ username: 'stage-selector-user' }} onNavigate={vi.fn()} />);

    expect(await screen.findByLabelText('一级知识学习路径')).toBeInTheDocument();
    expect(document.querySelector('.home-portal__route-stage-switch')).not.toBeInTheDocument();
    expect(document.querySelector('.learning-stage-card')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /查看具体学习阶段/ }));

    expect(await screen.findByRole('button', { name: '进入经典与现代医学基础阶段' })).toBeInTheDocument();
    expect(screen.queryByText('《中医学基础》')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '进入经典与现代医学基础阶段' }));

    await waitFor(() => {
      expect(document.querySelector('.home-portal__route-view-content')).toHaveAttribute('data-view', 'orbit');
    });
    expect(screen.getByRole('button', { name: /进入《伤寒论选读》/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /进入《金匮要略》/ })).toBeInTheDocument();
    expect(document.querySelector('.learning-stage-card')).not.toBeInTheDocument();
    expect(screen.getByText('经典与现代医学基础')).toBeInTheDocument();
  });

  it('opens the learner survey only from the empty personalized route entry', async () => {
    const fetchMock = installHomeFetch({}, {
      learningPathPayload: { ...routePayload, nodes: [], availability: 'requires_long_term_plan' },
    });
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    const routeSource = await screen.findByRole('group', { name: '学习路径类型' });
    const classicButton = within(routeSource).getByRole('button', { name: '经典路径' });
    const personalizedButton = within(routeSource).getByRole('button', { name: '个性化路径' });
    expect(classicButton).toHaveAttribute('aria-pressed', 'true');
    expect(personalizedButton).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(personalizedButton);
    expect(personalizedButton).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/learning-path'))).toBe(true));

    expect(screen.queryByRole('button', { name: '学情调研' })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: '去制定个性化路径' }));
    expect(screen.getByTestId('mock-survey')).toBeInTheDocument();
  });

  it('passes the survey custom requirements into the personalized path planner', async () => {
    installHomeFetch({}, {
      learningPathPayload: { ...routePayload, nodes: [], availability: 'requires_long_term_plan' },
    });
    buildPersonalizedLearningPath.mockClear();
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: '个性化路径' }));
    fireEvent.click(await screen.findByRole('button', { name: '去制定个性化路径' }));
    expect(screen.getByTestId('mock-survey')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '模拟保存调研' }));

    await waitFor(() => expect(buildPersonalizedLearningPath).toHaveBeenCalledTimes(1));
    const callArgs = buildPersonalizedLearningPath.mock.calls[0][0];
    expect(callArgs.customRequirements).toBe('CUSTOM_TEST_0829：希望侧重方剂背诵。');
  });

  it.each([
    ['waiting_human_review', '规划等待审核处理'],
    ['connection_lost', '规划连接中断，可恢复查询'],
    ['planning_failed', '学习路径规划未完成'],
  ])('distinguishes %s from a clarification request', async (code, title) => {
    installHomeFetch({}, { learningPathPayload: { ...routePayload, nodes: [], availability: 'requires_long_term_plan' } });
    buildPersonalizedLearningPath.mockRejectedValueOnce(Object.assign(new Error('执行状态说明'), {
      code, sessionId: 'CONV_RECOVER', runId: 'THREAD_RECOVER', stageIndex: 1,
    }));
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '个性化路径' }));
    fireEvent.click(await screen.findByRole('button', { name: '去制定个性化路径' }));
    fireEvent.click(screen.getByRole('button', { name: '模拟保存调研' }));
    expect(await screen.findByRole('heading', { name: title })).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: '直接补充信息' })).not.toBeInTheDocument();
    expect(screen.queryByText('规划需要你补充信息')).not.toBeInTheDocument();
    if (code === 'connection_lost') {
      fireEvent.click(screen.getByRole('button', { name: '恢复原任务查询' }));
      await waitFor(() => expect(buildPersonalizedLearningPath).toHaveBeenCalledTimes(2));
      expect(buildPersonalizedLearningPath.mock.calls[1][0].continuation).toEqual({ sessionId: 'CONV_RECOVER', runId: 'THREAD_RECOVER', stageIndex: 1, recover: true });
    }
  });

  it('answers a planning clarification inside the progress dialog and resumes the same stage', async () => {
    installHomeFetch({}, {
      learningPathPayload: { ...routePayload, nodes: [], availability: 'requires_long_term_plan' },
    });
    buildPersonalizedLearningPath
      .mockRejectedValueOnce(Object.assign(new Error('请说明计划跨度和当前学习进度'), {
        code: 'interrupted',
        visible: '请说明计划跨度和当前学习进度',
        sessionId: 'CONV_INTERRUPTED',
        runId: 'THREAD_INTERRUPTED',
        stageIndex: 1,
        interrupt: { questions: ['请说明计划跨度和当前学习进度'] },
      }))
      .mockResolvedValueOnce({ sessionId: 'CONV_INTERRUPTED' });
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: '个性化路径' }));
    fireEvent.click(await screen.findByRole('button', { name: '去制定个性化路径' }));
    fireEvent.click(screen.getByRole('button', { name: '模拟保存调研' }));

    expect(await screen.findByText('请说明计划跨度和当前学习进度')).toBeInTheDocument();
    const answer = screen.getByRole('textbox', { name: '直接补充信息' });
    fireEvent.change(answer, { target: { value: '计划六周，已学完《中医学基础》。' } });
    fireEvent.click(screen.getByRole('button', { name: '继续规划' }));

    await waitFor(() => expect(buildPersonalizedLearningPath).toHaveBeenCalledTimes(2));
    expect(buildPersonalizedLearningPath.mock.calls[1][0]).toEqual(expect.objectContaining({
      clarificationAnswer: '计划六周，已学完《中医学基础》。',
      continuation: {
        sessionId: 'CONV_INTERRUPTED',
        runId: 'THREAD_INTERRUPTED',
        stageIndex: 1,
      },
    }));
    expect(await screen.findByText('个性化学习路径已生成')).toBeInTheDocument();
  });

  it('does not reuse another qualification target personalized plan', async () => {
    installHomeFetch({}, {
      qualificationTarget: {
        target_id: 'target-pharmacist',
        exam_track_id: 'track-pharmacist',
        official_name: '执业药师职业资格考试（中药学类）',
        textbook_route_id: 'textbook-tcm-pharmacy',
      },
      learningContext: {
        long_term_plan: {
          content: '中医执业医师长期规划',
          planning_route: {
            goal_name: '中医执业医师资格考试',
            textbook_route: { route: { route_id: 'textbook-tcm-physician' } },
          },
        },
        short_term_plan: null,
      },
    });
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: '个性化路径' }));

    expect(await screen.findByTestId('personalized-path-empty')).toHaveTextContent('还没有当前考试的个性化路径');
    // The route panel flips to the empty state synchronously with the route
    // state update, while the hero progress line updates in a follow-up effect.
    // Wait for the stale progress title to be cleared before asserting.
    await waitFor(() => {
      expect(screen.queryByText('中医基础与文化语言')).not.toBeInTheDocument();
    });
  });

  it('keeps a loading state visible while the personalized route is requested', async () => {
    let resolvePersonalizedPath;
    const personalizedPath = new Promise((resolve) => { resolvePersonalizedPath = resolve; });
    installHomeFetch({}, {
      qualificationTarget: {
        target_id: 'target-tcm',
        exam_track_id: 'track-tcm',
        official_name: '中医类别执业医师资格考试',
        exam_date: '2026-11-29T23:59:59+08:00',
        textbook_route_id: 'textbook-integrated',
      },
      learningPathPromise: personalizedPath,
    });
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    expect((await screen.findAllByText('中西医结合基础阶段')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: '个性化路径' }));

    expect(await screen.findByText('正在读取个性化学习路径…')).toBeInTheDocument();

    resolvePersonalizedPath(response(routePayload));
    expect((await screen.findAllByText('中医基础与文化语言')).length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getByLabelText('中医类别执业医师资格考试学习路径')).toHaveAttribute('data-content-ready', 'true'));
  });

  it('opens the assistant with the current learning context when adding a task', async () => {
    const onNavigate = vi.fn();
    installHomeFetch({ current_learning_task: null });
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={onNavigate} />);

    const calendar = await screen.findByRole('complementary', { name: '学习日历' });
    expect(within(calendar).getByLabelText(/，今天$/)).toHaveAttribute('data-task-incomplete', 'false');
    fireEvent.click(await screen.findByRole('button', { name: /添加新任务/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'assistant',
      params: expect.objectContaining({
        newConversation: true,
        context: expect.stringContaining('为今天添加一项可执行的学习任务'),
      }),
    });
  });

  it('reloads the homepage route when the sidebar changes the qualification target', async () => {
    installHomeFetch({});
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    expect((await screen.findAllByText('中医基础与文化语言')).length).toBeGreaterThan(0);
    window.dispatchEvent(new CustomEvent('shizhen:learning-target-changed', {
      detail: {
        target_id: 'target-integrated',
        exam_track_id: 'track-integrated',
        official_name: '中西医结合执业医师资格考试',
        textbook_route_id: 'textbook-integrated',
      },
    }));

    expect((await screen.findAllByText('中西医结合基础阶段')).length).toBeGreaterThan(0);
    expect(screen.getByRole('heading', { name: '中西医结合执业医师资格考试' })).toBeInTheDocument();
  });

  it('opens reviewed knowledge points and current daily learning items', async () => {
    const onNavigate = vi.fn();
    installHomeFetch({
      current_learning_task: {
        task_id: 'TASK_1',
        title: '今日学习任务',
        items: [
          {
            task_item_id: 'ITEM_1',
            title: '完成四君子汤练习',
            kp_name: '四君子汤',
            estimated_minutes: 15,
            progress: { rate: 0.5 },
            action: {
              destination: 'workshop.practice',
              params: { taskItemId: 'ITEM_1', kpId: 'KP_1' },
            },
          },
          {
            task_item_id: 'ITEM_VIDEO',
            item_type: 'video_section',
            title: '观看补气剂章节视频',
            estimated_minutes: 20,
            resource_ref: { title: '补气剂章节精讲', url: 'https://example.test/video.mp4' },
            action: {
              destination: 'workshop.knowledge_video',
              params: {
                taskItemId: 'ITEM_VIDEO',
                video: { title: '补气剂章节精讲', url: 'https://example.test/video.mp4' },
              },
            },
          },
        ],
      },
      review_queue: {
        entries: [{
          is_due: true,
          memory_unit: {
            memory_unit_id: 'memory-1',
            kp_id: 'KP_1',
            prompt_abstract: '四君子汤配伍',
          },
          task: { review_task_id: 'review-1' },
        }],
      },
    });

    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={onNavigate} />);

    const plan = screen.getByLabelText('当前学习计划');
    fireEvent.click(await within(plan).findByRole('button', { name: /完成四君子汤练习/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'question_training',
        taskItemId: 'ITEM_1',
        kpId: 'KP_1',
        kpName: '四君子汤',
        returnTo: { page: 'qualification-route', params: {} },
      },
    });

    fireEvent.click(within(plan).getByRole('button', { name: /观看补气剂章节视频/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: expect.objectContaining({
        view: 'workspace',
        taskType: 'video_learning',
        resourceView: 'videos',
        taskItemId: 'ITEM_VIDEO',
        directVideo: { title: '补气剂章节精讲', url: 'https://example.test/video.mp4' },
        returnTo: { page: 'qualification-route', params: {} },
      }),
    });

    fireEvent.click(within(plan).getByRole('tab', { name: /复习任务/ }));
    fireEvent.click(within(plan).getByRole('button', { name: /四君子汤配伍/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'topic_training',
        kpId: 'KP_1',
        kpName: '四君子汤配伍',
        reviewTaskId: 'review-1',
        returnTo: { page: 'qualification-route', params: {} },
      },
    });

  });

  it('opens a video daily task into the textbook section instead of the video panel', async () => {
    loadAtlasDetail.mockResolvedValue({
      kp: {
        lv1: '中医学基础',
        chapter_id: 'CH_7941b46c46ff4070',
        section_id: 'SEC_37c766305b00366f',
      },
    });
    const onNavigate = vi.fn();
    installHomeFetch({
      current_learning_task: {
        task_id: 'TASK_VIDEO',
        title: '今日学习任务',
        items: [
          {
            task_item_id: 'ITEM_VIDEO_DEEP',
            item_type: 'video_section',
            title: '观看《中医学基础》绪论章节"中医学的学科属性"小节章节视频',
            estimated_minutes: 25,
            resource_ref: {
              bvid: 'BV1RR4y147uY',
              kp_id: '003264',
            },
          },
        ],
      },
    });

    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={onNavigate} />);

    const plan = screen.getByLabelText('当前学习计划');
    fireEvent.click(await within(plan).findByRole('button', { name: /中医学的学科属性/ }));

    await waitFor(() => expect(loadAtlasDetail).toHaveBeenCalledWith('003264', expect.anything()));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        view: 'textbook-chapters',
        route: 'textbook_14_5',
        lv1: '中医学基础',
        chapterId: 'CH_7941b46c46ff4070',
        sectionId: 'SEC_37c766305b00366f',
        taskItemId: 'ITEM_VIDEO_DEEP',
        returnTo: { page: 'qualification-route', params: {} },
      },
    });
  });

  it('falls back to the video panel when the textbook location cannot be resolved', async () => {
    loadAtlasDetail.mockRejectedValue(new Error('atlas unavailable'));
    const onNavigate = vi.fn();
    installHomeFetch({
      current_learning_task: {
        task_id: 'TASK_VIDEO_FALLBACK',
        title: '今日学习任务',
        items: [
          {
            task_item_id: 'ITEM_VIDEO_FB',
            item_type: 'video_section',
            title: '观看章节视频',
            estimated_minutes: 20,
            resource_ref: {
              bvid: 'BV_FALLBACK',
              kp_id: 'KP_MISSING',
            },
            action: {
              destination: 'workshop.knowledge_video',
              params: {
                taskItemId: 'ITEM_VIDEO_FB',
                video: { bvid: 'BV_FALLBACK', title: '章节视频' },
              },
            },
          },
        ],
      },
    });

    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={onNavigate} />);

    const plan = screen.getByLabelText('当前学习计划');
    fireEvent.click(await within(plan).findByRole('button', { name: /观看章节视频/ }));

    await waitFor(() => expect(loadAtlasDetail).toHaveBeenCalled());
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: expect.objectContaining({
        view: 'workspace',
        taskType: 'video_learning',
        taskItemId: 'ITEM_VIDEO_FB',
      }),
    });
  });

  it('opens a knowledge practice daily task into the matching topic training point', async () => {
    const onNavigate = vi.fn();
    installHomeFetch({
      current_learning_task: {
        task_id: 'TASK_PRACTICE',
        title: '今日学习任务',
        items: [
          {
            task_item_id: 'ITEM_KP_PRACTICE',
            item_type: 'knowledge_practice',
            title: '完成知识点 四君子汤 练习',
            kp_id: 'KP_1',
            kp_name: '四君子汤',
            estimated_minutes: 6,
            resource_ref: {},
            action: {
              destination: 'workshop.practice',
              params: {
                taskItemId: 'ITEM_KP_PRACTICE',
                kpId: 'KP_1',
                kpName: '四君子汤',
              },
            },
          },
        ],
      },
    });

    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={onNavigate} />);

    const plan = screen.getByLabelText('当前学习计划');
    fireEvent.click(await within(plan).findByRole('button', { name: /四君子汤/ }));

    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'topic_training',
        kpId: 'KP_1',
        kpName: '四君子汤',
        taskItemId: 'ITEM_KP_PRACTICE',
        returnTo: { page: 'qualification-route', params: {} },
      },
    });
    expect(loadAtlasDetail).not.toHaveBeenCalled();
  });

  it('shows the real video duration as the video task estimate', async () => {
    installHomeFetch({
      current_learning_task: {
        task_id: 'TASK_VIDEO_META',
        title: '今日学习任务',
        items: [
          {
            task_item_id: 'ITEM_VIDEO_META',
            item_type: 'video_section',
            title: '观看《中医学基础》绪论章节"中医学的学科属性"小节章节视频',
            estimated_minutes: 25,
            resource_ref: {
              bvid: 'BV1RR4y147uY',
              kp_id: '003264',
              duration_seconds: 239,
            },
          },
        ],
      },
    });

    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    const plan = screen.getByLabelText('当前学习计划');
    expect(await within(plan).findByRole('button', { name: /中医学的学科属性/ })).toHaveTextContent('4分钟');
  });

  it('shows persisted long and short planning narratives from the route header', async () => {
    installHomeFetch({}, {
      learningContext: {
        long_term_plan: {
          content: '【最终目标】## 目标契约\n最终目标是系统掌握中医执业医师。\n\n## 长期阶段路径\n| 阶段 | 阶段目标 |\n|---|---|\n| 1. 中医基础 | 建立基础概念 |',
        },
        short_term_plan: {
          content: '## 本周安排\n- 完成中医基础理论复习。',
        },
      },
    });
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: '规划详情' }));

    expect(await screen.findByRole('heading', { name: '目标契约', level: 2 })).toBeInTheDocument();
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '阶段' })).toBeInTheDocument();
    expect(screen.getByRole('listitem')).toHaveTextContent('完成中医基础理论复习。');
    const details = screen.getByRole('region', { name: '长期规划和短期规划说明' });
    const planningDocument = details.querySelector('.home-portal__planning-document');
    expect(planningDocument).toBeInTheDocument();
    expect([...planningDocument.querySelectorAll(':scope > section > h3')].map((heading) => heading.textContent)).toEqual([
      '长期规划',
      '短期规划',
    ]);
    const stylesheet = readFileSync(resolve(cwd(), 'src/index.css'), 'utf8');
    expect(stylesheet).toMatch(/\.home-portal__planning-markdown table\s*\{[^}]*table-layout:\s*fixed;/s);
    expect(stylesheet).toMatch(/\.home-portal__planning-markdown th,\s*\.home-portal__planning-markdown td\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
    fireEvent.click(screen.getByRole('button', { name: '返回' }));
    expect(await screen.findByRole('heading', { name: '中医类别执业医师资格考试' })).toBeInTheDocument();
  });

  it('records a daily check-in and keeps the new homepage usable after summary failure', async () => {
    const fetchMock = installHomeFetch(
      { detail: '首页数据暂不可用' },
      { dashboardOk: false, dashboardStatus: 503 },
    );
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('首页数据暂不可用');
  expect(screen.getByRole('button', { name: '今日签到' })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: '今日签到' }));
    expect(await screen.findByRole('button', { name: '今日已签到，连续4天' })).toBeDisabled();
    expect(screen.getByText('今日签到成功')).toHaveAttribute('role', 'status');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/checkin$/),
      expect.objectContaining({ method: 'POST' }),
    );
  });
});
