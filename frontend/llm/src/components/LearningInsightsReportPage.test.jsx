import React from 'react';
import {
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import LearningInsightsReportPage from './LearningInsightsReportPage';
import {
  loadReportsData,
  loadResourceEffectiveness,
  recordResourceRecommendationEvent,
} from '../pageDataLoaders.js';

vi.mock('../pageDataLoaders.js', () => ({
  emptyReport: {
    window: { days: 30 },
    dimensions: [],
    activity_trends: { series: [] },
    data_quality: {},
    resource_match_report: { target: {}, summary: {}, matches: [], no_match_reason: '' },
  },
  loadReportsData: vi.fn(),
  loadResourceEffectiveness: vi.fn(),
  recordResourceRecommendationEvent: vi.fn(),
}));

vi.mock('../utils/api', () => ({ fetchJsonWithAuthFallback: vi.fn() }));

describe('LearningInsightsReportPage', () => {
  it('renders the reference-style report and removes the retired report sections', async () => {
    const onNavigate = vi.fn();
    loadResourceEffectiveness.mockResolvedValue({
      error: '',
      effectiveness: {
        window_days: 30,
        funnel: {
          displayed_resource_count: 1,
          clicked_resource_count: 0,
          completed_resource_count: 0,
        },
        learning_outcomes: {
          post_resource_attempt_count: 0,
          status: 'insufficient_evidence',
        },
        ranking_feedback: { eligible_for_weight_calibration: false },
      },
    });
    recordResourceRecommendationEvent.mockResolvedValue({
      error: '',
      event: { recorded: true },
    });
    loadReportsData.mockResolvedValue({
      error: '',
      report: {
        window: { days: 30 },
        dimensions: [
          { key: 'mastery', label: '知识掌握', value: 0.72 },
          { key: 'accuracy', label: '练习正确', value: 0.58, evidence_count: 2 },
          { key: 'consistency', label: '学习规律', value: 0.66 },
          { key: 'retention', label: '复习保持', value: 0.64 },
          { key: 'execution', label: '任务执行', value: 0.48 },
          { key: 'engagement', label: '资源使用', value: 0.63 },
        ],
        activity_trends: {
          series: [
            { date: '2026-07-20', focus_minutes: 20, task_completion_rate: 0.5, login_days: 1 },
            { date: '2026-07-21', focus_minutes: 6, task_completion_rate: 0.75, login_days: 1 },
            { date: '2026-07-22', focus_minutes: 0, task_completion_rate: 0, login_days: 0 },
          ],
        },
        weak_points: [
          { kp_id: 'KP_1', kp_name: '方剂组成与功效', mastery_score: 0.42, reason: '易混淆' },
          { kp_id: 'KP_2', kp_name: '中诊辨证要点', mastery_score: 0.31, reason: '关键症状判断不稳' },
          { kp_id: 'KP_3', kp_name: '中药功效分类', mastery_score: 0.47, reason: '相近药物区分不清' },
        ],
        mastery_heatmap: [
          { kp_id: 'KP_1', kp_name: '方剂组成与功效', score: 0.42, confidence: 0.8, attempt_count: 2, retention: 0.5 },
          { kp_id: 'KP_2', kp_name: '中诊辨证要点', score: 0.31, confidence: 0.7, attempt_count: 1, retention: null },
        ],
        data_quality: { attempt_count: 2, login_days: 2 },
        activity_summary: {
          window_days: 30,
          counters: {
            learning_tasks: { total: 2, by_status: { completed: 2 } },
            focus_sessions: { total: 2, active_seconds: 1560 },
            activities: { total: 2, by_type: { question_attempt: 2 } },
          },
        },
        resource_match_report: {
          recommendation_view_id: 'recommendation-view:1',
          target: { kp_ids: ['KP_1'] },
          summary: { coverage: 1 },
          matches: [{
            resource_id: 'CARD_1',
            resource_type: 'knowledge_card',
            title: '四君子汤知识卡',
            kp_ids: ['KP_1'],
            score: 0.9,
            estimated_minutes: 12,
            reasons: ['覆盖当前薄弱或计划知识点'],
            components: { knowledge_fit: 1, quality: 0.8, format_fit: 1, time_fit: 1 },
            component_sources: {
              knowledge_fit: 'resource.kp_ids intersect target.kp_ids',
              quality: 'knowledge_card_bundle',
              format_fit: 'user_profiles.exercise_preferences/custom_needs',
              time_fit: 'content_type_default',
            },
            feedback: {
              recommendation_view_id: 'recommendation-view:1',
              resource_id: 'CARD_1',
              resource_type: 'knowledge_card',
              kp_ids: ['KP_1'],
              event_endpoint: '/api/v1/resource-recommendations/events',
              supported_events: ['impression', 'click', 'complete'],
            },
          }],
          no_match_reason: '',
        },
      },
    });
    render(<LearningInsightsReportPage onNavigate={onNavigate} currentUser={{ username: 'alice' }} />);

    expect(await screen.findByText('累计学习时长')).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: '学情报告快捷入口' })).not.toBeInTheDocument();
    expect(screen.getByText('完成练习')).toBeInTheDocument();
    expect(screen.getByText('平均正确率')).toBeInTheDocument();
    expect(screen.getByText('活跃天数')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '能力分析' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '学习能力雷达图' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '每日学习趋势' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /有效学习时长柱状图与任务完成率折线图/ })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '薄弱知识点' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '学习活跃度' })).toBeInTheDocument();
    expect(screen.getAllByText('方剂组成与功效')).not.toHaveLength(0);
    expect(screen.getAllByText('中诊辨证要点')).not.toHaveLength(0);
    expect(screen.getByRole('button', { name: '去专项巩固' })).toBeInTheDocument();
    screen.getByRole('button', { name: '去专项巩固' }).click();
    expect(onNavigate).toHaveBeenCalledWith({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'topic_training',
        kpId: 'KP_1',
        kpName: '方剂组成与功效',
        returnTo: { page: 'personalization', params: { view: 'reports' } },
      },
    });
    expect(screen.queryByText('知识点掌握热力图')).not.toBeInTheDocument();
    expect(screen.queryByText('复习队列')).not.toBeInTheDocument();
    expect(screen.queryByText('多尺度学习状态')).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: '资源匹配报告' })).toBeInTheDocument();
    expect(screen.getByText('四君子汤知识卡')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '资源推荐效果' })).toBeInTheDocument();
    await waitFor(() => expect(recordResourceRecommendationEvent).toHaveBeenCalledWith(expect.objectContaining({
      eventType: 'impression',
    })));
    fireEvent.click(screen.getByRole('button', { name: '匹配依据' }));
    expect(screen.getByText('资源知识点与当前薄弱点、计划知识点的交集')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '打开资源' }));
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'knowledge_cards',
        cardId: 'CARD_1',
        kpId: 'KP_1',
        returnTo: { page: 'personalization', params: { view: 'reports' } },
      },
    }));
    fireEvent.click(screen.getByRole('button', { name: '我已学完' }));
    expect(await screen.findByRole('button', { name: '已完成' })).toBeDisabled();
    expect(screen.queryByText('监测口径、数据来源与参考依据')).not.toBeInTheDocument();
    expect(screen.queryByText('主动干预')).not.toBeInTheDocument();
  });
});
