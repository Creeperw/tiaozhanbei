import React, { useEffect, useState } from 'react';
import { BookOpen, Clock, RefreshCcw, Sparkles } from 'lucide-react';
import { API_BASE, fetchJsonWithAuthFallback, fetchWithAuth, readJsonResponse } from '../utils/api';
import { emptyPlan, loadPlanningData } from '../pageDataLoaders.js';

export default function PlanningPage() {
  const [plan, setPlan] = useState(emptyPlan);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [latestReview, setLatestReview] = useState(null);
  const [reviewBusy, setReviewBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const loadPlan = async () => {
      setLoading(true);
      setError('');
      const { plan: nextPlan, error: nextError } = await loadPlanningData({
        fetcher: fetchJsonWithAuthFallback,
      });
      try {
        const reviewRes = await fetchWithAuth(`${API_BASE}/v1/plan-reviews?limit=1`);
        const reviewData = await readJsonResponse(reviewRes, { items: [] });
        if (!cancelled && reviewRes.ok) setLatestReview(reviewData.items?.[0] || null);
      } catch {
        if (!cancelled) setLatestReview(null);
      }
      if (!cancelled) {
        setPlan(nextPlan);
        setError(nextError);
        setLoading(false);
      }
    };

    loadPlan();
    return () => {
      cancelled = true;
    };
  }, []);

  const runReview = async () => {
    setReviewBusy(true);
    setError('');
    try {
      const response = await fetchWithAuth(`${API_BASE}/v1/plan-reviews/run`, { method: 'POST' });
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(payload.detail || '规划复盘失败');
      setLatestReview(payload);
    } catch (reviewError) {
      setError(reviewError.message || '规划复盘失败');
    } finally {
      setReviewBusy(false);
    }
  };

  const decideReview = async (decision) => {
    if (!latestReview?.review_id) return;
    setReviewBusy(true);
    try {
      const response = await fetchWithAuth(`${API_BASE}/v1/plan-reviews/${latestReview.review_id}/decision`, { method: 'POST', body: JSON.stringify({ decision }) });
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(payload.detail || '复盘决定保存失败');
      setLatestReview(payload);
    } catch (reviewError) {
      setError(reviewError.message || '复盘决定保存失败');
    } finally {
      setReviewBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <section className="personalization-task-summary rounded-[28px] border border-emerald-100 bg-emerald-50/70 p-5 shadow-sm shadow-emerald-100/60 sm:p-6">
        <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-white px-3 py-1 text-sm font-medium text-emerald-800">
          <Sparkles size={16} />
          长短期规划摘要
        </div>
        <h2 className="text-2xl font-semibold tracking-tight text-slate-950">
          {plan.plan_summary.goal || (loading ? '正在生成学习规划…' : '先完善学习目标')}
        </h2>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
          当前群体：{plan.plan_summary.learner_group || '未填写'}；当前重点：{plan.plan_summary.current_focus || '待积累学习信号'}。
        </p>
      </section>

      <section className="grid gap-6 xl:grid-cols-2" aria-label="规划文字说明">
        <article className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/60 sm:p-6">
          <div className="mb-4 text-sm font-semibold text-slate-950">长期规划说明</div>
          <div className="whitespace-pre-wrap text-sm leading-7 text-slate-700">
            {plan.long_term_plan_content || (loading ? '正在读取长期规划…' : '尚未制定长期规划。')}
          </div>
        </article>
        <article className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/60 sm:p-6">
          <div className="mb-4 text-sm font-semibold text-slate-950">短期规划说明</div>
          <div className="whitespace-pre-wrap text-sm leading-7 text-slate-700">
            {plan.short_term_plan_content || (loading ? '正在读取短期规划…' : '尚未制定短期规划。')}
          </div>
        </article>
      </section>

      {plan.long_term_plan_stages.length > 0 && (
        <section className="rounded-[28px] border border-emerald-100 bg-white p-5 shadow-sm shadow-emerald-100/60 sm:p-6" aria-label="长期规划阶段路线">
          <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-slate-950">
            <BookOpen size={16} />
            长期规划阶段路线
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            {plan.long_term_plan_stages.map((stage) => (
              <article key={stage.stage} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                <div className="text-sm font-semibold text-emerald-900">第 {stage.stage} 阶段</div>
                <div className="mt-2 text-sm leading-6 text-slate-700">{stage.goal}</div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {(stage.book || []).map((book) => (
                    <span key={book} className="rounded-full border border-emerald-100 bg-white px-2.5 py-1 text-xs text-emerald-800">{book}</span>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      <section>
        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/60 sm:p-6">
          <div className="mb-5 flex items-center gap-2 text-sm font-medium text-slate-500">
            <BookOpen size={16} />
            今日任务卡
          </div>
          <div className="space-y-3">
            {plan.daily_tasks.map((task) => (
              <div key={task.key} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm font-semibold text-slate-900">{task.title}</div>
                  <div className="inline-flex items-center gap-1 rounded-full bg-white px-2 py-1 text-xs text-slate-500">
                    <Clock size={12} />
                    {task.duration_min} 分钟
                  </div>
                </div>
                <p className="mt-2 text-sm leading-6 text-slate-700">{task.reason}</p>
              </div>
            ))}
            {plan.daily_tasks.length === 0 && (
              <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
                {loading ? '正在加载今日任务…' : '尚未制定今日任务，请先完成短期计划，再让智能助教安排今天的任务。'}
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="rounded-[28px] bg-[#f2f8f4] p-5 sm:p-6" aria-label="规划自动复盘">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div><div className="flex items-center gap-2 text-sm font-semibold text-emerald-950"><RefreshCcw size={16} />规划自动复盘</div><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">系统结合任务完成率、知识点掌握度和到期复习压力检查计划。长期规划只会给出提案，不会被静默修改。</p></div>
          <button type="button" className="button button--secondary" disabled={reviewBusy} onClick={runReview}>{reviewBusy ? '复盘中…' : '立即复盘'}</button>
        </div>
        {latestReview ? <article className="mt-5 rounded-2xl bg-white p-4"><div className="flex flex-wrap items-center justify-between gap-3"><strong className="text-sm text-slate-950">{latestReview.summary}</strong><span className="text-xs text-slate-500">{latestReview.period_key}</span></div><div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-600">{(latestReview.evidence || []).map((item) => <span key={item} className="rounded-lg bg-slate-100 px-2 py-1">{item}</span>)}</div>{latestReview.status === 'proposal_pending' && <div className="mt-4 flex gap-2"><button type="button" className="button button--primary" disabled={reviewBusy} onClick={() => decideReview('accept')}>接受调整</button><button type="button" className="button button--secondary" disabled={reviewBusy} onClick={() => decideReview('reject')}>保持原计划</button></div>}</article> : <div className="mt-5 rounded-2xl bg-white/70 p-4 text-sm text-slate-500">尚无复盘记录。系统会按周生成，或者你可以立即运行一次。</div>}
      </section>

      {error && !loading && (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>
      )}
    </div>
  );
}
