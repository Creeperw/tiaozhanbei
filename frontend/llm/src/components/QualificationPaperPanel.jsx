import React, { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, BookOpenCheck, ChevronDown, ChevronRight, Clock3, Loader2, GraduationCap, Stethoscope, Heart, HeartPulse, Pill } from 'lucide-react';
import { fetchWithAuth, readJsonResponse } from '../utils/api';
import QualificationAttemptWorkspace from './QualificationAttemptWorkspace';

const request = async (path, options = {}) => {
  const response = await fetchWithAuth(`/api/v1${path}`, options);
  const data = await readJsonResponse(response, {});
  if (!response.ok) throw new Error(data.detail || '请求失败');
  return data;
};

function ModeSelector({ paper, duration, loading, onDurationChange, onOpen }) {
  return (
    <section role="region" aria-label="选择作答模式" className="border-t border-emerald-200 bg-emerald-50/70 px-4 py-4 sm:px-5">
      <div className="grid gap-3 lg:grid-cols-2">
        <button type="button" aria-label="练习模式" disabled={loading} onClick={() => onOpen('practice')} className="group flex min-h-24 items-start justify-between gap-4 rounded-xl border border-emerald-200 bg-white p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-emerald-400 hover:shadow-md disabled:opacity-50">
          <span><strong className="block text-sm text-emerald-950">练习模式</strong><span className="mt-1.5 block text-xs leading-5 text-slate-600">随做随看单题解析，适合知识巩固和错因复盘。</span></span>
          {loading ? <Loader2 className="animate-spin text-emerald-700" size={17} /> : <ChevronRight className="text-emerald-700 transition group-hover:translate-x-0.5" size={17} />}
        </button>
        <div className="rounded-xl border border-slate-300 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><strong className="block text-sm text-slate-950">测试模式</strong><span className="mt-1.5 block text-xs leading-5 text-slate-600">限时作答，交卷后统一查看结果和解析。</span></div>
            <label className="text-xs font-medium text-slate-700">时长<span className="ml-2 inline-flex items-center rounded-lg border border-slate-300 bg-slate-50 px-2.5 py-1.5"><input aria-label="测试时长" type="number" min="10" max="300" value={duration} onChange={(event) => onDurationChange(Math.max(10, Math.min(300, Number(event.target.value) || 60)))} className="w-12 bg-transparent text-right text-sm font-semibold text-slate-900 outline-none" /><span className="ml-1 text-slate-500">分钟</span></span></label>
          </div>
          <button type="button" disabled={loading} onClick={() => onOpen('test')} className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:opacity-50"><Clock3 size={16} />开始测试</button>
        </div>
      </div>
      <p className="mt-3 text-xs text-emerald-900">已选择：{paper.title} · {paper.question_count} 题</p>
    </section>
  );
}

const STORAGE_DONE = 'qp-completed-papers';
const getCompleted = () => { try { return JSON.parse(localStorage.getItem(STORAGE_DONE) || '{}'); } catch { return {}; } };

export default function QualificationPaperPanel({ enabled, onBack }) {
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
    request('/qualification-papers/catalog').then(async (data) => {
      if (!active) return;
      setCatalog(data);
      localStorage.setItem('qp-catalog-cache', JSON.stringify(data));
      // Restore any saved attempt for the currently selected exam
      const examPapers = data.papers.filter(p => p.exam_id === examId);
      for (const paper of examPapers) {
        const savedId = sessionStorage.getItem(`qp-attempt-${paper.template_id}`);
        if (savedId) {
          try {
            const saved = await request(`/qualification-paper-attempts/${savedId}`);
            if (active && saved.status !== 'submitted' && saved.template_id === paper.template_id) {
              setAttempt(saved);
              return;
            }
          } catch { sessionStorage.removeItem(`qp-attempt-${paper.template_id}`); }
        }
      }
    }).catch((reason) => active && setError(reason.message));
    return () => { active = false; };
  }, []);

  const selectedExam = catalog.exams.find((item) => item.exam_id === examId);
  const papers = useMemo(() => catalog.papers.filter((item) => (!examId || item.exam_id === examId) && (!year || item.year === year) && (!paperType || item.paper_type === paperType)), [catalog.papers, examId, year, paperType]);
  const years = [...new Set(catalog.papers.filter((item) => item.exam_id === examId).map((item) => item.year))];
  const types = [...new Set(catalog.papers.filter((item) => item.exam_id === examId).map((item) => item.paper_type))];

  const openAttempt = async (answerMode) => {
    if (!selectedPaper) return;
    setLoading(true);
    setError('');
    try {
      // Check for existing saved attempt for this paper AND matching mode
      const savedId = sessionStorage.getItem(`qp-attempt-${selectedPaper.template_id}`);
      if (savedId) {
        try {
          const saved = await request(`/qualification-paper-attempts/${savedId}`);
          if (saved.status !== 'submitted' && saved.answer_mode === answerMode) {
            setAttempt(saved);
            setLoading(false);
            return;
          }
        } catch { sessionStorage.removeItem(`qp-attempt-${selectedPaper.template_id}`); }
      }
      // Create new attempt
      const body = { answer_mode: answerMode };
      if (answerMode === 'test') body.duration_minutes = duration;
      const createdAttempt = await request(`/qualification-papers/${selectedPaper.template_id}/attempts`, { method: 'POST', body: JSON.stringify(body) });
      sessionStorage.setItem(`qp-attempt-${selectedPaper.template_id}`, createdAttempt.attempt_id);
      setAttempt(createdAttempt);
    } catch (reason) {
      setError(reason.message || '创建作答失败');
    } finally {
      setLoading(false);
    }
  };

  if (!enabled) return null;
  if (attempt) return <QualificationAttemptWorkspace attempt={attempt} examId={examId} onExit={() => {
    if (attempt.status === 'submitted') {
      sessionStorage.removeItem(`qp-attempt-${attempt.template_id}`);
      const completed = getCompleted();
      completed[examId] = (completed[examId] || 0) + 1;
      localStorage.setItem(STORAGE_DONE, JSON.stringify(completed));
    }
    setAttempt(null);
  }} />;
  return (
    <div className="flex flex-col h-full">
      <header className="flex items-center justify-between gap-4 border-b border-slate-200 px-5 py-4">
        <div className="flex items-center gap-4 min-w-0">
          {onBack && (
            <button type="button" onClick={onBack} className="inline-flex items-center gap-2 rounded-lg border-2 border-emerald-600 bg-white px-3 py-2 text-sm font-semibold text-emerald-700 shadow-sm transition hover:bg-emerald-50 shrink-0">
              <ArrowLeft size={16} />返回训练工坊
            </button>
          )}
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-slate-950">{selectedExam ? `五类资格考试套题：${selectedExam.name}` : '五类资格考试套题'}</h2>
            {!examId && <p className="mt-1 text-sm text-slate-600">按考试类别、年份和套题类型选择真题、回忆题或模拟题。</p>}
          </div>
        </div>
        <BookOpenCheck className="shrink-0 text-emerald-700" size={24} aria-hidden="true" />
      </header>
      <div className="flex-1 overflow-y-auto px-5 py-4">
      {!examId ? (
        <div className="flex flex-col gap-3">{catalog.exams.map((exam, idx) => {
          const availableCount = Number(exam.available_paper_count ?? catalog.papers.filter((paper) => paper.exam_id === exam.exam_id).length);
          const completed = getCompleted();
          const doneCount = completed[exam.exam_id] || 0;
          const available = availableCount > 0;
          const icons = [GraduationCap, Stethoscope, Heart, HeartPulse, Pill];
          const Icon = icons[idx % icons.length];
          return <button key={exam.exam_id} type="button" aria-label={exam.name} disabled={!available} onClick={() => setExamId(exam.exam_id)}
            className="flex min-h-20 items-center gap-4 rounded-xl border border-slate-200 bg-white px-5 text-left shadow-sm transition hover:border-emerald-300 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-50 w-full"
          >
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg" style={{background:'#f0fdf4',color:'#059669'}}>
              <Icon size={20} aria-hidden="true" />
            </span>
            <span className="flex-1 min-w-0">
              <span className="block text-sm font-semibold text-slate-800">{exam.name}</span>
              <span className="mt-1 block text-xs text-slate-500">{available ? `${availableCount} 份可作答套题` : '题目整理中，暂不开放作答'}</span>
            </span>
            <span className="flex items-center gap-3 shrink-0">
              <span className="text-xs font-medium text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-full px-3 py-1">已完成{doneCount}套</span>
              {available ? <ChevronRight size={18} aria-hidden="true" className="text-emerald-600" /> : <span className="text-xs text-slate-400">整理中</span>}
            </span>
          </button>;
        })}</div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-5 py-3">
            <button type="button" onClick={() => { setExamId(''); setYear(''); setPaperType(''); setSelectedPaper(null); }} className="inline-flex items-center gap-2 rounded-lg border-2 border-emerald-600 bg-white px-4 py-2.5 text-base font-semibold text-emerald-700 shadow-sm transition hover:bg-emerald-50"><ArrowLeft size={18} />返回考试类别</button>
            <label className="text-base font-semibold text-slate-900">年份<select value={year} onChange={(event) => { setYear(event.target.value); setSelectedPaper(null); }} className="ml-2 rounded-lg border border-slate-400 bg-white px-3 py-2.5 text-base text-slate-800"><option value="">全部</option>{years.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
            <label className="text-base font-semibold text-slate-900">套题类型<select value={paperType} onChange={(event) => { setPaperType(event.target.value); setSelectedPaper(null); }} className="ml-2 rounded-lg border border-slate-400 bg-white px-3 py-2.5 text-base text-slate-800"><option value="">全部</option>{types.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
          </div>
          <div className="space-y-3">
            {papers.map((paper) => {
              const selected = selectedPaper?.template_id === paper.template_id;
              return (
                <article key={paper.template_id} aria-label={paper.title} className={`overflow-hidden rounded-xl border bg-white shadow-sm transition ${selected ? 'border-emerald-500 ring-1 ring-emerald-200' : 'border-slate-200 hover:border-emerald-300 hover:shadow-md'}`}>
                  <button type="button" onClick={() => setSelectedPaper(selected ? null : paper)} disabled={!paper.question_count} aria-expanded={selected} className="flex w-full items-center justify-between gap-3 px-4 py-4 text-left disabled:cursor-not-allowed disabled:opacity-60 sm:px-5"><span><strong className="block text-sm text-slate-900">{paper.title}</strong><span className="mt-1.5 block text-xs text-slate-500">{paper.year} · {paper.paper_type} · {paper.question_count ? `${paper.question_count} 题` : '题目整理中'}</span></span>{selected ? <ChevronDown className="text-emerald-700" size={18} aria-hidden="true" /> : <ChevronRight size={18} aria-hidden="true" />}</button>
                  {selected && <ModeSelector paper={paper} duration={duration} loading={loading} onDurationChange={setDuration} onOpen={openAttempt} />}
                </article>
              );
            })}
          </div>
        </>
      )}
      {error && <p role="alert" className="rounded-lg border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800">{error}</p>}
      </div>
    </div>
  );
}
