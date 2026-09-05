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

  it('renders the read-only learning profile with completion and an edit entry point', async () => {
    const user = userEvent.setup();
    render(<PersonalizationPage embedded view="user-profile" />);

    expect(await screen.findByText('非医学专业')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '我的学习画像' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '基础信息' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '学习偏好' })).toBeInTheDocument();
    expect(screen.getByLabelText('画像完整度 38%')).toBeInTheDocument();
    expect(screen.getByText('非医学专业')).toBeInTheDocument();
    expect(screen.getByText('零基础；非医学专业')).toBeInTheDocument();
    expect(screen.queryByText('个性数据')).not.toBeInTheDocument();
    expect(screen.queryByText('用户群体')).not.toBeInTheDocument();
    expect(screen.queryByText('直接展示已填写的偏好内容；修改与锁定请通过“编辑画像”完成。')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('专业背景')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '刷新' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '导出' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '编辑画像' }));
    expect(screen.getByRole('dialog', { name: '编辑画像' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '编辑基础信息' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '编辑学习偏好' })).toBeInTheDocument();
    expect(screen.getByLabelText('专业背景')).toHaveValue('非医学专业');
    expect(screen.getByLabelText('当前基础')).toHaveValue('零基础；非医学专业');
    expect(screen.queryByLabelText('用户群体')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '锁定资源偏好' })).toHaveAttribute('aria-pressed', 'false');

    await user.click(screen.getByRole('button', { name: '锁定资源偏好' }));
    expect(screen.getByRole('button', { name: '解除锁定资源偏好' })).toHaveAttribute('aria-pressed', 'true');
    await user.clear(screen.getByLabelText('资源偏好'));
    await user.type(screen.getByLabelText('资源偏好'), '知识卡片、分阶测试题');

    await user.click(screen.getByRole('button', { name: '保存画像' }));
    let learnerProfileSave;
    await waitFor(() => {
      learnerProfileSave = fetchWithAuth.mock.calls.find(([url, options]) => (
        url.includes('/personalization/learner-profile') && options?.method === 'PUT'
      ));
      expect(learnerProfileSave).toBeDefined();
    });
    expect(JSON.parse(learnerProfileSave[1].body)).toMatchObject({
      education_major: '非医学专业',
      learning_background: '零基础；非医学专业',
      resource_preferences: '知识卡片、分阶测试题',
      locked_fields: ['resource_preferences'],
      lock_reason: { resource_preferences: '用户在学习画像中锁定' },
    });
    expect(fetchWithAuth.mock.calls.filter(([url, options]) => url.endsWith('/personalization/profile') && options?.method === 'PUT')).toHaveLength(0);
  });

  it('shows authoritative preferences instead of the stale overview column', async () => {
    fetchWithAuth.mockImplementation(async url => ({
      ok: true,
      json: async () => url.endsWith('/personalization/overview')
        ? { profile: { exercise_preferences: '视频 练习题' }, stats: {} }
        : url.endsWith('/personalization/learner-profile')
          ? { resource_preferences: '案例训练', locked_fields: [], survey: {} }
          : responseFor(url),
    }));
    render(<PersonalizationPage embedded view="user-profile" />);
    expect(await screen.findByText('案例训练')).toBeInTheDocument();
    expect(screen.queryByText('视频 练习题')).not.toBeInTheDocument();
  });
});
