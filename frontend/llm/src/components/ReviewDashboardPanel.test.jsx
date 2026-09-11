import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ReviewDashboardPanel from './ReviewDashboardPanel';
import { loadReviewDashboard } from '../pageDataLoaders';
import { loadAllLearningHistory } from '../legacyLearningClient.js';

vi.mock('../pageDataLoaders', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, loadReviewDashboard: vi.fn() };
});

vi.mock('../legacyLearningClient.js', () => ({ loadAllLearningHistory: vi.fn() }));

describe('ReviewDashboardPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loadAllLearningHistory.mockResolvedValue({ items: [], total: 0, record_scope: 'legacy_archive', audit_status: 'not_evaluated' });
    loadReviewDashboard.mockResolvedValue({
      dashboard: {
        schema_version: '1.0',
        summary: { knowledge_point_count: 1, average_mastery: 76, due_count: 1, history_count: 1 },
        queue: {
          entries: [{
            is_due: true,
            retention_estimate: 0.72,
            memory_unit: { kp_id: 'KP_1', mastery_score: 76, next_review_at: '2026-07-22T10:00:00Z' },
          }],
        },
        mastery: [{ kp_id: 'KP_1', kp_name: '四君子汤', mastery_score: 76, mastery_confidence: 0.8, attempt_count: 2, review_stage: 'learning', last_review_at: '2026-07-22T09:00:00Z' }],
        mastery_history: [{ history_id: 'H_1', kp_id: 'KP_1', kp_name: '四君子汤', mastery_score: 76, calculated_at: '2026-07-22T09:00:00Z' }],
        review_states: [],
        review_tasks: [],
      },
      error: '',
    });
  });

  it('renders legacy mastery as read-only percentages while preserving formal summary and queue', async () => {
    loadAllLearningHistory.mockResolvedValue({
      total: 1,
      record_scope: 'legacy_archive',
      audit_status: 'not_evaluated',
      items: [{
        id: 8,
        kp_id: 'KP_LEGACY_9',
        mastery: 0.58,
        confidence: 0.8,
        review_count: 3,
        wrong_count: 1,
        last_review_at: '2026-07-23T08:30:00Z',
      }],
    });
    render(<ReviewDashboardPanel />);

    expect((await screen.findAllByText('76%')).length).toBeGreaterThan(0);
    expect(screen.getByText('已到期')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看历史掌握' }));

    expect(await screen.findByText('未命名知识点（ID：KP_LEGACY_9）')).toBeInTheDocument();
    expect(loadAllLearningHistory).toHaveBeenCalledWith('mastery', expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(screen.getByText('掌握值 58%')).toBeInTheDocument();
    expect(screen.getByText('置信度 80%')).toBeInTheDocument();
    expect(screen.getByText('复习次数 3')).toBeInTheDocument();
    expect(screen.getByText('错题次数 1')).toBeInTheDocument();
    expect(screen.getByText('来源：旧版归档')).toBeInTheDocument();
    expect(screen.getAllByText('未审核').length).toBeGreaterThan(0);
    expect(screen.getAllByText('76%').length).toBeGreaterThan(0);
  });

  it('distinguishes empty legacy mastery history from a request failure', async () => {
    render(<ReviewDashboardPanel />);
    fireEvent.click(await screen.findByRole('button', { name: '查看历史掌握' }));
    expect(await screen.findByText('暂无历史掌握记录。')).toBeInTheDocument();

    cleanup();
    loadAllLearningHistory.mockRejectedValueOnce(new Error('历史掌握读取失败'));
    render(<ReviewDashboardPanel />);
    fireEvent.click(await screen.findByRole('button', { name: '查看历史掌握' }));
    expect(await screen.findByText('历史掌握读取失败')).toBeInTheDocument();
  });

  it('orders the summary, heatmap and queue, then the detail sections vertically', async () => {
    render(<ReviewDashboardPanel />);

    expect((await screen.findAllByText('四君子汤')).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('已到期')).toBeInTheDocument();
    expect(screen.getByText('平均掌握度')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '知识点掌握情况' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '知识点掌握度' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '最近复习与掌握变化' })).toBeInTheDocument();
    expect(screen.getAllByText(/76/).length).toBeGreaterThan(0);

    const summary = screen.getByText('复习与掌握').closest('section');
    const heatmap = screen.getByRole('region', { name: '知识点掌握情况' });
    const mastery = screen.getByRole('region', { name: '知识点掌握度' });
    const history = screen.getByRole('region', { name: '最近复习与掌握变化' });
    expect(summary.compareDocumentPosition(heatmap) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(heatmap.compareDocumentPosition(mastery) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(mastery.compareDocumentPosition(history) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
