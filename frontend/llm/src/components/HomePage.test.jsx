import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import HomePage from './HomePage';

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

describe('HomePage 0726(1)', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('renders the complete target, short route, review rail, and feature entry layout', async () => {
    installHomeFetch({
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

    render(<HomePage currentUser={{ display_name: '林同学' }} onNavigate={vi.fn()} />);

    expect(await screen.findByRole('heading', {
      name: '早上好，林同学，今天继续学习中医基础与文化语言',
    })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '学习目标' })).toHaveValue('target-tcm');
    expect(screen.getByText('短期学习路径')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '复习任务' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: /四君子汤配伍/ })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /进入功能/ })).toHaveLength(4);
  });

  it('opens reviewed knowledge points and current daily learning items', async () => {
    const onNavigate = vi.fn();
    installHomeFetch({
      current_learning_task: {
        task_id: 'TASK_1',
        title: '今日学习任务',
        items: [{
          task_item_id: 'ITEM_1',
          title: '完成四君子汤练习',
          kp_name: '四君子汤',
          estimated_minutes: 15,
          progress: { rate: 0.5 },
          action: {
            destination: 'workshop.practice',
            params: { taskItemId: 'ITEM_1', kpId: 'KP_1' },
          },
        }],
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

    render(<HomePage currentUser={{ username: 'alice' }} onNavigate={onNavigate} />);

    fireEvent.click(await screen.findByRole('button', { name: /四君子汤配伍/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'question_training',
        kpId: 'KP_1',
        reviewTaskId: 'review-1',
      },
    });

    fireEvent.click(screen.getByRole('tab', { name: '学习任务' }));
    fireEvent.click(screen.getByRole('button', { name: /完成四君子汤练习/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'question_training',
        taskItemId: 'ITEM_1',
        kpId: 'KP_1',
      },
    });
  });

  it('shows persisted long and short planning narratives from the route header', async () => {
    installHomeFetch({});
    render(<HomePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: '了解详情' }));

    expect(await screen.findByText('【最终目标】通过中医执业医师资格考试。')).toBeInTheDocument();
    expect(screen.getByText('【本周安排】完成中医基础理论复习。')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '长期规划和短期规划说明' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '返回短期学习路径' }));
    expect(await screen.findByText('短期学习路径')).toBeInTheDocument();
  });

  it('routes all new homepage feature cards to executable pages', async () => {
    const onNavigate = vi.fn();
    installHomeFetch({});
    render(<HomePage currentUser={{ username: 'alice' }} onNavigate={onNavigate} />);

    fireEvent.click(await screen.findByRole('button', { name: /智能问答/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'assistant',
      params: { newConversation: true },
    });

    fireEvent.click(screen.getByRole('button', { name: /资料检索/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: {
        view: 'overview',
        libraryOnly: true,
        expandAll: true,
        hidePlan: true,
      },
    });
  });

  it('records a daily check-in and keeps the new homepage usable after summary failure', async () => {
    const fetchMock = installHomeFetch(
      { detail: '首页数据暂不可用' },
      { dashboardOk: false, dashboardStatus: 503 },
    );
    render(<HomePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('首页数据暂不可用');
    expect(screen.getByRole('button', { name: /智能问答/ })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: '今日签到' }));
    expect(await screen.findByRole('button', { name: '今日已签到，连续4天' })).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('今日签到成功');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/checkin$/),
      expect.objectContaining({ method: 'POST' }),
    );
  });
});
