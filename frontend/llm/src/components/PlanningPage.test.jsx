import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import PlanningPage from './PlanningPage';

vi.mock('../utils/api', () => ({
  fetchJsonWithAuthFallback: vi.fn(),
  fetchWithAuth: vi.fn(async () => ({ ok: true, text: async () => '{"items":[]}' })),
  readJsonResponse: vi.fn(async (response, fallback) => JSON.parse(await response.text()) || fallback),
  API_BASE: '/api',
}));

vi.mock('../pageDataLoaders.js', () => ({
  emptyPlan: {
    plan_summary: {},
    weekly_plan: { evidence: [] },
    daily_tasks: [],
    long_term_plan_content: '',
    long_term_plan_stages: [],
    short_term_plan_content: '',
    daily_task_timer: null,
    path_candidates: { schema_version: '1.0', scope: '', items: [] },
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
      daily_task_timer: null,
      path_candidates: {
        schema_version: '1.0',
        scope: 'daily_task',
        items: [
          {
            candidate_id: 'PATH_AVAILABLE',
            eligible: true,
            stage: { name: '基础阶段' },
            knowledge_points: [{ kp_id: 'KP_1', name: '四君子汤' }],
            estimated_minutes: 20,
            score: 0.8,
            score_components: {
              mastery_fit: { available: true, value: 0.75 },
              repetition_penalty: { available: false, value: null, unavailable_reason: 'no_recent_attempts' },
            },
            hard_constraint_results: [],
          },
          {
            candidate_id: 'PATH_BLOCKED',
            eligible: false,
            stage: { name: '进阶阶段' },
            books: [{ name: '《中医诊断学》' }],
            knowledge_points: [],
            estimated_minutes: 30,
            score: 0.9,
            score_components: {},
            blocked_reasons: ['prerequisite_not_satisfied:中医基础'],
            hard_constraint_results: [{ key: 'prerequisite_satisfied', passed: false, reason: 'prerequisite_not_satisfied:中医基础' }],
          },
        ],
      },
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
    expect(screen.queryByText('本周计划卡')).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: '路径候选验证' })).toBeInTheDocument();
    expect(screen.getByText('可用候选')).toBeInTheDocument();
    expect(screen.getByText(/四君子汤/)).toBeInTheDocument();
    expect(screen.getByText('被阻断候选')).toBeInTheDocument();
    expect(screen.getAllByText('prerequisite_not_satisfied:中医基础').length).toBeGreaterThan(0);
    expect(screen.getByText('未纳入')).toBeInTheDocument();
    expect(screen.queryByText('KP_1')).not.toBeInTheDocument();
  });
});
