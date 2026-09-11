import React, { useEffect, useMemo, useState } from 'react';
import {
  CalendarDays,
  CheckCircle2,
  Clock3,
  FileCheck2,
  ListChecks,
  Loader2,
  X,
  Stethoscope,
  Target,
} from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import {
  categoryForActivity,
  isVerifiedPracticeActivity,
  patientModeForActivity,
} from './trainingHistoryActivity';

const historyGroups = [
  { key: 'special_training', title: '专项特训', description: '核心知识点巩固记录', icon: Target, tone: 'cyan' },
  { key: 'topic_training', title: '知识点特训', description: '章节与知识点练习记录', icon: Stethoscope, tone: 'teal' },
  { key: 'paper_workspace', title: '智能组卷', description: '试卷逐题作答记录（题数，不是试卷份数）', icon: FileCheck2, tone: 'green' },
  { key: 'question_training', title: '综合套题', description: '综合题与案例练习记录', icon: CheckCircle2, tone: 'emerald' },
  {
    key: 'ai_patient_simulation',
    title: '模拟病患',
    description: '问诊、辨证与处方记录',
    icon: Stethoscope,
    tone: 'rose',
    subgroups: [
      { key: 'plus', title: '随心练' },
      { key: 'acupuncture', title: '针灸专练' },
      { key: 'topic', title: '题型专练' },
    ],
  },
];

const groupToneClasses = {
  cyan: 'border-cyan-100 bg-cyan-50/45 text-cyan-800',
  teal: 'border-teal-100 bg-teal-50/45 text-teal-800',
  green: 'border-emerald-100 bg-emerald-50/45 text-emerald-800',
  emerald: 'border-emerald-100 bg-emerald-50/45 text-emerald-800',
  rose: 'border-rose-100 bg-rose-50/45 text-rose-800',
};

function activityStatus(activity) {
  const status = String(activity?.completion_status || '').toLowerCase();
  return ['completed', 'complete', 'done', 'submitted', 'passed'].some((value) => status.includes(value))
    ? { label: '已完成', className: 'bg-emerald-100 text-emerald-800' }
    : { label: '未完成', className: 'bg-amber-100 text-amber-800' };
}

function formatActivityDate(value) {
  if (!value) return '时间待记录';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace('T', ' ');
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function HistoryGroup({ group, activities, onViewAll }) {
  const Icon = group.icon;
  return (
    <article className={`training-history__group rounded-2xl border p-4 shadow-sm shadow-slate-100 ${groupToneClasses[group.tone]}`}>
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-white/80 shadow-sm"><Icon aria-hidden="true" size={20} /></span>
          <div className="min-w-0">
            <h3 className="truncate text-base font-bold text-slate-950">{group.title}</h3>
            <p className="mt-0.5 text-xs text-slate-600">{group.description}</p>
          </div>
        </div>
        <button type="button" className="training-history__group-action rounded-lg border border-white/90 bg-white/80 px-2.5 py-1.5 text-[11px] font-semibold text-emerald-700 shadow-sm transition hover:bg-white hover:text-emerald-800" onClick={() => onViewAll(group, activities)}>
          查看全部
        </button>
      </header>
      {activities.length > 0 ? (
        <div className="mt-4 space-y-2">
          {activities.slice(0, 2).map((activity, index) => {
            const status = activityStatus(activity);
            return (
              <div key={activity.activity_id || `${group.key}-${index}`} className="rounded-xl border border-white/80 bg-white/80 p-3">
                <div className="flex items-start justify-between gap-2">
                  <p className="min-w-0 truncate text-sm font-semibold text-slate-900">{activity.title || group.title}</p>
                  <span className={`shrink-0 rounded-md px-2 py-1 text-[11px] font-semibold ${status.className}`}>{status.label}</span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
                  <span className="inline-flex items-center gap-1"><CalendarDays size={13} />{formatActivityDate(activity.created_at || activity.timestamp)}</span>
                  {activity.duration_minutes > 0 && <span className="inline-flex items-center gap-1"><Clock3 size={13} />{activity.duration_minutes} 分钟</span>}
                  {activity.score !== null && activity.score !== undefined && <span>得分 {Math.round(Number(activity.score) <= 1 ? Number(activity.score) * 100 : Number(activity.score))}%</span>}
                </div>
              </div>
            );
          })}
          {activities.length > 2 && <p className="pt-1 text-center text-[11px] text-slate-500">已展示最新两条记录</p>}
        </div>
      ) : (
        <div className="mt-4 rounded-xl border border-dashed border-white/90 bg-white/55 px-3 py-5 text-center text-xs leading-5 text-slate-500">完成该类型练习后，记录会显示在这里。</div>
      )}
    </article>
  );
}

function PatientHistoryGroup({ group, activities, onViewAll }) {
  const Icon = group.icon;
  const grouped = Object.fromEntries(group.subgroups.map((subgroup) => [subgroup.key, []]));
  activities.forEach((activity) => grouped[patientModeForActivity(activity)].push(activity));

  return (
    <article className={`training-history__group training-history__group--patient rounded-2xl border p-4 shadow-sm shadow-slate-100 ${groupToneClasses[group.tone]}`}>
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-white/80 shadow-sm"><Icon aria-hidden="true" size={20} /></span>
          <div className="min-w-0">
            <h3 className="truncate text-base font-bold text-slate-950">{group.title}</h3>
            <p className="mt-0.5 text-xs text-slate-600">{group.description}</p>
          </div>
        </div>
        <button type="button" className="training-history__group-action rounded-lg border border-white/90 bg-white/80 px-2.5 py-1.5 text-[11px] font-semibold text-emerald-700 shadow-sm transition hover:bg-white hover:text-emerald-800" onClick={() => onViewAll(group, activities)}>
          查看全部
        </button>
      </header>
      <div className="training-history__patient-modes mt-4 grid gap-3 md:grid-cols-3">
        {group.subgroups.map((subgroup) => (
          <section key={subgroup.key} className="training-history__patient-mode rounded-xl border border-white/80 bg-white/65 p-3">
            <div className="flex items-center justify-between gap-2">
              <h4 className="text-sm font-bold text-slate-900">{subgroup.title}</h4>
              <span className="rounded-full bg-white/85 px-2 py-0.5 text-[11px] font-bold text-slate-600">{grouped[subgroup.key].length}</span>
            </div>
            {grouped[subgroup.key].length > 0 ? (
              <div className="mt-3 space-y-2">
                {grouped[subgroup.key].slice(0, 2).map((activity, index) => {
                  const status = activityStatus(activity);
                  return (
                    <div key={activity.activity_id || `${subgroup.key}-${index}`} className="rounded-lg border border-white/90 bg-white/85 p-2.5">
                      <div className="flex items-start justify-between gap-2">
                        <p className="min-w-0 truncate text-xs font-semibold text-slate-900">{activity.title || subgroup.title}</p>
                        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${status.className}`}>{status.label}</span>
                      </div>
                      <p className="mt-1 text-[11px] text-slate-500">{formatActivityDate(activity.created_at || activity.timestamp)}</p>
                    </div>
                  );
                })}
                {grouped[subgroup.key].length > 2 && <p className="pt-1 text-center text-[11px] text-slate-500">已展示最新两条记录</p>}
              </div>
            ) : (
              <p className="mt-3 rounded-lg border border-dashed border-white/90 px-2 py-3 text-center text-[11px] leading-5 text-slate-500">暂无记录</p>
            )}
          </section>
        ))}
      </div>
    </article>
  );
}

export default function TrainingHistoryPanel({ enabled = true }) {
  const [activities, setActivities] = useState([]);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState('');
  const [selectedGroup, setSelectedGroup] = useState(null);

  useEffect(() => {
    if (!enabled) return undefined;
    let active = true;
    const loadHistory = async () => {
      const records = [];
      let offset = 0;
      do {
        const result = await fetchJsonWithAuthFallback({
          paths: [`/v1/practice/history?days=30&offset=${offset}&limit=100`],
          validator: (value) => value && typeof value === 'object' && Array.isArray(value.recent_activities),
        });
        if (!active) return [];
        records.push(...result.data.recent_activities);
        const next = result.data.next_offset;
        if (next === null || next === undefined) break;
        if (!Number.isInteger(next) || next <= offset) throw new Error('历史分页数据异常');
        offset = next;
      } while (active);
      return records;
    };
    loadHistory().then((records) => {
      if (!active) return;
      setActivities(records.filter(isVerifiedPracticeActivity));
      setError('');
    }).catch((reason) => {
      if (!active) return;
      setActivities([]);
      setError(reason.message || '历史记录加载失败');
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [enabled]);

  const groupedActivities = useMemo(() => {
    const grouped = Object.fromEntries(historyGroups.map((group) => [group.key, []]));
    activities.forEach((activity) => {
      const category = categoryForActivity(activity);
      if (category) grouped[category].push(activity);
    });
    return grouped;
  }, [activities]);

  const questionActivities = activities.filter((activity) => activity.attempt_type !== 'case'
    && activity.activity_type !== 'case_training');
  const completedCount = questionActivities.filter((activity) => activityStatus(activity).label === '已完成').length;
  const caseCount = activities.length - questionActivities.length;
  const latestActivity = activities[0];
  const openGroupHistory = (group, groupActivities) => setSelectedGroup({ key: group.key, title: `${group.title} · 全部记录`, activities: groupActivities });

  return (
    <section className="training-history-panel space-y-5" aria-label="历史记录" aria-busy={loading}>
      {loading && <div className="flex items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-8 text-sm text-slate-500"><Loader2 className="animate-spin" size={17} />正在加载练习记录…</div>}
      {error && !loading && <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
      {!loading && (
        <>
          <div className="training-history__summary grid gap-3 sm:grid-cols-3">
            <div className="rounded-2xl border border-emerald-100 bg-emerald-50/70 px-4 py-3">
              <span className="inline-flex items-center gap-2 text-xs font-semibold text-emerald-700"><ListChecks size={15} />练习记录</span>
              <strong className="mt-1 block text-2xl font-bold text-slate-950">{questionActivities.length}</strong>
              <span className="text-xs text-slate-500">近 30 天作答题数；模拟病患另计 {caseCount} 次</span>
            </div>
            <div className="rounded-2xl border border-cyan-100 bg-cyan-50/70 px-4 py-3">
              <span className="inline-flex items-center gap-2 text-xs font-semibold text-cyan-700"><CheckCircle2 size={15} />已完成</span>
              <strong className="mt-1 block text-2xl font-bold text-slate-950">{completedCount}</strong>
              <span className="text-xs text-slate-500">与练习概览使用同一作答口径</span>
            </div>
            <div className="rounded-2xl border border-violet-100 bg-violet-50/70 px-4 py-3">
              <span className="inline-flex items-center gap-2 text-xs font-semibold text-violet-700"><CalendarDays size={15} />最近练习</span>
              <strong className="mt-1 block truncate text-base font-bold text-slate-950">{latestActivity ? formatActivityDate(latestActivity.created_at || latestActivity.timestamp) : '暂无记录'}</strong>
              <span className="text-xs text-slate-500">持续记录学习进度</span>
            </div>
          </div>
          <div className="training-history__grid grid gap-4 lg:grid-cols-2">{historyGroups.map((group) => group.subgroups ? <PatientHistoryGroup key={group.key} group={group} activities={groupedActivities[group.key]} onViewAll={openGroupHistory} /> : <HistoryGroup key={group.key} group={group} activities={groupedActivities[group.key]} onViewAll={openGroupHistory} />)}</div>
        </>
      )}
      {selectedGroup && (
        <div className="training-history__modal fixed inset-0 z-50 grid place-items-center bg-slate-950/35 p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedGroup(null); }}>
          <section className="max-h-[80vh] w-full max-w-2xl overflow-hidden rounded-2xl bg-white shadow-2xl" role="dialog" aria-modal="true" aria-labelledby="training-history-modal-title">
            <header className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
              <div><h2 id="training-history-modal-title" className="text-lg font-bold text-slate-950">{selectedGroup.title}</h2><p className="mt-1 text-xs text-slate-500">共 {selectedGroup.activities.length} 条记录</p></div>
              <button type="button" aria-label="关闭全部练习记录" className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900" onClick={() => setSelectedGroup(null)}><X size={18} /></button>
            </header>
            <div className="max-h-[calc(80vh-88px)] space-y-2 overflow-y-auto p-5">
              {selectedGroup.activities.length > 0 ? selectedGroup.activities.map((activity, index) => {
                const status = activityStatus(activity);
                const category = categoryForActivity(activity);
                const group = historyGroups.find((item) => item.key === category);
                return <div key={activity.activity_id || `all-${index}`} className="rounded-xl border border-slate-100 bg-slate-50/70 p-3"><div className="flex items-start justify-between gap-3"><div><p className="text-sm font-semibold text-slate-900">{activity.title || group?.title || '练习记录'}</p><p className="mt-1 text-xs text-slate-500">{group?.title || '其他练习'} · {formatActivityDate(activity.created_at || activity.timestamp)}</p></div><span className={`shrink-0 rounded-md px-2 py-1 text-[11px] font-semibold ${status.className}`}>{status.label}</span></div></div>;
              }) : <p className="py-12 text-center text-sm text-slate-500">暂无已做练习记录</p>}
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
