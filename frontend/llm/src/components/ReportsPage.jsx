import React, { useEffect, useState } from 'react';
import { Activity, BarChart3, CheckCircle2, LineChart, Target } from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { emptyReport, loadReportsData } from '../pageDataLoaders.js';

export default function ReportsPage() {
  const [report, setReport] = useState(emptyReport);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const loadReport = async () => {
      setLoading(true);
      setError('');
      const { report: nextReport, error: nextError } = await loadReportsData({
        fetcher: fetchJsonWithAuthFallback,
      });
      if (!cancelled) {
        setReport(nextReport);
        setError(nextError);
        setLoading(false);
      }
    };

    loadReport();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-6">
      <section className="personalization-task-summary rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm shadow-slate-200/60">
        <div className="mb-5 flex items-center gap-2 text-sm font-medium text-slate-500">
          <LineChart size={16} />
          学情报告初版
        </div>
        <h2 className="text-2xl font-semibold tracking-tight text-slate-950">
          {report.learner_overview.goal || (loading ? '正在生成学情报告…' : '待完善学习目标')}
        </h2>
        <p className="mt-3 text-sm leading-6 text-slate-600">
          当前群体：{report.learner_overview.learner_group || '未填写'}；报告重点：{report.learner_overview.current_focus || '待积累学习信号'}。
        </p>
      </section>

      <section className="grid gap-6 lg:grid-cols-[1fr_0.9fr]">
        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/60 sm:p-6">
          <div className="mb-5 flex items-center gap-2 text-sm font-medium text-slate-500">
            <BarChart3 size={16} />
            能力雷达摘要
          </div>
          <div className="space-y-4">
            {report.mastery_radar.map((item) => (
              <div key={item.name}>
                <div className="mb-2 flex items-center justify-between text-sm">
                  <span className="font-medium text-slate-800">{item.name}</span>
                  <span className="text-slate-500">{Math.round((item.value || 0) * 100)}%</span>
                </div>
                <div className="h-2 rounded-full bg-slate-100">
                  <div
                    className="h-2 rounded-full bg-emerald-500"
                    style={{ width: `${Math.max(6, Math.round((item.value || 0) * 100))}%` }}
                  />
                </div>
              </div>
            ))}
            {report.mastery_radar.length === 0 && (
              <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">
                {loading ? '正在加载能力数据…' : '暂无能力数据，完成练习后将生成雷达摘要。'}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-6">
          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/60 sm:p-6">
            <div className="mb-5 flex items-center gap-2 text-sm font-medium text-slate-500">
              <Target size={16} />
              薄弱点与错因
            </div>
            <div className="space-y-3">
              {report.weak_points.map((item) => (
                <div key={`${item.title}-${item.evidence}`} className="rounded-2xl border border-amber-100 bg-amber-50/70 p-4">
                  <div className="text-sm font-semibold text-slate-900">{item.title}</div>
                  <p className="mt-2 text-sm leading-6 text-slate-700">{item.evidence}</p>
                </div>
              ))}
            </div>
            <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm leading-6 text-slate-700">
              错题数：{report.mistake_summary.total_mistakes ?? 0}；主要错因：{report.mistake_summary.top_error_type || '暂无'}
            </div>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/60 sm:p-6">
            <div className="mb-5 flex items-center gap-2 text-sm font-medium text-slate-500">
              <Activity size={16} />
              T 阶段与资源匹配
            </div>
            <div className="rounded-2xl border border-emerald-100 bg-emerald-50/70 p-4">
              <div className="text-sm font-semibold text-slate-900">{report.t_stage.stage_name || '待积累行为数据'}</div>
              <p className="mt-2 text-sm leading-6 text-slate-700">推荐难度：{report.resource_match.recommended_difficulty || '待判断'}；匹配度：{Math.round((report.resource_match.difficulty_match || 0) * 100)}%</p>
            </div>
            <div className="mt-4 space-y-2">
              {(report.next_actions || []).map((item) => (
                <div key={item} className="flex items-start gap-2 rounded-2xl border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                  <CheckCircle2 size={16} className="mt-0.5 text-emerald-600" />
                  {item}
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {error && !loading && (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>
      )}
    </div>
  );
}
