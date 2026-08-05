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
  'comprehensive': { taskType: 'question_training' },
  'smart-paper': { taskType: 'paper_workspace' },
  'patient-simulation': { taskType: 'ai_patient_simulation' },
  'history': { taskType: 'training_history' },
  'favorites': { taskType: 'question_favorites' },
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
};

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

  // 顶层单段页面
  if (SLUG_TO_PAGE[segments[0]]) return { page: SLUG_TO_PAGE[segments[0]], params: {} };

  return null;
}
