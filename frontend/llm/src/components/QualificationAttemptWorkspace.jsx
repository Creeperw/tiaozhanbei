import React, { useEffect, useMemo, useState } from 'react';
import { Bookmark, ChevronLeft, ChevronRight, ClipboardList, PanelRightOpen } from 'lucide-react';
import { fetchWithAuth, readJsonResponse } from '../utils/api';

const request = async (path, options = {}) => {
  const response = await fetchWithAuth(`/api/v1${path}`, options);
  const data = await readJsonResponse(response, {});
  if (!response.ok) throw new Error(data.detail || '请求失败');
  return data;
};

const optionValue = (option) => option.option_id || option.id || '';

export default function QualificationAttemptWorkspace({ attempt: initialAttempt, onExit }) {
  const [attempt, setAttempt] = useState(initialAttempt);
  const [position, setPosition] = useState(initialAttempt.current_position || 1);
  const [answers, setAnswers] = useState(Object.fromEntries(initialAttempt.items.map((item) => [item.question_id, item.answer || ''])));
  const [marked, setMarked] = useState(initialAttempt.marked_positions || []);
  const [cardOpen, setCardOpen] = useState(false);
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [seconds, setSeconds] = useState(initialAttempt.answer_mode === 'test' ? (initialAttempt.duration_minutes || 60) * 60 : null);
  const current = attempt.items[position - 1];
  const submitted = attempt.status === 'submitted' || Boolean(report);

  useEffect(() => {
    if (submitted || attempt.answer_mode !== 'test' || seconds === null || seconds <= 0) return undefined;
    const timer = window.setInterval(() => setSeconds((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [attempt.answer_mode, seconds, submitted]);

  const answered = useMemo(() => new Set(Object.entries(answers).filter(([, value]) => String(value).trim()).map(([key]) => key)), [answers]);
  const save = async (paused = false, nextPosition = position, nextMarked = marked) => {
    const saved = await request(`/qualification-paper-attempts/${attempt.attempt_id}/progress`, {
      method: 'PUT', body: JSON.stringify({ answers, current_position: nextPosition, marked_positions: nextMarked, paused }),
    });
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
  if (report) return <section className="space-y-5"><button type="button" onClick={onExit} className="text-sm font-medium text-emerald-700">返回套题列表</button><div className="border border-emerald-200 bg-emerald-50 p-5"><h2 className="text-xl font-semibold text-emerald-950">作答分数</h2><p className="mt-2 text-3xl font-semibold text-emerald-800">{report.score} / {report.max_score}</p><div className="mt-4 flex flex-wrap gap-2">{report.items.map((item) => <button key={item.question_id} type="button" onClick={() => { setPosition(item.position); setReport(null); }} className={`h-8 w-8 rounded-full text-xs font-semibold ${item.answer_status === 'pending' ? 'bg-slate-400 text-white' : item.is_correct ? 'bg-emerald-600 text-white' : 'bg-rose-600 text-white'}`} title={item.answer_status === 'pending' ? '答案待补充' : item.is_correct ? '回答正确' : '回答错误'}>{item.position}</button>)}</div><p className="mt-3 text-xs text-emerald-900">灰色题目答案待补充，不计入分数。</p></div></section>;
  if (!current) return <section role="alert" className="border border-rose-300 bg-rose-50 p-4 text-sm text-rose-800">该套题暂时没有可作答题目。</section>;
  return <section className="relative min-h-[560px] border border-slate-200 bg-white p-5"><header className="flex items-center justify-between gap-3 border-b border-slate-200 pb-4"><button type="button" onClick={async () => { try { await save(true); onExit(); } catch (reason) { setError(reason.message); } }} className="text-sm font-medium text-slate-700">退出考试</button><div className="text-sm font-medium text-slate-700">第 {position} / {attempt.items.length} 题{seconds !== null ? ` · ${formatTime(seconds)}` : ''}</div><button type="button" title="展开答题卡" onClick={() => setCardOpen(!cardOpen)} className="inline-flex h-9 w-9 items-center justify-center border border-slate-300 text-slate-700"><PanelRightOpen size={18} /></button></header>
    <article className="mx-auto max-w-3xl py-10"><div className="text-lg font-medium leading-8 text-slate-950">{position}. {current.question_content}</div><div className="mt-6 space-y-3">{current.options.map((option) => { const value = optionValue(option); const checked = String(answers[current.question_id] || '').split(',').includes(value); return <label key={value} className={`flex cursor-pointer gap-3 border p-3 text-sm ${checked ? 'border-emerald-500 bg-emerald-50' : 'border-slate-200'}`}><input type={current.question_type === 'multiple_choice' ? 'checkbox' : 'radio'} checked={checked} onChange={() => selectAnswer(value)} name={current.question_id} disabled={submitted} />{value}. {option.content}</label>; })}</div>{attempt.answer_mode === 'practice' && <PracticeExplanation attemptId={attempt.attempt_id} questionId={current.question_id} />}</article>
    <footer className="mx-auto flex max-w-3xl flex-wrap justify-between gap-3 border-t border-slate-200 pt-4"><button type="button" disabled={position === 1} onClick={() => changePosition(position - 1)} className="inline-flex items-center gap-1 border border-slate-300 px-3 py-2 text-sm disabled:opacity-40"><ChevronLeft size={16} />上一题</button><button type="button" onClick={toggleMarked} className={`inline-flex items-center gap-2 border px-3 py-2 text-sm ${marked.includes(position) ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-300 text-slate-700'}`}><Bookmark size={16} />标记本题</button><button type="button" disabled={position === attempt.items.length} onClick={() => changePosition(position + 1)} className="inline-flex items-center gap-1 border border-slate-300 px-3 py-2 text-sm disabled:opacity-40">下一题<ChevronRight size={16} /></button><button type="button" onClick={submit} className="ml-auto inline-flex items-center gap-2 bg-slate-900 px-4 py-2 text-sm font-medium text-white"><ClipboardList size={16} />提交答案</button></footer>
    {cardOpen && <aside className="absolute right-0 top-0 h-full w-64 border-l border-slate-200 bg-white p-4 shadow-xl"><h3 className="font-semibold text-slate-900">答题卡</h3><div className="mt-4 flex flex-wrap gap-2">{attempt.items.map((item, index) => { const itemPosition = index + 1; const color = marked.includes(itemPosition) ? 'bg-slate-900 text-white' : answered.has(item.question_id) ? 'bg-emerald-600 text-white' : 'border border-slate-300 bg-white text-slate-700'; return <button key={item.question_id} type="button" onClick={() => changePosition(itemPosition)} className={`h-8 w-8 rounded-full text-xs ${color}`}>{itemPosition}</button>; })}</div></aside>}
    {error && <p role="alert" className="mt-4 border border-rose-300 bg-rose-50 p-2 text-sm text-rose-800">{error}</p>}</section>;
}

function PracticeExplanation({ attemptId, questionId }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState('');
  return <div className="mt-6"><button type="button" onClick={async () => { try { setDetail(await request(`/qualification-paper-attempts/${attemptId}/items/${questionId}/explanation`)); } catch (reason) { setError(reason.message); } }} className="text-sm font-medium text-emerald-700">查看解析</button>{detail && <div className="mt-2 border-l-2 border-emerald-500 pl-3 text-sm leading-6 text-slate-700">正确答案：{detail.answer.join('、')}<br />{detail.explanation || '暂无解析'}</div>}{error && <p className="mt-2 text-sm text-rose-700">{error}</p>}</div>;
}
