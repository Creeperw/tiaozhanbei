import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';

import PersonalizationPage from './PersonalizationPage';
import { fetchWithAuth } from '../utils/api';

vi.mock('../utils/api', () => ({
  API_BASE: 'http://api.test',
  MAIN_API_BASE: 'http://main-api.test/api/v1',
  fetchWithAuth: vi.fn(),
}));
function responseFor(url) {
  if (url.includes('/personalization/overview')) {
    return { profile: {}, stats: { by_category: {}, by_source: {} } };
  }
  if (url.includes('/personalization/learner-profile')) {
    return { locked_fields: [], survey: {}, lock_reason: {} };
  }
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

  it('does not render the removed legacy profile or trend panels', () => {
    render(<PersonalizationPage embedded view="profile" />);

    expect(screen.queryByRole('heading', { name: '学习者画像' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '学习状态趋势' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '学习记忆数据库' })).not.toBeInTheDocument();
  });

  it('renders the memory workspace without profile controls', () => {
    render(<PersonalizationPage embedded view="memory" />);

    expect(screen.getByRole('heading', { name: '学习记忆数据库' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '学习者画像' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '上传 Markdown 学习资料' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('选择 .md')).not.toBeInTheDocument();
  });

  it('renders the learning memory workspace with the frequency controls at the top', () => {
    render(<PersonalizationPage embedded view="unified" />);

    expect(screen.queryByRole('heading', { name: '学习者画像' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '学习状态趋势' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '学习记忆' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '学情分析智能体更新频率' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '每日一次' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: '刷新' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '清理过期' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '导出' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '学习记忆数据库' })).toBeInTheDocument();
  });

  it('persists an updated analysis frequency immediately', async () => {
    const user = userEvent.setup();
    render(<PersonalizationPage embedded view="unified" />);

    await user.click(screen.getByRole('button', { name: '每周一次' }));

    await waitFor(() => {
      const frequencySave = fetchWithAuth.mock.calls.find(([url, options]) => (
        url.includes('/personalization/learner-settings')
          && options?.method === 'PUT'
      ));
      expect(frequencySave).toBeDefined();
      expect(JSON.parse(frequencySave[1].body)).toEqual({ analysis_frequency: 'weekly' });
    });
    expect(screen.getByRole('button', { name: '每周一次' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('renders the two-column user profile form with lock controls', async () => {
    const user = userEvent.setup();
    render(<PersonalizationPage embedded view="user-profile" />);

    const educationInput = await screen.findByRole('textbox', { name: '学历/专业' });
    expect(screen.getByRole('heading', { name: '用户画像' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '学习基础画像' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '学习偏好画像' })).toBeInTheDocument();
    expect(educationInput).toHaveValue('非医学专业');
    expect(screen.getByRole('textbox', { name: '学习基础' })).toHaveValue('零基础；非医学专业');
    expect(screen.queryByText('完善个人学习信息，系统会据此优化学习计划、资源推荐与干预建议。')).not.toBeInTheDocument();
    expect(screen.queryByText('用于确认学习起点、目标和可持续投入的节奏。')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '刷新' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '导出' })).not.toBeInTheDocument();
    ['学历/专业', '学习基础', '用户群体', '学习目标', '可投入时间', '资源偏好', '当前困难/薄弱点', '个性化学习需求', '学习习惯'].forEach((label) => {
      expect(screen.getByRole('textbox', { name: label })).toBeEnabled();
    });
    expect(screen.getByRole('button', { name: '锁定学历/专业' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: '锁定资源偏好' })).toHaveAttribute('aria-pressed', 'false');

    await user.clear(educationInput);
    await user.type(educationInput, '中医学本科');
    expect(educationInput).toHaveValue('中医学本科');

    await user.click(screen.getByRole('button', { name: '锁定学历/专业' }));
    await user.click(screen.getByRole('button', { name: '锁定资源偏好' }));
    expect(educationInput).toBeDisabled();
    expect(screen.getByRole('textbox', { name: '资源偏好' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '解锁学历/专业' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '解锁资源偏好' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: '解锁学历/专业' }));
    expect(educationInput).toBeEnabled();
    await user.click(screen.getByRole('button', { name: '锁定学历/专业' }));

    await user.click(screen.getByRole('button', { name: '保存用户画像' }));
    let learnerProfileSave;
    await waitFor(() => {
      learnerProfileSave = fetchWithAuth.mock.calls.find(([url, options]) => (
        url.includes('/personalization/learner-profile') && options?.method === 'PUT'
      ));
      expect(learnerProfileSave).toBeDefined();
    });
    const savedProfile = JSON.parse(learnerProfileSave[1].body);
    expect(savedProfile).toMatchObject({
      education_major: '中医学本科',
      learning_background: '零基础；非医学专业',
      lock_reason: {
        education_major: '用户在用户画像页锁定',
        resource_preferences: '用户在用户画像页锁定',
      },
    });
    expect(savedProfile.locked_fields).toEqual(expect.arrayContaining(['education_major', 'resource_preferences']));
  });
});
