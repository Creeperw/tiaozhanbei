import {
  BookMarked,
  ClipboardCheck,
  Files,
  FolderHeart,
  HeartPulse,
  Stethoscope,
  Target,
} from 'lucide-react';

export const PRIMARY_TRAINING_GROUPS = [
  {
    title: '日常练习',
    cards: [
      {
        key: 'special_training',
        initialMode: 'case_training',
        title: '专项特训',
        description: '覆盖核心知识点，通过案例简答系统巩固基础能力。',
        icon: Target,
        tone: 'cyan',
      },
      {
        key: 'topic_training',
        initialMode: 'objective',
        title: '知识点特训',
        description: '按教材章节定位知识点，聚焦薄弱环节精准提升。',
        icon: Stethoscope,
        tone: 'teal',
      },
      {
        key: 'mistake_redo',
        title: '错题重做',
        description: '回到历史错题反复巩固，直到真正掌握。',
        icon: FolderHeart,
        tone: 'red',
      },
    ],
  },
  {
    title: '综合模拟',
    cards: [
      {
        key: 'question_training',
        initialMode: 'objective',
        title: '综合套题',
        description: '从正式题库抽取客观题与案例，模拟综合考试场景。',
        icon: ClipboardCheck,
        tone: 'emerald',
      },
      {
        key: 'paper_workspace',
        title: '智能组卷',
        description: '自选题型与题量，AI 审核生成试卷，支持练习或限时测试。',
        icon: Files,
        tone: 'green',
      },
      {
        key: 'ai_patient_simulation',
        title: '模拟病患',
        description: 'AI 扮演患者，练习问诊、辨证与处方全流程能力。',
        icon: HeartPulse,
        tone: 'rose',
      },
    ],
  },
];

export const UTILITY_CARDS = [
  {
    key: 'training_history',
    title: '历史记录',
    description: '按练习类型查看做过的题目和学习记录。',
    icon: FolderHeart,
  },
  {
    key: 'question_favorites',
    title: '我的题单',
    description: '集中回顾已收藏的重点题目与解析。',
    icon: BookMarked,
  },
];

export const DEFAULT_TRAINING_OVERVIEW_STATS = Object.freeze({
  windowPracticeCount: null,
  todayGoal: 20,
  averageAccuracy: null,
  totalHours: null,
  totalQuestions: null,
  recentTaskKey: 'question_training',
});

const finiteNumberOrNull = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const nonNegativeNumberOrNull = (value) => {
  const parsed = finiteNumberOrNull(value);
  return parsed === null ? null : Math.max(0, parsed);
};

const percentageOrNull = (value) => {
  const parsed = finiteNumberOrNull(value);
  return parsed === null ? null : Math.min(100, Math.max(0, parsed));
};

const scoreAsPercentage = (value) => {
  const parsed = finiteNumberOrNull(value);
  if (parsed === null) return null;
  return Math.round((parsed <= 1 ? parsed * 100 : parsed) * 10) / 10;
};

const allCards = [...PRIMARY_TRAINING_GROUPS.flatMap((group) => group.cards), ...UTILITY_CARDS];

const recentTaskKeyFromActivity = (activity = {}) => {
  const source = `${activity.resource_type || ''} ${activity.activity_type || ''}`.toLowerCase();
  if (source.includes('favorite')) return 'question_favorites';
  if (source.includes('history')) return 'training_history';
  if (source.includes('mistake')) return 'mistake_redo';
  if (source.includes('paper')) return 'paper_workspace';
  if (source.includes('patient') || source.includes('case')) return 'ai_patient_simulation';
  if (source.includes('topic')) return 'topic_training';
  if (source.includes('special') || source.includes('knowledge_point')) return 'special_training';
  return source.includes('question') || source.includes('practice') ? 'question_training' : null;
};

const focusMinutesFromStatistics = (statistics = {}) => {
  const lifetime = statistics?.lifetime || {};
  return nonNegativeNumberOrNull(lifetime.focus_minutes ?? lifetime.total_focus_minutes);
};

export function normalizeTrainingOverviewStats(stats = {}) {
  const safeStats = stats && typeof stats === 'object' && !Array.isArray(stats) ? stats : {};
  return {
    windowPracticeCount: nonNegativeNumberOrNull(safeStats.windowPracticeCount),
    todayGoal: nonNegativeNumberOrNull(safeStats.todayGoal) ?? DEFAULT_TRAINING_OVERVIEW_STATS.todayGoal,
    averageAccuracy: percentageOrNull(safeStats.averageAccuracy),
    totalHours: nonNegativeNumberOrNull(safeStats.totalHours),
    totalQuestions: nonNegativeNumberOrNull(safeStats.totalQuestions),
    recentTaskKey: allCards.some((card) => card.key === safeStats.recentTaskKey)
      ? safeStats.recentTaskKey
      : DEFAULT_TRAINING_OVERVIEW_STATS.recentTaskKey,
  };
}

export function buildTrainingOverviewStats(statistics = {}, activitySummary = {}) {
  const lifetime = statistics?.lifetime || {};
  const currentWindow = statistics?.current_window || {};
  const recentActivities = Array.isArray(activitySummary?.recent_activities)
    ? activitySummary.recent_activities
    : [];
  const focusMinutes = focusMinutesFromStatistics(statistics);
  return {
    windowPracticeCount: nonNegativeNumberOrNull(currentWindow.questions_completed),
    todayGoal: DEFAULT_TRAINING_OVERVIEW_STATS.todayGoal,
    averageAccuracy: scoreAsPercentage(currentWindow.score_rate),
    totalHours: focusMinutes === null ? null : Math.round((focusMinutes / 60) * 10) / 10,
    totalQuestions: nonNegativeNumberOrNull(lifetime.questions_completed),
    recentTaskKey: recentTaskKeyFromActivity(recentActivities.find(recentTaskKeyFromActivity))
      || DEFAULT_TRAINING_OVERVIEW_STATS.recentTaskKey,
  };
}

const legacyTaskTypes = {
  practice_grading: { taskType: 'question_training', initialMode: 'objective' },
  case_training: { taskType: 'ai_patient_simulation', initialMode: 'ai_patient_simulation' },
  knowledge_cards: { taskType: 'knowledge_cards', initialMode: 'knowledge_cards' },
  knowledge_card_generation: { taskType: 'knowledge_cards', initialMode: 'knowledge_cards' },
  paper_generation: { taskType: 'paper_workspace', initialMode: 'paper_workspace' },
};

export function normalizeTaskIntent(taskType = '') {
  return legacyTaskTypes[taskType] || {
    taskType: taskType || 'question_training',
    initialMode: taskType,
  };
}

export const WORKSPACE_TITLES = {
  question_training: '综合套题',
  special_training: '专项特训',
  topic_training: '知识点特训',
  ai_patient_simulation: '模拟病患',
  mistake_redo: '错题重做',
  training_history: '历史记录',
  paper_workspace: '智能组卷',
  knowledge_cards: '知识卡片',
  question_favorites: '我的题单',
};

export function findTrainingCard(key) {
  return allCards.find((card) => card.key === key) || allCards[0];
}
