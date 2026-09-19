/**
 * URL 路由映射：pageIntent ↔ 浏览器路径 双向转换。
 *
 * 前端内部仍使用 state-based 路由（pageIntent），本模块为其提供 URL 外壳：
 * - intentToPath(intent)   → 把页面意图序列化为 /practice/special-training 等路径
 * - pathToIntent(pathname) → 把路径还原为 pageIntent
 *
 * URL 中只保留页面级路由；深层上下文（知识图谱轨道、试卷 id 等）继续由
 * sessionStorage 持久化兜底，避免 URL 无限膨胀。
 */

// ── 练习工坊子模块 slug（对应 taskRegistry.js 的卡片 key）──
const WORKSHOP_SLUGS = {
  'special-training': { taskType: 'special_training', initialMode: 'case_training' },
  'topic-training': { taskType: 'topic_training' },
  'mistake-redo': { taskType: 'mistake_redo' },
  'mistake-variation': { taskType: 'mistake_variation' },
  'comprehensive': { taskType: 'question_training' },
  'smart-paper': { taskType: 'paper_workspace' },
  'patient-simulation': { taskType: 'ai_patient_simulation' },
  'history': { taskType: 'training_history' },
  'favorites': { taskType: 'question_favorites' },
  'knowledge-favorites': { taskType: 'knowledge_favorites' },
  'study-notes': { taskType: 'study_notes' },
};

const SLUG_TO_TASK_TYPE = Object.fromEntries(
  Object.entries(WORKSHOP_SLUGS).map(([slug, intent]) => [intent.taskType, slug]),
);

// ── 个人数据子视图 slug ──
const PERSONALIZATION_VIEW_SLUGS = {
  reports: 'reports',
  'user-profile': 'profile',
  review: 'review',
  memory: 'memory',
  resources: 'resources',
};

const PERSONALIZATION_SLUG_TO_VIEW = Object.fromEntries(
  Object.entries(PERSONALIZATION_VIEW_SLUGS).map(([view, slug]) => [slug, view]),
);

// ── 顶层页面 slug（单段路径）──
const PAGE_SLUGS = {
  dashboard: '/dashboard',
  'learning-path': '/learning-path',
  assistant: '/assistant',
  knowledge: '/knowledge',
  personalization: '/personalization',
  settings: '/settings',
  'training-workshop': '/practice',
  practice: '/resources',
  // 管理入口是导航里的顶层落点，必须有 URL：缺这条映射时 intentToPath 返回
  // null，pushState 被跳过，刷新或打开收藏链接会回落到 sessionStorage 里
  // 上一次的页面，用户看到的是别的页面而不是管理入口。
  'admin-feedback': '/admin-feedback',
};

// 带子路径的页面。它们不能放进 PAGE_SLUGS（那张表只支持单段路径），
// 但同样必须有 URL：缺映射时 intentToPath 返回 null，pushState 被跳过，
// 刷新后页面由 sessionStorage 决定，用户看到的会是上一次的页面。
const CAPABILITY_ROOT_PATH = '/capabilities';
const LEARNING_PATH_TASKS_PATH = '/learning-path/tasks';

// ── 反向：路径段 → 页面 ──
const SLUG_TO_PAGE = Object.fromEntries(
  Object.entries(PAGE_SLUGS).map(([page, path]) => [path.slice(1), page]),
);

/**
 * 把 pageIntent 序列化为 URL 路径。
 * 无法映射的意图返回 null，调用方保持原有导航方式（URL 不变化）。
 */
export function intentToPath(intent) {
  const page = intent?.page;
  const params = intent?.params || {};

  // 练习工坊子模块：/practice/<slug>
  if (page === 'practice' && params.view === 'workspace' && params.taskType) {
    const slug = SLUG_TO_TASK_TYPE[params.taskType];
    if (slug) return `/practice/${slug}`;
    return '/practice';
  }

  // 练习工坊总览（training-workshop 页）
  if (page === 'training-workshop') return '/practice';

  // 教学资源（practice 默认视图）
  if (page === 'practice') return '/resources';

  // 个人数据子视图
  if (page === 'personalization') {
    const slug = PERSONALIZATION_VIEW_SLUGS[params.view || 'reports'];
    return slug === 'reports' ? '/personalization' : `/personalization/${slug}`;
  }

  // 平台核心能力详情：/capabilities/<capabilityKey>
  if (page === 'capability-detail') {
    return params.capability ? `${CAPABILITY_ROOT_PATH}/${params.capability}` : CAPABILITY_ROOT_PATH;
  }

  // 学习与复习任务：/learning-path/tasks
  if (page === 'learning-path-tasks') return LEARNING_PATH_TASKS_PATH;

  // 顶层页面
  if (PAGE_SLUGS[page]) return PAGE_SLUGS[page];

  return null;
}

/**
 * 把 URL 路径还原为 pageIntent。
 * 无法识别的路径返回 null（由调用方决定 fallback 页面）。
 */
export function pathToIntent(pathname) {
  const path = pathname || '/';
  const segments = path.split('/').filter(Boolean);

  // 根路径 → 首页
  if (segments.length === 0) return { page: 'dashboard', params: {} };

  // /practice/<slug>：练习工坊子模块
  if (segments[0] === 'practice') {
    if (segments.length === 1) return { page: 'training-workshop', params: {} };
    const workshop = WORKSHOP_SLUGS[segments[1]];
    if (workshop) {
      return {
        page: 'practice',
        params: { view: 'workspace', taskType: workshop.taskType, ...(workshop.initialMode ? { initialMode: workshop.initialMode } : {}) },
      };
    }
    return { page: 'training-workshop', params: {} };
  }

  // /personalization/<view>
  if (segments[0] === 'personalization') {
    const view = PERSONALIZATION_SLUG_TO_VIEW[segments[1]] || 'reports';
    return { page: 'personalization', params: { view } };
  }

  // /capabilities/<capabilityKey>：平台核心能力详情
  if (segments[0] === 'capabilities') {
    return {
      page: 'capability-detail',
      params: segments[1] ? { capability: segments[1] } : {},
    };
  }

  // /learning-path/tasks：学习与复习任务。必须排在单段页面表之前，
  // 否则会被 /learning-path 抢先匹配成学习路径页。
  if (segments[0] === 'learning-path' && segments[1] === 'tasks') {
    return { page: 'learning-path-tasks', params: {} };
  }

  // 顶层单段页面
  if (SLUG_TO_PAGE[segments[0]]) return { page: SLUG_TO_PAGE[segments[0]], params: {} };

  return null;
}
