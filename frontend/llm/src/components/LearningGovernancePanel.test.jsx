import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import LearningGovernancePanel from './LearningGovernancePanel';
import { fetchWithAuth } from '../utils/api';

vi.mock('../utils/api', () => ({
  API_BASE: 'http://api.test',
  fetchWithAuth: vi.fn(),
  readJsonResponse: vi.fn((response, fallback) => Promise.resolve(response?.payload ?? fallback)),
}));

const notificationSettings = {
  in_app_enabled: true,
  categories: { review_due: true, intervention: true, plan_review: true },
  digest_frequency: 'realtime',
  quiet_hours: { start: '22:00', end: '07:00' },
};

describe('LearningGovernancePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchWithAuth.mockImplementation((url) => {
      if (url.includes('/v1/notifications?')) return Promise.resolve({ ok: true, payload: { unread_count: 2, items: [] } });
      if (url.includes('/v1/interventions?')) return Promise.resolve({ ok: true, payload: { items: [] } });
      if (url.includes('/v1/plan-reviews?')) return Promise.resolve({ ok: true, payload: { items: [] } });
      if (url.endsWith('/personalization/learner-settings')) return Promise.resolve({ ok: true, payload: { settings: { notification_preferences: notificationSettings } } });
      return Promise.resolve({ ok: false, payload: { detail: 'Unexpected request' } });
    });
  });

  it('uses a notification settings button in place of the automatic governance label', async () => {
    render(<LearningGovernancePanel />);

    expect(screen.getByRole('button', { name: '通知设置' })).toBeVisible();
    expect(screen.queryByText('自动治理中心')).not.toBeInTheDocument();
    expect(await screen.findByText('需要你处理的学习信号')).toBeInTheDocument();
  });

  it('opens and closes the notification preferences dialog', async () => {
    render(<LearningGovernancePanel />);

    fireEvent.click(screen.getByRole('button', { name: '通知设置' }));
    const dialog = await screen.findByRole('dialog', { name: '通知设置' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByRole('heading', { name: '通知与主动提醒' })).toBeVisible();
    expect(screen.getByLabelText('勿扰开始')).toHaveValue('22:00');

    fireEvent.click(screen.getByRole('button', { name: '关闭通知设置' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '通知设置' })).not.toBeInTheDocument());
  });

  it('shows an applied notice after accepting an intervention that lands', async () => {
    fetchWithAuth.mockImplementation((url) => {
      if (url.includes('/v1/notifications?')) return Promise.resolve({ ok: true, payload: { unread_count: 2, items: [] } });
      if (url.includes('/v1/interventions/9/feedback')) return Promise.resolve({
        ok: true,
        payload: {
          intervention_id: 9,
          applied: { applied: true, title: '错题复盘：中医诊断学·舌诊、四君子汤', reason: '', summary: '建议今日安排错题复盘' },
        },
      });
      if (url.includes('/v1/interventions?')) return Promise.resolve({ ok: true, payload: { items: [{ intervention_id: 9, action: '安排错题复盘', t_stage: 'T5', reason: '存在9个到期复习', lifecycle_status: 'delivered', actionable: true }] } });
      if (url.includes('/v1/plan-reviews?')) return Promise.resolve({ ok: true, payload: { items: [] } });
      if (url.endsWith('/personalization/learner-settings')) return Promise.resolve({ ok: true, payload: { settings: { notification_preferences: notificationSettings } } });
      return Promise.resolve({ ok: false, payload: { detail: 'Unexpected request' } });
    });
    render(<LearningGovernancePanel />);

    fireEvent.click(await screen.findByRole('button', { name: '接受建议' }));
    await waitFor(() => expect(fetchWithAuth).toHaveBeenCalledWith(
      expect.stringContaining('/v1/interventions/9/feedback'),
      expect.objectContaining({ method: 'POST' }),
    ));
    expect(await screen.findByText(/已加入今日任务/)).toBeVisible();
  });

  it('shows the reason when an accepted intervention cannot land', async () => {
    fetchWithAuth.mockImplementation((url) => {
      if (url.includes('/v1/notifications?')) return Promise.resolve({ ok: true, payload: { unread_count: 2, items: [] } });
      if (url.includes('/v1/interventions/9/feedback')) return Promise.resolve({
        ok: true,
        payload: { intervention_id: 9, feedback_committed: false, applied: { applied: false, retryable: true, reason: '当前没有进行中的每日任务，无法安排。', summary: '', title: '' } },
      });
      if (url.includes('/v1/interventions?')) return Promise.resolve({ ok: true, payload: { items: [{ intervention_id: 9, action: '安排错题复盘', t_stage: 'T5', reason: '存在9个到期复习', lifecycle_status: 'delivered', actionable: true }] } });
      if (url.includes('/v1/plan-reviews?')) return Promise.resolve({ ok: true, payload: { items: [] } });
      if (url.endsWith('/personalization/learner-settings')) return Promise.resolve({ ok: true, payload: { settings: { notification_preferences: notificationSettings } } });
      return Promise.resolve({ ok: false, payload: { detail: 'Unexpected request' } });
    });
    render(<LearningGovernancePanel />);

    fireEvent.click(await screen.findByRole('button', { name: '接受建议' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('当前没有进行中的每日任务，无法安排。');
    expect(screen.getByRole('button', { name: '接受建议' })).toBeEnabled();
  });

  it('does not show accept for an intervention without an executor', async () => {
    fetchWithAuth.mockImplementation((url) => {
      if (url.includes('/v1/notifications?')) return Promise.resolve({ ok: true, payload: { unread_count: 0, items: [] } });
      if (url.includes('/v1/interventions?')) return Promise.resolve({ ok: true, payload: { items: [{ intervention_id: 8, action: '缩小单次任务范围', t_stage: 'T1', reason: '当前任务耗时偏高', lifecycle_status: 'delivered', actionable: false }] } });
      if (url.includes('/v1/plan-reviews?')) return Promise.resolve({ ok: true, payload: { items: [] } });
      return Promise.resolve({ ok: false, payload: { detail: 'Unexpected request' } });
    });

    render(<LearningGovernancePanel />);

    expect(await screen.findByText('当前任务耗时偏高')).toBeVisible();
    expect(screen.queryByRole('button', { name: '接受建议' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '稍后处理' })).toBeEnabled();
  });

  it('shows the replan notice after accepting a plan review', async () => {
    fetchWithAuth.mockImplementation((url) => {
      if (url.includes('/v1/notifications?')) return Promise.resolve({ ok: true, payload: { unread_count: 2, items: [] } });
      if (url.includes('/v1/interventions?')) return Promise.resolve({ ok: true, payload: { items: [] } });
      if (url.includes('/v1/plan-reviews/REVIEW_9/decision')) return Promise.resolve({
        ok: true,
        payload: { review_id: 'REVIEW_9', applied: { applied: true, replan_started: true, summary: '已启动多智能体重规划，完成后会通过通知提醒你。', reason: '' } },
      });
      if (url.includes('/v1/plan-reviews?')) return Promise.resolve({ ok: true, payload: { items: [{ review_id: 'REVIEW_9', outcome: 'short_replan_suggested', summary: '已连续3天低完成', period_key: '2026-W33', evidence: ['任务完成率 10%'], status: 'proposal_pending', execution_status: 'queued', proposal: { target_layer: 'short_term', operation: 'replan_for_low_completion' } }] } });
      if (url.endsWith('/personalization/learner-settings')) return Promise.resolve({ ok: true, payload: { settings: { notification_preferences: notificationSettings } } });
      return Promise.resolve({ ok: false, payload: { detail: 'Unexpected request' } });
    });
    render(<LearningGovernancePanel />);

    fireEvent.click(await screen.findByRole('button', { name: '接受调整' }));
    expect(await screen.findByText(/重规划已排队/)).toBeVisible();
  });

  it('shows the reduce-load notice after accepting a daily adjustment', async () => {
    fetchWithAuth.mockImplementation((url) => {
      if (url.includes('/v1/notifications?')) return Promise.resolve({ ok: true, payload: { unread_count: 2, items: [] } });
      if (url.includes('/v1/interventions?')) return Promise.resolve({ ok: true, payload: { items: [] } });
      if (url.includes('/v1/plan-reviews/REVIEW_8/decision')) return Promise.resolve({
        ok: true,
        payload: { review_id: 'REVIEW_8', applied: { applied: true, summary: '今日配套题已从10道减至7道，先完成核心内容。', reason: '' } },
      });
      if (url.includes('/v1/plan-reviews?')) return Promise.resolve({ ok: true, payload: { items: [{ review_id: 'REVIEW_8', outcome: 'daily_adjustment_suggested', summary: '近期任务完成率偏低', period_key: '2026-W33', evidence: ['任务完成率 12%'], status: 'proposal_pending' }] } });
      if (url.endsWith('/personalization/learner-settings')) return Promise.resolve({ ok: true, payload: { settings: { notification_preferences: notificationSettings } } });
      return Promise.resolve({ ok: false, payload: { detail: 'Unexpected request' } });
    });
    render(<LearningGovernancePanel />);

    fireEvent.click(await screen.findByRole('button', { name: '接受调整' }));
    expect(await screen.findByText(/已从10道减至7道/)).toBeVisible();
  });

  it('does not offer async retry for a daily adjustment with stale failure metadata', async () => {
    fetchWithAuth.mockImplementation((url) => {
      if (url.includes('/v1/notifications?')) return Promise.resolve({ ok: true, payload: { unread_count: 0, items: [] } });
      if (url.includes('/v1/interventions?')) return Promise.resolve({ ok: true, payload: { items: [] } });
      if (url.includes('/v1/plan-reviews?')) return Promise.resolve({
        ok: true,
        payload: {
          items: [{
            review_id: 'REVIEW_DAILY_STALE',
            outcome: 'daily_adjustment_suggested',
            summary: '近期任务完成率偏低',
            period_key: '2026-W35',
            evidence: ['任务完成率 12%'],
            status: 'accepted',
            execution_status: 'failed',
            execution: { error: 'stale async failure' },
            proposal: { target_layer: 'daily_task', operation: 'reduce_load' },
          }],
        },
      });
      return Promise.resolve({ ok: false, payload: { detail: 'Unexpected request' } });
    });
    render(<LearningGovernancePanel />);

    expect(await screen.findByText('近期任务完成率偏低')).toBeVisible();
    expect(screen.queryByText('stale async failure')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重试调整' })).not.toBeInTheDocument();
    expect(screen.queryByText('执行失败')).not.toBeInTheDocument();
  });

  it('does not poll for a daily adjustment with stale queued metadata', async () => {
    vi.useFakeTimers();
    try {
      fetchWithAuth.mockImplementation((url) => {
        if (url.includes('/v1/notifications?')) return Promise.resolve({ ok: true, payload: { unread_count: 0, items: [] } });
        if (url.includes('/v1/interventions?')) return Promise.resolve({ ok: true, payload: { items: [] } });
        if (url.includes('/v1/plan-reviews?')) return Promise.resolve({
          ok: true,
          payload: {
            items: [{
              review_id: 'REVIEW_DAILY_QUEUED',
              outcome: 'daily_adjustment_suggested',
              summary: '今日任务已经同步处理',
              period_key: '2026-W35',
              evidence: [],
              status: 'accepted',
              execution_status: 'queued',
              proposal: { target_layer: 'daily_task', operation: 'reduce_load' },
            }],
          },
        });
        return Promise.resolve({ ok: false, payload: { detail: 'Unexpected request' } });
      });
      render(<LearningGovernancePanel />);

      await vi.waitFor(() => expect(screen.getByText('今日任务已经同步处理')).toBeVisible());
      const reviewCalls = () => fetchWithAuth.mock.calls.filter(([url]) => url.includes('/v1/plan-reviews?')).length;
      expect(reviewCalls()).toBe(1);
      await vi.advanceTimersByTimeAsync(5000);
      expect(reviewCalls()).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('offers retry only for a failed short-term cascade', async () => {
    fetchWithAuth.mockImplementation((url) => {
      if (url.includes('/v1/notifications?')) return Promise.resolve({ ok: true, payload: { unread_count: 0, items: [] } });
      if (url.includes('/v1/interventions?')) return Promise.resolve({ ok: true, payload: { items: [] } });
      if (url.includes('/v1/plan-reviews?')) return Promise.resolve({
        ok: true,
        payload: {
          items: [{
            review_id: 'REVIEW_SHORT_FAILED',
            outcome: 'short_replan_suggested',
            summary: '连续低完成，需调整短期计划',
            period_key: '2026-W35',
            evidence: ['连续 3 天低于 50%'],
            status: 'accepted',
            execution_status: 'failed',
            execution: { error: 'isolated coordinator failure' },
            proposal: { target_layer: 'short_term', operation: 'replan_for_low_completion' },
          }],
        },
      });
      return Promise.resolve({ ok: false, payload: { detail: 'Unexpected request' } });
    });
    render(<LearningGovernancePanel />);

    expect(await screen.findByText('isolated coordinator failure')).toBeVisible();
    expect(screen.getByRole('button', { name: '重试调整' })).toBeEnabled();
    expect(screen.getByText('执行失败')).toBeVisible();
  });
});
