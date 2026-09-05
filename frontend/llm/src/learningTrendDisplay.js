const chartDefinitions = [
  ['task-completion-rate', '任务完成率', '%', (item) => Math.round(Number(item.task_completion_rate || 0) * 100)],
  ['focus-minutes', '专注时长', ' 分钟', (item) => Number(item.focus_minutes || 0)],
  ['login-days', '学习活跃日', ' 天', (item) => Number(item.login_days || 0)],
];

export function buildLearningTrendCharts(trend) {
  const series = Array.isArray(trend?.series) ? trend.series : [];
  const dates = series.map((item) => (item.date || '').slice(5));
  return chartDefinitions.map(([key, label, suffix, valueFor]) => ({
    key,
    label,
    suffix,
    values: series.map(valueFor),
    dates,
  }));
}
