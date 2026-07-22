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
        mastery: [{ kp_id: 'KP_1', kp_name: '四君子汤', mastery_score: 76, attempt_count: 2, review_stage: 'learning' }],
        mastery_history: [{ history_id: 'H_1', kp_id: 'KP_1', kp_name: '四君子汤', mastery_score: 76, calculated_at: '2026-07-22T09:00:00Z' }],
        review_states: [],
        review_tasks: [],
      },
      error: '',
    });
  });

  it('shows queue, mastery and history with knowledge point names', async () => {
    render(<ReviewDashboardPanel />);

    expect(await screen.findAllByText('四君子汤')).toHaveLength(3);
    expect(screen.getByText('已到期')).toBeInTheDocument();
    expect(screen.getByText('平均掌握度')).toBeInTheDocument();
    expect(screen.getAllByText(/76/).length).toBeGreaterThan(0);
  });
});
