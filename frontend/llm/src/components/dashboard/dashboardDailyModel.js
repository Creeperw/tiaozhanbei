const COMPLETED_STATES = new Set(['completed', 'complete', 'done', 'finished']);
const CURRENT_STATES = new Set(['current', 'in_progress', 'active', 'doing']);
const BLOCKED_STATES = new Set(['blocked', 'locked', 'disabled']);

function text(value) {
  return typeof value === 'string' ? value.trim() : '';
}

function taskId(task, index = 0) {
  return task?.task_id || task?.key || task?.id || task?.title || `daily-task-${index}`;
}

function taskState(task) {
  const status = text(task?.status).toLowerCase();
  if (COMPLETED_STATES.has(status)) return 'completed';
  if (CURRENT_STATES.has(status)) return 'current';
  if (BLOCKED_STATES.has(status)) return 'blocked';
  return 'pending';
}

function taskDescription(task) {
  const description = text(task?.reason) || text(task?.description);
  const match = description.match(/^围绕“([\s\S]+)”快速检测掌握情况$/);
  if (!match) return description;

  try {
    const payload = JSON.parse(match[1]);
    if (payload?.status !== 'onboarding_completed') return description;
    const difficulty = text(payload?.survey_answers?.current_difficulties)
      || text(payload?.l0_baseline?.current_difficulties);
    return difficulty
      ? `围绕“${difficulty}”快速检测掌握情况`
      : '正在准备个性化短练';
  } catch {
    return description;
  }
}

export function buildDailyFocus(dashboard = {}) {
  const tasks = Array.isArray(dashboard.today_tasks) ? dashboard.today_tasks : [];
  const task = tasks.find((item) => taskState(item) === 'current')
    || tasks.find((item) => taskState(item) === 'pending');
  const hero = dashboard.hero || {};

  if (!task) {
    return {
      id: 'hero-focus',
      title: text(hero.focus) || text(hero.goal) || '开始今日学习',
      description: text(hero.focus) ? text(hero.goal) : '',
      duration: '',
      state: 'pending',
    };
  }

  return {
    id: taskId(task),
    title: text(task.title) || text(task.name) || text(hero.focus) || '今日学习任务',
    description: taskDescription(task) || text(hero.goal),
    duration: text(task.duration) || text(task.estimated_duration),
    state: taskState(task),
  };
}

export function buildDailySchedule(dashboard = {}) {
  const tasks = Array.isArray(dashboard.today_tasks) ? dashboard.today_tasks : [];
  return tasks.slice(0, 4).map((task, index) => ({
    id: taskId(task, index),
    title: text(task.title) || text(task.name) || `学习任务 ${index + 1}`,
    description: taskDescription(task),
    duration: text(task.duration) || text(task.estimated_duration),
    time: text(task.time) || text(task.scheduled_time) || text(task.start_time),
    state: taskState(task),
    source: text(task.source),
  }));
}

export function buildDailyFeedback(dashboard = {}) {
  const metrics = Array.isArray(dashboard.yesterday_feedback?.metrics)
    ? dashboard.yesterday_feedback.metrics
    : [];

  return metrics
    .filter((item) => text(item?.label) && text(item?.value))
    .slice(0, 4)
    .map((item, index) => ({
      key: item.key || `feedback-${index}`,
      label: text(item.label),
      value: text(item.value),
    }));
}
