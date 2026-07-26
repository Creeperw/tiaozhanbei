import React, { useEffect, useMemo, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { loadMistakes, submitMistakeAnswerContext, submitTrainingWorkspaceTask } from '../pageDataLoaders';

const requestId = () => `variation-${crypto.randomUUID()}`;

export default function MistakeVariationPanel({ enabled }) {
  const [mistakes, setMistakes] = useState([]);
  const [total, setTotal] = useState(0);
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedMistakeId, setSelectedMistakeId] = useState('');
  const [questions, setQuestions] = useState([]);
  const [selectedQuestion, setSelectedQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [answerState, setAnswerState] = useState('');
  const [reason, setReason] = useState('');
  const [contextNotes, setContextNotes] = useState('');

  const selectedMistake = useMemo(
    () => mistakes.find((item) => String(item.mistake_id) === selectedMistakeId),
    [mistakes, selectedMistakeId],
  );

  const refreshMistakes = async () => {
    const loaded = [];
    let offset = 0;
    let expectedTotal = 0;
    let loadError = '';
    let hasMore = true;
    while (hasMore) {
      const response = await loadMistakes({
        fetcher: fetchJsonWithAuthFallback,
        status: statusFilter,
        offset,
        limit: 100,
      });
      loadError = response.error || '';
      if (loadError) break;
      const page = response.mistakes.items;
      loaded.push(...page);
      expectedTotal = response.mistakes.total;
      offset += page.length;
      hasMore = Boolean(response.mistakes.has_more) && page.length > 0;
    }
    setMistakes(loaded);
    setTotal(expectedTotal);
    if (!loaded.some((item) => String(item.mistake_id) === selectedMistakeId)) {
      setSelectedMistakeId('');
    }
    if (loadError) setError(loadError);
  };

  useEffect(() => { refreshMistakes(); }, [statusFilter]);

  const run = async (action) => {
    setLoading(true);
    setError('');
    try {
      await action();
    } finally {
      setLoading(false);
    }
  };

  const generate = () => run(async () => {
    const mistakeId = Number(selectedMistakeId);
    if (!Number.isInteger(mistakeId) || mistakeId <= 0 || !selectedMistake?.variation_available) {
      setError(selectedMistake?.variation_reason || '请选择一条可生成变式的错题。');
      return;
    }
    const response = await submitTrainingWorkspaceTask({
      fetcher: fetchJsonWithAuthFallback,
      task: {
        task_type: 'mistake_variation',
        title: '错题变式',
        query: '根据当前错题生成一道变式练习',
        inputs: { mistake_id: mistakeId, variation_count: 1 },
        options: { need_audit: true, variation_count: 1 },
      },
    });
    if (response.error) {
      setError(response.error);
      return;
    }
    const generated = response.taskResult.artifact?.content?.questions;
    if (!Array.isArray(generated) || generated.length === 0) {
      setError('变式题未返回可作答内容。');
      return;
    }
    setQuestions(generated);
    setSelectedQuestion(generated[0].question_version_id);
    setAnswer('');
    setResult(null);
  });

  const grade = () => run(async () => {
    const mistakeId = Number(selectedMistakeId);
    if (!selectedQuestion || !answer.trim()) {
      setError('请完成变式题作答。');
      return;
    }
    const response = await submitTrainingWorkspaceTask({
      fetcher: fetchJsonWithAuthFallback,
      task: {
        task_type: 'mistake_variation',
        title: '错题变式批改',
        query: '批改当前错题变式作答',
        inputs: {
          action: 'answer',
          mistake_id: mistakeId,
          question_version_id: selectedQuestion,
          student_answer: answer.trim(),
          request_id: requestId(),
        },
        options: { need_audit: true },
      },
    });
    if (response.error) {
      setError(response.error);
      return;
    }
    setResult(response.taskResult);
    await refreshMistakes();
  });

  if (!enabled) return <p className="mt-5 text-sm leading-6 text-slate-600">错题变式暂未开放。</p>;

  const selected = questions.find((item) => item.question_version_id === selectedQuestion);
  const grading = result?.artifact?.content?.grading?.grading || {};
  return (
    <div className="mt-5 space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-950">全部错题记录</h3>
          <p className="mt-1 text-xs text-slate-500">当前筛选共 {total} 条；所有错误都会保留，满足审核条件的错题可生成变式。</p>
        </div>
        <button type="button" onClick={() => run(refreshMistakes)} disabled={loading} className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-700">
          <RefreshCw size={14} />刷新
        </button>
      </div>

      <div className="flex flex-wrap gap-2" role="group" aria-label="错题状态筛选">
        {[
          ['all', '全部'],
          ['active', '待复盘'],
          ['resolved', '已解决'],
        ].map(([value, label]) => (
          <button key={value} type="button" aria-pressed={statusFilter === value} onClick={() => setStatusFilter(value)} className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${statusFilter === value ? 'border-emerald-300 bg-emerald-50 text-emerald-900' : 'border-slate-200 text-slate-600'}`}>{label}</button>
        ))}
      </div>

      <div className="max-h-96 space-y-3 overflow-y-auto pr-1" aria-label="错题列表">
        {mistakes.map((item) => {
          const selectedItem = selectedMistakeId === String(item.mistake_id);
          return (
            <button
              key={item.mistake_id}
              type="button"
              aria-pressed={selectedItem}
              onClick={() => {
                setSelectedMistakeId(String(item.mistake_id));
                setQuestions([]);
                setResult(null);
                setError('');
                setAnswerState('');
                setReason('');
                setContextNotes('');
              }}
              className={`w-full rounded-xl border p-3 text-left ${selectedItem ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white'}`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs font-semibold text-slate-500">错题 #{item.mistake_id} · {item.status}</span>
                <span className={`text-xs font-medium ${item.variation_available ? 'text-emerald-700' : 'text-amber-700'}`}>{item.variation_available ? '可生成变式' : '仅保留记录'}</span>
              </div>
              <p className="mt-2 text-sm font-medium leading-6 text-slate-900">{item.stem}</p>
              <div className="mt-2 grid gap-1 text-xs leading-5 text-slate-600 sm:grid-cols-2">
                <span>题型：{item.question_type || '未知'}</span>
                <span>得分：{item.score ?? '未记录'}{item.max_score ? ` / ${item.max_score}` : ''}</span>
                <span>错因：{item.answer_context_required && !item.answer_context_completed ? '待补充作答情况后分析' : (item.error_type || item.summary || '待分析')}</span>
                <span>作答：{item.student_answer || '未记录'}</span>
              </div>
            </button>
          );
        })}
        {mistakes.length === 0 && <p className="rounded-xl bg-slate-50 px-4 py-6 text-sm leading-6 text-slate-600">当前筛选下暂无错题。完成客观题、案例简答、AI 病患模拟或变式作答后，错误结果会自动记录在这里。</p>}
      </div>

      {selectedMistake?.answer_context_required && !selectedMistake.answer_context_completed && (
        <section className="space-y-3 rounded-xl border border-amber-200 bg-amber-50 p-4" aria-label="错题作答情况调研">
          <div><h4 className="text-sm font-semibold text-amber-950">先回忆当时怎么做的</h4><p className="mt-1 text-xs leading-5 text-amber-800">系统先了解你的把握和判断过程，再分析错因并生成变式。</p></div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs font-medium text-slate-700">当时的把握
              <select value={answerState} onChange={(event) => setAnswerState(event.target.value)} className="mt-1.5 w-full rounded-lg border border-amber-200 bg-white p-2 text-sm">
                <option value="">请选择</option>
                {['确定后作答', '犹豫后作答', '排除后猜测', '完全猜测', '误读题意'].map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
            <label className="text-xs font-medium text-slate-700">你认为更接近的原因
              <select value={reason} onChange={(event) => setReason(event.target.value)} className="mt-1.5 w-full rounded-lg border border-amber-200 bg-white p-2 text-sm">
                <option value="">请选择</option>
                {['概念混淆', '审题遗漏', '记忆不清', '选项辨析困难', '操作失误', '其他'].map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
          </div>
          <label className="block text-xs font-medium text-slate-700">补充说明（可选）
            <textarea value={contextNotes} onChange={(event) => setContextNotes(event.target.value)} className="mt-1.5 min-h-20 w-full rounded-lg border border-amber-200 bg-white p-2 text-sm" />
          </label>
          <button type="button" disabled={loading || !answerState || !reason} onClick={() => run(async () => {
            const response = await submitMistakeAnswerContext({ fetcher: fetchJsonWithAuthFallback, mistakeId: Number(selectedMistakeId), answerState, reason, notes: contextNotes });
            if (response.error) { setError(response.error); return; }
            setMistakes((items) => items.map((item) => item.mistake_id === response.mistake.mistake_id ? response.mistake : item));
          })} className="rounded-lg bg-amber-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">保存作答情况</button>
        </section>
      )}
      {selectedMistake && !selectedMistake.variation_available && selectedMistake.answer_context_completed && <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-800">{selectedMistake.variation_reason}</p>}
      <button type="button" onClick={generate} disabled={loading || !selectedMistake?.variation_available} className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50">
        {loading && <Loader2 size={16} className="animate-spin" />}生成变式
      </button>

      {selected && <div className="space-y-3 border-t border-slate-200 pt-4">
        <p className="text-sm font-semibold leading-6 text-slate-900">{selected.stem}</p>
        <p className="text-xs leading-5 text-slate-500">知识点：{selected.kp_names?.join('、') || '已关联，名称待同步'}</p>
        <label className="block text-sm font-medium text-slate-700">你的答案
          <textarea value={answer} onChange={(event) => setAnswer(event.target.value)} disabled={loading} className="mt-2 min-h-24 w-full rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm" />
        </label>
        <button type="button" onClick={grade} disabled={loading || !answer.trim()} className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">提交批改</button>
      </div>}
      {result && <div className="border-l-2 border-emerald-300 pl-3 text-sm leading-6 text-slate-700"><p>得分：{grading.score} / {grading.max_score}</p><p>{grading.feedback || grading.error_reason || '批改已完成。'}</p></div>}
      {error && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm leading-6 text-rose-700">{error}</p>}
    </div>
  );
}
