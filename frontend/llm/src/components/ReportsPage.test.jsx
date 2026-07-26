import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import ReportsPage from './ReportsPage';
import { loadReportsData } from '../pageDataLoaders.js';

vi.mock('../pageDataLoaders.js', () => ({
  emptyReport: {
    window: { days: 30 }, overview: {}, dimensions: [],
    activity_trends: { series: [] }, mastery_heatmap: [], weak_points: [],
    mistake_distribution: [], data_quality: {}, automation: {},
    data_sources: [], methodology: { references: [], limitations: [] },
    resource_match_report: { summary: {}, matches: [] }, mastery_radar: [],
    learner_overview: {}, t_stage: {},
    multiscale: { macro: {}, meso: {}, micro: {}, source_refs: [] },
  },
  loadReportsData: vi.fn(),
}));

vi.mock('../utils/api', () => ({ fetchJsonWithAuthFallback: vi.fn() }));

describe('ReportsPage', () => {
  it('renders evidence-backed insights and resource matching', async () => {
    loadReportsData.mockResolvedValue({
      error: '',
      report: {
        window: { days: 30 },
        overview: { stage_name: '节奏恢复', summary: '任务执行正在恢复。', confidence: 0.72, due_review_count: 2 },
        dimensions: [
          { key: 'mastery', label: '知识掌握', value: 0.62, evidence_count: 3, formula: 'mean(mastery)' },
          { key: 'retention', label: '复习保持', value: 0.55, evidence_count: 2, formula: 'mean(retention)' },
          { key: 'execution', label: '任务执行', value: 0.48, evidence_count: 5, formula: 'completed/tasks' },
          { key: 'accuracy', label: '练习得分率', value: 0.73, evidence_count: 4, formula: 'sum(scores)/sum(max_scores)' },
        ],
        activity_trends: {
          series: [
            { date: '2026-07-16', focus_minutes: 20, task_completion_rate: 0.25, login_days: 1 },
            { date: '2026-07-17', focus_minutes: 45, task_completion_rate: 0.5, login_days: 1 },
            { date: '2026-07-18', focus_minutes: 0, task_completion_rate: 0, login_days: 0 },
            { date: '2026-07-19', focus_minutes: 35, task_completion_rate: 0.75, login_days: 1 },
            { date: '2026-07-20', focus_minutes: 60, task_completion_rate: 0.7, login_days: 1 },
            { date: '2026-07-21', focus_minutes: 30, task_completion_rate: 0.5, login_days: 1 },
            { date: '2026-07-22', focus_minutes: 35, task_completion_rate: 0.5, login_days: 1 },
          ],
        },
        weak_points: [{ kp_id: 'KP_1', kp_name: '四君子汤配伍', mastery_score: 0.4, reason: '掌握度偏低' }],
        mistake_distribution: [{ error_type: '配伍关系混淆', count: 2 }],
        data_quality: { confidence: 0.72, sample_count: 11, sources: ['learning_task'], is_sufficient_for_intervention: true },
        data_sources: [{ source_id: 'learning_tasks', table: 'learning_task', fields: ['status'], window_days: 30 }],
        methodology: {
          references: [{ reference_id: 'caliper', title: 'Caliper Analytics', url: 'https://example.com/caliper', note: '学习事件语义参考。' }],
          limitations: ['数据覆盖度不是统计置信区间。'],
        },
        automation: { intervention: { reason: '建议减少今日任务数量。' }, plan_review: { summary: '建议增加复习窗口。' } },
        resource_match_report: {
          summary: { coverage: 1 },
          methodology: { formula: 'weighted match' },
          matches: [{
            resource_id: 'CARD_1',
            resource_type: 'knowledge_card',
            title: '四君子汤知识卡',
            score: 0.9,
            estimated_minutes: 12,
            reasons: ['覆盖当前薄弱知识点'],
            components: { knowledge_fit: 1, quality: 0.8, format_fit: 1, time_fit: 1 },
            component_sources: {
              knowledge_fit: 'resource.kp_ids intersect target.kp_ids',
              quality: 'knowledge_card_bundle',
              format_fit: 'user_profiles.exercise_preferences/custom_needs',
              time_fit: 'content_type_default',
            },
          }],
        },
        multiscale: {
          schema_version: '1.0',
          macro: { current_stage: { name: '中医基础与文化语言' }, stage_books: [{ name: '中医学基础' }] },
          meso: {
            task_completion_rate: { available: true, value: 0.75, source_refs: ['learning_task:1'] },
          },
          micro: {
            question_accuracy: { available: false, value: null, unavailable_reason: 'no_question_attempts', source_refs: [] },
          },
          source_refs: [{ source_id: 'learning_task:1', table: 'learning_task' }],
        },
      },
    });

    render(<ReportsPage />);

    expect(await screen.findByLabelText(/2026-07-22：有效学习 35 分钟/)).toBeInTheDocument();
    const trendRegion = screen.getByRole('region', { name: '每日学习趋势' });
    expect(trendRegion).toBeInTheDocument();
    expect(within(trendRegion).getByText('90%')).toBeInTheDocument();
    expect(screen.getByText('有效学习时长（分钟）')).toBeInTheDocument();
    expect(screen.getByText('任务完成率（%）')).toBeInTheDocument();
    expect(screen.getByText('日期')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '学习活跃度' })).toBeInTheDocument();
    expect(screen.queryByText('配伍关系混淆')).not.toBeInTheDocument();
    expect(screen.queryByText('需要优先补强')).not.toBeInTheDocument();
    expect(screen.getByText('四君子汤知识卡')).toBeInTheDocument();
    expect(screen.getByText('样本状态：可用于谨慎干预')).toBeInTheDocument();
    expect(screen.getAllByText('练习得分率').length).toBeGreaterThan(0);
    expect(screen.getByText('73%')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '学习能力雷达图' }).querySelectorAll('circle')).toHaveLength(0);
    expect(screen.getByText('监测口径、数据来源与参考依据')).toBeInTheDocument();
    expect(screen.getByText('宏观状态')).toBeInTheDocument();
    expect(screen.getByText('中医基础与文化语言')).toBeInTheDocument();
    expect(screen.getByText('中观状态')).toBeInTheDocument();
    expect(screen.getByText('微观状态')).toBeInTheDocument();
    expect(screen.getByText(/no_question_attempts/)).toBeInTheDocument();
    expect(screen.queryByText(/KP_/)).not.toBeInTheDocument();
    expect(screen.queryByText('节奏恢复')).not.toBeInTheDocument();
    expect(screen.queryByText('重新计算')).not.toBeInTheDocument();
    expect(screen.queryByText('数据覆盖度')).not.toBeInTheDocument();

    const basisButton = screen.getByRole('button', { name: '匹配依据' });
    expect(basisButton).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(basisButton);
    expect(basisButton).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByLabelText('四君子汤知识卡匹配依据详情')).toBeInTheDocument();
    expect(screen.getByText('资源知识点与当前薄弱点、计划知识点的交集')).toBeInTheDocument();
    expect(screen.queryByText(/难度/)).not.toBeInTheDocument();
  });
});
