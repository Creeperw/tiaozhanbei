import React, { useEffect, useMemo, useState } from 'react';
import { BookOpenCheck, ChevronRight, Clock3, Loader2 } from 'lucide-react';
import { fetchWithAuth, readJsonResponse } from '../utils/api';
import QualificationAttemptWorkspace from './QualificationAttemptWorkspace';

const request = async (path, options = {}) => {
  const response = await fetchWithAuth(`/api/v1${path}`, options);
  const data = await readJsonResponse(response, {});
  if (!response.ok) throw new Error(data.detail || '请求失败');
  return data;
};

export default function QualificationPaperPanel({ enabled }) {
  const [catalog, setCatalog] = useState({ exams: [], papers: [] });
  const [examId, setExamId] = useState('');
  const [year, setYear] = useState('');
  const [paperType, setPaperType] = useState('');
  const [selectedPaper, setSelectedPaper] = useState(null);
  const [duration, setDuration] = useState(60);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(null);

  useEffect(() => {
    let active = true;
    request('/qualification-papers/catalog').then((data) => {
      if (active) setCatalog(data);
    }).catch((reason) => active && setError(reason.message));
    return () => { active = false; };
  }, []);

  const selectedExam = catalog.exams.find((item) => item.exam_id === examId);
  const papers = useMemo(() => catalog.papers.filter((item) => (
    (!examId || item.exam_id === examId)
    && (!year || item.year === year)
    && (!paperType || item.paper_type === paperType)
  )), [catalog.papers, examId, year, paperType]);
  const years = [...new Set(catalog.papers.filter((item) => item.exam_id === examId).map((item) => item.year))];
  const types = [...new Set(catalog.papers.filter((item) => item.exam_id === examId).map((item) => item.paper_type))];

  const openAttempt = async (answerMode) => {
    if (!selectedPaper) return;
    setLoading(true);
    setError('');
    try {
      const body = { answer_mode: answerMode };
      if (answerMode === 'test') body.duration_minutes = duration;
      const attempt = await request(`/qualification-papers/${selectedPaper.template_id}/attempts`, {
        method: 'POST', body: JSON.stringify(body),
      });
      sessionStorage.setItem('qualification-paper-attempt-id', attempt.attempt_id);
      setAttempt(attempt);
    } catch (reason) {
      setError(reason.message || '创建作答失败');
    } finally {
      setLoading(false);
    }
  };

  if (!enabled) return null;
  if (attempt) return <QualificationAttemptWorkspace attempt={attempt} onExit={() => setAttempt(null)} />;
  return (
    <div className="mt-5 space-y-5">
      <header className="flex items-start justify-between gap-4 border-b border-slate-200 pb-4">
        <div><h2 className="text-lg font-semibold text-slate-950">五类资格考试套题</h2><p className="mt-1 text-sm text-slate-600">按考试类别、年份和套题类型选择真题或模拟题。</p></div>
        <BookOpenCheck className="shrink-0 text-emerald-700" size={24} aria-hidden="true" />
      </header>
      {!examId ? <div className="grid gap-3 md:grid-cols-2">{catalog.exams.map((exam) => <button key={exam.exam_id} type="button" onClick={() => setExamId(exam.exam_id)} className="flex min-h-20 items-center justify-between border border-slate-200 px-4 text-left text-sm font-medium text-slate-800 transition hover:border-emerald-400 hover:bg-emerald-50"><span>{exam.name}</span><ChevronRight size={18} aria-hidden="true" /></button>)}</div> : <>
        <div className="flex flex-wrap items-end gap-3"><button type="button" onClick={() => { setExamId(''); setYear(''); setPaperType(''); setSelectedPaper(null); }} className="text-sm font-medium text-emerald-700 hover:text-emerald-900">返回考试类别</button><label className="text-sm text-slate-700">年份<select value={year} onChange={(event) => setYear(event.target.value)} className="ml-2 border border-slate-300 bg-white px-2 py-1.5"><option value="">全部</option>{years.map((value) => <option key={value} value={value}>{value}</option>)}</select></label><label className="text-sm text-slate-700">套题类型<select value={paperType} onChange={(event) => setPaperType(event.target.value)} className="ml-2 border border-slate-300 bg-white px-2 py-1.5"><option value="">全部</option>{types.map((value) => <option key={value} value={value}>{value}</option>)}</select></label></div>
        <h3 className="text-base font-semibold text-slate-900">{selectedExam?.name}</h3>
        <div className="space-y-2">{papers.map((paper) => <button key={paper.template_id} type="button" onClick={() => setSelectedPaper(paper)} disabled={!paper.question_count} className={`flex w-full items-center justify-between gap-3 border px-4 py-3 text-left disabled:cursor-not-allowed disabled:opacity-60 ${selectedPaper?.template_id === paper.template_id ? 'border-emerald-500 bg-emerald-50' : 'border-slate-200 hover:border-emerald-300'}`}><span><strong className="block text-sm text-slate-900">{paper.title}</strong><span className="mt-1 block text-xs text-slate-500">{paper.year} · {paper.paper_type} · {paper.question_count ? `${paper.question_count} 题` : '题目整理中'}</span></span><ChevronRight size={18} aria-hidden="true" /></button>)}</div>
      </>}
      {selectedPaper && <section className="border border-emerald-200 bg-emerald-50 p-4" aria-label="选择作答模式"><h3 className="font-semibold text-emerald-950">{selectedPaper.title}</h3><div className="mt-3 flex flex-wrap items-end gap-3"><button type="button" disabled={loading} onClick={() => openAttempt('practice')} className="bg-emerald-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{loading && <Loader2 className="mr-2 inline animate-spin" size={15} />}练习模式</button><label className="text-sm font-medium text-emerald-950">测试时长<input aria-label="测试时长" type="number" min="10" max="300" value={duration} onChange={(event) => setDuration(Math.max(10, Math.min(300, Number(event.target.value) || 60)))} className="ml-2 w-20 border border-emerald-300 bg-white px-2 py-2" /> 分钟</label><button type="button" disabled={loading} onClick={() => openAttempt('test')} className="inline-flex items-center gap-2 border border-emerald-700 px-4 py-2 text-sm font-medium text-emerald-900 disabled:opacity-50"><Clock3 size={16} />测试模式</button></div></section>}
      {error && <p role="alert" className="border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800">{error}</p>}
    </div>
  );
}
