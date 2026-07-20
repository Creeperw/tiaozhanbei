import React, { useEffect, useRef, useState } from 'react';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../../utils/api';
import { Button, EmptyState, InlineError, Skeleton } from '../ui';

async function request(path, options) {
  const response = await fetchWithAuth(`${API_BASE}${path}`, options);
  const payload = await readJsonResponse(response, {});
  if (!response.ok) throw new Error(payload.detail || '练习请求失败');
  return payload;
}

export default function AtlasPracticePanel({ knowledgePoint, scope = 'public' }) {
  const [question, setQuestion] = useState(null);
  const [answer, setAnswer] = useState('');
  const [result, setResult] = useState(null);
  const [loadingQuestion, setLoadingQuestion] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const operationGenerationRef = useRef(0);

  useEffect(() => {
    const generation = operationGenerationRef.current + 1;
    operationGenerationRef.current = generation;
    let cancelled = false;
    setQuestion(null);
    setAnswer('');
    setResult(null);
    setSubmitting(false);
    setError('');
    const load = async () => {
      setLoadingQuestion(true);
      try {
        const payload = await request(
          `/training/practice/next?kp_id=${encodeURIComponent(knowledgePoint.kpId)}&scope=${encodeURIComponent(scope)}`,
        );
        if (!cancelled && generation === operationGenerationRef.current) {
          setQuestion(payload.available ? payload.question : null);
        }
      } catch (loadError) {
        if (!cancelled && generation === operationGenerationRef.current) {
          setError(loadError.message || '正式题目加载失败');
        }
      } finally {
        if (!cancelled && generation === operationGenerationRef.current) {
          setLoadingQuestion(false);
        }
      }
    };
    load();
    return () => { cancelled = true; };
  }, [knowledgePoint.kpId, scope]);

  const submit = async () => {
    if (!question || !answer.trim() || submitting) return;
    const generation = operationGenerationRef.current;
    setSubmitting(true);
    setError('');
    try {
      const payload = await request('/training/practice/grade', {
        method: 'POST',
        body: JSON.stringify({
          question_id: question.question_id,
          question_type: question.question_type,
          stem: question.stem,
          student_answer: answer.trim(),
          knowledge_points: question.kp_ids,
          difficulty: question.difficulty,
          request_id: question.request_id,
        }),
      });
      if (generation === operationGenerationRef.current) setResult(payload);
    } catch (submitError) {
      if (generation === operationGenerationRef.current) {
        setError(submitError.message || '答案提交失败');
      }
    } finally {
      if (generation === operationGenerationRef.current) setSubmitting(false);
    }
  };

  if (loadingQuestion) return <Skeleton label="正在加载正式练习题" lines={3} />;
  if (error && !question) return <InlineError message={error} />;
  if (!question) {
    return (
      <EmptyState
        title="该知识点暂无可练习的正式题目"
        description="可先查看资料或询问助教；题库补齐后会在此提供受控练习。"
      />
    );
  }

  return (
    <section className="mt-5" aria-labelledby="atlas-practice-question">
      <div className="text-xs font-semibold text-emerald-700">
        {question.source_scope === 'user' ? '我的题目' : '正式题库'} · {knowledgePoint.kpName}
      </div>
      <p id="atlas-practice-question" className="mt-2 text-sm font-medium leading-6 text-slate-950">
        {question.stem}
      </p>
      <p className="mt-2 text-xs text-slate-500">
        难度 {question.difficulty} · {question.question_type}
      </p>
      <label htmlFor="atlas-practice-answer" className="mt-5 block text-sm font-medium text-slate-700">
        你的答案
      </label>
      <textarea
        id="atlas-practice-answer"
        value={answer}
        onChange={(event) => setAnswer(event.target.value)}
        className="mt-2 min-h-32 w-full rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm leading-6 text-slate-800 outline-none transition focus:border-emerald-400 focus:bg-white focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
        disabled={submitting || Boolean(result)}
      />
      {!result && (
        <Button className="mt-4" onClick={submit} disabled={!answer.trim()} loading={submitting}>
          提交答案
        </Button>
      )}
      {error && <div className="mt-4"><InlineError message={error} /></div>}
      {result && (
        <div className="mt-4 border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950" role="status">
          <div className="font-semibold">得分：{result.grading?.score ?? '待确认'}</div>
          <p className="mt-2 leading-6">{result.grading?.analysis || '批改已完成。'}</p>
          <p className="mt-2 text-xs text-emerald-800">学习写回：{result.writeback?.status || '未返回'}</p>
        </div>
      )}
    </section>
  );
}
