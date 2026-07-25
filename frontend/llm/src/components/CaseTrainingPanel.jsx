import React, { useState } from 'react';
import { HelpCircle, Loader2, RotateCcw, Send } from 'lucide-react';
import { fetchWithAuth, readJsonResponse } from '../utils/api';

const storageKey = 'simulated-patient-session-id';

const callPatient = async (sessionId, action, payload = {}) => {
  const response = await fetchWithAuth('/api/v1/simulated-patient', {
    method: 'POST', body: JSON.stringify({ session_id: sessionId, action, ...payload }),
  });
  const data = await readJsonResponse(response, {});
  if (!response.ok || !data.success) throw new Error(data.error || data.detail || '模拟病患服务暂不可用');
  return data;
};

export default function CaseTrainingPanel({ enabled }) {
  const [sessionId, setSessionId] = useState(() => sessionStorage.getItem(storageKey) || '');
  const [messages, setMessages] = useState([]);
  const [patient, setPatient] = useState(null);
  const [question, setQuestion] = useState('');
  const [diagnosis, setDiagnosis] = useState({ syndrome: '', prescription: '', composition: '', notes: '' });
  const [report, setReport] = useState(null);
  const [turnCount, setTurnCount] = useState(0);
  const [helpAvailable, setHelpAvailable] = useState(false);
  const [help, setHelp] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const run = async (action, payload = {}) => {
    setLoading(true); setError('');
    try {
      const id = sessionId || `sp-${crypto.randomUUID()}`;
      const result = await callPatient(id, action, payload);
      setSessionId(result.session_id || id);
      sessionStorage.setItem(storageKey, result.session_id || id);
      setTurnCount(result.turn_count || 0);
      setHelpAvailable(Boolean(result.help_available));
      return result;
    } catch (reason) { setError(reason.message); return null; } finally { setLoading(false); }
  };
  const start = async () => {
    const result = await run('start');
    if (!result) return;
    const opening = result.data?.patient_reply || '';
    setPatient(result.data?.patient_info || null);
    setMessages(opening ? [{ role: 'patient', content: opening }] : []);
    setReport(null); setHelp(null);
  };
  const ask = async () => {
    if (!question.trim()) return;
    const asked = question.trim();
    const result = await run('dialogue', { user_input: asked });
    if (!result) return;
    setMessages((value) => [...value, { role: 'student', content: asked }, { role: 'patient', content: result.data?.patient_reply || '患者暂未作答。' }]);
    setQuestion('');
  };
  const requestHelp = async (helpType) => {
    const result = await run('help', { help_type: helpType });
    if (result) setHelp(result.data || {});
  };
  const submit = async () => {
    const result = await run('submit', { diagnosis });
    if (result) { setReport(result.data?.grading_report || {}); sessionStorage.removeItem(storageKey); }
  };
  const reset = async () => {
    const result = await run('reset');
    if (result) { setMessages([{ role: 'patient', content: result.data?.patient_reply || '' }]); setReport(null); }
  };
  if (!enabled) return null;
  if (report) return <section className="mt-5 space-y-4"><div className="border border-emerald-200 bg-emerald-50 p-5"><h2 className="text-lg font-semibold text-emerald-950">模拟病患评分报告</h2><p className="mt-2 text-3xl font-semibold text-emerald-800">{report.score ?? '--'} 分</p><p className="mt-3 text-sm leading-6 text-emerald-950">{report.feedback || report.summary || '已完成本次诊断评估。'}</p></div><button type="button" onClick={start} className="border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700">开始下一病例</button></section>;
  return <section className="mt-5 space-y-5"><header className="flex items-start justify-between gap-3 border-b border-slate-200 pb-4"><div><h2 className="text-lg font-semibold text-slate-950">模拟病患问诊</h2><p className="mt-1 text-sm text-slate-600">通过连续问诊完成辨证与诊疗思路训练。</p>{patient && <p className="mt-2 text-xs text-slate-500">患者：{patient.gender} · {patient.age_range}</p>}</div>{sessionId && <button type="button" title="重置病例" onClick={reset} disabled={loading} className="inline-flex h-9 w-9 items-center justify-center border border-slate-300 text-slate-700"><RotateCcw size={16} /></button>}</header>
    {!sessionId ? <button type="button" onClick={start} disabled={loading} className="inline-flex items-center gap-2 bg-rose-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50">{loading && <Loader2 size={16} className="animate-spin" />}开始问诊</button> : <><div className="max-h-72 space-y-3 overflow-y-auto border-b border-slate-200 pb-4">{messages.map((message, index) => <div key={`${message.role}-${index}`} className={`border-l-2 pl-3 text-sm leading-6 ${message.role === 'patient' ? 'border-rose-400 text-slate-700' : 'border-emerald-500 text-slate-900'}`}><strong>{message.role === 'patient' ? '患者' : '我'}</strong><p>{message.content}</p></div>)}</div><div className="flex gap-2"><textarea aria-label="问诊问题" value={question} onChange={(event) => setQuestion(event.target.value)} className="min-h-20 flex-1 border border-slate-300 p-3 text-sm" placeholder="请输入问诊问题" /><button type="button" title="发送问诊" onClick={ask} disabled={loading || !question.trim()} className="h-10 w-10 self-end bg-emerald-700 text-white disabled:opacity-50"><Send size={16} className="mx-auto" /></button></div>{helpAvailable && <div className="flex flex-wrap gap-2"><button type="button" onClick={() => requestHelp('question')} className="inline-flex items-center gap-2 border border-amber-300 px-3 py-2 text-sm text-amber-900"><HelpCircle size={16} />关键问题</button><button type="button" onClick={() => requestHelp('interpretation')} className="border border-amber-300 px-3 py-2 text-sm text-amber-900">症状解读</button>{help && <p className="w-full text-sm leading-6 text-amber-900">{help.question || help.interpretation || help.answer}</p>}</div>}<div className="grid gap-3 border-t border-slate-200 pt-4 md:grid-cols-2">{[['syndrome', '疾病/证型'], ['prescription', '治法或方剂'], ['composition', '方剂组成'], ['notes', '诊疗说明']].map(([key, label]) => <label key={key} className="text-sm font-medium text-slate-700">{label}<textarea value={diagnosis[key]} onChange={(event) => setDiagnosis({ ...diagnosis, [key]: event.target.value })} className="mt-2 min-h-20 w-full border border-slate-300 p-2 text-sm" /></label>)}</div><div className="flex items-center justify-between"><span className="text-xs text-slate-500">已问诊 {turnCount} 轮</span><button type="button" onClick={submit} disabled={loading || !diagnosis.syndrome.trim()} className="bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">提交诊断</button></div></>}{error && <p role="alert" className="border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}</section>;
}
