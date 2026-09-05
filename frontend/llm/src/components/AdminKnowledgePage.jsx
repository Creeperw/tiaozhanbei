import React, { useEffect, useState } from 'react';
import { ClipboardCheck, FilePlus2, RefreshCw, ShieldCheck } from 'lucide-react';
import { API_BASE, fetchWithAuth } from '../utils/api';

export default function AdminKnowledgePage() {
  const [stem, setStem] = useState('');
  const [questionType, setQuestionType] = useState('short_answer');
  const [pdfFile, setPdfFile] = useState(null);
  const [task, setTask] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    const loadTasks = async () => {
      try {
        const response = await fetchWithAuth(`${API_BASE}/knowledge/admin/question-ingestion-tasks`);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || '任务列表加载失败');
        if (active) {
          const recentTasks = payload.tasks || [];
          setTasks(recentTasks);
          setTask((current) => current || recentTasks[0] || null);
        }
      } catch (cause) {
        if (active) setError(cause.message || '任务列表加载失败');
      }
    };
    loadTasks();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!task?.task_id || !['queued', 'running'].includes(task.status)) return undefined;
    let active = true;
    const refresh = async () => {
      try {
        const response = await fetchWithAuth(`${API_BASE}/knowledge/admin/question-ingestion-tasks/${task.task_id}`);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || '任务状态刷新失败');
        if (active) {
          setTask(payload);
          setTasks((current) => [payload, ...current.filter((item) => item.task_id !== payload.task_id)]);
        }
      } catch (cause) {
        if (active) setError(cause.message || '任务状态刷新失败');
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 1500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [task?.task_id, task?.status]);

  const submitPayload = async (payload) => {
    const response = await fetchWithAuth(`${API_BASE}/knowledge/admin/question-ingestion-tasks`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    const taskPayload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(taskPayload.detail || '导入任务提交失败');
    setTask(taskPayload);
    setTasks((current) => [taskPayload, ...current.filter((item) => item.task_id !== taskPayload.task_id)]);
  };

  const submit = async (event) => {
    event.preventDefault();
    if (!stem.trim()) return;
    setSubmitting(true);
    setError('');
    try {
      await submitPayload({ stem: stem.trim(), question_type: questionType });
      setStem('');
    } catch (cause) {
      setError(cause.message || '导入任务提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  const submitPdf = async (event) => {
    event.preventDefault();
    if (!pdfFile) return;
    setSubmitting(true);
    setError('');
    try {
      const formData = new FormData();
      formData.append('file', pdfFile);
      const upload = await fetchWithAuth(`${API_BASE}/knowledge/admin/question-ingestion-pdf-upload`, { method: 'POST', body: formData });
      const uploaded = await upload.json().catch(() => ({}));
      if (!upload.ok || !uploaded.file_id) throw new Error(uploaded.detail || 'PDF 上传失败');
      const response = await fetchWithAuth(`${API_BASE}/knowledge/admin/question-ingestion-pdf-tasks`, {
        method: 'POST',
        body: JSON.stringify({ file_id: uploaded.file_id }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || 'PDF 解析任务提交失败');
      setTask(payload);
      setTasks((current) => [payload, ...current.filter((item) => item.task_id !== payload.task_id)]);
    } catch (cause) {
      setError(cause.message || 'PDF 解析任务提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  const retry = async () => {
    if (!task?.task_id) return;
    setSubmitting(true);
    setError('');
    try {
      const response = await fetchWithAuth(`${API_BASE}/knowledge/admin/question-ingestion-tasks/${task.task_id}/retry`, { method: 'POST' });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || '任务重试失败');
      setTask(payload);
      setTasks((current) => [payload, ...current.filter((item) => item.task_id !== payload.task_id)]);
    } catch (cause) {
      setError(cause.message || '任务重试失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <section className="overflow-hidden rounded-[28px] border border-emerald-100 bg-white shadow-sm shadow-emerald-100/50">
        <div className="grid gap-6 bg-[linear-gradient(135deg,#ecfdf5_0%,#ffffff_68%)] p-6 lg:grid-cols-[1.2fr_0.8fr] lg:p-8">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-white px-3 py-1 text-sm font-semibold text-emerald-800">
              <ShieldCheck size={16} /> 正式题库治理
            </div>
            <h2 className="mt-4 text-3xl font-semibold tracking-tight text-slate-950">题目先审核，再进入训练。</h2>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">已发布且完成知识点关联的题目才会参与个性化训练；待关联题保留在正式题库，等待管理员复核。</p>
          </div>
          <div className="flex items-end justify-end">
            <div className="w-full rounded-2xl border border-emerald-100 bg-white/90 p-4 text-sm text-slate-600 shadow-sm">
              <div className="font-semibold text-emerald-950">当前接入范围</div>
              <div className="mt-2">题目提交、审核任务状态与正式题库发布链路。</div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
        <div className="space-y-6">
          <form onSubmit={submit} className="rounded-[26px] border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-center gap-2 text-slate-900"><FilePlus2 className="text-emerald-600" size={20} /><h3 className="font-semibold">提交单题审核</h3></div>
          <p className="mt-2 text-sm text-slate-500">用于验证自动去重、证据审核与正式入库流程。</p>
          <textarea value={stem} onChange={(event) => setStem(event.target.value)} placeholder="输入题干" className="mt-5 min-h-36 w-full resize-y rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none transition focus:border-emerald-400 focus:ring-4 focus:ring-emerald-50" />
          <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <select value={questionType} onChange={(event) => setQuestionType(event.target.value)} className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-emerald-400">
              <option value="short_answer">简答题</option>
              <option value="single_choice">单选题</option>
              <option value="multiple_choice">多选题</option>
            </select>
            <button type="submit" disabled={submitting || !stem.trim()} className="inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50">
              <RefreshCw size={16} className={submitting ? 'animate-spin' : ''} />{submitting ? '正在提交' : '提交审核'}
            </button>
          </div>
          </form>

          <form onSubmit={submitPdf} className="rounded-[26px] border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex items-center gap-2 text-slate-900"><FilePlus2 className="text-emerald-600" size={20} /><h3 className="font-semibold">解析题目 PDF</h3></div>
            <p className="mt-2 text-sm text-slate-500">上传后由 MinerU 解析题目，再依次进行语义去重、证据审核和质量审核。</p>
            <input type="file" accept="application/pdf,.pdf" onChange={(event) => setPdfFile(event.target.files?.[0] || null)} className="mt-5 block w-full text-sm text-slate-600 file:mr-4 file:rounded-xl file:border-0 file:bg-emerald-50 file:px-3 file:py-2 file:text-sm file:font-medium file:text-emerald-800 hover:file:bg-emerald-100" />
            <div className="mt-4 flex justify-end"><button type="submit" disabled={submitting || !pdfFile} className="inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"><RefreshCw size={16} className={submitting ? 'animate-spin' : ''} />{submitting ? '正在提交' : '提交 PDF 解析'}</button></div>
          </form>
          {error && <div className="rounded-xl border border-rose-100 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}
        </div>

        <section className="rounded-[26px] border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-center gap-2 text-slate-900"><ClipboardCheck className="text-teal-600" size={20} /><h3 className="font-semibold">最近任务</h3></div>
          {!task && <p className="mt-6 text-sm leading-6 text-slate-500">提交一条题目后，这里会显示任务编号和最终处理状态。</p>}
          {task && <div className="mt-5 rounded-2xl border border-emerald-100 bg-emerald-50/60 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.16em] text-emerald-700">任务状态</div>
            <div className="mt-2 font-mono text-sm text-slate-800">{task.task_id}</div>
            <div className="mt-3 inline-flex rounded-full bg-white px-2.5 py-1 text-sm font-medium text-emerald-800 ring-1 ring-emerald-100">{task.status === 'queued' ? '已排队，等待处理' : task.status === 'running' ? '正在审核' : task.outcome_status === 'active' ? '已发布到正式题库' : task.outcome_status || task.status}</div>
            {task.published_question_id && <div className="mt-3 text-sm text-slate-600">正式题目：{task.published_question_id}</div>}
            {task.status === 'completed' && task.outcome_status && task.outcome_status !== 'active' && <div className="mt-3 text-sm leading-6 text-amber-800">任务已处理，但题目尚未发布；当前结果：{task.outcome_status}。</div>}
            {task.error_code && <div className="mt-3 text-sm text-rose-700">处理失败：{task.error_code}</div>}
            {task.status === 'failed' && <button type="button" onClick={retry} disabled={submitting} className="mt-3 inline-flex items-center gap-2 rounded-xl border border-emerald-200 bg-white px-3 py-2 text-sm font-medium text-emerald-800 transition hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50"><RefreshCw size={15} className={submitting ? 'animate-spin' : ''} />重新提交</button>}
          </div>}
          {tasks.length > 0 && <div className="mt-4 space-y-2 border-t border-slate-100 pt-4">{tasks.map((item) => <button key={item.task_id} type="button" onClick={() => setTask(item)} className={`flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-xs transition ${task?.task_id === item.task_id ? 'bg-emerald-50 text-emerald-900' : 'hover:bg-slate-50 text-slate-600'}`}><span className="font-mono">{item.task_id}</span><span>{item.status === 'failed' ? '失败' : item.status === 'completed' ? item.outcome_status || '已完成' : item.status}</span></button>)}</div>}
        </section>
      </section>
    </div>
  );
}
