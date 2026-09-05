import {
  BookMarked,
  CalendarDays,
  ClipboardCheck,
  Files,
  FolderHeart,
  HeartPulse,
  NotebookPen,
  Stethoscope,
  Target,
  UploadCloud,
} from 'lucide-react';
import { focusMinutesFromStatistics } from '../learningPlanDashboard';

export const trainingCards = [
  {
    key: 'topic_training',
    initialMode: 'objective',
    title: '知识点特训',
    description: '按教材章节定位知识点，聚焦薄弱环节精准提升。',
    icon: Stethoscope,
    tone: 'teal',
  },
  {
    key: 'paper_workspace',
    title: '智能组卷',
    description: '自选题型与题量，AI 审核生成试卷，支持练习或限时测试。',
    icon: Files,
    tone: 'green',
  },
  {
    key: 'question_training',
    initialMode: 'objective',
    title: '综合套题',
    description: '从正式题库抽取客观题与案例，模拟综合考试场景。',
    icon: ClipboardCheck,
    tone: 'emerald',
  },
  {
    key: 'ai_patient_simulation',
    title: '模拟病患',
    description: 'AI 扮演患者，练习问诊、辨证与处方全流程能力。',
    icon: HeartPulse,
    tone: 'rose',
  },
  {
    key: 'mistake_redo',
    title: '错题重做',
    description: '自动收录错题，AI 生成变式，反复巩固直至掌握。',
    icon: FolderHeart,
    tone: 'red',
  },
];

export const featuredTrainingCard = {
  key: 'special_training',
  initialMode: 'case_training',
  title: '专项特训',
  description: '覆盖核心知识点，系统巩固基础能力。',
  icon: Target,
  tone: 'cyan',
};

export const overviewTrainingCards = [
  featuredTrainingCard,
  trainingCards[0],
  trainingCards[2],
  trainingCards[1],
  ...trainingCards.slice(3),
];

export const groupedTrainingCards = [
  { key: 'daily', title: '日常练习', cards: [overviewTrainingCards[0], overviewTrainingCards[1], overviewTrainingCards[5]] },
  { key: 'paper', title: '综合模拟', cards: [overviewTrainingCards[2], overviewTrainingCards[3], overviewTrainingCards[4]] },
];

export const uploadQuestionBankCard = {
  key: 'question_workspace',
  title: '上传题库',
  description: '上传学习资料，沉淀个人专属题库。',
  icon: UploadCloud,
  tone: 'amber',
};

export const utilityCards = [
  {
    key: 'training_history',
    title: '历史记录',
    description: '按练习类型查看做过的题目和学习记录。',
    icon: FolderHeart,
    available: true,
  },
  {
    key: 'question_favorites',
    title: '我的题单',
    description: '整理重点题目与解析，构建个人题单随时回顾。',
    icon: BookMarked,
    available: true,
  },
  {
    key: 'study_notes',
    title: '笔记本',
    description: '按笔记本整理心得，沉淀学习思考。',
    icon: NotebookPen,
    available: true,
  },
];

export const DEFAULT_TRAINING_OVERVIEW_STATS = Object.freeze({
  streakDays: null,
  todayAccuracy: null,
  windowPracticeCount: null,
  todayGoal: 20,
  averageAccuracy: null,
  totalHours: null,
  totalQuestions: null,
  recentTaskKey: 'question_training',
});

export const resumableTrainingCards = [
  featuredTrainingCard,
  ...trainingCards,
  ...utilityCards,
  uploadQuestionBankCard,
  { key: 'knowledge_cards', title: '知识卡片', initialMode: 'knowledge_cards' },
];

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

export function normalizeTrainingOverviewStats(stats = {}) {
  const safeStats = stats && typeof stats === 'object' && !Array.isArray(stats) ? stats : {};
  const recentTaskKey = resumableTrainingCards.some((card) => card.key === safeStats.recentTaskKey)
    ? safeStats.recentTaskKey
    : DEFAULT_TRAINING_OVERVIEW_STATS.recentTaskKey;

  return {
    streakDays: nonNegativeNumberOrNull(safeStats.streakDays),
    todayAccuracy: percentageOrNull(safeStats.todayAccuracy),
    windowPracticeCount: nonNegativeNumberOrNull(safeStats.windowPracticeCount),
    todayGoal: nonNegativeNumberOrNull(safeStats.todayGoal) ?? DEFAULT_TRAINING_OVERVIEW_STATS.todayGoal,
    averageAccuracy: percentageOrNull(safeStats.averageAccuracy),
    totalHours: nonNegativeNumberOrNull(safeStats.totalHours),
    totalQuestions: nonNegativeNumberOrNull(safeStats.totalQuestions),
    recentTaskKey,
  };
}

const scoreAsPercentage = (value) => {
  const parsed = finiteNumberOrNull(value);
  if (parsed === null) return null;
  return Math.round((parsed <= 1 ? parsed * 100 : parsed) * 10) / 10;
};

const weakPointIdentity = (kpId, kpName) => {
  const normalizedName = String(kpName || '')
    .normalize('NFKC')
    .replace(/[\s·•，,。；;：:（）()《》<>【】[\]]+/g, '')
    .toLowerCase();
  return normalizedName ? `name:${normalizedName}` : `id:${String(kpId || '').trim().toLowerCase()}`;
};

const questionCandidates = (item) => {
  const nested = item.questions
    || item.relatedQuestions
    || item.related_questions
    || item.question_items;
  if (Array.isArray(nested) && nested.length > 0) return nested;
  if (
    item.questionId || item.question_id || item.questionVersionId || item.question_version_id
    || item.questionTitle || item.question_title || item.questionText || item.question_text
    || item.stem || item.attemptItemId || item.attempt_item_id
  ) {
    return [item];
  }
  return [];
};

const normalizeRelatedQuestion = (question, fallbackIndex) => {
  if (!question || typeof question !== 'object') return null;
  const id = String(
    question.questionId || question.question_id
    || question.questionVersionId || question.question_version_id
    || question.attemptItemId || question.attempt_item_id || '',
  ).trim();
  const title = String(
    question.questionTitle || question.question_title
    || question.title || question.stem
    || question.questionText || question.question_text || '',
  ).trim();
  const masteryScore = scoreAsPercentage(
    question.masteryScore ?? question.mastery_score ?? question.score ?? question.accuracy,
  );
  if (!id && !title) return null;
  return {
    id,
    title: title || `相关题目 ${fallbackIndex + 1}`,
    masteryScore,
  };
};

export const normalizeWeakKnowledgePoints = (payload = {}) => {
  const source = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.weak_points) ? payload.weak_points : [];
  const groups = new Map();

  source.forEach((item) => {
    if (!item || typeof item !== 'object') return;
    const kpName = String(item.kpName || item.kp_name || item.name || item.title || '').trim();
    const kpId = String(item.kpId || item.kp_id || '').trim();
    const identity = weakPointIdentity(kpId, kpName);
    if (!kpName || identity === 'id:') return;

    if (!groups.has(identity)) {
      groups.set(identity, {
        kpId,
        kpIds: [],
        kpName,
        masteryScores: [],
        reasons: [],
        relatedQuestions: [],
        sourceCount: 0,
        explicitQuestionCount: 0,
      });
    }

    const group = groups.get(identity);
    group.sourceCount += 1;
    if (kpId && !group.kpIds.includes(kpId)) group.kpIds.push(kpId);
    if (!group.kpId && kpId) group.kpId = kpId;

    const masteryScore = scoreAsPercentage(item.masteryScore ?? item.mastery_score ?? item.score);
    if (masteryScore !== null) group.masteryScores.push(masteryScore);

    const reason = String(item.reason || '').trim();
    if (reason && !group.reasons.includes(reason)) group.reasons.push(reason);

    const explicitCount = nonNegativeNumberOrNull(
      item.questionCount ?? item.question_count ?? item.attemptCount ?? item.attempt_count,
    );
    if (explicitCount !== null) group.explicitQuestionCount += explicitCount;

    questionCandidates(item).forEach((question, questionIndex) => {
      const normalized = normalizeRelatedQuestion(question, group.relatedQuestions.length + questionIndex);
      if (!normalized) return;
      const questionIdentity = String(normalized.id || normalized.title).trim().toLowerCase();
      if (!group.relatedQuestions.some((current) => String(current.id || current.title).trim().toLowerCase() === questionIdentity)) {
        group.relatedQuestions.push(normalized);
      }
    });
  });

  return Array.from(groups.values()).slice(0, 5).map((group) => {
    const masteryScore = group.masteryScores.length > 0
      ? Math.round((group.masteryScores.reduce((sum, score) => sum + score, 0) / group.masteryScores.length) * 10) / 10
      : null;
    const inferredQuestionCount = group.relatedQuestions.length || group.sourceCount;
    return {
      kpId: group.kpId,
      kpIds: group.kpIds,
      kpName: group.kpName,
      masteryScore,
      reason: group.reasons[0] || '近期练习掌握度偏低，建议优先巩固。',
      reasons: group.reasons,
      questionCount: Math.max(inferredQuestionCount, group.explicitQuestionCount),
      relatedQuestions: group.relatedQuestions,
      sourceCount: group.sourceCount,
    };
  });
};

const recentTaskKeyFromActivity = (activity = {}) => {
  const activityType = String(activity.activity_type || '').trim().toLowerCase();
  const resourceType = String(activity.resource_type || '').trim().toLowerCase();
  const taskType = String(activity.task_type || '').trim().toLowerCase();
  const exactMappings = new Map([
    ['training_workspace_task:question_training', 'question_training'],
    ['training_workspace_task:special_training', 'special_training'],
    ['training_workspace_task:topic_training', 'topic_training'],
    ['training_workspace_task:mistake_redo', 'mistake_redo'],
    ['training_workspace_task:mistake_variation', 'mistake_variation'],
    ['training_workspace_task:paper_generation', 'paper_workspace'],
    ['question_attempt:question', 'question_training'],
    ['question_attempt:user_question', 'question_training'],
    ['paper_submission:paper', 'paper_workspace'],
    ['case_training:case_session', 'ai_patient_simulation'],
    ['case_training:simulated_patient_session', 'ai_patient_simulation'],
    ['question_favorite:question', 'question_favorites'],
    ['study_note:note', 'study_notes'],
  ]);
  return exactMappings.get(`${activityType}:${resourceType}`)
    || (activityType === 'training_workspace_task' ? exactMappings.get(`${activityType}:${taskType}`) : null)
    || (activityType === 'question_attempt' && resourceType === 'question_favorites' ? 'question_favorites' : null)
    || null;
};

const isScoredTrainingActivity = (activity = {}) => {
  const activityType = String(activity.activity_type || '').trim().toLowerCase();
  const resourceType = String(activity.resource_type || '').trim().toLowerCase();
  return (
    (activityType === 'question_attempt' && ['question', 'user_question'].includes(resourceType))
    || (activityType === 'paper_submission' && resourceType === 'paper')
    || (activityType === 'case_training' && ['case_session', 'simulated_patient_session'].includes(resourceType))
    || (activityType === 'training_workspace_task' && String(activity.completion_status || '').toLowerCase() === 'completed')
  );
};

export const buildTrainingOverviewStats = (statistics = {}, activitySummary = {}, checkin = {}) => {
  if (statistics?.metrics && typeof statistics.metrics === 'object') {
    const metrics = statistics.metrics;
    const metricValue = (name, fallback = null) => {
      const entry = metrics[name];
      if (!entry || entry.available === false) return fallback;
      return entry.value ?? fallback;
    };
    const recentActivities = Array.isArray(statistics.recent_activities)
      ? statistics.recent_activities
      : [];
    const recentTask = recentActivities.find((activity) => recentTaskKeyFromActivity(activity));
    const focusMinutes = metricValue('focus_minutes');
    return normalizeTrainingOverviewStats({
      streakDays: metricValue('checkin_streak'),
      todayAccuracy: scoreAsPercentage(metricValue('today_score_rate')),
      windowPracticeCount: metricValue('questions_completed'),
      todayGoal: DEFAULT_TRAINING_OVERVIEW_STATS.todayGoal,
      averageAccuracy: scoreAsPercentage(metricValue('score_rate')),
      totalHours: focusMinutes === null ? null : Math.round((Number(focusMinutes) / 60) * 10) / 10,
      totalQuestions: metricValue('questions_completed_lifetime'),
      recentTaskKey: recentTaskKeyFromActivity(recentTask) || DEFAULT_TRAINING_OVERVIEW_STATS.recentTaskKey,
    });
  }
  const lifetime = statistics?.lifetime || {};
  const currentWindow = statistics?.current_window || {};
  const recentActivities = Array.isArray(activitySummary?.recent_activities)
    ? activitySummary.recent_activities
    : [];
  const latestResumableActivity = recentActivities.find(recentTaskKeyFromActivity);
  const focusMinutes = focusMinutesFromStatistics(statistics);
  const todayStr = String(activitySummary?.calculated_at || new Date().toISOString()).slice(0, 10);
  const todayActivities = recentActivities.filter(
    (activity) => isScoredTrainingActivity(activity)
      && String(activity.timestamp || activity.created_at || '').slice(0, 10) === todayStr,
  );
  const todayScores = todayActivities.map((activity) => finiteNumberOrNull(activity?.score))
    .filter((score) => score !== null);
  const todayAccuracy = todayScores.length > 0
    ? todayScores.reduce((sum, score) => sum + (score <= 1 ? score * 100 : score), 0) / todayScores.length
    : null;

  return normalizeTrainingOverviewStats({
    streakDays: nonNegativeNumberOrNull(checkin?.streak),
    todayAccuracy: todayAccuracy !== null ? Math.round(todayAccuracy * 10) / 10 : null,
    windowPracticeCount: nonNegativeNumberOrNull(currentWindow.questions_completed),
    todayGoal: DEFAULT_TRAINING_OVERVIEW_STATS.todayGoal,
    averageAccuracy: scoreAsPercentage(currentWindow.score_rate),
    totalHours: focusMinutes === null ? null : Math.round((focusMinutes / 60) * 10) / 10,
    totalQuestions: nonNegativeNumberOrNull(lifetime.questions_completed),
    recentTaskKey: recentTaskKeyFromActivity(latestResumableActivity)
      || DEFAULT_TRAINING_OVERVIEW_STATS.recentTaskKey,
  });
};

export const workspaceTitles = {
  question_training: '综合套题',
  special_training: '专项特训',
  topic_training: '知识点特训',
  ai_patient_simulation: '模拟病患',
  mistake_variation: '错题库',
  mistake_redo: '错题重做',
  training_history: '历史记录',
  paper_workspace: '智能组卷',
  knowledge_cards: '知识卡片',
  question_favorites: '我的题单',
  knowledge_favorites: '知识点收藏',
  study_notes: '笔记本',
};

const legacyTaskTypes = {
  practice_grading: { taskType: 'question_training', initialMode: 'objective' },
  case_training: { taskType: 'ai_patient_simulation', initialMode: 'ai_patient_simulation' },
  knowledge_cards: { taskType: 'knowledge_cards', initialMode: 'knowledge_cards' },
  knowledge_card_generation: { taskType: 'knowledge_cards', initialMode: 'knowledge_cards' },
  paper_generation: { taskType: 'paper_workspace', initialMode: 'paper_workspace' },
};

export const normalizeTaskIntent = (taskType = '') => {
  if (legacyTaskTypes[taskType]) return legacyTaskTypes[taskType];
  const normalizedTaskType = taskType || 'question_training';
  const module = resumableTrainingCards.find((card) => card.key === normalizedTaskType);
  return {
    taskType: normalizedTaskType,
    initialMode: module?.initialMode || normalizedTaskType,
  };
};

export const getTrainingModule = (taskType) => resumableTrainingCards.find((card) => card.key === taskType)
  || { key: taskType, title: workspaceTitles[taskType] || '练习任务', initialMode: taskType };

export const trainingIcons = { CalendarDays, Target };
