import React, { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { loadPaper, savePaperAnswers, submitPaper, submitTrainingWorkspaceTask } from '../pageDataLoaders';

const questionTypes = [
  ['single_choice', '单选题'],
  ['short_answer', '简答题'],
  ['case_quiz', '案例题'],
];
const paperStorageKey = 'training-paper-id';

export default function PaperGenerationPanel({ enabled }) {
  const [topic, setTopic] = useState('围绕四君子汤与脾胃气虚证完成训练');
  const [difficulty, setDifficulty] = useState(1);
  const [distribution, setDistribution] = useState({ single_choice: 1, short_answer: 0, case_quiz: 0 });
  const [paper, setPaper] = useState(null);
  const [answers, setAnswers] = useState({});
  const [submissionRequestId, setSubmissionRequestId] = useState('');
  const [submitted, setSubmitted] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const questionCount = useMemo(
    () => Object.values(distribution).reduce((total, count) => total + count, 0),
    [distribution],
  );
  const types = questionTypes.filter(([key]) => distribution[key] > 0).map(([key]) => key);
  const canGenerate = enabled && topic.trim() && questionCount > 0 && questionCount <= 50;
  const paperLocked = paper?.status === 'submitted' || Boolean(submitted);
  const allAnswered = paper?.items?.every((item) => answers[item.paper_item_id]?.trim());

  const restorePaper = (loaded) => {
    setPaper(loaded.paper);
    setAnswers(Object.fromEntries(loaded.paper.items.map((item) => [item.paper_item_id, item.answer])));
    setSubmitted(loaded.paper.status === 'submitted' ? loaded.paper.result : null);
    setSubmissionRequestId(`paper-${crypto.randomUUID()}`);
  };

  useEffect(() => {
    let active = true;
    const paperId = sessionStorage.getItem(paperStorageKey);
    if (!paperId) return () => { active = false; };
    loadPaper({ fetcher: fetchJsonWithAuthFallback, paperId }).then((loaded) => {
      if (!active) return;
      if (loaded.error) {
        sessionStorage.removeItem(paperStorageKey);
        return;
      }
      restorePaper(loaded);
    });
    return () => { active = false; };
  }, []);

  const setCount = (key, value) => {
    const count = Math.max(0, Math.min(50, Number.parseInt(value, 10) || 0));
    setDistribution({ ...distribution, [key]: count });
  };

  const generate = async () => {
    if (!canGenerate) {
      setError('请填写主题，并设置 1 至 50 道题的题型分布。');
      return;
    }
    setLoading(true);
    setError('');
    setPaper(null);
    setSubmitted(null);
    try {
      const response = await submitTrainingWorkspaceTask({
        fetcher: fetchJsonWithAuthFallback,
        task: {
          task_type: 'paper_generation',
          title: '训练试卷',
          query: topic.trim(),
          inputs: {
            topic: topic.trim(),
            difficulty,
            question_count: questionCount,
            types,
            distribution: Object.fromEntries(types.map((key) => [key, distribution[key]])),
          },
          options: { need_audit: true },
        },
      });
      if (response.error) {
        setError(response.error);
        return;
      }
      if (response.taskResult.status !== 'completed' || response.taskResult.audit?.decision !== 'pass') {
        setError(response.taskResult.summary || response.taskResult.audit?.reason || '试卷未通过审核。');
        return;
      }
      const paperId = response.taskResult.artifact?.content?.paper_id;
      const loaded = await loadPaper({ fetcher: fetchJsonWithAuthFallback, paperId });
      if (loaded.error) {
        setError(loaded.error);
        return;
      }
      sessionStorage.setItem(paperStorageKey, paperId);
      restorePaper(loaded);
    } finally {
      setLoading(false);
    }
  };

  const save = async () => {
    setLoading(true);
    setError('');
    try {
      const saved = await savePaperAnswers({ fetcher: fetchJsonWithAuthFallback, paperId: paper.paper_id, answers });
      if (saved.error) {
        setError(saved.error);
        return false;
      }
      setPaper(saved.paper);
      setAnswers(Object.fromEntries(saved.paper.items.map((item) => [item.paper_item_id, item.answer])));
      return true;
    } finally {
      setLoading(false);
    }
  };

  const submit = async () => {
    const saved = await save();
    if (!saved || !allAnswered) return;
    setLoading(true);
    setError('');
    try {
      const response = await submitPaper({ fetcher: fetchJsonWithAuthFallback, paperId: paper.paper_id, requestId: submissionRequestId });
      if (response.error) {
        setError(response.error);
        return;
      }
      setSubmitted(response.result);
    } finally {
      setLoading(false);
    }
  };

  if (!enabled) return <p className="mt-5 text-sm leading-6 text-slate-600">试卷生成暂未开放。</p>;

  return (
    <div className="mt-5 space-y-5">
      {!paper && <>
        <label className="block text-sm font-medium text-slate-700">训练主题
          <textarea value={topic} onChange={(event) => setTopic(event.target.value)} disabled={loading} className="mt-2 min-h-20 w-full rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm" />
        </label>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-sm font-medium text-slate-700">难度
            <select value={difficulty} onChange={(event) => setDifficulty(Number(event.target.value))} disabled={loading} className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm">
              {[1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <p className="self-end text-sm text-slate-600">题量：{questionCount} 题</p>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {questionTypes.map(([key, label]) => <label key={key} className="text-sm font-medium text-slate-700">{label}
            <input type="number" min="0" max="50" value={distribution[key]} onChange={(event) => setCount(key, event.target.value)} disabled={loading} className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" />
          </label>)}
        </div>
        <button type="button" onClick={generate} disabled={loading || !canGenerate} className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50">
          {loading && <Loader2 size={16} className="animate-spin" />}生成试卷
        </button>
      </>}
      {paper && <div className="space-y-4 border-t border-slate-200 pt-4">
        <h3 className="text-base font-semibold text-slate-950">{paper.title}</h3>
        {paper.items.map((item) => <label key={item.paper_item_id} className="block text-sm font-medium text-slate-700">{item.position}. {item.stem}
          <textarea value={answers[item.paper_item_id] || ''} onChange={(event) => setAnswers({ ...answers, [item.paper_item_id]: event.target.value })} disabled={loading || paperLocked} className="mt-2 min-h-24 w-full rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm" />
        </label>)}
        {!paperLocked && <div className="flex flex-wrap gap-2"><button type="button" onClick={save} disabled={loading} className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 disabled:opacity-50">保存答案</button><button type="button" onClick={submit} disabled={loading || !allAnswered} className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">提交试卷</button></div>}
      </div>}
      {submitted && <div className="border-l-2 border-emerald-300 pl-3 text-sm leading-6 text-slate-700"><p>总分：{submitted.score} / {submitted.max_score}</p><p>已完成 {submitted.items?.length || 0} 道题的服务端评分。</p></div>}
      {error && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm leading-6 text-rose-700">{error}</p>}
    </div>
  );
}
