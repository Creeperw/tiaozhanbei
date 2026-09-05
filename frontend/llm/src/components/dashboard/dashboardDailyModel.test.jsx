import { describe, expect, it } from 'vitest';
import {
  buildDailyFeedback,
  buildDailyFocus,
  buildDailySchedule,
} from './dashboardDailyModel';

describe('dashboardDailyModel', () => {
  it('uses the first unfinished task as the daily focus', () => {
    const focus = buildDailyFocus({
      hero: { focus: '复习方证对应', goal: '完成今日计划' },
      today_tasks: [
        { key: 'done', title: '晨间回顾', status: 'completed' },
        { key: 'locked', title: '尚未解锁的冲刺题', status: 'locked' },
        { key: 'formula', title: '方剂学第 3 章', reason: '掌握方证对应', duration: '25 分钟' },
        { key: 'quiz', title: '完成章节测验', status: 'pending' },
      ],
    });

    expect(focus).toMatchObject({
      id: 'formula',
      title: '方剂学第 3 章',
      description: '掌握方证对应',
      duration: '25 分钟',
    });
  });

  it('falls back to real hero content when there are no tasks', () => {
    expect(buildDailyFocus({
      hero: { focus: '补强中医基础理论', goal: '稳定完成每日学习计划' },
      today_tasks: [],
    })).toMatchObject({
      title: '补强中医基础理论',
      description: '稳定完成每日学习计划',
      duration: '',
    });
  });

  it('keeps task order, normalizes state, and limits the schedule to four items', () => {
    const schedule = buildDailySchedule({
      today_tasks: [
        { key: 'a', title: 'A', status: 'completed' },
        { key: 'b', title: 'B', status: 'in_progress', duration: '10 分钟' },
        { key: 'c', title: 'C' },
        { key: 'd', title: 'D', status: 'locked' },
        { key: 'e', title: 'E' },
      ],
    });

    expect(schedule.map((item) => item.title)).toEqual(['A', 'B', 'C', 'D']);
    expect(schedule.map((item) => item.state)).toEqual(['completed', 'current', 'pending', 'blocked']);
    expect(schedule[1].duration).toBe('10 分钟');
  });

  it('sanitizes a legacy onboarding JSON task reason before it reaches the homepage', () => {
    const onboarding = JSON.stringify({
      status: 'onboarding_completed',
      survey_answers: { current_difficulties: '方剂组成混淆、缺少练习反馈' },
    });
    const dashboard = {
      today_tasks: [{
        key: 'micro-review',
        title: '完成一次短练',
        reason: `围绕“${onboarding}”快速检测掌握情况`,
      }],
    };

    expect(buildDailyFocus(dashboard).description).toBe('围绕“方剂组成混淆、缺少练习反馈”快速检测掌握情况');
    expect(buildDailySchedule(dashboard)[0].description).toBe('围绕“方剂组成混淆、缺少练习反馈”快速检测掌握情况');
  });

  it('only exposes feedback metrics supplied by the backend', () => {
    const feedback = buildDailyFeedback({
      yesterday_feedback: { metrics: [
        { key: 'accuracy', label: '正确率', value: '82%' },
        { key: 'empty', label: '待复习', value: '' },
        { key: 'memory', label: '活跃学习记忆', value: '6' },
      ] },
      status_cards: [{ key: 'stage', label: '当前阶段', value: '持续观察' }],
    });

    expect(feedback).toEqual([
      { key: 'accuracy', label: '正确率', value: '82%' },
      { key: 'memory', label: '活跃学习记忆', value: '6' },
    ]);
    expect(feedback.some((item) => item.label.includes('薄弱点'))).toBe(false);
  });

  it('does not relabel current status cards as yesterday feedback', () => {
    expect(buildDailyFeedback({
      status_cards: [{ key: 'accuracy', label: '当前正确率', value: '82%' }],
    })).toEqual([]);
  });
});
