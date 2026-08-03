const intent = (page, params = {}) => ({ page, params });
const PRIMARY_NAV = [
  { key: 'learning-target', label: '考试类别', kind: 'learning-target' },
  { key: 'learning-path', label: '学习路径', intent: intent('learning-path') },
  { key: 'practice', label: '教学资源', intent: intent('practice') },
  { key: 'training-workshop', label: '训练工坊', children: [{ label: '题目训练', intent: intent('training-workshop', { taskType: 'topic_training' }) }, { label: 'AI 病患模拟', intent: intent('training-workshop', { taskType: 'ai_patient_simulation' }) }, { label: '历史记录', intent: intent('training-workshop', { taskType: 'training_history' }) }, { label: '错题变式', intent: intent('training-workshop', { taskType: 'mistake_variation' }) }, { label: '试卷生成', intent: intent('training-workshop', { taskType: 'paper_generation' }) }] },
  {
    key: 'personalization',
    label: '个人数据',
    intent: intent('personalization', { view: 'reports' }),
    children: [
      { label: '学情报告', intent: intent('personalization', { view: 'reports' }) },
      { label: '学习画像', intent: intent('personalization', { view: 'user-profile' }) },
      { label: '复习与掌握', intent: intent('personalization', { view: 'review' }) },
      { label: '学习记忆', intent: intent('personalization', { view: 'memory' }) },
      { label: '上传资源', intent: intent('personalization', { view: 'resources' }) },
    ],
  },
];
const INTERNAL_ALLOWED_PAGES = ['assistant', 'knowledge', 'settings', 'capability-detail', 'learning-path-tasks'];
const SUPPORT_NAV = [
  { key: 'admin-feedback', label: '管理入口', roles: ['admin'], children: [{ label: '反馈管理', intent: intent('admin-feedback') }, { label: '知识治理', intent: intent('knowledge', { view: 'personal' }) }] },
];

export const PAGE_TITLES = {
  dashboard: '培训助手首页',
  'learning-path': '学习路径',
  'learning-path-tasks': '学习与复习任务',
  assistant: '智能助教',
  practice: '教学资源',
  'training-workshop': '训练工坊',
  knowledge: '知识库',
  personalization: '学情报告',
  settings: '用户设置',
  'capability-detail': '平台核心能力',
  'admin-feedback': '管理入口',
};
const MODULE_ROUTES = {
  practice: { endpoint: '/training/practice/grade' },
  practiceWorkspace: { endpoint: '/training/workspace/tasks' },
  planning: { endpoint: '/training/plan/summary' },
  reports: { endpoint: '/training/report' },
};

export function getAppShellConfig({ currentUser, currentPage, selectedSessionId = null }) {
  const role = currentUser?.role || 'user';
  const knowledgeView = currentPage === 'question-workspace'
    ? 'questions'
    : currentPage === 'admin-knowledge'
      ? 'personal'
      : null;
  const requestedPage = knowledgeView ? 'knowledge' : currentPage;
  const visibleSupportNav = SUPPORT_NAV.filter((item) => !item.roles || item.roles.includes(role));
  const allowedPages = new Set([
    'dashboard',
    ...PRIMARY_NAV.filter((item) => item.kind !== 'learning-target').map((item) => item.key),
    ...INTERNAL_ALLOWED_PAGES,
    ...visibleSupportNav.map((item) => item.key),
  ]);
  const normalizedPage = allowedPages.has(requestedPage) ? requestedPage : 'dashboard';
  const homeAction = normalizedPage === 'dashboard'
    ? null
    : normalizedPage === 'learning-path-tasks'
      ? { key: 'learning-path', label: '返回' }
      : { key: 'dashboard', label: '返回主页' };
  const shellMode = ['assistant', 'practice', 'training-workshop', 'knowledge'].includes(normalizedPage) ? 'workspace' : 'standard';

  return {
    defaultPage: 'dashboard',
    currentPage: normalizedPage,
    shellMode,
    selectedSessionId,
    knowledgeView,
    pageTitle: PAGE_TITLES[normalizedPage] || PAGE_TITLES.dashboard,
    primaryNav: [...PRIMARY_NAV, ...visibleSupportNav],
    supportNav: visibleSupportNav,
    moduleRoutes: MODULE_ROUTES,
    homeAction,
    assistantHomeAction: normalizedPage === 'assistant'
      ? { ...homeAction, showWhenSessionMissing: true }
      : null,
  };
}
