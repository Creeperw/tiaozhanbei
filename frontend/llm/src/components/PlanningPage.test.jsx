import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import PlanningPage from './PlanningPage';

vi.mock('../utils/api', () => ({
  fetchJsonWithAuthFallback: vi.fn(),
}));

vi.mock('../pageDataLoaders.js', () => ({
  emptyPlan: {
    plan_summary: {},
    weekly_plan: { evidence: [] },
    daily_tasks: [],
    long_term_plan_content: '',
    long_term_plan_stages: [],
    short_term_plan_content: '',
  },
  loadPlanningData: vi.fn(async () => ({
    error: '',
    source: '/api/v1/learning-context',
    plan: {
      plan_summary: { goal: '传统医学师承考核' },
      weekly_plan: { evidence: [] },
      daily_tasks: [],
      long_term_plan_content: '【最终目标】通过考核。',
      short_term_plan_content: '',
      long_term_plan_stages: [
        {
          stage: 1,
          book: ['《中医学基础》'],
          goal: '建立中医基础。',
        },
      ],
    },
  })),
}));

describe('PlanningPage structured long-term route', () => {
  it('renders stages, books, and goals from the persisted plan', async () => {
    render(<PlanningPage />);

    expect(await screen.findByRole('region', { name: '长期规划阶段路线' })).toBeInTheDocument();
    expect(screen.getByText('第 1 阶段')).toBeInTheDocument();
    expect(screen.getByText('《中医学基础》')).toBeInTheDocument();
    expect(screen.getByText('建立中医基础。')).toBeInTheDocument();
  });
});
