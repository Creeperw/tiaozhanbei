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
import MistakeVariationPanel from './MistakeVariationPanel';
import PaperGenerationPanel from './PaperGenerationPanel';
import SmartPaperPanel from './SmartPaperPanel';
import QuestionWorkspacePage from './QuestionWorkspacePage';
import KnowledgeCardLibrary from './KnowledgeCardLibrary';
import KnowledgePointTrainingHub from './KnowledgePointTrainingHub';
import QuestionFavoritesPanel from './QuestionFavoritesPanel';
import StudyNotesPanel from './StudyNotesPanel';
import { isTrainingTaskResultApproved } from '../pageDataLoaders.js';
import { practiceContextFromIntent } from './exam-atlas/examAtlasPageContext';

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
  return <p className="[overflow-wrap:anywhere] py-5 text-sm leading-6 text-slate-500">{children}</p>;
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
    key: 'paper_workspace',
    title: '智能组卷',
    description: '按学习目标组卷，灵活安排练习节奏。',
    icon: Files,
    tone: 'green',
  },
  {
    key: 'topic_training',
    initialMode: 'objective',
    title: '专题训练',
    description: '真实病例场景训练，提升临床思维。',
    icon: Stethoscope,
    tone: 'teal',
  },
  {
    key: 'question_training',
    initialMode: 'objective',
    title: '综合套题',
    description: '按知识点分类训练，逐个击破薄弱点。',
    icon: ClipboardCheck,
    tone: 'emerald',
  },
  {
    key: 'ai_patient_simulation',
    title: '模拟病患',
    description: '模拟问诊与辨证，训练临床沟通与思路。',
    icon: HeartPulse,
    tone: 'rose',
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

const uploadQuestionBankCard = {
  key: 'question_workspace',
  title: '上传题库',
  description: '上传学习资料，沉淀个人专属题库。',
  icon: UploadCloud,
  tone: 'amber',
};

const utilityCards = [
  {
    key: 'mistake_variation',
    title: '错题库',
    description: '整理错题记录，生成变式并针对性复盘。',
    icon: FolderHeart,
    available: true,
  },
  {
    key: 'question_favorites',
    title: '知识收藏',
    description: '重点内容，随时回顾。',
    icon: BookMarked,
    available: true,
  },
  {
    key: 'study_notes',
    title: '学习笔记',
    description: '记录心得，沉淀思考。',
    icon: NotebookPen,
    available: true,
  },
];

const DEFAULT_TRAINING_OVERVIEW_STATS = Object.freeze({
  streakDays: null,
  lastAccuracy: null,
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
    lastAccuracy: percentageOrNull(safeStats.lastAccuracy),
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
  const latestScoredActivity = recentActivities.find(
    (activity) => (
      isScoredTrainingActivity(activity)
      && finiteNumberOrNull(activity?.score) !== null
    ),
  );
  const latestResumableActivity = recentActivities.find(recentTaskKeyFromActivity);
  const focusMinutes = nonNegativeNumberOrNull(lifetime.focus_minutes);

  return {
    streakDays: nonNegativeNumberOrNull(checkin?.streak),
    lastAccuracy: scoreAsPercentage(latestScoredActivity?.score)
      ?? scoreAsPercentage(currentWindow.score_rate),
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
  paper_workspace: '智能组卷',
  knowledge_cards: '知识卡片',
  question_favorites: '知识收藏',
  study_notes: '学习笔记',
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
  const formatHours = (value) => value === null ? '--' : `${value} 小时`;
  const formatDays = (value) => value === null ? '--' : `${value} 天`;
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
            label="上次正确率"
            value={formatPercent(stats.lastAccuracy)}
            hint="稳保持，稳步提升"
            tone="mint"
          />
        </div>
        <TrainingBannerIllustration />
      </header>

      <div className="practice-overview__layout">
        <section className="practice-overview__main" aria-label="训练模块">
          <button type="button" className="practice-overview__featured-card" onClick={() => onOpenModule(featuredTrainingCard)}>
            <span className="practice-overview__featured-icon"><Target aria-hidden="true" size={30} /></span>
            <span className="practice-overview__featured-copy">
              <span className="practice-overview__featured-title"><strong>{featuredTrainingCard.title}</strong><em>推荐</em></span>
              <small>覆盖核心知识点，系统巩固基础能力。</small>
              <span>20 题 <i /> 15 分钟 <i /> 覆盖核心知识点</span>
            </span>
            <span className="practice-overview__featured-action">
              <b>开始练习 <ArrowRight aria-hidden="true" size={16} /></b>
              <small>上次练习：待接入</small>
              <small>正确率：{formatPercent(stats.lastAccuracy)}</small>
            </span>
          </button>
          <div className="practice-overview__training-grid">
            {trainingCards.map((card) => {
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

export default function PracticePage({ navigationContext = {}, overviewStats }) {
  const selectedKnowledgePoint = practiceContextFromIntent(navigationContext);
  const initialTaskIntent = normalizeTaskIntent(navigationContext.taskType);
  const [activeTaskType, setActiveTaskType] = useState(() => initialTaskIntent.taskType);
  const [activeInitialMode, setActiveInitialMode] = useState(() => initialTaskIntent.initialMode);
  const [taskResult, setTaskResult] = useState(null);
  const [mobilePage, setMobilePage] = useState('task');
  const [view, setView] = useState(() => (navigationContext.taskType || navigationContext.view === 'workspace' ? 'workspace' : 'overview'));
  const [loadedOverviewStats, setLoadedOverviewStats] = useState(DEFAULT_TRAINING_OVERVIEW_STATS);
  const taskItemId = navigationContext.taskItemId || navigationContext.task_item_id || '';

  useEffect(() => {
    if (overviewStats !== undefined) return undefined;

    let active = true;
    const requestOverview = (path) => fetchJsonWithAuthFallback({
      paths: [path],
      fallback: {},
    }).then((result) => result.data).catch(() => ({}));

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
    setTaskResult({
      task_id: result?.attempt_id || `practice-${question.question_id}`,
      task_type: 'practice_grading',
      status: 'completed',
      title: `${question.question_type}批改结果`,
      summary: grading.is_correct ? '回答正确，学习记录已更新。' : '回答错误，已进入错题记录。',
      artifact: {
        artifact_type: 'grading_result',
        title: '练习批改结果',
        content: { grading, remediation: {} },
      },
      evidence_pack: {},
      audit: { decision: 'pass', reason: '正式题库受控批改已完成' },
      trace: [],
      learning_updates: { writeback: result?.writeback || {} },
      next_actions: [],
    });
    setMobilePage('result');
  };

  const openWorkshopModule = ({ key, initialMode }) => {
    setActiveTaskType(key);
    setActiveInitialMode(initialMode || key);
    setTaskResult(null);
    setMobilePage('task');
    setView('workspace');
  };

  const taskResultApproved = isTrainingTaskResultApproved(taskResult);

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
          <button type="button" className="practice-workspace__back" onClick={() => setView('overview')}>
            <ArrowLeft aria-hidden="true" size={18} />返回训练工坊
          </button>
        </div>
        <QuestionWorkspacePage />
      </div>
    );
  }

  const isSP = activeTaskType === 'ai_patient_simulation';

  if (isSP) {
    return <SimulatedPatientChat onBack={() => setView('overview')} />;
  }

  return (
    <div className="space-y-5 text-slate-800">
      <div className="practice-workspace__toolbar">
        <button type="button" className="practice-workspace__back" onClick={() => setView('overview')}>
          <ArrowLeft aria-hidden="true" size={18} />返回训练工坊
        </button>
      </div>
      <header>
        <span className="app-shell__section-label">训练工坊</span>
        <h1 className="mt-1 text-2xl font-semibold text-slate-950">{workspaceTitles[activeTaskType] || '训练任务'}</h1>
      </header>

      <div className="practice-mobile-tabs" role="tablist" aria-label="移动端训练视图">
        {[
          ['task', '任务'],
          ['result', '结果'],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={mobilePage === key}
            onClick={() => setMobilePage(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {selectedKnowledgePoint && (
        <section className="border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-950" aria-label="当前考纲知识点">
          <div className="font-semibold">当前训练上下文：{selectedKnowledgePoint.kpName}</div>
          <div className="mt-1 font-mono text-xs text-emerald-800">{selectedKnowledgePoint.kpId}</div>
          <p className="mt-2 leading-6 text-emerald-900">
            该知识点已带入训练工坊；当前兼容示例题不代表该知识点的正式题目，正式题源筛选将在题库接入后启用。
          </p>
        </section>
      )}

      <div className="min-w-0 space-y-5">
          <section data-mobile-active={String(mobilePage === 'task')} className="practice-task-panel rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/50">
            {activeTaskType === 'question_training' && !taskItemId ? (
              <QualificationPaperPanel enabled />
            ) : activeTaskType === 'mistake_variation' ? (
              <MistakeVariationPanel enabled />
            ) : activeTaskType === 'paper_workspace' ? (
              <SmartPaperPanel enabled paperId={navigationContext.paperId || navigationContext.paper_id || ''} taskItemId={taskItemId} />
            ) : activeTaskType === 'knowledge_cards' ? (
              <KnowledgeCardLibrary
                cardId={navigationContext.cardId || navigationContext.card_id || ''}
                kpId={navigationContext.kpId || navigationContext.kp_id || ''}
                taskItemId={taskItemId}
              />
            ) : activeTaskType === 'question_favorites' ? (
              <QuestionFavoritesPanel />
            ) : activeTaskType === 'study_notes' ? (
              <StudyNotesPanel />
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
              <div className="mt-5 rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-5 text-sm leading-6 text-slate-600">
                此模块正在准备中，暂不支持提交任务。
              </div>
            )}

          </section>

          <section
            data-testid="practice-result-panel"
            data-mobile-active={String(mobilePage === 'result')}
            aria-busy="false"
            aria-labelledby="training-artifact-title"
            className="practice-result-panel rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/50"
          >
            <div className="flex items-center gap-2 text-sm font-medium text-slate-600">
              <FileText size={16} aria-hidden="true" />
              <h2 id="training-artifact-title" className="text-sm font-semibold text-slate-900">训练产物</h2>
            </div>
            {taskResult && !taskResultApproved ? (
              <div role="alert" className="mt-4 border border-rose-300 bg-rose-50 px-4 py-4 text-rose-950">
                <h3 className="text-base font-semibold">{displayValue(taskResult.title || taskResult.artifact?.title)}</h3>
                <p className="mt-2 text-sm font-semibold leading-6">审核未通过/任务未完成，该候选内容不可作为学习依据。</p>
                <p className="mt-2 text-sm leading-6">状态：{displayValue(taskResult.status)}</p>
                <p className="mt-1 text-sm leading-6">审核原因：{displayValue(taskResult.audit?.reason)}</p>
                <p className="mt-3 text-sm font-semibold">请调整输入后重试。</p>
              </div>
            ) : (
              <div className="mt-4 [overflow-wrap:anywhere]"><ArtifactResult taskResult={taskResult} /></div>
            )}
          </section>
      </div>
    </div>
  );
}
