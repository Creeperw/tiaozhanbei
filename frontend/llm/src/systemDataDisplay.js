const formatBeijingDateTime = (value) => {
  const date = new Date(value.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`);
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date).replaceAll('/', '-');
};

const percent = (value) => `${(Number(value || 0) * 100).toFixed(1).replace(/\.0$/, '')}%`;

export function formatSystemDataMetrics(systemData) {
  const timeData = systemData?.time_data || {};
  const completion = systemData?.task_completion_rate || {};
  const resourceClickRate = systemData?.resource_click_rate || {};
  const calculatedAt = systemData?.calculated_at;

  if (!calculatedAt) return [];

  return [
    {
      key: 'login-frequency',
      label: '近 30 天登录',
      value: `${timeData.login_frequency?.value || 0} 天`,
    },
    {
      key: 'focus-time-period',
      label: '主要专注时段',
      value: timeData.focus_time_period?.value || '暂无',
    },
    {
      key: 'task-completion',
      label: '任务完成率',
      value: percent(completion.value),
    },
    {
      key: 'resource-click',
      label: '推荐资源点击率',
      value: percent(resourceClickRate.value),
    },
    {
      key: 'calculated-at',
      label: '最近计算',
      value: formatBeijingDateTime(calculatedAt),
    },
  ];
}
