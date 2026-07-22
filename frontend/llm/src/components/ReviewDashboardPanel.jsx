import React, { useEffect, useMemo, useState } from 'react';
import { BrainCircuit, CalendarClock, CheckCircle2, History, RefreshCw } from 'lucide-react';

import { emptyReviewDashboard, loadReviewDashboard } from '../pageDataLoaders';
import { fetchJsonWithAuthFallback } from '../utils/api';

const formatTime = (value) => {
  if (!value) return '尚未安排';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '尚未安排' : date.toLocaleString('zh-CN', { hour12: false });
};

const masteryTone = (score) => {
  if (score >= 80) return 'bg-emerald-500';
  if (score >= 60) return 'bg-amber-400';
  return 'bg-rose-400';
};

export default function ReviewDashboardPanel() {
  const [dashboard, setDashboard] = useState(emptyReviewDashboard);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let active = true;
    loadReviewDashboard({ fetcher: fetchJsonWithAuthFallback }).then((result) => {
      if (!active) return;
      setDashboard(result.dashboard);
      setError(result.error);
      setLoading(false);
    });
    return () => { active = false; };
  }, [refreshKey]);

  const names = useMemo(() => new Map(
    (dashboard.mastery || []).map((item) => [item.kp_id, item.kp_name || item.kp_id]),
  ), [dashboard.mastery]);
  const queueEntries = dashboard.queue?.entries || [];

  return (
    <div className="space-y-5">
      <section className="rounded-[30px] border border-emerald-100 bg-white/90 p-6 shadow-lg shadow-emerald-100/40">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-xl font-black text-slate-900">复习与掌握</h2>
            <p className="mt-1 text-sm text-slate-500">仅统计已完成并通过批改的知识点题目；生成知识卡本身不会进入复习队列。</p>
          </div>
          <button type="button" onClick={() => { setLoading(true); setRefreshKey((value) => value + 1); }} className="inline-flex items-center gap-2 rounded-xl border border-emerald-100 bg-white px-3 py-2 text-sm text-emerald-800">
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />刷新
          </button>
        </div>
        {error && <p className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">{error}</p>}
        <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[
            [BrainCircuit, '已评估知识点', dashboard.summary?.knowledge_point_count ?? 0],
            [CheckCircle2, '平均掌握度', dashboard.summary?.average_mastery == null ? '尚未评估' : `${dashboard.summary.average_mastery}%`],
            [CalendarClock, '当前到期', dashboard.summary?.due_count ?? 0],
            [History, '掌握记录', dashboard.summary?.history_count ?? 0],
          ].map(([Icon, label, value]) => (
            <div key={label} className="rounded-2xl border border-emerald-100 bg-emerald-50/50 p-4">
              {React.createElement(Icon, { size: 18, className: 'text-emerald-700' })}
              <div className="mt-3 text-2xl font-black text-slate-900">{value}</div>
              <div className="mt-1 text-xs text-slate-500">{label}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-[30px] border border-emerald-100 bg-white/90 p-6 shadow-sm">
        <h3 className="font-black text-slate-900">复习队列</h3>
        <div className="mt-4 space-y-3">
          {queueEntries.map((entry) => {
            const unit = entry.memory_unit || {};
            return (
              <article key={`${unit.kp_id}:${unit.next_review_at}`} className="rounded-2xl border border-slate-100 bg-slate-50/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <strong className="text-slate-900">{names.get(unit.kp_id) || unit.prompt_abstract || unit.kp_id}</strong>
                  <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${entry.is_due ? 'bg-rose-100 text-rose-700' : 'bg-emerald-100 text-emerald-700'}`}>{entry.is_due ? '已到期' : '待复习'}</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500">
                  <span>掌握度 {Number(unit.mastery_score || 0).toFixed(1)}%</span>
                  <span>预计保持率 {(Number(entry.retention_estimate || 0) * 100).toFixed(0)}%</span>
                  <span>下次复习 {formatTime(unit.next_review_at)}</span>
                </div>
              </article>
            );
          })}
          {!loading && queueEntries.length === 0 && <p className="rounded-2xl border border-dashed border-slate-200 p-5 text-sm text-slate-500">当前没有复习任务。完成知识点配套题并通过批改后，会自动加入这里。</p>}
        </div>
      </section>

      <section className="rounded-[30px] border border-emerald-100 bg-white/90 p-6 shadow-sm">
        <h3 className="font-black text-slate-900">知识点掌握度</h3>
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          {(dashboard.mastery || []).map((item) => (
            <article key={item.kp_id} className="rounded-2xl border border-slate-100 p-4">
              <div className="flex items-center justify-between gap-3 text-sm">
                <strong className="truncate text-slate-900">{item.kp_name || item.kp_id}</strong>
                <span className="font-bold text-slate-700">{Number(item.mastery_score || 0).toFixed(1)}%</span>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100"><i className={`block h-full rounded-full ${masteryTone(Number(item.mastery_score || 0))}`} style={{ width: `${Math.max(0, Math.min(100, Number(item.mastery_score || 0)))}%` }} /></div>
              <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
                <span>作答 {item.attempt_count || 0} 次</span>
                <span>阶段 {item.review_stage || 'new'}</span>
                <span>最近复习 {formatTime(item.last_review_at)}</span>
              </div>
            </article>
          ))}
          {!loading && (dashboard.mastery || []).length === 0 && <p className="text-sm text-slate-500">尚无经过批改的知识点掌握记录。</p>}
        </div>
      </section>

      <section className="rounded-[30px] border border-emerald-100 bg-white/90 p-6 shadow-sm">
        <h3 className="font-black text-slate-900">最近复习与掌握变化</h3>
        <div className="mt-4 max-h-[360px] space-y-2 overflow-y-auto pr-1">
          {(dashboard.mastery_history || []).map((item) => (
            <div key={item.history_id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-100 px-4 py-3 text-sm">
              <span className="font-medium text-slate-800">{item.kp_name || item.kp_id}</span>
              <span className="text-slate-600">掌握度 {Number(item.mastery_score || 0).toFixed(1)}%</span>
              <time className="text-xs text-slate-400">{formatTime(item.calculated_at)}</time>
            </div>
          ))}
          {!loading && (dashboard.mastery_history || []).length === 0 && <p className="text-sm text-slate-500">完成首次有效作答后，这里会展示掌握度变化历史。</p>}
        </div>
      </section>
    </div>
  );
}
