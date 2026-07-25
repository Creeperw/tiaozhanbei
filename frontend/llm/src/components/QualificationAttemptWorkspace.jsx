import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Bookmark, ChevronLeft, ChevronRight, ClipboardList, LogOut, PanelRightClose, PanelRightOpen, X } from 'lucide-react';
import { fetchWithAuth, readJsonResponse } from '../utils/api';

const request = async (path, options = {}) => {
  const response = await fetchWithAuth(`/api/v1${path}`, options);
  const data = await readJsonResponse(response, {});
  if (!response.ok) throw new Error(data.detail || '请求失败');
  return data;
};

const optionValue = (option) => option.option_id || option.id || '';
const buttonBase = 'inline-flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-40';

export default function QualificationAttemptWorkspace({ attempt: initialAttempt, onExit }) {
  const [attempt, setAttempt] = useState(initialAttempt);
  const [position, setPosition] = useState(initialAttempt.current_position || 1);
  const [answers, setAnswers] = useState(Object.fromEntries(initialAttempt.items.map((item) => [item.question_id, item.answer || ''])));
  const [marked, setMarked] = useState(initialAttempt.marked_positions || []);
  const [cardOpen, setCardOpen] = useState(false);
  const [report, setReport] = useState(null);
  const [reportPosition, setReportPosition] = useState(1);
  const [error, setError] = useState('');
  const [seconds, setSeconds] = useState(initialAttempt.answer_mode === 'test' ? (initialAttempt.remaining_seconds ?? (initialAttempt.duration_minutes || 60) * 60) : null);
  const cardToggleRef = useRef(null);
  const current = attempt.items[position - 1];
  const submitted = attempt.status === 'submitted' || Boolean(report);

  useEffect(() => {
    if (submitted || attempt.answer_mode !== 'test' || seconds === null || seconds <= 0) return undefined;
    const timer = window.setInterval(() => setSeconds((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [attempt.answer_mode, seconds, submitted]);

  useEffect(() => {
    if (!cardOpen) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') {
        setCardOpen(false);
        cardToggleRef.current?.focus();
      }
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [cardOpen]);

  const answered = useMemo(() => new Set(Object.entries(answers).filter(([, value]) => String(value).trim()).map(([key]) => key)), [answers]);
  const save = async (paused = false, nextPosition = position, nextMarked = marked) => {
    const saved = await request(`/qualification-paper-attempts/${attempt.attempt_id}/progress`, { method: 'PUT', body: JSON.stringify({ answers, current_position: nextPosition, marked_positions: nextMarked, paused }) });
    setAttempt(saved);
    return saved;
  };
  const changePosition = async (next) => {
    const safe = Math.max(1, Math.min(attempt.items.length, next));
    setPosition(safe);
    try { await save(false, safe); } catch (reason) { setError(reason.message); }
  };
  const toggleMarked = async () => {
    const next = marked.includes(position) ? marked.filter((item) => item !== position) : [...marked, position];
    setMarked(next);
    try { await save(false, position, next); } catch (reason) { setError(reason.message); }
  };
  const submit = async () => {
    try {
      await save(false);
      const result = await request(`/qualification-paper-attempts/${attempt.attempt_id}/submit`, { method: 'POST', body: JSON.stringify({ request_id: `submit-${crypto.randomUUID()}` }) });
      setReport(result);
      setReportPosition(1);
      setAttempt((value) => ({ ...value, status: 'submitted' }));
    } catch (reason) { setError(reason.message); }
  };
  const selectAnswer = (value) => {
    if (submitted) return;
    if (current.question_type === 'multiple_choice') {
      const currentValues = String(answers[current.question_id] || '').split(',').filter(Boolean);
      const next = currentValues.includes(value) ? currentValues.filter((item) => item !== value) : [...currentValues, value];
      setAnswers({ ...answers, [current.question_id]: next.join(',') });
    } else setAnswers({ ...answers, [current.question_id]: value });
  };
  const formatTime = (value) => `${String(Math.floor((value || 0) / 60)).padStart(2, '0')}:${String((value || 0) % 60).padStart(2, '0')}`;

  if (report) {
    const reportItem = report.items.find((item) => item.position === reportPosition) || report.items[0];
    return (
      <section className="space-y-5">
        <button type="button" onClick={onExit} className={`${buttonBase} border-slate-300 bg-white text-slate-700 shadow-sm hover:border-emerald-400 hover:text-emerald-800`}><ArrowLeft size={16} />返回套题列表</button>
        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5 shadow-sm"><h2 className="text-xl font-semibold text-emerald-950">作答分数</h2><p className="mt-2 text-3xl font-semibold text-emerald-800">{report.score} / {report.max_score}</p><div className="mt-4 flex flex-wrap gap-2">{report.items.map((item) => <button key={item.question_id} type="button" onClick={() => setReportPosition(item.position)} className={`h-8 w-8 rounded-full text-xs font-semibold ${item.position === reportPosition ? 'ring-2 ring-emerald-700 ring-offset-2' : ''} ${item.answer_status === 'pending' ? 'bg-slate-400 text-white' : item.is_correct ? 'bg-emerald-600 text-white' : 'bg-rose-600 text-white'}`} title={item.answer_status === 'pending' ? '答案待补充' : item.is_correct ? '回答正确' : '回答错误'}>{item.position}</button>)}</div><p className="mt-3 text-xs text-emerald-900">灰色题目答案待补充，不计入分数。</p></div>
        {reportItem && <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" aria-label="题目结果"><div className="flex items-center justify-between gap-3"><h3 className="text-base font-semibold text-slate-950">第 {reportItem.position} 题结果</h3><span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${reportItem.answer_status === 'pending' ? 'bg-slate-100 text-slate-600' : reportItem.is_correct ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'}`}>{reportItem.answer_status === 'pending' ? '待补充答案' : reportItem.is_correct ? '回答正确' : '需要复盘'}</span></div><dl className="mt-4 space-y-3 text-sm leading-6 text-slate-700"><div><dt className="font-semibold text-slate-900">你的答案</dt><dd>{reportItem.submitted_answer || '未作答'}</dd></div><div><dt className="font-semibold text-slate-900">正确答案</dt><dd>{reportItem.standard_answer?.join('、') || '待补充'}</dd></div><div><dt className="font-semibold text-slate-900">解析</dt><dd>{reportItem.explanation || '暂无解析'}</dd></div></dl></section>}
      </section>
    );
  }
  if (!current) return <section role="alert" className="rounded-xl border border-rose-300 bg-rose-50 p-4 text-sm text-rose-800">该套题暂时没有可作答题目。</section>;

  return (
    <section className="relative min-h-[560px] overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-slate-50/80 px-4 py-3 sm:px-5">
        <button type="button" onClick={async () => { try { await save(true); onExit(); } catch (reason) { setError(reason.message); } }} className={`${buttonBase} border-slate-300 bg-white text-slate-700 shadow-sm hover:border-rose-300 hover:text-rose-700`}><LogOut size={16} />退出并保存</button>
        <div className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm font-semibold text-slate-700 shadow-sm">第 {position} / {attempt.items.length} 题{seconds !== null ? ` · ${formatTime(seconds)}` : ''}</div>
        <button ref={cardToggleRef} type="button" title={cardOpen ? '收起答题卡' : '展开答题卡'} aria-label={cardOpen ? '收起答题卡' : '展开答题卡'} aria-expanded={cardOpen} aria-controls="qualification-answer-card" onClick={() => setCardOpen(!cardOpen)} className={`${buttonBase} border-slate-300 bg-white text-slate-700 shadow-sm hover:border-emerald-400 hover:text-emerald-800`}>{cardOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}<span className="hidden sm:inline">答题卡</span></button>
      </header>
      <article className="mx-auto max-w-3xl px-5 py-8 sm:py-10">
        <div className="flex gap-3 text-lg font-medium leading-8 text-slate-950"><span className="mt-0.5 flex h-7 min-w-7 items-center justify-center rounded-full bg-emerald-100 px-2 text-sm font-bold text-emerald-800">{position}</span><span>{current.question_content}</span></div>
        <div className="mt-6 space-y-3">{current.options.map((option) => { const value = optionValue(option); const checked = String(answers[current.question_id] || '').split(',').includes(value); return <label key={value} className={`flex cursor-pointer gap-3 rounded-xl border p-3.5 text-sm transition ${checked ? 'border-emerald-500 bg-emerald-50 shadow-sm' : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50'}`}><input type={current.question_type === 'multiple_choice' ? 'checkbox' : 'radio'} checked={checked} onChange={() => selectAnswer(value)} name={current.question_id} disabled={submitted} /><span><strong className="mr-1.5 text-slate-900">{value}.</strong>{option.content}</span></label>; })}</div>
        {attempt.answer_mode === 'practice' && <PracticeExplanation key={current.question_id} attemptId={attempt.attempt_id} questionId={current.question_id} />}
      </article>
      <footer className="mx-auto flex max-w-3xl flex-wrap gap-3 border-t border-slate-200 px-5 py-4"><button type="button" disabled={position === 1} onClick={() => changePosition(position - 1)} className={`${buttonBase} border-slate-300 bg-white text-slate-700`}><ChevronLeft size={16} />上一题</button><button type="button" onClick={toggleMarked} className={`${buttonBase} ${marked.includes(position) ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-300 bg-white text-slate-700'}`}><Bookmark size={16} />标记本题</button><button type="button" disabled={position === attempt.items.length} onClick={() => changePosition(position + 1)} className={`${buttonBase} border-slate-300 bg-white text-slate-700`}>下一题<ChevronRight size={16} /></button><button type="button" onClick={submit} className={`${buttonBase} ml-auto border-slate-900 bg-slate-900 text-white hover:bg-slate-800`}><ClipboardList size={16} />提交答案</button></footer>
      {cardOpen && <><button type="button" aria-label="关闭答题卡遮罩" onClick={() => setCardOpen(false)} className="absolute inset-0 z-10 bg-slate-950/10 backdrop-blur-[1px]" /><aside id="qualification-answer-card" role="complementary" aria-label="答题卡" className="absolute bottom-0 right-0 top-0 z-20 flex w-[min(20rem,88vw)] flex-col border-l border-slate-200 bg-white shadow-2xl"><div className="flex items-center justify-between border-b border-slate-200 px-4 py-4"><div><h3 className="font-semibold text-slate-900">答题卡</h3><p className="mt-1 text-xs text-slate-500">已答 {answered.size} / {attempt.items.length}</p></div><button type="button" aria-label="收起答题卡" onClick={() => { setCardOpen(false); cardToggleRef.current?.focus(); }} className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-300 text-slate-600 transition hover:border-emerald-400 hover:text-emerald-800"><X size={17} /></button></div><div data-testid="answer-card-grid" className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4"><div className="grid grid-cols-5 gap-2">{attempt.items.map((item, index) => { const itemPosition = index + 1; const active = itemPosition === position; const color = active ? 'ring-2 ring-emerald-700 ring-offset-2' : marked.includes(itemPosition) ? 'bg-slate-900 text-white' : answered.has(item.question_id) ? 'bg-emerald-600 text-white' : 'border border-slate-300 bg-white text-slate-700'; return <button key={item.question_id} type="button" aria-label={`第 ${itemPosition} 题`} onClick={() => changePosition(itemPosition)} className={`h-9 rounded-full text-xs font-semibold transition hover:scale-105 ${color}`}>{itemPosition}</button>; })}</div></div></aside></>}
      {error && <p role="alert" className="mx-5 mb-4 rounded-lg border border-rose-300 bg-rose-50 p-2 text-sm text-rose-800">{error}</p>}
    </section>
  );
}

function PracticeExplanation({ attemptId, questionId }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState('');
  return <div className="mt-6"><button type="button" onClick={async () => { try { setDetail(await request(`/qualification-paper-attempts/${attemptId}/items/${questionId}/explanation`)); } catch (reason) { setError(reason.message); } }} className="rounded-lg border border-emerald-300 bg-white px-3 py-2 text-sm font-semibold text-emerald-800 transition hover:bg-emerald-50">查看解析</button>{detail && <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm leading-6 text-slate-700"><strong className="text-emerald-950">正确答案：{detail.answer.join('、') || '待补充'}</strong><p className="mt-2">{detail.explanation || '暂无解析'}</p></div>}{error && <p className="mt-2 text-sm text-rose-700">{error}</p>}</div>;
}
