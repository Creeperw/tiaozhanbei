import React from 'react';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ReviewDashboardPanel from './ReviewDashboardPanel';
import { loadReviewDashboard } from '../pageDataLoaders';

vi.mock('../pageDataLoaders', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, loadReviewDashboard: vi.fn() };
});

describe('ReviewDashboardPanel', () => {
  beforeEach(() => {
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
