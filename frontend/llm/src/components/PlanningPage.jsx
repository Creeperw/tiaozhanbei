import React, { useEffect, useState } from 'react';
import { BookOpen, CalendarCheck, Clock, Sparkles } from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { emptyPlan, loadPlanningData } from '../pageDataLoaders.js';

export default function PlanningPage() {
  const [plan, setPlan] = useState(emptyPlan);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const loadPlan = async () => {
      setLoading(true);
      setError('');
      const { plan: nextPlan, error: nextError } = await loadPlanningData({
        fetcher: fetchJsonWithAuthFallback,
      });
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

      <section className="grid gap-6 lg:grid-cols-[0.95fr_1.05fr]">
        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/60 sm:p-6">
          <div className="mb-5 flex items-center gap-2 text-sm font-medium text-slate-500">
            <CalendarCheck size={16} />
            本周计划卡
          </div>
          <h3 className="text-lg font-semibold text-slate-950">{plan.weekly_plan.focus || '待生成本周重点'}</h3>
          <p className="mt-3 text-sm leading-6 text-slate-700">{plan.weekly_plan.acceptance || '完成练习与复盘后，将生成更明确的验收标准。'}</p>
          {plan.weekly_plan.evidence?.length > 0 && (
            <div className="mt-4 space-y-2">
              {plan.weekly_plan.evidence.map((item) => (
                <div key={item} className="rounded-2xl border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600">{item}</div>
              ))}
            </div>
          )}
        </div>

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
                {loading ? '正在加载任务卡…' : '暂无任务卡，完成一次练习批改后会自动增强规划。'}
              </div>
            )}
          </div>
        </div>
      </section>

      {error && !loading && (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>
      )}
    </div>
  );
}
