import React, {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  ArrowUpRight,
  CalendarDays,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  DatabaseZap,
  Info,
} from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import {
  emptyReport,
  loadReportsData,
  loadResourceEffectiveness,
  recordResourceRecommendationEvent,
} from '../pageDataLoaders.js';
import LearningActivityHeatmap from './LearningActivityHeatmap';
import LearningTrendDualAxisChart from './LearningTrendDualAxisChart';
import { focusMinutesFromStatistics } from './learningPlanDashboard';

const dimensionOrder = ['mastery', 'accuracy', 'consistency', 'retention', 'execution', 'engagement'];
const dimensionLabels = {
  mastery: '知识掌握',
  accuracy: '练习得分',
  consistency: '学习规律',
  retention: '复习保持',
  execution: '任务执行',
  engagement: '资源使用',
};

const clampRatio = (value) => Math.max(0, Math.min(1, Number(value) || 0));
const percent = (value) => `${Math.round(clampRatio(value) * 100)}%`;
const integer = (value) => Math.max(0, Math.round(Number(value) || 0));

const parseDateKey = (value) => {
  const text = String(value || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
  const [year, month, day] = text.split('-').map(Number);
  const date = new Date(year, month - 1, day);
  return Number.isNaN(date.getTime()) ? null : date;
};

const formatMonthDay = (date) => (
  `${String(date.getMonth() + 1).padStart(2, '0')}.${String(date.getDate()).padStart(2, '0')}`
);

function reportDateRange(report, series) {
  const days = Number(report.window?.days) || 30;
  const latestKey = series.map((item) => String(item?.date || '').slice(0, 10))
    .filter((value) => parseDateKey(value))
    .sort()
    .at(-1);
  const end = parseDateKey(latestKey) || new Date();
  const today = new Date();
  if (
    end.getFullYear() === today.getFullYear()
    && end.getMonth() === today.getMonth()
    && end.getDate() === today.getDate()
  ) {
    end.setDate(end.getDate() - 1);
  }
  const start = new Date(end);
  start.setDate(start.getDate() - days + 1);
  return `近${days}天（${formatMonthDay(start)}-${formatMonthDay(end)}）`;
}

function normalizeDimensions(report) {
  const rawDimensions = report.dimensions?.length
    ? report.dimensions
    : (report.mastery_radar || []).map((item, index) => ({
      key: `legacy-${index}`,
      label: item.name,
      value: item.value,
    }));
  return [...rawDimensions]
    .map((item, index) => ({
      ...item,
      key: item.key || `dimension-${index}`,
      label: dimensionLabels[item.key] || item.label || `能力维度 ${index + 1}`,
      value: clampRatio(item.value),
    }))
    .sort((left, right) => {
      const leftIndex = dimensionOrder.indexOf(left.key);
      const rightIndex = dimensionOrder.indexOf(right.key);
      return (leftIndex < 0 ? dimensionOrder.length : leftIndex)
        - (rightIndex < 0 ? dimensionOrder.length : rightIndex);
    })
    .slice(0, 6);
}

function calculateSummary(report, series, lifetimeFocusMinutes = null) {
  const dataQuality = report.data_quality || {};
  const activitySummary = report.activity_summary || {};
  const counters = activitySummary.counters || {};
  const focusSessions = counters.focus_sessions || {};
  const tasks = counters.learning_tasks || {};
  const activities = counters.activities || {};
  const accuracy = (report.dimensions || []).find((item) => item.key === 'accuracy');
  const windowFocusMinutes = Number.isFinite(Number(focusSessions.active_seconds))
    ? Number(focusSessions.active_seconds) / 60
    : series.reduce((total, item) => total + Math.max(0, Number(item?.focus_minutes) || 0), 0);
  const totalFocusMinutes = Number.isFinite(lifetimeFocusMinutes)
    ? lifetimeFocusMinutes
    : windowFocusMinutes;
  const activeDays = Number.isFinite(Number(dataQuality.login_days))
    ? Number(dataQuality.login_days)
    : series.reduce((total, item) => total + Math.max(0, Number(item?.login_days) || 0), 0);
  const questionCount = integer(dataQuality.question_count ?? dataQuality.attempt_count ?? activities.by_type?.question_attempt ?? accuracy?.evidence_count);
  const completedPractice = integer(dataQuality.completed_practice_count ?? activities.by_type?.question_attempt ?? tasks.by_status?.completed ?? questionCount);
  return {
    totalFocusMinutes: integer(totalFocusMinutes),
    averageFocusMinutes: integer(windowFocusMinutes / Math.max(Number(activitySummary.window_days) || 30, 1)),
    completedPractice,
    questionCount,
    accuracy: accuracy?.value,
    activeDays: integer(activeDays),
  };
}

function SummaryMetric({ icon, label, value, unit = '', detail, tone = 'emerald' }) {
  const iconClass = tone === 'teal' ? 'bg-teal-50 text-teal-600' : 'bg-emerald-50 text-emerald-600';
  return (
    <article className="flex min-h-[132px] items-center gap-4 rounded-2xl border border-slate-200/90 bg-white px-5 py-5 shadow-sm shadow-slate-200/45">
      <div className={`grid h-14 w-14 shrink-0 place-items-center rounded-full ${iconClass}`}>
        {React.createElement(icon, { 'aria-hidden': true, size: 28, strokeWidth: 2 })}
      </div>
      <div className="min-w-0">
        <p className="text-sm font-medium text-slate-500">{label}</p>
        <p className="mt-1 flex items-baseline gap-1 text-3xl font-bold tracking-tight text-slate-950">
          <span>{value}</span>
          {unit && <span className="text-base font-semibold text-slate-700">{unit}</span>}
        </p>
        <p className="mt-1 text-sm text-slate-500">{detail}</p>
      </div>
    </article>
  );
}

function CapabilityRadar({ dimensions }) {
  const size = 384;
  const center = size / 2;
  const radius = 112;
  const values = dimensions.slice(0, 6);
  if (values.length < 3) {
    return <div className="flex min-h-64 items-center justify-center rounded-2xl bg-slate-50 px-5 text-sm text-slate-500">完成几次真实练习后生成能力分析。</div>;
  }
  const point = (index, scale = 1) => {
    const angle = -Math.PI / 2 + (index * Math.PI * 2) / values.length;
    return [center + Math.cos(angle) * radius * scale, center + Math.sin(angle) * radius * scale];
  };
  const polygon = (scale) => values.map((_, index) => point(index, scale).join(',')).join(' ');
  const dataPolygon = values.map((item, index) => point(index, Math.max(0.04, item.value)).join(',')).join(' ');

  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="mx-auto aspect-square w-full max-w-[24rem]" role="img" aria-label="学习能力雷达图">
      {[0.25, 0.5, 0.75, 1].map((scale) => (
        <polygon key={scale} points={polygon(scale)} fill="none" stroke="#d8e5df" strokeWidth="1" />
      ))}
      {values.map((item, index) => {
        const [x, y] = point(index, 1);
        const [labelX, labelY] = point(index, 1.27);
        return (
          <g key={item.key}>
            <line x1={center} y1={center} x2={x} y2={y} stroke="#e2e8f0" />
            <text x={labelX} y={labelY} textAnchor="middle" dominantBaseline="middle" fill="#475569" fontSize="12">{item.label}</text>
          </g>
        );
      })}
      <polygon points={dataPolygon} fill="rgba(165, 214, 167, .25)" stroke="#A5D6A7" strokeWidth="2.5" />
      {values.map((item, index) => {
        const [x, y] = point(index, Math.max(0.04, item.value));
        return <circle key={item.key} cx={x} cy={y} r="4" fill="#A5D6A7" />;
      })}
    </svg>
  );
}

function CapabilityLegend({ dimensions }) {
  const dotColors = ['bg-[#A5D6A7]', 'bg-[#B5D8F0]', 'bg-[#D1C4E9]', 'bg-[#FFCCBC]', 'bg-[#F8BBD0]', 'bg-[#B2DFDB]'];
  return (
    <div className="grid gap-3 rounded-2xl border border-slate-100 bg-white/80 p-4 sm:grid-cols-2 xl:grid-cols-1">
      {dimensions.map((item, index) => (
        <div key={item.key} className="flex items-center justify-between gap-3 text-sm">
          <span className="flex min-w-0 items-center gap-2 text-slate-700">
            <i aria-hidden="true" className={`h-2.5 w-2.5 shrink-0 rounded-full ${dotColors[index % dotColors.length]}`} />
            <span className="truncate">{item.label}</span>
          </span>
          <strong className="shrink-0 font-mono font-semibold tabular-nums text-slate-900">
            {item.status === 'insufficient_evidence' ? '数据不足' : percent(item.value)}
          </strong>
        </div>
      ))}
    </div>
  );
}

function CapabilityCard({ dimensions }) {
  return (
    <section className="rounded-[24px] border border-slate-200/90 bg-white p-5 shadow-sm shadow-slate-200/45 sm:p-6" aria-label="能力分析">
      <h2 className="text-xl font-bold text-slate-950">能力分析</h2>
      <div className="mt-3 grid items-center gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(170px,0.72fr)]">
        <CapabilityRadar dimensions={dimensions} />
        <CapabilityLegend dimensions={dimensions} />
      </div>
    </section>
  );
}

function WeakPointsCard({ weakPoints, onNavigate }) {
  const items = weakPoints.slice(0, 3);
  return (
    <section className="rounded-[24px] border border-slate-200/90 bg-white p-5 shadow-sm shadow-slate-200/45 sm:p-6" aria-label="薄弱知识点">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-950">薄弱知识点</h2>
          <p className="mt-2 text-base text-slate-500">优先巩固以下 {items.length || 3} 个知识点</p>
        </div>
        <button
          type="button"
          onClick={() => {
            const first = items[0];
            onNavigate?.({
              page: 'practice',
              params: {
                view: 'workspace',
                taskType: 'topic_training',
                ...(first?.kp_id ? { kpId: first.kp_id, kpName: first.kp_name } : {}),
                returnTo: { page: 'personalization', params: { view: 'reports' } },
              },
            });
          }}
          className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-gradient-to-r from-[#C8E6C9] to-[#A8E6CF] px-5 text-base font-semibold text-emerald-900 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
        >
          去专项巩固 <span aria-hidden="true" className="text-xl leading-none">›</span>
        </button>
      </div>
      {items.length > 0 ? (
        <div className="mt-5 grid gap-3 md:grid-cols-3">
          {items.map((item, index) => {
            const score = item.mastery_score ?? item.score;
            return (
              <article key={item.kp_id || item.kp_name || index} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm shadow-slate-100">
                <div className="grid h-10 w-10 place-items-center rounded-full bg-emerald-50 text-xl font-bold text-emerald-600">{index + 1}</div>
                <h3 className="mt-4 min-h-12 text-lg font-bold leading-6 text-slate-950">{item.kp_name || item.name || '未命名知识点'}</h3>
                <p className="mt-3 flex items-start gap-2 text-sm leading-6 text-slate-600">
                  <span aria-hidden="true" className="mt-2 h-2 w-2 shrink-0 rounded-full bg-orange-400" />
                  <span>{item.reason || '当前掌握度较低，建议优先巩固。'}</span>
                </p>
                <div className="mt-5 border-t border-dashed border-slate-200 pt-4 text-center">
                  <p className="text-sm text-slate-500">掌握度</p>
                  <p className="mt-1 text-3xl font-bold tabular-nums text-emerald-600">{score === null || score === undefined ? '—' : percent(score)}</p>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="mt-5 rounded-2xl bg-slate-50 px-5 py-8 text-center text-sm text-slate-500">暂无可确认的薄弱知识点。</div>
      )}
    </section>
  );
}

const resourceTypeLabels = {
  knowledge_card: '知识卡片',
  question: '配套题目',
  video: '视频资源',
};

const matchComponentLabels = {
  knowledge_fit: '知识点覆盖',
  quality: '资源质量',
  format_fit: '形式偏好',
  time_fit: '时间适配',
};

const matchSourceLabels = {
  'resource.kp_ids intersect target.kp_ids': '资源知识点与当前薄弱点、计划知识点的交集',
  'user_profiles.exercise_preferences/custom_needs': '学习画像中的资源偏好与自定义需求',
  not_available_excluded_from_weighting: '当前没有可靠数据，本项未参与加权',
  neutral_default_no_quality_evidence: '暂无质量证据，采用中性基线',
  content_type_default: '按资源类型的默认完成时长估算',
  question_type_default: '按题型的默认作答时长估算',
  user_response_time_mean_30d: '最近 30 天同题平均作答时长',
  knowledge_card_bundle: '知识卡资源包记录',
  'teaching_resources.quality_score': '教学资源库质量评分',
  'question_bank_items.quality_score': '正式题库质量评分',
};

function resourceIntent(item) {
  const kpId = item.kp_ids?.[0] || '';
  const returnTo = { page: 'personalization', params: { view: 'reports' } };
  if (item.resource_type === 'knowledge_card') {
    return {
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'knowledge_cards',
        cardId: item.resource_id,
        kpId,
        returnTo,
      },
    };
  }
  if (item.resource_type === 'video') {
    return {
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'knowledge_cards',
        resourceView: 'videos',
        kpId,
        returnTo,
      },
    };
  }
  return {
    page: 'practice',
    params: {
      view: 'workspace',
      taskType: 'topic_training',
      kpId,
      kpName: item.title || '',
      returnTo,
    },
  };
}

function ResourceMatchCard({ item, busyEvent, completed, onEvent }) {
  const [basisOpen, setBasisOpen] = useState(false);
  const basisId = useId();
  const components = Object.entries(item.components || {})
    .filter(([key]) => matchComponentLabels[key]);
  return (
    <article className="flex h-full flex-col rounded-2xl border border-slate-200/80 bg-slate-50 p-4 transition hover:-translate-y-0.5 hover:border-emerald-200 hover:bg-emerald-50/60">
      <div className="flex items-start justify-between gap-4">
        <div>
          <span className="text-xs font-semibold text-emerald-700">
            {resourceTypeLabels[item.resource_type] || item.resource_type || '学习资源'}
          </span>
          <h3 className="mt-1 text-base font-bold leading-6 text-slate-950">{item.title}</h3>
        </div>
        <span className="shrink-0 font-mono text-sm font-semibold tabular-nums text-emerald-800">
          匹配 {percent(item.score)}
        </span>
      </div>
      <p className="mt-3 text-sm leading-6 text-slate-600">{(item.reasons || []).join('；') || '作为当前学习目标的补充资源。'}</p>
      <div className="mt-4 flex items-center justify-between gap-3 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1"><Clock3 size={13} />约 {integer(item.estimated_minutes)} 分钟</span>
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 font-semibold text-emerald-800 transition hover:bg-emerald-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700"
          aria-expanded={basisOpen}
          aria-controls={basisId}
          onClick={() => setBasisOpen((value) => !value)}
        >
          匹配依据
          <ArrowUpRight aria-hidden="true" size={13} className={`transition-transform ${basisOpen ? 'rotate-90' : ''}`} />
        </button>
      </div>
      {basisOpen && (
        <dl id={basisId} className="mt-3 space-y-2 border-t border-emerald-100 pt-3" aria-label={`${item.title}匹配依据详情`}>
          {components.map(([key, value]) => (
            <div key={key} className="grid grid-cols-[auto_1fr_auto] items-start gap-2 text-xs">
              <dt className="font-semibold text-slate-700">{matchComponentLabels[key]}</dt>
              <dd className="leading-5 text-slate-500">
                {matchSourceLabels[item.component_sources?.[key]] || item.component_sources?.[key] || '由当前学习数据计算'}
              </dd>
              <dd className="font-mono font-semibold tabular-nums text-emerald-800">
                {value === null || value === undefined ? '未纳入' : percent(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
      <div className="mt-auto flex flex-wrap gap-2 pt-4">
        <button
          type="button"
          disabled={Boolean(busyEvent)}
          onClick={() => onEvent(item, 'click')}
          className="inline-flex min-h-10 flex-1 items-center justify-center rounded-xl bg-emerald-600 px-3 text-sm font-semibold text-white transition hover:bg-emerald-700 disabled:cursor-wait disabled:opacity-60"
        >
          {busyEvent === 'click' ? '正在打开…' : '打开资源'}
        </button>
        <button
          type="button"
          disabled={Boolean(busyEvent) || completed}
          onClick={() => onEvent(item, 'complete')}
          className="inline-flex min-h-10 items-center justify-center rounded-xl border border-emerald-200 bg-white px-3 text-sm font-semibold text-emerald-800 transition hover:bg-emerald-50 disabled:cursor-default disabled:opacity-60"
        >
          {busyEvent === 'complete' ? '正在记录…' : completed ? '已完成' : '我已学完'}
        </button>
      </div>
    </article>
  );
}

function ResourceEffectivenessSummary({ effectiveness }) {
  if (!effectiveness) return null;
  const funnel = effectiveness.funnel || {};
  const outcomes = effectiveness.learning_outcomes || {};
  const attemptCount = integer(outcomes.post_resource_attempt_count);
  const outcomeText = outcomes.status !== 'observed'
    ? '学习成效证据尚不足，不调整排序权重'
    : attemptCount > 0
      ? `已有 ${attemptCount} 次后续练习证据`
      : outcomes.mastery_delta !== null && outcomes.mastery_delta !== undefined
        ? `已观察到掌握度变化 ${Math.round(Number(outcomes.mastery_delta) * 100)} 个百分点`
        : '已有学习结果证据';
  return (
    <div role="region" className="grid min-w-full gap-2 rounded-2xl bg-emerald-50/70 p-3 sm:min-w-0 sm:grid-cols-4" aria-label="资源推荐效果">
      <div><span className="text-xs text-slate-500">已展示</span><strong className="mt-1 block text-lg text-slate-950">{integer(funnel.displayed_resource_count)}</strong></div>
      <div><span className="text-xs text-slate-500">已打开</span><strong className="mt-1 block text-lg text-slate-950">{integer(funnel.clicked_resource_count)}</strong></div>
      <div><span className="text-xs text-slate-500">已完成</span><strong className="mt-1 block text-lg text-slate-950">{integer(funnel.completed_resource_count)}</strong></div>
      <div><span className="text-xs text-slate-500">效果判断</span><strong className="mt-1 block text-sm leading-5 text-emerald-900">{outcomeText}</strong></div>
    </div>
  );
}

function ResourceMatchSection({
  resourceReport,
  effectiveness,
  busyByResource,
  completedResources,
  onEvent,
}) {
  const matches = (resourceReport.matches || []).slice(0, 6);
  return (
    <section className="rounded-[24px] border border-slate-200/90 bg-white p-5 shadow-sm shadow-slate-200/45 sm:p-6" aria-label="资源匹配报告">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-bold text-slate-950"><DatabaseZap size={20} className="text-emerald-600" />资源匹配报告</h2>
          <p className="mt-2 text-sm leading-6 text-slate-500">结合当前薄弱点、今日任务、资源偏好和可用时间推荐；使用反馈会进入效果闭环。</p>
        </div>
        <div className="text-right">
          <strong className="font-mono text-2xl tabular-nums text-emerald-700">{percent(resourceReport.summary?.coverage)}</strong>
          <span className="block text-xs text-slate-500">目标知识点覆盖</span>
        </div>
      </div>
      <div className="mt-5"><ResourceEffectivenessSummary effectiveness={effectiveness} /></div>
      {matches.length > 0 ? (
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {matches.map((item) => (
            <ResourceMatchCard
              key={`${item.resource_type}-${item.resource_id}`}
              item={item}
              busyEvent={busyByResource[item.resource_id] || ''}
              completed={completedResources.has(item.resource_id)}
              onEvent={onEvent}
            />
          ))}
        </div>
      ) : (
        <div className="mt-5 rounded-2xl bg-slate-50 px-5 py-8 text-center text-sm text-slate-500">
          {resourceReport.no_match_reason || '当前没有可验证的匹配资源。'}
        </div>
      )}
    </section>
  );
}

export default function LearningInsightsReportPage({ onNavigate }) {
  const [report, setReport] = useState(emptyReport);
  const [effectiveness, setEffectiveness] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lifetimeFocusMinutes, setLifetimeFocusMinutes] = useState(null);
  const [error, setError] = useState('');
  const [resourceEventError, setResourceEventError] = useState('');
  const [busyByResource, setBusyByResource] = useState({});
  const [completedResources, setCompletedResources] = useState(() => new Set());
  const impressedViews = useRef(new Set());

  const refreshEffectiveness = useCallback(async () => {
    const result = await loadResourceEffectiveness({
      fetcher: fetchJsonWithAuthFallback,
      days: 30,
    });
    setEffectiveness(result.effectiveness);
    return result;
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadReport = async () => {
      setLoading(true);
      setError('');
      const [result, statisticsResult] = await Promise.all([
        loadReportsData({ fetcher: fetchJsonWithAuthFallback }),
        Promise.resolve(fetchJsonWithAuthFallback({
          paths: ['/v1/learning-statistics/overview?days=30'],
          fallback: {},
        })).catch(() => null),
      ]);
      if (!cancelled) {
        setReport(result.report);
        setLifetimeFocusMinutes(focusMinutesFromStatistics(statisticsResult?.data));
        setError(result.error);
        setLoading(false);
        void refreshEffectiveness();
      }
    };
    loadReport();
    return () => { cancelled = true; };
  }, [refreshEffectiveness]);

  const resourceReport = report.resource_match_report || emptyReport.resource_match_report;

  useEffect(() => {
    const first = (resourceReport.matches || []).find((item) => item.feedback);
    const viewId = first?.feedback?.recommendation_view_id || resourceReport.recommendation_view_id;
    if (!first || !viewId || impressedViews.current.has(viewId)) return;
    impressedViews.current.add(viewId);
    recordResourceRecommendationEvent({
      fetcher: fetchJsonWithAuthFallback,
      feedback: first.feedback,
      eventType: 'impression',
    }).then((result) => {
      if (result.error) {
        setResourceEventError(result.error);
        return;
      }
      void refreshEffectiveness();
    });
  }, [refreshEffectiveness, resourceReport]);

  const handleResourceEvent = async (item, eventType) => {
    setResourceEventError('');
    setBusyByResource((current) => ({ ...current, [item.resource_id]: eventType }));
    const result = await recordResourceRecommendationEvent({
      fetcher: fetchJsonWithAuthFallback,
      feedback: item.feedback,
      eventType,
    });
    setBusyByResource((current) => ({ ...current, [item.resource_id]: '' }));
    if (result.error) setResourceEventError(result.error);
    if (eventType === 'complete' && !result.error) {
      setCompletedResources((current) => new Set([...current, item.resource_id]));
      void refreshEffectiveness();
    }
    if (eventType === 'click') {
      onNavigate?.(resourceIntent(item));
    }
  };

  const trendSeries = useMemo(() => report.activity_trends?.series || [], [report.activity_trends?.series]);
  const dimensions = useMemo(() => normalizeDimensions(report), [report]);
  const summary = useMemo(
    () => calculateSummary(report, trendSeries, lifetimeFocusMinutes),
    [lifetimeFocusMinutes, report, trendSeries],
  );
  const dateRange = useMemo(() => reportDateRange(report, trendSeries), [report, trendSeries]);
  const weakPoints = useMemo(() => report.weak_points || [], [report.weak_points]);

  return (
    <div className="reports-page space-y-6 text-slate-800" aria-busy={loading}>
      <header className="flex flex-wrap items-end justify-between gap-5">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-slate-950 sm:text-[2.15rem]">我的学情报告</h1>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 shadow-sm" aria-label={`报告统计周期：${dateRange}`}>
            <CalendarDays aria-hidden="true" size={17} className="text-slate-500" />
            <span>{dateRange}</span>
          </span>
          <span className="inline-flex items-center gap-1.5 text-sm text-slate-500" title="统计来自当前学习行为、练习记录和能力评估数据">
            数据说明
            <Info aria-hidden="true" size={16} />
          </span>
        </div>
      </header>

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-label="学习结论">
        <SummaryMetric icon={Clock3} label="累计学习时长" value={summary.totalFocusMinutes} unit="分钟" detail={`日均 ${summary.averageFocusMinutes} 分钟`} />
        <SummaryMetric icon={ClipboardCheck} label="完成练习" value={summary.completedPractice} unit="次" detail={`共 ${summary.questionCount} 题`} tone="teal" />
        <SummaryMetric icon={CheckCircle2} label="平均正确率" value={summary.accuracy === undefined ? '—' : percent(summary.accuracy)} detail={`共 ${summary.questionCount} 题`} />
        <SummaryMetric icon={CalendarDays} label="活跃天数" value={summary.activeDays} unit="天" detail="本月" tone="teal" />
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.04fr)_minmax(0,1fr)]">
        <CapabilityCard dimensions={dimensions} />
        <LearningTrendDualAxisChart series={trendSeries.slice(-14)} />
      </section>

      <section className="grid gap-5 xl:grid-cols-2">
        <WeakPointsCard weakPoints={weakPoints} onNavigate={onNavigate} />
        <LearningActivityHeatmap series={trendSeries.slice(-84)} />
      </section>

      <ResourceMatchSection
        resourceReport={resourceReport}
        effectiveness={effectiveness}
        busyByResource={busyByResource}
        completedResources={completedResources}
        onEvent={handleResourceEvent}
      />

      {resourceEventError && <div role="alert" className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">{resourceEventError}</div>}
      {error && !loading && <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
    </div>
  );
}
