import test from 'node:test';
import assert from 'node:assert/strict';

import { formatSystemDataMetrics } from './systemDataDisplay.js';

test('formats system data metrics for dashboard display', () => {
  const metrics = formatSystemDataMetrics({
    time_data: {
      login_frequency: { value: 3, unit: 'days' },
      focus_time_period: { value: '14:00-14:59', unit: 'hour_slot' },
    },
    task_completion_rate: { value: 2 / 3, unit: 'ratio' },
    resource_click_rate: { value: 0.25 },
    calculated_at: '2026-07-15T11:42:57.648464',
  });

  assert.deepEqual(metrics, [
    { key: 'login-frequency', label: '近 30 天登录', value: '3 天' },
    { key: 'focus-time-period', label: '主要专注时段', value: '14:00-14:59' },
    { key: 'task-completion', label: '任务完成率', value: '66.7%' },
    { key: 'resource-click', label: '推荐资源点击率', value: '25%' },
    { key: 'calculated-at', label: '最近计算', value: '2026-07-15 19:42' },
  ]);
});

test('omits unavailable system data metrics', () => {
  assert.deepEqual(formatSystemDataMetrics({}), []);
});
