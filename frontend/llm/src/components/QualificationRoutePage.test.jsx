import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import QualificationRoutePage from './QualificationRoutePage';

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
        items: [{
          target_id: 'target-tcm',
          exam_track_id: 'track-tcm',
          official_name: '中医类别执业医师资格考试',
          exam_date: '2026-11-29T23:59:59+08:00',
        }],
      }));
    }
    if (path.endsWith('/personalization/learning-target')) {
      return Promise.resolve(response({
        target: { exam_track_id: 'track-tcm' },
      }));
    }
    if (path.includes('/learning-path')) {
      return Promise.resolve(response(routePayload));
    }
    if (path.includes('/learning-routes/textbook-integrated')) {
      return Promise.resolve(response({
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
  afterEach(() => vi.unstubAllGlobals());

  it('renders the learning path by default with the current plan on the right', async () => {
    installHomeFetch({
      current_learning_task: {
        task_id: 'TASK_TODAY',
        title: '完成中医学基础第一章学习',
        completion_criteria: '完成章节视频并通过知识点训练',
        expected_output: '一份章节要点记录',
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
          series: [{ date: '2026-07-23', login_days: 1, focus_minutes: 12 }],
        },
        recent_activities: [],
      },
      review_queue: {
        entries: [{
          is_due: true,
          memory_unit: {
            memory_unit_id: 'memory-1',
            kp_id: 'KP_1',
            prompt_abstract: '四君子汤配伍',
            mastery_score: 45,
          },
          task: { review_task_id: 'review-1' },
        }],
      },
    });

    render(<QualificationRoutePage currentUser={{ display_name: '林同学' }} onNavigate={vi.fn()} />);

    expect(await screen.findByRole('heading', { name: /早上好，林同学，今天继续学习/ })).toBeInTheDocument();
    expect(screen.getByText(/距离中医类别执业医师资格考试还有/)).toBeInTheDocument();
    expect(screen.queryByLabelText('多智能体协作角色')).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '学习目标' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '中医类别执业医师资格考试学习路径规划' })).toBeInTheDocument();
    expect(screen.getByText('阶段学习路径')).toBeInTheDocument();
    expect(await screen.findByText('中医基础与文化语言')).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: '今日学习计划' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '今日任务' })).toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: '学习日历' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '学习与复习任务' })).toBeInTheDocument();
    expect(screen.getByLabelText('今日任务完成 1/2')).toBeInTheDocument();
    expect(screen.getByLabelText('2026年7月23日，已学习')).toBeInTheDocument();
    expect(screen.getByText('完成阴阳学说训练')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /添加新任务/ })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '复习任务' })).not.toBeInTheDocument();

    const pathViewButton = screen.getByRole('button', { name: '返回学习路径' });
    const cardViewButton = screen.getByRole('button', { name: '查看阶段卡片' });
    expect(pathViewButton).toHaveAttribute('aria-pressed', 'true');
    expect(cardViewButton).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(cardViewButton);
    expect(cardViewButton).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: '返回学习路径' }));
    expect(pathViewButton).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('heading', { name: '中医类别执业医师资格考试学习路径规划' })).toBeInTheDocument();
  });

  it('opens the assistant with the current learning context when adding a task', async () => {
    const onNavigate = vi.fn();
    installHomeFetch({ current_learning_task: null });
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={onNavigate} />);

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

    expect(await screen.findByText('中医基础与文化语言')).toBeInTheDocument();
    window.dispatchEvent(new CustomEvent('shizhen:learning-target-changed', {
      detail: {
        target_id: 'target-integrated',
        exam_track_id: 'track-integrated',
        official_name: '中西医结合执业医师资格考试',
        textbook_route_id: 'textbook-integrated',
      },
    }));

    expect(await screen.findByText('中西医结合基础阶段')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '中西医结合执业医师资格考试学习路径规划' })).toBeInTheDocument();
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
        taskType: 'knowledge_cards',
        resourceView: 'videos',
        taskItemId: 'ITEM_VIDEO',
        directVideo: { title: '补气剂章节精讲', url: 'https://example.test/video.mp4' },
        returnTo: { page: 'qualification-route', params: {} },
      }),
    });

  });

  it('shows persisted long and short planning narratives from the route header', async () => {
    installHomeFetch({});
    render(<QualificationRoutePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: '了解详情' }));

    expect(await screen.findByText('【最终目标】通过中医执业医师资格考试。')).toBeInTheDocument();
    expect(screen.getByText('【本周安排】完成中医基础理论复习。')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '长期规划和短期规划说明' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '返回学习路径' }));
    expect(await screen.findByRole('heading', { name: '中医类别执业医师资格考试学习路径规划' })).toBeInTheDocument();
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
    expect(screen.getByRole('status')).toHaveTextContent('今日签到成功');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/checkin$/),
      expect.objectContaining({ method: 'POST' }),
    );
  });
});
