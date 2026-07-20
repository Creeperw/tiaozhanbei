import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { loadVariationSources, submitTrainingWorkspaceTask } from '../pageDataLoaders';

const requestId = () => `variation-${crypto.randomUUID()}`;

export default function MistakeVariationPanel({ enabled }) {
  const [sources, setSources] = useState([]);
  const [selectedMistakeId, setSelectedMistakeId] = useState('');
  const [questions, setQuestions] = useState([]);
  const [selectedQuestion, setSelectedQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const refreshSources = async () => {
    const response = await loadVariationSources({ fetcher: fetchJsonWithAuthFallback });
    setSources(response.sources.items);
    if (response.error) setError(response.error);
  };

  useEffect(() => { refreshSources(); }, []);

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
    if (!Number.isInteger(mistakeId) || mistakeId <= 0) {
      setError('请选择一条可用错题。');
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
  });

  if (!enabled) return <p className="mt-5 text-sm leading-6 text-slate-600">错题变式暂未开放。</p>;

  const selected = questions.find((item) => item.question_version_id === selectedQuestion);
  const grading = result?.artifact?.content?.grading?.grading || {};
  return (
    <div className="mt-5 space-y-5">
      <label className="block text-sm font-medium text-slate-700">选择错题来源
        <select value={selectedMistakeId} onChange={(event) => setSelectedMistakeId(event.target.value)} disabled={loading} className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm">
          <option value="">请选择一条可用错题</option>
          {sources.map((item) => <option key={item.mistake_id} value={item.mistake_id}>#{item.mistake_id} · {item.stem}</option>)}
        </select>
      </label>
      {sources.length === 0 && <p className="text-sm leading-6 text-slate-600">暂无可用错题。完成普通练习并通过审核后，可在此生成变式。</p>}
      <button type="button" onClick={generate} disabled={loading || !selectedMistakeId} className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50">
        {loading && <Loader2 size={16} className="animate-spin" />}生成变式
      </button>
      {selected && <div className="space-y-3 border-t border-slate-200 pt-4">
        <p className="text-sm font-semibold leading-6 text-slate-900">{selected.stem}</p>
        <p className="text-xs leading-5 text-slate-500">知识点：{selected.kp_ids?.join('、') || '暂无'}</p>
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
