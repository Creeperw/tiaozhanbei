import test from 'node:test';
import assert from 'node:assert/strict';

import { buildLearningTrendCharts } from './learningTrendDisplay.js';

test('builds chart series from learning trend payload', () => {
  const charts = buildLearningTrendCharts({
    series: [
      { date: '2026-07-15', login_days: 1, focus_minutes: 15, task_completion_rate: 0.5 },
      { date: '2026-07-16', login_days: 0, focus_minutes: 30, task_completion_rate: 1 },
    ],
  });

  assert.deepEqual(charts, [
    { key: 'task-completion-rate', label: '任务完成率', suffix: '%', values: [50, 100], dates: ['07-15', '07-16'] },
    { key: 'focus-minutes', label: '专注时长', suffix: ' 分钟', values: [15, 30], dates: ['07-15', '07-16'] },
    { key: 'login-days', label: '学习活跃日', suffix: ' 天', values: [1, 0], dates: ['07-15', '07-16'] },
  ]);
});

test('uses zero values when a learning trend series is unavailable', () => {
  assert.deepEqual(buildLearningTrendCharts({ series: [] }), [
    { key: 'task-completion-rate', label: '任务完成率', suffix: '%', values: [], dates: [] },
    { key: 'focus-minutes', label: '专注时长', suffix: ' 分钟', values: [], dates: [] },
    { key: 'login-days', label: '学习活跃日', suffix: ' 天', values: [], dates: [] },
  ]);
});
