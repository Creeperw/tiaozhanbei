import React, { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle, ChevronRight, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { MAIN_API_BASE, fetchWithAuth } from '../../utils/api';

const levelCopy = {
  excellent: { label: '结构优秀', tone: 'emerald' },
  good: { label: '结构良好', tone: 'teal' },
  review: { label: '建议复核', tone: 'amber' },
  poor: { label: '需要复核', tone: 'rose' },
};

const toneClasses = {
  emerald: 'border-emerald-100 bg-emerald-50 text-emerald-800',
  teal: 'border-teal-100 bg-teal-50 text-teal-800',
  amber: 'border-amber-100 bg-amber-50 text-amber-800',
  rose: 'border-rose-100 bg-rose-50 text-rose-800',
};

const percent = value => `${Math.round((Number(value) || 0) * 100)}%`;

const readPayload = async (response, fallback) => {
  let data = fallback;
  if (typeof response?.text === 'function') {
    const text = await response.text();
    if (text?.trim()) {
      try {
        data = JSON.parse(text);
      } catch {
        data = fallback;
      }
    }
  } else if (typeof response?.json === 'function') {
    data = await response.json().catch(() => fallback);
  }
  if (!response.ok) throw new Error(data?.detail || '识别审查数据加载失败');
  return data;
};

const KnowledgeRecognitionReports = ({ refreshToken = 0 }) => {
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');

  const loadReports = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetchWithAuth(`${MAIN_API_BASE}/knowledge/content/recognition-reports?offset=0&limit=20`);
      const payload = await readPayload(response, { items: [] });
      setReports(Array.isArray(payload.items) ? payload.items : []);
    } catch (loadError) {
      setError(loadError.message || '识别审查数据加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadReports(); }, [loadReports, refreshToken]);

  useEffect(() => {
    if (!detail) return undefined;
    const closeOnEscape = event => {
      if (event.key === 'Escape') setDetail(null);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [detail]);

  const openReport = async report => {
    setDetailLoading(true);
    setDetailError('');
    setDetail({ ...report, pending: true });
    try {
      const response = await fetchWithAuth(`${MAIN_API_BASE}/knowledge/content/recognition-reports/${encodeURIComponent(report.report_id)}`);
      setDetail(await readPayload(response, report));
    } catch (loadError) {
      setDetailError(loadError.message || '审查详情加载失败');
    } finally {
      setDetailLoading(false);
    }
  };

  return (
    <section className="mb-6 rounded-[24px] border border-emerald-100 bg-white/95 p-5 shadow-sm shadow-emerald-100/50" aria-labelledby="recognition-review-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-[.12em] text-emerald-700">Recognition review</p>
          <h2 id="recognition-review-heading" className="mt-1 flex items-center gap-2 text-xl font-bold text-slate-900"><ShieldCheck size={20} />教材识别审查</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-600">根据标准化 Markdown、切片和章节映射按确定性规则计算。该分数反映结构识别质量，不代表医学内容已经人工审定。</p>
        </div>
        <button type="button" onClick={loadReports} disabled={loading} className="inline-flex items-center gap-2 rounded-xl border border-emerald-100 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-800 disabled:opacity-50">
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />刷新审查
        </button>
      </div>

      {error && <div role="alert" className="mt-4 rounded-xl border border-rose-100 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}
      {loading && reports.length === 0 && <div role="status" className="mt-4 text-sm text-slate-500">正在读取教材识别报告…</div>}
      {!loading && !error && reports.length === 0 && <div className="mt-4 rounded-xl bg-slate-50 px-4 py-5 text-center text-sm text-slate-500">上传并完成教材解析后，这里会显示结构识别置信度。</div>}

      <div className="mt-4 grid gap-3 xl:grid-cols-2">
        {reports.map(report => {
          const level = levelCopy[report.confidence_level] || levelCopy.review;
          return (
            <button key={report.report_id} type="button" onClick={() => openReport(report)} className="group rounded-2xl border border-slate-100 bg-[#f8fbf9] p-4 text-left transition hover:border-emerald-200 hover:bg-emerald-50/50">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <strong className="block truncate text-base text-slate-900">{report.title || '用户教材'}</strong>
                  <span className="mt-1 block text-xs text-slate-500">{report.book_count} 本教材 · {Number(report.source_chunk_count || 0).toLocaleString('zh-CN')} 个切片</span>
                </div>
                <ChevronRight size={18} className="shrink-0 text-slate-400 transition-transform group-hover:translate-x-0.5" />
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
                <span className={`rounded-full border px-2 py-1 font-semibold ${toneClasses[level.tone]}`}>结构识别置信度 {percent(report.structural_confidence)}</span>
                <span className={`rounded-full border px-2 py-1 ${toneClasses[level.tone]}`}>{level.label}</span>
                <span className="rounded-full border border-slate-100 bg-white px-2 py-1 text-slate-600">待复核切片 {report.needs_review_chunk_count || 0}</span>
              </div>
            </button>
          );
        })}
      </div>

      {detail && (
        <div className="fixed inset-0 z-[100] grid place-items-center bg-slate-950/45 p-4" role="dialog" aria-modal="true" aria-labelledby="recognition-report-title" onMouseDown={event => event.target === event.currentTarget && setDetail(null)}>
          <div className="max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-3xl bg-white p-6 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs font-bold uppercase tracking-[.12em] text-emerald-700">Read-only review</p>
                <h2 id="recognition-report-title" className="mt-1 text-2xl font-bold text-slate-950">{detail.title || '用户教材'} · 识别审查</h2>
                <p className="mt-1 text-sm text-slate-600">只读报告 · 确定性结构规则 v1</p>
              </div>
              <button type="button" onClick={() => setDetail(null)} aria-label="关闭识别审查详情" className="rounded-xl border border-slate-100 p-2 text-slate-500 hover:bg-slate-50"><X size={18} /></button>
            </div>

            {detailLoading && <div role="status" className="mt-6 text-sm text-slate-500">正在加载审查详情…</div>}
            {detailError && <div role="alert" className="mt-6 rounded-xl border border-rose-100 bg-rose-50 p-3 text-sm text-rose-700">{detailError}</div>}
            {!detailLoading && !detailError && (
              <>
                <div className="mt-6 grid gap-3 sm:grid-cols-3">
                  <ReviewStat label="结构识别置信度" value={percent(detail.structural_confidence)} />
                  <ReviewStat label="切片映射" value={`${Number(detail.mapped_chunk_count || 0).toLocaleString('zh-CN')} / ${Number(detail.source_chunk_count || 0).toLocaleString('zh-CN')}`} />
                  <ReviewStat label="待复核切片" value={Number(detail.needs_review_chunk_count || 0).toLocaleString('zh-CN')} />
                </div>
                <div className="mt-6">
                  <h3 className="text-base font-bold text-slate-900">分项指标</h3>
                  <div className="mt-3 space-y-2">
                    {(detail.metrics || []).map(metric => (
                      <div key={metric.key} className="flex items-center justify-between gap-4 rounded-xl border border-slate-100 px-3 py-2 text-sm">
                        <span className="flex items-center gap-2 text-slate-700">{metric.passed ? <CheckCircle size={15} className="text-emerald-600" /> : <AlertTriangle size={15} className="text-amber-600" />}{metric.label}</span>
                        <strong className="text-slate-900">{metric.ratio == null ? '—' : percent(metric.ratio)}{metric.denominator != null ? ` · ${metric.numerator}/${metric.denominator}` : ''}</strong>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="mt-6">
                  <h3 className="text-base font-bold text-slate-900">问题与风险 <span className="text-sm font-normal text-slate-500">({detail.issue_count_total || 0})</span></h3>
                  {(detail.issues || []).length === 0 ? (
                    <div className="mt-3 rounded-xl border border-emerald-100 bg-emerald-50 p-3 text-sm text-emerald-800">未发现结构识别异常。</div>
                  ) : (
                    <div className="mt-3 max-h-64 space-y-2 overflow-y-auto">
                      {(detail.issues || []).map((issue, index) => (
                        <div key={`${issue.code}-${issue.chunk_uid || issue.node_id || index}`} className={`rounded-xl border px-3 py-2 text-sm ${issue.severity === 'error' ? toneClasses.rose : toneClasses.amber}`}>
                          <strong>{issue.code}</strong><span className="ml-2">{issue.message}</span>{issue.chunk_uid && <code className="ml-2 text-xs">{issue.chunk_uid}</code>}
                        </div>
                      ))}
                    </div>
                  )}
                  {detail.issues_truncated && <p className="mt-2 text-xs text-slate-500">问题较多，当前仅展示前 100 条。</p>}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </section>
  );
};

const ReviewStat = ({ label, value }) => (
  <div className="rounded-2xl border border-emerald-50 bg-emerald-50/50 p-4">
    <p className="text-xs font-medium text-slate-500">{label}</p>
    <strong className="mt-1 block text-2xl text-slate-900">{value}</strong>
  </div>
);

export default KnowledgeRecognitionReports;
