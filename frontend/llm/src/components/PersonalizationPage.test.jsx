import React from 'react';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import PersonalizationPage from './PersonalizationPage';
import { fetchWithAuth } from '../utils/api';

vi.mock('../utils/api', () => ({
  API_BASE: 'http://api.test',
  MAIN_API_BASE: 'http://main-api.test/api/v1',
  fetchWithAuth: vi.fn(),
}));
vi.mock('./LearningTrendChart', () => ({ default: () => <div>trend-chart</div> }));

function responseFor(url) {
  if (url.includes('/personalization/overview')) {
    return { profile: {}, stats: { by_category: {}, by_source: {} } };
  }
  if (url.includes('/personalization/learner-profile')) {
    return { locked_fields: [], survey: {}, lock_reason: {} };
  }
  if (url.includes('/personalization/learning-trends')) return { series: [] };
  if (url.includes('/learning-context')) {
    return {
      user_profile: {
        learning_background: '零基础；非医学专业',
        learning_goal: '中医执业医师',
      },
      onboarding: {
        status: 'onboarding_completed',
        survey_answers: {
          learner_group_title: '学历教育群体',
          major_or_role: '非医学专业',
          tcm_foundation: '零基础',
          target_exam_or_course: '中医执业医师',
          daily_available_minutes: 45,
          preferred_time_slot: '晚间',
          resource_preference: ['知识卡片', '分阶测试题'],
        },
      },
      long_term_plan: { planning_route: { goal_name: '中医执业医师资格考试' } },
    };
  }
  return [];
}

describe('PersonalizationPage single-task views', () => {
  beforeEach(() => {
    fetchWithAuth.mockReset();
    fetchWithAuth.mockImplementation(async (url) => ({
      ok: true,
      json: async () => responseFor(url),
    }));
  });

  it('renders profile controls without the memory workspace', () => {
    render(<PersonalizationPage embedded view="profile" />);

    expect(screen.getByRole('heading', { name: '学习者画像' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '学习记忆数据库' })).not.toBeInTheDocument();
  });

  it('shows the goal and background confirmed by the memory agent', async () => {
    render(<PersonalizationPage embedded view="profile" />);

    expect(await screen.findByLabelText('学习目标（记忆智能体已确认）')).toHaveValue('中医执业医师');
    expect(screen.getByLabelText('学习基础（记忆智能体已确认）')).toHaveValue('零基础；非医学专业');
    expect(screen.getByRole('heading', { name: '注册学情调查' })).toBeInTheDocument();
    expect(screen.getByText('学历教育群体')).toBeInTheDocument();
    expect(screen.getByText('知识卡片、分阶测试题')).toBeInTheDocument();
  });

  it('renders the memory workspace without profile controls', () => {
    render(<PersonalizationPage embedded view="memory" />);

    expect(screen.getByRole('heading', { name: '学习记忆数据库' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '学习者画像' })).not.toBeInTheDocument();
  });

  it('renders profile and memory together in the unified workspace', () => {
    render(<PersonalizationPage embedded view="unified" />);

    expect(screen.getByRole('heading', { name: '学习者画像' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '学习记忆数据库' })).toBeInTheDocument();
  });
});
