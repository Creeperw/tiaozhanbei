import React, { useEffect, useId, useMemo, useState } from 'react';
import {
  Activity,
  ArrowUpRight,
  BellRing,
  BookOpenCheck,
  CircleGauge,
  Clock3,
  DatabaseZap,
  FileCheck2,
  RefreshCw,
  Target,
} from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { emptyReport, loadReportsData } from '../pageDataLoaders.js';
import LearningTrendChart from './LearningTrendChart';

const percent = (value) => `${Math.round(Math.max(0, Math.min(1, Number(value) || 0)) * 100)}%`;

const metricLabels = {
  task_completion_rate: '任务完成率',
  learning_regularity: '学习规律度',
  question_accuracy: '答题正确率',
  average_response_time: '平均答题用时',
  average_mastery: '平均掌握度',
  recent_focus_minutes: '近期专注时长',
  current_task_load: '当前任务量',
};

const unavailableReasonLabels = {
  no_tasks_in_window: '窗口内暂无任务',
  no_learning_activity_in_window: '窗口内暂无学习活动',
  no_question_attempts: '暂无答题记录（no_question_attempts）',
  no_response_time_observations: '暂无答题用时记录',
  no_mastery_observations: '暂无掌握度记录',
  mastery_or_confidence_missing: '掌握度或可信度缺失',
  no_focus_sessions: '暂无专注学习记录',
  no_pending_tasks: '暂无待完成任务',
  pending_task_duration_missing_or_incomplete: '待完成任务时长不完整',
};

function metricText(metric) {
  if (!metric || metric.available === false || metric.value === null || metric.value === undefined) {
    return '不可用';
  }
  if (metric.unit === 'seconds') return `${metric.value} 秒`;
  if (metric.unit === 'minutes') return `${metric.value} 分钟`;
  return percent(metric.value);
}

function MetricSummary({ metrics }) {
  return (
    <dl className="mt-3 space-y-2">
      {metrics.map(([key, metric]) => (
        <div key={key} className="rounded-xl bg-slate-50 px-3 py-2">
          <div className="flex items-center justify-between gap-3 text-xs">
            <dt className="text-slate-600">{metricLabels[key] || key}</dt>
            <dd className="font-mono font-semibold text-slate-900">{metricText(metric)}</dd>
          </div>
          {metric?.available === false && (
            <p className="mt-1 text-xs text-amber-800">
              {unavailableReasonLabels[metric.unavailable_reason] || metric.unavailable_reason || '未提供不可用原因'}
            </p>
          )}
        </div>
      ))}
    </dl>
  );
}

function MultiscaleSummary({ state }) {
  if (!state) return null;
  const macro = state.macro || {};
  const meso = state.meso || {};
  const micro = state.micro || {};
  const stageBooks = (macro.stage_books || []).map((item) => (
    typeof item === 'string' ? item : item?.name
  )).filter(Boolean);
  const mesoMetrics = ['task_completion_rate', 'learning_regularity']
    .filter((key) => meso[key])
    .map((key) => [key, meso[key]]);
  const microMetrics = [
    'question_accuracy',
    'average_response_time',
    'average_mastery',
    'recent_focus_minutes',
    'current_task_load',
  ].filter((key) => micro[key]).map((key) => [key, micro[key]]);

  return (
    <section className="rounded-[28px] bg-white p-5 shadow-sm shadow-emerald-950/5 sm:p-6" aria-label="多尺度学习状态">
      <div className="grid gap-4 md:grid-cols-3">
        <article className="rounded-2xl border border-slate-100 p-4">
          <h3 className="text-sm font-semibold text-slate-950">宏观状态</h3>
          <p className="mt-3 text-sm text-slate-700">{macro.current_stage?.name || '当前阶段不可用'}</p>
          {stageBooks.length > 0 && <p className="mt-2 text-xs text-slate-500">{stageBooks.join('、')}</p>}
        </article>
        <article className="rounded-2xl border border-slate-100 p-4">
          <h3 className="text-sm font-semibold text-slate-950">中观状态</h3>
          {mesoMetrics.length > 0 ? <MetricSummary metrics={mesoMetrics} /> : <p className="mt-3 text-sm text-slate-500">暂无中观指标。</p>}
        </article>
        <article className="rounded-2xl border border-slate-100 p-4">
          <h3 className="text-sm font-semibold text-slate-950">微观状态</h3>
          {microMetrics.length > 0 ? <MetricSummary metrics={microMetrics} /> : <p className="mt-3 text-sm text-slate-500">暂无微观指标。</p>}
        </article>
      </div>
      <div className="mt-4 text-xs text-slate-500">
        数据来源：{(state.source_refs || []).map((source) => source.table || source.source_type).filter(Boolean).join('、') || '暂无可追溯来源'}
      </div>
    </section>
  );
}

function RadarChart({ dimensions }) {
  const size = 286;
  const center = size / 2;
  const radius = 92;
  const values = dimensions.slice(0, 6);
  if (values.length < 3) return null;
  const point = (index, scale = 1) => {
    const angle = -Math.PI / 2 + (index * Math.PI * 2) / values.length;
    return [center + Math.cos(angle) * radius * scale, center + Math.sin(angle) * radius * scale];
  };
  const polygon = (scale) => values.map((_, index) => point(index, scale).join(',')).join(' ');
  const dataPolygon = values.map((item, index) => point(index, Math.max(0.04, Number(item.value) || 0)).join(',')).join(' ');

  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="mx-auto aspect-square w-full max-w-[19rem]" role="img" aria-label="学习能力雷达图">
      {[0.25, 0.5, 0.75, 1].map((scale) => (
        <polygon key={scale} points={polygon(scale)} fill="none" stroke="#d8e5df" strokeWidth="1" />
      ))}
      {values.map((item, index) => {
        const [x, y] = point(index, 1);
        const [labelX, labelY] = point(index, 1.28);
        return (
          <g key={item.key || item.label}>
            <line x1={center} y1={center} x2={x} y2={y} stroke="#e2e8f0" />
            <text x={labelX} y={labelY} textAnchor="middle" dominantBaseline="middle" fill="#475569" fontSize="11">{item.label}</text>
          </g>
        );
      })}
      <polygon points={dataPolygon} fill="rgba(5, 150, 105, .16)" stroke="#047857" strokeWidth="2.5" />
      {values.map((item, index) => {
        const [x, y] = point(index, Math.max(0.04, Number(item.value) || 0));
        return <circle key={item.key || item.label} cx={x} cy={y} r="3.5" fill="#047857" />;
      })}
    </svg>
  );
}

function MetricStrip({ dimensions }) {
  return (
    <div className="grid gap-px overflow-hidden rounded-2xl bg-slate-200 sm:grid-cols-3">
      {dimensions.map((item) => (
        <div key={item.key || item.label} className="bg-white px-4 py-3">
          <div className="text-xs font-medium text-slate-500">{item.label}</div>
          <div className="mt-1 font-mono text-xl font-semibold tabular-nums text-slate-950">{item.status === 'insufficient_evidence' ? '数据不足' : percent(item.value)}</div>
        </div>
      ))}
    </div>
  );
}

const matchComponentLabels = {
  knowledge_fit: '知识点覆盖',
  quality: '资源质量',
  format_fit: '形式偏好',
  time_fit: '时间适配',
  difficulty_fit: '难度适配',
};

const matchSourceLabels = {
  'resource.kp_ids intersect target.kp_ids': '资源知识点与当前薄弱点、计划知识点的交集',
  'user_profiles.exercise_preferences/custom_needs': '学习画像中的资源偏好与自定义需求',
  'question_bank_items.difficulty vs user_profile_survey': '题库难度与学情调查中的难度偏好',
  not_available_excluded_from_weighting: '当前没有可靠数据，本项未参与加权',
  neutral_default_no_quality_evidence: '暂无质量证据，采用中性基线',
  content_type_default: '按资源类型的默认完成时长估算',
  question_type_default: '按题型的默认作答时长估算',
  user_response_time_mean_30d: '最近 30 天同题平均作答时长',
  knowledge_card_bundle: '知识卡资源包记录',
  'teaching_resources.quality_score': '教学资源库质量评分',
  'question_bank_items.quality_score': '正式题库质量评分',
};

function ResourceMatchCard({ item }) {
  const [basisOpen, setBasisOpen] = useState(false);
  const basisId = useId();
  const typeLabel = {
    knowledge_card: '知识卡片',
    question: '配套题目',
    video: '视频资源',
  }[item.resource_type] || item.resource_type || '学习资源';
  const components = Object.entries(item.components || {})
    .filter(([key]) => matchComponentLabels[key]);
  return (
    <article className="group flex h-full flex-col rounded-2xl bg-slate-50 p-4 transition duration-200 hover:-translate-y-0.5 hover:bg-emerald-50/70">
      <div className="flex items-start justify-between gap-4">
        <div>
          <span className="text-xs font-medium text-emerald-800">{typeLabel}</span>
          <h4 className="mt-1 text-sm font-semibold leading-6 text-slate-950">{item.title}</h4>
        </div>
        <span className="font-mono text-sm font-semibold tabular-nums text-emerald-800">{percent(item.score)}</span>
      </div>
      <p className="mt-3 text-xs leading-5 text-slate-600">{(item.reasons || []).join('；')}</p>
      <div className="mt-auto flex items-center justify-between gap-3 pt-4 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1"><Clock3 size={13} />约 {item.estimated_minutes || 0} 分钟</span>
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 font-medium text-emerald-800 transition hover:bg-emerald-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
          aria-expanded={basisOpen}
          aria-controls={basisId}
          onClick={() => setBasisOpen((value) => !value)}
        >
          匹配依据
          <ArrowUpRight aria-hidden="true" size={13} className={`transition-transform ${basisOpen ? 'rotate-90' : ''}`} />
        </button>
      </div>
      {basisOpen && (
        <div id={basisId} className="mt-3 border-t border-emerald-100 pt-3" aria-label={`${item.title}匹配依据详情`}>
          {components.length > 0 ? (
            <dl className="space-y-2">
              {components.map(([key, value]) => (
                <div key={key} className="grid grid-cols-[auto_1fr_auto] items-start gap-2 text-xs">
                  <dt className="font-medium text-slate-700">{matchComponentLabels[key]}</dt>
                  <dd className="leading-5 text-slate-500">
                    {matchSourceLabels[item.component_sources?.[key]] || item.component_sources?.[key] || '由当前学习数据计算'}
                  </dd>
                  <dd className="font-mono font-semibold tabular-nums text-emerald-800">
                    {value === null || value === undefined ? '未纳入' : percent(value)}
                  </dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-xs leading-5 text-slate-600">{(item.reasons || []).join('；') || '当前资源作为补充学习材料推荐。'}</p>
          )}
        </div>
      )}
    </article>
  );
}

function MethodologyPanel({ report, resourceReport }) {
  const sources = report.data_sources || [];
  const references = report.methodology?.references || [];
  const dimensions = report.dimensions || [];
  return (
    <details className="group rounded-[24px] bg-white p-5 shadow-sm shadow-emerald-950/5 sm:p-6">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4">
        <span className="flex items-center gap-2 text-sm font-semibold text-slate-950"><FileCheck2 size={16} />监测口径、数据来源与参考依据</span>
        <span className="text-xs text-emerald-800 group-open:hidden">展开审计信息</span>
      </summary>
      <div className="mt-5 grid gap-5 xl:grid-cols-3">
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">持久化来源</h3>
          <div className="mt-3 space-y-2">
            {sources.map((source) => (
              <div key={source.source_id} className="rounded-xl bg-slate-50 px-3 py-2">
                <div className="font-mono text-xs text-slate-800">{source.table || source.source_id}</div>
                <div className="mt-1 text-xs leading-5 text-slate-500">{source.window_days ? `${source.window_days} 天窗口` : '当前状态快照'} · {(source.fields || source.events || []).join('、') || '聚合来源'}</div>
              </div>
            ))}
          </div>
        </section>
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">计算公式</h3>
          <div className="mt-3 space-y-2">
            {dimensions.map((item) => (
              <div key={item.key} className="rounded-xl bg-slate-50 px-3 py-2">
                <div className="text-xs font-medium text-slate-800">{item.label} · {item.evidence_count || 0} 条证据</div>
                <code className="mt-1 block break-words text-[11px] leading-5 text-slate-500">{item.formula || '未提供公式'}</code>
              </div>
            ))}
            <div className="rounded-xl bg-emerald-50 px-3 py-2 text-xs leading-5 text-emerald-900">资源匹配：{resourceReport.methodology?.formula || '尚未生成匹配公式'}</div>
          </div>
        </section>
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">研究与标准参考</h3>
          <div className="mt-3 space-y-3">
            {references.map((reference) => (
              <a key={reference.reference_id} href={reference.url} target="_blank" rel="noreferrer" className="block rounded-xl border border-slate-100 px-3 py-2 transition hover:border-emerald-200 hover:bg-emerald-50/50">
                <div className="text-xs font-medium text-slate-900">{reference.title}</div>
                <div className="mt-1 text-xs leading-5 text-slate-500">{reference.note}</div>
              </a>
            ))}
            {(report.methodology?.limitations || []).map((item) => <p key={item} className="text-xs leading-5 text-amber-800">• {item}</p>)}
          </div>
        </section>
      </div>
    </details>
  );
}

export default function ReportsPage() {
  const [report, setReport] = useState(emptyReport);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const loadReport = async () => {
      setLoading(true);
      setError('');
      const { report: nextReport, error: nextError } = await loadReportsData({ fetcher: fetchJsonWithAuthFallback });
      if (!cancelled) {
        setReport(nextReport);
        setError(nextError);
        setLoading(false);
      }
    };
    loadReport();
    return () => { cancelled = true; };
  }, [reloadKey]);

  const dimensions = report.dimensions?.length
    ? report.dimensions
    : (report.mastery_radar || []).map((item, index) => ({ key: `legacy-${index}`, label: item.name, value: item.value }));
  const overview = report.overview?.stage_name
    ? report.overview
    : {
      stage_name: report.t_stage?.stage_name || '数据积累中',
      summary: report.learner_overview?.current_focus || '完成学习任务和练习后，这里会形成连续的学习状态判断。',
      confidence: 0,
      due_review_count: 0,
    };
  const trendCharts = useMemo(() => {
    const recent = (report.activity_trends?.series || []).slice(-14);
    return [
      { key: 'focus', label: '每日有效学习', suffix: ' 分钟', dates: recent.map((item) => item.date), values: recent.map((item) => Number(item.focus_minutes) || 0) },
      { key: 'tasks', label: '每日任务完成率', suffix: '%', dates: recent.map((item) => item.date), values: recent.map((item) => Math.round((Number(item.task_completion_rate) || 0) * 100)) },
    ];
  }, [report.activity_trends]);
  const resourceReport = report.resource_match_report || emptyReport.resource_match_report;
  const dataQuality = report.data_quality || emptyReport.data_quality;

  return (
    <div className="space-y-6">
      <section className="overflow-hidden rounded-[30px] bg-[#f2f8f4] px-5 pb-6 pt-5 sm:px-7 sm:pb-7">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-3xl">
            <div className="flex items-center gap-2 text-sm font-medium text-emerald-900"><CircleGauge size={17} />最近 {report.window?.days || 30} 天学情</div>
            <h2 className="mt-3 text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">{overview.stage_name}</h2>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-600">{overview.summary}</p>
          </div>
          <button type="button" className="button button--secondary" disabled={loading} onClick={() => setReloadKey((value) => value + 1)}>
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />重新计算
          </button>
        </div>
        <div className="mt-6 grid gap-3 sm:grid-cols-3">
          <div><div className="text-xs text-slate-500">数据覆盖度</div><strong className="mt-1 block font-mono text-xl tabular-nums text-slate-950">{percent(overview.confidence)}</strong></div>
          <div><div className="text-xs text-slate-500">行为与答题样本</div><strong className="mt-1 block font-mono text-xl tabular-nums text-slate-950">{dataQuality.sample_count || 0}</strong></div>
          <div><div className="text-xs text-slate-500">到期复习</div><strong className="mt-1 block font-mono text-xl tabular-nums text-slate-950">{overview.due_review_count || 0}</strong></div>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[0.88fr_1.12fr]">
        <article className="rounded-[28px] bg-white p-5 shadow-sm shadow-emerald-950/5 sm:p-6">
          <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2 text-sm font-semibold text-slate-950"><Activity size={16} />能力结构</div><span className="text-xs text-slate-500">可审计监测计算</span></div>
          {dimensions.length >= 3 ? <RadarChart dimensions={dimensions} /> : <div className="my-8 rounded-2xl bg-slate-50 p-5 text-sm text-slate-500">完成几次真实练习后生成能力结构。</div>}
          <MetricStrip dimensions={dimensions} />
        </article>
        <div className="grid gap-4 sm:grid-cols-2">
          {trendCharts.map((chart) => <LearningTrendChart key={chart.key} chart={chart} />)}
          <article className="rounded-2xl bg-white p-5 shadow-sm shadow-emerald-950/5">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-950"><Target size={16} />需要优先补强</div>
            <div className="mt-4 space-y-3">
              {(report.weak_points || []).slice(0, 4).map((item) => (
                <div key={item.kp_id || item.title} className="flex items-center justify-between gap-4 border-b border-slate-100 pb-3 last:border-0 last:pb-0">
                  <div><div className="text-sm font-medium text-slate-900">{item.kp_name || item.title}</div><div className="mt-1 text-xs text-slate-500">{item.reason || item.evidence}</div></div>
                  {item.mastery_score !== undefined && <span className="font-mono text-sm tabular-nums text-amber-800">{percent(item.mastery_score)}</span>}
                </div>
              ))}
              {(report.weak_points || []).length === 0 && <p className="text-sm leading-6 text-slate-500">暂未发现有足够证据支持的薄弱知识点。</p>}
            </div>
          </article>
          <article className="rounded-2xl bg-white p-5 shadow-sm shadow-emerald-950/5">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-950"><BookOpenCheck size={16} />错题分布</div>
            <div className="mt-4 space-y-3">
              {(report.mistake_distribution || []).slice(0, 5).map((item) => {
                const max = Math.max(1, ...(report.mistake_distribution || []).map((entry) => entry.count || 0));
                return <div key={item.error_type}><div className="flex justify-between gap-3 text-xs"><span className="text-slate-700">{item.error_type}</span><span className="font-mono tabular-nums text-slate-500">{item.count}</span></div><div className="mt-1.5 h-1.5 rounded-full bg-slate-100"><div className="h-full rounded-full bg-amber-500" style={{ width: `${Math.max(6, (item.count / max) * 100)}%` }} /></div></div>;
              })}
              {(report.mistake_distribution || []).length === 0 && <p className="text-sm leading-6 text-slate-500">暂无已确认错因；客观题答错后需先完成错因调研。</p>}
            </div>
          </article>
        </div>
      </section>

      <MultiscaleSummary state={report.multiscale} />

      <section className="rounded-[28px] bg-white p-5 shadow-sm shadow-emerald-950/5 sm:p-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div><div className="flex items-center gap-2 text-sm font-semibold text-slate-950"><DatabaseZap size={16} />资源匹配报告</div><p className="mt-2 text-sm text-slate-600">按知识点覆盖、资源质量、形式偏好、可用时间及有证据的难度信息综合排序。</p></div>
          <div className="text-right"><div className="font-mono text-xl font-semibold tabular-nums text-slate-950">{percent(resourceReport.summary?.coverage)}</div><div className="text-xs text-slate-500">当前目标覆盖</div></div>
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(resourceReport.matches || []).slice(0, 6).map((item) => <ResourceMatchCard key={`${item.resource_type}-${item.resource_id}`} item={item} />)}
        </div>
        {(resourceReport.matches || []).length === 0 && <div className="mt-5 rounded-2xl bg-slate-50 p-5 text-sm text-slate-500">{resourceReport.no_match_reason || '当前没有可验证的匹配资源。'}</div>}
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <article className="rounded-[24px] bg-amber-50/70 p-5">
          <div className="flex items-center gap-2 text-sm font-semibold text-amber-950"><BellRing size={16} />主动干预</div>
          <p className="mt-3 text-sm leading-6 text-amber-950/80">{report.automation?.intervention?.reason || '当前没有需要主动干预的信号；系统会持续检查任务完成率、投入变化和复习压力。'}</p>
        </article>
        <article className="rounded-[24px] bg-emerald-50/70 p-5">
          <div className="flex items-center gap-2 text-sm font-semibold text-emerald-950"><BookOpenCheck size={16} />规划自动复盘</div>
          <p className="mt-3 text-sm leading-6 text-emerald-950/80">{report.automation?.plan_review?.summary || '规划复盘尚未运行。长期规划不会被系统静默覆盖。'}</p>
        </article>
      </section>

      <MethodologyPanel report={report} resourceReport={resourceReport} />

      <section className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-slate-100 px-4 py-3 text-xs text-slate-600">
        <span>数据来源：{(dataQuality.sources || []).length} 类持久化记录</span>
        <span>样本状态：{dataQuality.is_sufficient_for_intervention ? '可用于谨慎干预' : '仅供观察，不触发干预'}</span>
      </section>
      {error && !loading && <div role="alert" className="rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
    </div>
  );
}
