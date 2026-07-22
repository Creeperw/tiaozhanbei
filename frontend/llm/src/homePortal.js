export const EMPTY_HOME_PAYLOAD = {
  continue_learning: [],
  today_tasks: [],
  status_cards: [],
  announcements: [],
  checkin_status: { checked_in_today: false, streak: 0, total_checkins: 0, calendar_days: [] },
};

const HOME_IMAGE_BASE = '/design-images/home';

export const HOME_ACTIONS = [
  {
    key: 'continue-learning',
    title: '继续学习',
    image: `${HOME_IMAGE_BASE}/continue-learning.png`,
    intent: { page: 'practice', params: { view: 'workspace', taskType: 'practice_grading' } },
  },
  {
    key: 'pending-tasks',
    title: '待办任务',
    image: `${HOME_IMAGE_BASE}/pending-tasks.png`,
    intent: { page: 'practice', params: { view: 'overview' } },
  },
  {
    key: 'ai-qa',
    title: '智能问答',
    image: `${HOME_IMAGE_BASE}/ai-qa.png`,
    intent: { page: 'assistant', params: {} },
  },
  {
    key: 'resource-search',
    title: '资料检索',
    image: `${HOME_IMAGE_BASE}/resource-search.png`,
    intent: { page: 'knowledge', params: { view: 'sources' } },
  },
  {
    key: 'knowledge-graph',
    title: '知识图谱',
    image: `${HOME_IMAGE_BASE}/knowledge-graph.png`,
    intent: { page: 'knowledge', params: { view: 'atlas', source: 'dashboard' } },
  },
  {
    key: 'question-workspace',
    title: '题目工作区',
    image: `${HOME_IMAGE_BASE}/question-workspace.png`,
    intent: { page: 'question-workspace', params: {} },
  },
  {
    key: 'focused-practice',
    title: '专项练习',
    image: `${HOME_IMAGE_BASE}/focused-practice.png`,
    intent: { page: 'practice', params: { view: 'workspace', taskType: 'practice_grading' } },
  },
  {
    key: 'mistake-reinforcement',
    title: '错题巩固',
    image: `${HOME_IMAGE_BASE}/mistake-reinforcement.png`,
    intent: { page: 'practice', params: { view: 'workspace', taskType: 'mistake_variation' } },
  },
  {
    key: 'case-training',
    title: '案例实训',
    image: `${HOME_IMAGE_BASE}/case-training.png`,
    intent: { page: 'practice', params: { view: 'workspace', taskType: 'case_training' } },
  },
];

const EMPTY_CONTINUE_LEARNING = {
  title: '选择一项学习内容开始',
  sessionId: null,
  progress: null,
};

const EMPTY_PENDING_TASKS = {
  count: 0,
  duration: '打开训练工坊查看安排',
};

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function announcementText(announcement) {
  const value = announcement && typeof announcement === 'object'
    ? announcement.title || announcement.content || announcement.message
    : announcement;
  return typeof value === 'string' ? value.trim() : '';
}

function findProgress(statusCards) {
  for (const card of asArray(statusCards)) {
    const match = String(card?.value || '').trim().match(/^(\d{1,3}(?:\.\d+)?)%$/);
    if (!match) continue;
    const progress = Number(match[1]);
    if (Number.isFinite(progress) && progress >= 0 && progress <= 100) return progress;
  }
  return null;
}

function cloneIntent(intent) {
  return { page: intent.page, params: { ...intent.params } };
}

export function getHomeActionIntent(key, payload = EMPTY_HOME_PAYLOAD) {
  const action = HOME_ACTIONS.find((item) => item.key === key);
  if (!action) return { page: 'dashboard', params: {} };

  const intent = cloneIntent(action.intent);
  if (key !== 'continue-learning') return intent;

  const sessionId = asArray(payload?.continue_learning)[0]?.session_id;
  return sessionId
    ? { ...intent, params: { ...intent.params, sessionId } }
    : intent;
}

export function buildHomePortalState(payload = EMPTY_HOME_PAYLOAD) {
  const normalizedPayload = payload && typeof payload === 'object' ? payload : EMPTY_HOME_PAYLOAD;
  const continueLearning = asArray(normalizedPayload.continue_learning)[0] || {};
  const firstTask = asArray(normalizedPayload.today_tasks)[0] || {};

  return {
    continueLearning: {
      title: String(continueLearning.title || EMPTY_CONTINUE_LEARNING.title),
      sessionId: continueLearning.session_id || null,
      progress: findProgress(normalizedPayload.status_cards),
    },
    pendingTasks: {
      count: asArray(normalizedPayload.today_tasks).length,
      duration: String(firstTask.duration || EMPTY_PENDING_TASKS.duration),
    },
    announcements: asArray(normalizedPayload.announcements)
      .map(announcementText)
      .filter(Boolean),
  };
}
