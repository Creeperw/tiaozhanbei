const PRIMARY_NAV = [
  { key: 'dashboard', label: '平台首页' },
  { key: 'practice', label: '学习工坊' },
  { key: 'training-workshop', label: '训练工坊' },
  { key: 'personalization', label: '个性数据' },
  { key: 'settings', label: '用户设置' },
];
const SUPPORT_NAV = [
  { key: 'admin-feedback', label: '管理入口', roles: ['admin'] },
];

export const PAGE_TITLES = {
  dashboard: '培训助手首页',
  assistant: '智能助教',
  practice: '学习工坊',
  'training-workshop': '训练工坊',
  knowledge: '知识库',
  personalization: '个性数据',
  settings: '用户设置',
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
    ...PRIMARY_NAV.map((item) => item.key),
    'assistant',
    'knowledge',
    ...visibleSupportNav.map((item) => item.key),
  ]);
  const normalizedPage = allowedPages.has(requestedPage) ? requestedPage : 'dashboard';
  const homeAction = normalizedPage === 'dashboard' ? null : { key: 'dashboard', label: '返回主页' };
  const shellMode = ['assistant', 'practice', 'training-workshop', 'knowledge'].includes(normalizedPage) ? 'workspace' : 'standard';

  return {
    defaultPage: 'dashboard',
    currentPage: normalizedPage,
    shellMode,
    selectedSessionId,
    knowledgeView,
    pageTitle: PAGE_TITLES[normalizedPage] || PAGE_TITLES.dashboard,
    primaryNav: PRIMARY_NAV,
    supportNav: visibleSupportNav,
    moduleRoutes: MODULE_ROUTES,
    homeAction,
    assistantHomeAction: normalizedPage === 'assistant'
      ? { ...homeAction, showWhenSessionMissing: true }
      : null,
  };
}
