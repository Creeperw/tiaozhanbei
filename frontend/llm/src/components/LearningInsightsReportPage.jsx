import React, { useEffect, useMemo, useState } from 'react';
import {
  CalendarDays,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  Info,
} from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { emptyReport, loadReportsData } from '../pageDataLoaders.js';
import LearningActivityHeatmap from './LearningActivityHeatmap';
import LearningTrendDualAxisChart from './LearningTrendDualAxisChart';

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

function calculateSummary(report, series) {
  const dataQuality = report.data_quality || {};
  const activitySummary = report.activity_summary || {};
  const counters = activitySummary.counters || {};
  const focusSessions = counters.focus_sessions || {};
  const tasks = counters.learning_tasks || {};
  const activities = counters.activities || {};
  const accuracy = (report.dimensions || []).find((item) => item.key === 'accuracy');
  const totalFocusMinutes = Number.isFinite(Number(focusSessions.active_seconds))
    ? Number(focusSessions.active_seconds) / 60
    : series.reduce((total, item) => total + Math.max(0, Number(item?.focus_minutes) || 0), 0);
  const activeDays = Number.isFinite(Number(dataQuality.login_days))
    ? Number(dataQuality.login_days)
    : series.reduce((total, item) => total + Math.max(0, Number(item?.login_days) || 0), 0);
  const questionCount = integer(dataQuality.question_count ?? dataQuality.attempt_count ?? activities.by_type?.question_attempt ?? accuracy?.evidence_count);
  const completedPractice = integer(dataQuality.completed_practice_count ?? activities.by_type?.question_attempt ?? tasks.by_status?.completed ?? questionCount);
  return {
    totalFocusMinutes: integer(totalFocusMinutes),
    averageFocusMinutes: integer(totalFocusMinutes / Math.max(Number(activitySummary.window_days) || 30, 1)),
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

export default function LearningInsightsReportPage({ onNavigate, currentUser }) {
  const [report, setReport] = useState(emptyReport);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const loadReport = async () => {
      setLoading(true);
      setError('');
      const result = await loadReportsData({ fetcher: fetchJsonWithAuthFallback });
      if (!cancelled) {
        setReport(result.report);
        setError(result.error);
        setLoading(false);
      }
    };
    loadReport();
    return () => { cancelled = true; };
  }, []);

  const trendSeries = useMemo(() => report.activity_trends?.series || [], [report.activity_trends?.series]);
  const dimensions = useMemo(() => normalizeDimensions(report), [report]);
  const summary = useMemo(() => calculateSummary(report, trendSeries), [report, trendSeries]);
  const dateRange = useMemo(() => reportDateRange(report, trendSeries), [report, trendSeries]);
  const weakPoints = useMemo(() => report.weak_points || [], [report.weak_points]);
  return (
    <div className="reports-page space-y-6 text-slate-800" aria-busy={loading}>
      <header className="flex flex-wrap items-end justify-end gap-5">
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

      {error && !loading && <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
    </div>
  );
}
