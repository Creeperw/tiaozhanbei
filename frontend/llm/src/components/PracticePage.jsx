import React, { useEffect, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  BookMarked,
  CalendarDays,
  ChevronRight,
  CirclePlay,
  ClipboardCheck,
  Clock3,
  FileText,
  Files,
  FolderHeart,
  HeartPulse,
  NotebookPen,
  Stethoscope,
  Target,
  TrendingUp,
  UploadCloud,
} from 'lucide-react';
import { createLearningFocusTracker } from '../learningFocusTracker.js';
import { fetchJsonWithAuthFallback } from '../utils/api';
import QuestionTrainingPanel from './QuestionTrainingPanel';
import QualificationPaperPanel from './QualificationPaperPanel';
import SimulatedPatientChat from './SimulatedPatientChat';
import MistakeRedoPanel from './MistakeRedoPanel';
import MistakeVariationPanel from './MistakeVariationPanel';
import TrainingHistoryPanel from './TrainingHistoryPanel';
import PaperGenerationPanel from './PaperGenerationPanel';
import SmartPaperPanel from './SmartPaperPanel';
import QuestionWorkspacePage from './QuestionWorkspacePage';
import KnowledgeCardLibrary from './KnowledgeCardLibrary';
import KnowledgePointTrainingHub from './KnowledgePointTrainingHub';
import QuestionFavoritesPanel from './QuestionFavoritesPanel';
import StudyNotesPanel from './StudyNotesPanel';
import { practiceContextFromIntent } from './exam-atlas/examAtlasPageContext';
import { focusMinutesFromStatistics } from './learningPlanDashboard';

const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

function displayValue(value, depth = 0) {
  if (value === null || value === undefined || value === '') return '暂无';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (depth >= 2) return '已省略嵌套内容';
  if (Array.isArray(value)) {
    if (value.length === 0) return '暂无';
    const visibleItems = value.slice(0, 5).map((item) => displayValue(item, depth + 1));
    return `${visibleItems.join('；')}${value.length > 5 ? '；等' : ''}`;
  }
  if (isRecord(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return '暂无';
    const visibleEntries = entries.slice(0, 5)
      .map(([key, item]) => `${key}：${displayValue(item, depth + 1)}`);
    return `${visibleEntries.join('；')}${entries.length > 5 ? '；等' : ''}`;
  }
  return '暂无';
}

function contentSections(content) {
  if (!isRecord(content)) return [];
  const sections = Array.isArray(content.sections) ? content.sections : content.cards;
  if (!Array.isArray(sections)) return [];
  return sections.slice(0, 8).map((section, index) => ({
    key: section?.id || section?.key || `${index}-${section?.title || 'section'}`,
    title: typeof section?.title === 'string' ? section.title : `内容 ${index + 1}`,
    body: displayValue(section?.body ?? section?.content ?? section?.full ?? section),
  }));
}

function EmptyState({ children }) {
  return <p className="[overflow-wrap:anywhere] py-5 text-[15px] leading-6 text-slate-500">{children}</p>;
}

function KnowledgeCardContent({ content }) {
  const front = content.front;
  const back = content.back;
  const memoryAnchor = content.memory_anchor;
  const hasCardFields = front !== undefined || back !== undefined || memoryAnchor !== undefined;

  if (!hasCardFields) return null;
  return (
    <div className="space-y-4">
      {front !== undefined && <div><h4 className="text-sm font-semibold text-slate-900">正面</h4><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(front)}</p></div>}
      {back !== undefined && <div><h4 className="text-sm font-semibold text-slate-900">背面</h4><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(back)}</p></div>}
      {memoryAnchor !== undefined && <div className="border-l-2 border-emerald-300 pl-4"><h4 className="text-sm font-semibold text-slate-900">记忆锚点</h4><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(memoryAnchor)}</p></div>}
    </div>
  );
}

function ArtifactResult({ taskResult }) {
  const artifact = isRecord(taskResult?.artifact) ? taskResult.artifact : {};
  const content = artifact.content;
  const artifactType = artifact.artifact_type;

  if (!artifactType) {
    return <EmptyState>提交任务后，训练产物会在这里显示。</EmptyState>;
  }

  if (!isRecord(content)) {
    return (
      <div className="space-y-3">
        <h3 className="text-lg font-semibold text-slate-950">{displayValue(artifact.title || taskResult?.title)}</h3>
        <p className="mt-2 break-words whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(content)}</p>
      </div>
    );
  }

  if (artifactType === 'grading_result') {
    const grading = isRecord(content.grading) ? content.grading : {};
    const remediation = isRecord(content.remediation) ? content.remediation : {};
    const reviewCard = isRecord(remediation.review_card) ? remediation.review_card : {};
    const variants = Array.isArray(remediation.variant_questions) ? remediation.variant_questions.slice(0, 5) : [];
    const hasContent = Object.keys(grading).length > 0 || Object.keys(reviewCard).length > 0 || variants.length > 0;

    if (!hasContent) return <EmptyState>批改已返回，但暂未提供可展示的详细产物。</EmptyState>;

    return (
      <div className="space-y-5">
        <h3 className="text-lg font-semibold text-slate-950">{displayValue(artifact.title || taskResult?.title)}</h3>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200 pb-4 text-sm text-slate-700">
          <span className="font-semibold text-slate-950">得分：{displayValue(grading.score)} / 100</span>
          <span>{grading.is_correct ? '判定：回答正确' : '判定：需要复盘'}</span>
          {grading.error_type && <span>错因：{displayValue(grading.error_type)}</span>}
        </div>
        <div>
          <h4 className="text-sm font-semibold text-slate-900">分析</h4>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(grading.analysis)}</p>
        </div>
        {grading.question_explanation && (
          <div className="border-l-2 border-sky-300 pl-4">
            <h4 className="text-sm font-semibold text-slate-900">题目解析</h4>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(grading.question_explanation)}</p>
            <p className="mt-2 text-xs text-slate-500">
              解析来源：{grading.explanation_source === 'generated_on_first_attempt' ? '首次作答自动生成并保存' : '题目解析库'}
            </p>
          </div>
        )}
        {(reviewCard.title || reviewCard.content) && (
          <div className="border-l-2 border-emerald-300 pl-4">
            <h4 className="text-sm font-semibold text-slate-900">{displayValue(reviewCard.title)}</h4>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(reviewCard.content)}</p>
          </div>
        )}
        {variants.length > 0 && (
          <div>
            <h4 className="text-sm font-semibold text-slate-900">变式练习</h4>
            <ol className="mt-2 space-y-2 text-sm leading-6 text-slate-700">
              {variants.map((item, index) => <li key={item?.key || index}>{index + 1}. {displayValue(item?.stem ?? item)}</li>)}
            </ol>
          </div>
        )}
      </div>
    );
  }

  if (artifactType === 'handout' || artifactType === 'knowledge_card') {
    const sections = contentSections(content);
    const fallback = content.body ?? content.content ?? content.full ?? content.summary;
    const knowledgeCard = artifactType === 'knowledge_card' ? <KnowledgeCardContent content={content} /> : null;
    return (
      <div className="space-y-5">
        <div>
          <h3 className="text-lg font-semibold text-slate-950">{displayValue(artifact.title || taskResult?.title)}</h3>
          {taskResult?.summary && <p className="mt-2 text-sm leading-6 text-slate-600">{displayValue(taskResult.summary)}</p>}
        </div>
        {knowledgeCard || (sections.length > 0 ? sections.map((section) => (
          <div key={section.key} className="border-l-2 border-emerald-200 pl-4">
            <h4 className="text-sm font-semibold text-slate-900">{section.title}</h4>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{section.body}</p>
          </div>
        )) : fallback !== undefined ? (
          <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(fallback)}</p>
        ) : <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(content)}</p>)}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <h3 className="text-lg font-semibold text-slate-950">{displayValue(artifact.title || taskResult?.title)}</h3>
      <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(content)}</p>
    </div>
  );
}

const trainingCards = [
  {
    key: 'topic_training',
    initialMode: 'objective',
    title: '专题训练',
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
    description: 'AI 扮演患者，训练问诊、辨证与处方全流程能力。',
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

const featuredTrainingCard = {
  key: 'special_training',
  initialMode: 'case_training',
  title: '专项训练',
  description: '覆盖核心知识点，系统巩固基础能力。',
  icon: Target,
  tone: 'cyan',
};

const overviewTrainingCards = [featuredTrainingCard, ...trainingCards];

const uploadQuestionBankCard = {
  key: 'question_workspace',
  title: '上传题库',
  description: '上传学习资料，沉淀个人专属题库。',
  icon: UploadCloud,
  tone: 'amber',
};

const utilityCards = [
  {
    key: 'training_history',
    title: '历史记录',
    description: '按训练类型查看做过的题目和学习记录。',
    icon: FolderHeart,
    available: true,
  },
  {
    key: 'question_favorites',
    title: '收藏夹',
    description: '收藏重点题目与解析，构建个人知识库随时回顾。',
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

const DEFAULT_TRAINING_OVERVIEW_STATS = Object.freeze({
  streakDays: null,
  todayAccuracy: null,
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

const resumableTrainingCards = [
  featuredTrainingCard,
  ...trainingCards,
  ...utilityCards,
  uploadQuestionBankCard,
  {
    key: 'knowledge_cards',
    title: '知识卡片',
    initialMode: 'knowledge_cards',
  },
];

function normalizeTrainingOverviewStats(stats = {}) {
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

const recentTaskKeyFromActivity = (activity = {}) => {
  const source = `${activity.resource_type || ''} ${activity.activity_type || ''}`.toLowerCase();
  if (source.includes('favorite')) return 'question_favorites';
  if (source.includes('note')) return 'study_notes';
  if (source.includes('mistake')) return 'mistake_variation';
  if (source.includes('paper')) return 'paper_workspace';
  if (source.includes('patient') || source.includes('case')) return 'ai_patient_simulation';
  if (source.includes('topic')) return 'topic_training';
  if (source.includes('special') || source.includes('knowledge_point')) return 'special_training';
  return source.includes('question') || source.includes('practice') ? 'question_training' : null;
};

const isScoredTrainingActivity = (activity = {}) => {
  const activityType = String(activity.activity_type || '').toLowerCase();
  return ['question', 'practice', 'paper', 'case', 'grading', 'exam']
    .some((type) => activityType.includes(type));
};

const buildTrainingOverviewStats = (statistics = {}, activitySummary = {}, checkin = {}) => {
  const lifetime = statistics?.lifetime || {};
  const currentWindow = statistics?.current_window || {};
  const recentActivities = Array.isArray(activitySummary?.recent_activities)
    ? activitySummary.recent_activities
    : [];
  const latestResumableActivity = recentActivities.find(recentTaskKeyFromActivity);
  const focusMinutes = focusMinutesFromStatistics(statistics);

  // Today accuracy — filter activities from today only
  const todayStr = String(activitySummary?.calculated_at || new Date().toISOString()).slice(0, 10);
  const todayActivities = recentActivities.filter(
    (a) => isScoredTrainingActivity(a) && String(a.timestamp || a.created_at || '').slice(0, 10) === todayStr,
  );
  const todayScores = todayActivities.map((a) => finiteNumberOrNull(a?.score)).filter((s) => s !== null);
  const todayAccuracy = todayScores.length > 0
    ? todayScores.reduce((sum, s) => sum + (s <= 1 ? s * 100 : s), 0) / todayScores.length
    : null;

  return {
    streakDays: nonNegativeNumberOrNull(checkin?.streak),
    todayAccuracy: todayAccuracy !== null ? Math.round(todayAccuracy * 10) / 10 : null,
    windowPracticeCount: nonNegativeNumberOrNull(currentWindow.questions_completed),
    todayGoal: DEFAULT_TRAINING_OVERVIEW_STATS.todayGoal,
    averageAccuracy: scoreAsPercentage(currentWindow.score_rate),
    totalHours: focusMinutes === null ? null : Math.round((focusMinutes / 60) * 10) / 10,
    totalQuestions: nonNegativeNumberOrNull(lifetime.questions_completed),
    recentTaskKey: recentTaskKeyFromActivity(latestResumableActivity)
      || DEFAULT_TRAINING_OVERVIEW_STATS.recentTaskKey,
  };
};

const workspaceTitles = {
  question_training: '综合套题',
  special_training: '专项训练',
  topic_training: '专题训练',
  ai_patient_simulation: '模拟病患',
  mistake_variation: '错题库',
  mistake_redo: '错题重做',
  training_history: '历史记录',
  paper_workspace: '智能组卷',
  knowledge_cards: '知识卡片',
  question_favorites: '收藏夹',
  study_notes: '笔记本',
};

const legacyTaskTypes = {
  practice_grading: { taskType: 'question_training', initialMode: 'objective' },
  case_training: { taskType: 'ai_patient_simulation', initialMode: 'ai_patient_simulation' },
  knowledge_cards: { taskType: 'knowledge_cards', initialMode: 'knowledge_cards' },
  knowledge_card_generation: { taskType: 'knowledge_cards', initialMode: 'knowledge_cards' },
  paper_generation: { taskType: 'paper_workspace', initialMode: 'paper_workspace' },
};

const normalizeTaskIntent = (taskType = '') => legacyTaskTypes[taskType] || {
  taskType: taskType || 'question_training',
  initialMode: taskType,
};

function TrainingBannerIllustration() {
  const [hint, setHint] = useState('快来跟我一起练习吧');
  const phrases = ['快来跟我一起练习吧', '今天也要加油哦', '温故而知新', '学而时习之', '坚持就是胜利'];
  return (
    <div className="practice-overview__illustration">
      <span className="practice-overview__character-note" aria-live="polite">{hint}</span>
      <button
        type="button"
        className="practice-overview__character-button"
        onClick={() => {
          const next = phrases.filter((phrase) => phrase !== hint);
          setHint(next[Math.floor(Math.random() * next.length)]);
        }}
        aria-label="和李时珍互动"
      >
        <img
          className="practice-overview__character"
          src="/assistant-character/lizhizhen-center-cutout.png"
          alt=""
        />
      </button>
    </div>
  );
}

function OverviewMetricCard({ icon: Icon, label, value, hint, tone = 'green' }) {
  return (
    <div className={`practice-overview__hero-metric practice-overview__hero-metric--${tone}`}>
      <span className="practice-overview__metric-icon">{React.createElement(Icon, { 'aria-hidden': true, size: 21 })}</span>
      <span className="practice-overview__metric-copy">
        <small>{label}</small>
        <strong>{value}</strong>
        <em>{hint}</em>
      </span>
    </div>
  );
}

function OverviewSummaryMetric({ icon: Icon, label, value, hint, tone = 'green', progress }) {
  return (
    <div className={`practice-overview__summary-metric practice-overview__summary-metric--${tone}`}>
      <span className="practice-overview__summary-icon">{React.createElement(Icon, { 'aria-hidden': true, size: 22 })}</span>
      <span className="practice-overview__summary-copy">
        <small>{label}</small>
        <strong>{value}</strong>
        <em>{hint}</em>
        {progress !== undefined && (
          <span
            className="practice-overview__progress"
            role="progressbar"
            aria-label="今日目标完成度"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
          >
            <span style={{ width: `${progress}%` }} />
          </span>
        )}
      </span>
    </div>
  );
}

function TrainingOverview({ onOpenModule, overviewStats }) {
  const stats = normalizeTrainingOverviewStats(overviewStats);
  const recentCard = resumableTrainingCards
    .find((card) => card.key === stats.recentTaskKey) || trainingCards[2];
  const formatPercent = (value) => value === null ? '--' : `${value}%`;
  const formatDays = (value) => value === null ? '--' : `${value} 天`;
  const formatHours = (value) => value === null ? '--' : `${value} 小时`;
  const formatQuestions = (value) => value === null ? '累计练习待接入' : `累计练习 ${value} 题`;

  return (
    <section className="practice-overview" aria-labelledby="practice-overview-title">
      <header className="practice-overview__banner">
        <div className="practice-overview__hero-copy">
          <span className="practice-overview__greeting"><span>准备开始今天的训练</span> <span aria-hidden="true">🌿</span></span>
          <h1 id="practice-overview-title">训练工坊</h1>
          <p>今日建议完成 <strong>{stats.todayGoal}</strong> 道综合题，预计 <strong>15</strong> 分钟</p>
          <div className="practice-overview__hero-actions">
            <button type="button" className="practice-overview__primary-action" onClick={() => onOpenModule(featuredTrainingCard)}>
              <CirclePlay aria-hidden="true" size={18} />开始今日训练
            </button>
            <button type="button" className="practice-overview__secondary-action" onClick={() => onOpenModule(recentCard)}>
              <Clock3 aria-hidden="true" size={17} />继续上次练习
            </button>
          </div>
        </div>
        <div className="practice-overview__hero-metrics">
          <OverviewMetricCard
            icon={CalendarDays}
            label="连续学习"
            value={formatDays(stats.streakDays)}
            hint="再接再厉，保持节奏"
          />
          <OverviewMetricCard
            icon={TrendingUp}
            label="今日正确率"
            value={formatPercent(stats.todayAccuracy)}
            hint="稳保持，稳步提升"
            tone="mint"
          />
        </div>
        <TrainingBannerIllustration />
      </header>

      <div className="practice-overview__layout">
        <section className="practice-overview__main" aria-label="训练模块">
          <div className="practice-overview__training-grid">
            {overviewTrainingCards.map((card) => {
              const Icon = card.icon;
              return (
                <button
                  key={card.key}
                  type="button"
                  className={`practice-overview__training-card practice-overview__training-card--${card.tone}`}
                  onClick={() => onOpenModule(card)}
                >
                  <span className="practice-overview__card-icon"><Icon aria-hidden="true" size={26} /></span>
                  <span className="practice-overview__card-copy">
                    <strong>{card.title}</strong>
                    <small>{card.description}</small>
                  </span>
                  <ChevronRight className="practice-overview__card-arrow" aria-hidden="true" size={19} />
                </button>
              );
            })}
          </div>

          <section className="practice-overview__summary" role="region" aria-label="学习概览">
            <OverviewSummaryMetric
              icon={CalendarDays}
              label="近 30 天练习"
              value={stats.windowPracticeCount === null ? '--' : `${stats.windowPracticeCount} 题`}
              hint="正式审核完成题目"
            />
            <OverviewSummaryMetric
              icon={Target}
              label="平均正确率"
              value={formatPercent(stats.averageAccuracy)}
              hint={stats.averageAccuracy === null ? '暂无数据' : '继续保持'}
            />
            <OverviewSummaryMetric
              icon={Clock3}
              label="累计学习"
              value={formatHours(stats.totalHours)}
              hint={formatQuestions(stats.totalQuestions)}
              tone="purple"
            />
          </section>
        </section>

        <aside className="practice-overview__utilities" aria-label="学习工具">
          <div className="practice-overview__utility-list">
            {utilityCards.filter((card) => card.available).map((card) => {
              const Icon = card.icon;
              return (
                <button
                  key={card.title}
                  type="button"
                  className="practice-overview__utility-card"
                  onClick={() => onOpenModule(card)}
                >
                  <span className="practice-overview__utility-icon"><Icon aria-hidden="true" size={22} /></span>
                  <span><strong>{card.title}</strong><small>{card.description}</small></span>
                  <ChevronRight aria-hidden="true" size={18} />
                </button>
              );
            })}
            <button
              type="button"
              className="practice-overview__utility-card practice-overview__utility-card--upload"
              onClick={() => onOpenModule(uploadQuestionBankCard)}
            >
              <span className="practice-overview__utility-icon"><UploadCloud aria-hidden="true" size={22} /></span>
              <span><strong>上传题库</strong><small>上传学习资料，沉淀个人专属题库。</small><em>支持 Word / Excel / TXT · 智能解析</em></span>
              <ChevronRight aria-hidden="true" size={18} />
            </button>
          </div>
        </aside>
      </div>
    </section>
  );
}

export default function PracticePage({
  navigationContext = {},
  overviewStats,
  onNavigate,
}) {
  const selectedKnowledgePoint = practiceContextFromIntent(navigationContext);
  const initialTaskIntent = normalizeTaskIntent(navigationContext.taskType);
  const [activeTaskType, setActiveTaskType] = useState(() => initialTaskIntent.taskType);
  const [activeInitialMode, setActiveInitialMode] = useState(() => initialTaskIntent.initialMode);
  const [view, setView] = useState(() => (navigationContext.taskType || navigationContext.view === 'workspace' ? 'workspace' : 'overview'));
  const [loadedOverviewStats, setLoadedOverviewStats] = useState(DEFAULT_TRAINING_OVERVIEW_STATS);
  const taskItemId = navigationContext.taskItemId || navigationContext.task_item_id || '';
  const returnIntent = navigationContext.returnTo;
  const returnLabel = returnIntent?.page === 'assistant'
    ? '返回智能助教'
    : returnIntent?.page === 'learning-path'
      ? '返回学习路径'
    : returnIntent?.page === 'qualification-route'
      ? '返回今日学习'
      : returnIntent?.page === 'personalization' && returnIntent?.params?.view === 'reports'
        ? '返回学情报告'
        : returnIntent?.page === 'practice' && returnIntent?.params?.view === 'textbook-chapters'
          ? '返回教材学习'
      : '返回训练工坊';

  const leaveWorkspace = () => {
    if (returnIntent && onNavigate) {
      onNavigate(returnIntent);
      return;
    }
    setView('overview');
  };

  useEffect(() => {
    if (overviewStats !== undefined) return undefined;

    let active = true;
    const requestOverview = (path) => fetchJsonWithAuthFallback({
      paths: [path],
      fallback: {},
    }).then((result) => result.data).catch(() => ({}));

    // Auto check-in when visiting training workshop
    fetchJsonWithAuthFallback({ paths: ['/v1/checkin'], fallback: {}, options: { method: 'POST', body: '{}' } }).catch(() => {});

    Promise.all([
      requestOverview('/v1/learning-statistics/overview?days=30'),
      requestOverview('/v1/learning-activity/summary?days=7&recent_limit=100'),
      requestOverview('/v1/checkin'),
    ]).then(([statistics, activitySummary, checkin]) => {
      if (active) {
        setLoadedOverviewStats(buildTrainingOverviewStats(statistics, activitySummary, checkin));
      }
    });

    return () => {
      active = false;
    };
  }, [overviewStats]);

  useEffect(() => {
    const request = async (path, body) => {
      const result = await fetchJsonWithAuthFallback({
        paths: [path],
        options: {
          method: 'POST',
          keepalive: true,
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        },
      });
      return result.data;
    };
    const tracker = createLearningFocusTracker({
      request,
      resourceType: 'training_workspace',
      resourceId: 'practice',
    });
    tracker.start().catch(() => {});
    return () => {
      tracker.stop().catch(() => {});
    };
  }, []);

  const handlePracticeResult = (result, question) => {
    const grading = result?.grading || {};
    void grading;
    void question;
  };

  const openWorkshopModule = ({ key, initialMode }) => {
    setActiveTaskType(key);
    setActiveInitialMode(initialMode || key);
    setView('workspace');
  };

  if (view === 'overview') {
    return (
      <TrainingOverview
        onOpenModule={openWorkshopModule}
        overviewStats={overviewStats ?? loadedOverviewStats}
      />
    );
  }

  if (activeTaskType === 'question_workspace') {
    return (
      <div className="space-y-5 text-slate-800">
        <div className="practice-workspace__toolbar">
          <button type="button" className="practice-workspace__back" onClick={leaveWorkspace}>
            <ArrowLeft aria-hidden="true" size={18} />{returnLabel}
          </button>
        </div>
        <QuestionWorkspacePage />
      </div>
    );
  }

  const isSP = activeTaskType === 'ai_patient_simulation';

  if (isSP) {
    return (
      <div className="practice-workspace practice-workspace--ai_patient_simulation">
        <SimulatedPatientChat onBack={leaveWorkspace} />
      </div>
    );
  }

  return (
    <div className={`practice-workspace practice-workspace--${activeTaskType} space-y-5 text-slate-800`}>
      <div className={`practice-workspace__heading flex items-center gap-4 border-b border-slate-200 pb-4${activeTaskType === 'training_history' ? ' practice-workspace__heading--history' : ''}`}>
        <button type="button" className="practice-workspace__back" aria-label={returnLabel} onClick={leaveWorkspace}>
          <ArrowLeft aria-hidden="true" size={16} />返回
        </button>
        <h1 className="text-2xl font-bold text-slate-950">{workspaceTitles[activeTaskType] || '训练任务'}</h1>
      </div>
      {selectedKnowledgePoint && (
        <section className="border border-emerald-200 bg-emerald-50 px-4 py-3 text-[15px] text-emerald-950" aria-label="当前考纲知识点">
          <div className="font-semibold">当前训练上下文：{selectedKnowledgePoint.kpName}</div>
          <p className="mt-2 leading-6 text-emerald-900">
            已按该知识点筛选训练内容，作答结果会写回掌握度与复习记录。
          </p>
        </section>
      )}

      <div className="min-w-0 space-y-5">
          <section className={`practice-task-panel rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/50${activeTaskType === 'paper_workspace' ? ' practice-task-panel--paper' : ''}`}>
            {activeTaskType === 'question_training' && !taskItemId ? (
              <QualificationPaperPanel enabled />
            ) : activeTaskType === 'mistake_redo' ? (
              <MistakeRedoPanel />
            ) : activeTaskType === 'mistake_variation' ? (
              <MistakeVariationPanel enabled />
            ) : activeTaskType === 'training_history' ? (
              <TrainingHistoryPanel enabled />
            ) : activeTaskType === 'paper_workspace' ? (
              <SmartPaperPanel
                enabled
                paperId={navigationContext.paperId || navigationContext.paper_id || ''}
                taskItemId={taskItemId}
              />
            ) : activeTaskType === 'knowledge_cards' ? (
              <KnowledgeCardLibrary
                cardId={navigationContext.cardId || navigationContext.card_id || ''}
                kpId={navigationContext.kpId || navigationContext.kp_id || ''}
                taskItemId={taskItemId}
                initialResource={navigationContext.resourceView || navigationContext.resource_view || ''}
                directVideo={navigationContext.directVideo || navigationContext.video || null}
                directTitle={navigationContext.directTitle || navigationContext.kpName || navigationContext.kp_name || ''}
              />
            ) : activeTaskType === 'question_favorites' ? (
              <QuestionFavoritesPanel onNavigate={onNavigate} />
            ) : activeTaskType === 'study_notes' ? (
              <StudyNotesPanel onNavigate={onNavigate} />
            ) : activeTaskType === 'topic_training' ? (
              <KnowledgePointTrainingHub
                initialKnowledgePoint={selectedKnowledgePoint}
                taskItemId={taskItemId}
                onResult={handlePracticeResult}
              />
            ) : ['question_training', 'special_training'].includes(activeTaskType) ? (
              <QuestionTrainingPanel
                enabled
                selectedKnowledgePoint={selectedKnowledgePoint}
                initialMode={activeInitialMode}
                onResult={handlePracticeResult}
                taskItemId={taskItemId}
              />
            ) : (
              <div className="mt-5 rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-5 text-[15px] leading-6 text-slate-600">
                此模块正在准备中，暂不支持提交任务。
              </div>
            )}
          </section>
      </div>
    </div>
  );
}
