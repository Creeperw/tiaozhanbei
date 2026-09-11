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

  it('shows readable content instead of an 「未命名」 placeholder when a title is missing', async () => {
    // The memory-extraction agent is allowed to return content without a title
    // (the backend normalises a missing title to ""), so the placeholder used to
    // hide memories that are perfectly readable.
    const content = '用户需从《中医学基础》绪论开始学习，已作为起点确认。';
    fetchWithAuth.mockImplementation(async (url) => {
      if (url.includes('/personalization/memories')) {
        return {
          ok: true,
          json: async () => ([{
            id: 1,
            category: 'note',
            title: '',
            content,
            source: 'memory_agent',
            importance: 'normal',
            is_active: true,
            updated_at: '2026-09-01T00:00:00Z',
          }]),
        };
      }
      if (url.includes('/personalization/candidates')) {
        return {
          ok: true,
          json: async () => ([{
            id: 2,
            status: 'pending',
            title: '',
            content,
            source: 'memory_agent',
            importance: 'normal',
            reason: '记忆管理智能体识别为重要信息',
            updated_at: '2026-09-01T00:00:00Z',
          }]),
        };
      }
      return { ok: true, json: async () => responseFor(url) };
    });

    render(<PersonalizationPage embedded view="unified" />);

    expect(await screen.findAllByText(content)).not.toHaveLength(0);
    expect(screen.queryByText('未命名')).not.toBeInTheDocument();
    expect(screen.queryByText('未命名候选')).not.toBeInTheDocument();
  });

  it('shows readable names instead of internal enums for memory sources and categories', async () => {
    // memory source/category are fixed backend enums, so the page translates
    // them.  Values such as onboarding_survey or memory_agent used to be printed
    // verbatim, which reads like a system fault to the learner.
    fetchWithAuth.mockImplementation(async (url) => {
      if (url.includes('/personalization/overview')) {
        return {
          ok: true,
          json: async () => ({
            profile: {},
            stats: {
              by_category: { note: 1, legacy_unknown_category: 1 },
              by_source: {
                onboarding_survey: 1,
                memory_agent: 1,
                synthetic_judge_usage_v2: 1,
                legacy_unknown_source: 1,
              },
            },
          }),
        };
      }
      if (url.includes('/personalization/memories')) {
        return {
          ok: true,
          json: async () => ([{
            id: 11,
            category: 'note',
            // 标题刻意与来源标签不同名，否则标题会把「来源已映射」这件事掩盖掉。
            title: '每日学习时间安排',
            content: '每日可投入时间：35 分钟',
            source: 'onboarding_survey',
            importance: 'normal',
            is_active: true,
            updated_at: '2026-09-11T12:00:00Z',
          }]),
        };
      }
      if (url.includes('/personalization/candidates')) {
        return {
          ok: true,
          json: async () => ([{
            id: 12,
            status: 'pending',
            title: '',
            content: '用户需从《中医学基础》绪论开始学习。',
            source: 'memory_agent',
            importance: 'normal',
            reason: '记忆管理智能体识别为重要信息',
            updated_at: '2026-09-11T12:00:00Z',
          }]),
        };
      }
      return { ok: true, json: async () => responseFor(url) };
    });

    const { container } = render(<PersonalizationPage embedded view="unified" />);

    // 「入学学情调查」只可能来自 source 枚举映射（记忆标题是「每日学习时间安排」）。
    expect(await screen.findAllByText('入学学情调查')).not.toHaveLength(0);
    expect(screen.getAllByText('记忆管理智能体')).not.toHaveLength(0);
    expect(screen.getAllByText('示例数据导入')).not.toHaveLength(0);
    expect(screen.getByText('其他来源')).toBeInTheDocument();
    expect(screen.getByText('其他分类')).toBeInTheDocument();
    // 未登记的枚举值不得原样出现在页面上。
    const rendered = container.textContent;
    expect(rendered).not.toContain('onboarding_survey');
    expect(rendered).not.toContain('memory_agent');
    expect(rendered).not.toContain('synthetic_judge_usage_v2');
    expect(rendered).not.toContain('legacy_unknown_source');
    expect(rendered).not.toContain('legacy_unknown_category');
  });
});
