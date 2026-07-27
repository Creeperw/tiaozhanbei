const intent = (page, params = {}) => ({ page, params });
const PRIMARY_NAV = [
  { key: 'dashboard', label: '平台首页', children: [{ label: '平台总览', intent: intent('dashboard') }, { label: '继续学习', intent: intent('learning-path') }] },
  { key: 'learning-path', label: '学习路径', children: [{ label: '路径规划', intent: intent('learning-path') }, { label: '当前阶段', intent: intent('learning-path', { view: 'current-stage' }) }, { label: '教材学习', intent: intent('practice', { view: 'textbook-chapters' }) }] },
  { key: 'practice', label: '学习工坊', children: [{ label: '智能助教', intent: intent('assistant', { newConversation: true }) }, { label: '知识图谱', intent: intent('knowledge', { view: 'atlas' }) }, { label: '个人知识库', intent: intent('knowledge', { view: 'personal' }) }, { label: '资料上传', intent: intent('knowledge', { view: 'upload' }) }] },
  { key: 'training-workshop', label: '训练工坊', children: [{ label: '题目训练', intent: intent('training-workshop', { taskType: 'question_training' }) }, { label: 'AI 病患模拟', intent: intent('training-workshop', { taskType: 'simulated_patient' }) }, { label: '错题变式', intent: intent('training-workshop', { taskType: 'mistake_variation' }) }, { label: '试卷生成', intent: intent('training-workshop', { taskType: 'paper_generation' }) }] },
  { key: 'personalization', label: '个性数据', children: [{ label: '学习画像', intent: intent('personalization', { view: 'user-profile' }) }, { label: '能力分析', intent: intent('personalization', { view: 'ability-analysis' }) }, { label: '记忆管理', intent: intent('settings', { view: 'memory' }) }] },
];
const INTERNAL_ALLOWED_PAGES = ['assistant', 'knowledge', 'settings'];
const SUPPORT_NAV = [
  { key: 'admin-feedback', label: '管理入口', roles: ['admin'], children: [{ label: '反馈管理', intent: intent('admin-feedback') }, { label: '知识治理', intent: intent('knowledge', { view: 'personal' }) }] },
];

export const PAGE_TITLES = {
  dashboard: '培训助手首页',
  'learning-path': '学习路径',
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
    ...INTERNAL_ALLOWED_PAGES,
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
    primaryNav: [...PRIMARY_NAV, ...visibleSupportNav],
    supportNav: visibleSupportNav,
    moduleRoutes: MODULE_ROUTES,
    homeAction,
    assistantHomeAction: normalizedPage === 'assistant'
      ? { ...homeAction, showWhenSessionMissing: true }
      : null,
  };
}
