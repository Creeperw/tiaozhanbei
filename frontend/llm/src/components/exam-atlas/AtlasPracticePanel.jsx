import React, { useEffect, useMemo, useRef, useState } from 'react';
import { loadPracticeQuestion, submitPracticeAnswer } from '../../pageDataLoaders';
import { fetchJsonWithAuthFallback } from '../../utils/api';
import { Button, EmptyState, InlineError, Skeleton } from '../ui';

const multipleTypes = new Set(['multiple_choice', '多选题', '多项选择题']);
const singleTypes = new Set(['single_choice', 'true_false', '单选题', '单项选择题', '判断题']);

function optionProjection(option, index) {
  if (typeof option === 'string') return { value: option, label: option };
  const key = String(option?.option_id || option?.key || option?.label || String.fromCharCode(65 + index));
  const content = String(option?.content ?? option?.value ?? option?.text ?? '').trim();
  return { value: key, label: content ? `${key}. ${content}` : key };
}

export default function AtlasPracticePanel({
  knowledgePoint = null,
  scope = 'public',
  mode = 'objective',
  onResult,
}) {
  const [question, setQuestion] = useState(null);
  const [answer, setAnswer] = useState('');
  const [selectedAnswers, setSelectedAnswers] = useState([]);
  const [result, setResult] = useState(null);
  const [loadingQuestion, setLoadingQuestion] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [generation, setGeneration] = useState(0);
  const operationGenerationRef = useRef(0);
  const kpId = knowledgePoint?.kpId || knowledgePoint?.kp_id || '';
  const kpName = knowledgePoint?.kpName || knowledgePoint?.kp_name || '';

  useEffect(() => {
    const operation = operationGenerationRef.current + 1;
    operationGenerationRef.current = operation;
    let cancelled = false;
    const load = async () => {
      await Promise.resolve();
      if (cancelled || operation !== operationGenerationRef.current) return;
      setQuestion(null);
      setAnswer('');
      setSelectedAnswers([]);
      setResult(null);
      setSubmitting(false);
      setError('');
      setLoadingQuestion(true);
      const loaded = await loadPracticeQuestion({
        fetcher: fetchJsonWithAuthFallback,
        mode,
        kpId,
        topic: kpName,
        scope,
      });
      if (cancelled || operation !== operationGenerationRef.current) return;
      setQuestion(loaded.practice.available ? loaded.practice.question : null);
      if (loaded.error) setError(loaded.error);
      setLoadingQuestion(false);
    };
    load();
    return () => { cancelled = true; };
  }, [generation, kpId, mode, scope]);

  const questionOptions = useMemo(() => {
    const options = Array.isArray(question?.options) ? question.options : [];
    if (question?.question_type === 'true_false' && options.length === 0) return ['正确', '错误'].map(optionProjection);
    return options.map(optionProjection);
  }, [question]);
  const isMultiple = multipleTypes.has(question?.question_type);
  const isSingle = singleTypes.has(question?.question_type);
  const submittedAnswer = isMultiple ? selectedAnswers.join(',') : answer.trim();

  const toggleMultiple = (value) => {
    setSelectedAnswers((current) => (
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value]
    ));
  };

  const submit = async () => {
    if (!question || !submittedAnswer || submitting) return;
    const operation = operationGenerationRef.current;
    setSubmitting(true);
    setError('');
    const response = await submitPracticeAnswer({
      fetcher: fetchJsonWithAuthFallback,
      question,
      answer: submittedAnswer,
    });
    if (operation === operationGenerationRef.current) {
      if (response.error) setError(response.error);
      else {
        setResult(response.result);
        onResult?.(response.result, question);
      }
      setSubmitting(false);
    }
  };

  const nextQuestion = () => setGeneration((value) => value + 1);

  if (loadingQuestion) return <Skeleton label={`正在加载${mode === 'case' ? '案例简答题' : '客观题'}`} lines={3} />;
  if (error && !question) return <InlineError message={error} />;
  if (!question) {
    return (
      <EmptyState
        title={mode === 'case' ? '当前暂无可用案例简答题' : '当前暂无可用客观题'}
        description={kpName ? `“${kpName}”暂未匹配到相应题型，可切换题目范围或其他训练模式。` : '题库补齐后会在此提供受控练习。'}
      />
    );
  }

  return (
    <section className="mt-5" aria-labelledby="practice-question">
      <div className="text-xs font-semibold text-emerald-700">
        {question.source_scope === 'user' ? '我的题目' : '正式题库'}
        {kpName ? ` · ${kpName}` : ''}
        {' · '}{mode === 'case' ? '案例简答' : '客观题'}
      </div>
      <p id="practice-question" className="mt-2 text-sm font-medium leading-6 text-slate-950">
        {question.stem}
      </p>
      <p className="mt-2 text-xs text-slate-500">
        难度 {question.difficulty} · {question.question_type}
      </p>

      {(isSingle || isMultiple) && questionOptions.length > 0 ? (
        <fieldset className="mt-5 space-y-2" disabled={submitting || Boolean(result)}>
          <legend className="mb-2 text-sm font-medium text-slate-700">你的答案</legend>
          {questionOptions.map((option) => {
            const checked = isMultiple ? selectedAnswers.includes(option.value) : answer === option.value;
            return (
              <label key={option.value} className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-800">
                <input
                  type={isMultiple ? 'checkbox' : 'radio'}
                  name="practice-answer"
                  checked={checked}
                  onChange={() => (isMultiple ? toggleMultiple(option.value) : setAnswer(option.value))}
                />
                <span>{option.label}</span>
              </label>
            );
          })}
        </fieldset>
      ) : (
        <label htmlFor="practice-answer" className="mt-5 block text-sm font-medium text-slate-700">
          你的答案
          <textarea
            id="practice-answer"
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
            className="mt-2 min-h-28 w-full rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm leading-6 text-slate-800 outline-none transition focus:border-emerald-400 focus:bg-white focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
            disabled={submitting || Boolean(result)}
          />
        </label>
      )}

      {!result && (
        <Button className="mt-4" onClick={submit} disabled={!submittedAnswer} loading={submitting}>
          提交并批改
        </Button>
      )}
      {error && question && <div className="mt-4"><InlineError message={error} /></div>}
      {result && (
        <div className={`mt-4 border p-4 text-sm ${result.grading?.is_correct ? 'border-emerald-200 bg-emerald-50 text-emerald-950' : 'border-amber-200 bg-amber-50 text-amber-950'}`} role="status">
          <div className="font-semibold">{result.grading?.is_correct ? '回答正确' : '已记录为错题'} · 得分 {result.grading?.score ?? '待确认'}</div>
          <p className="mt-2 leading-6">{result.grading?.analysis || '批改已完成。'}</p>
          <p className="mt-2 text-xs">学习写回：{result.writeback?.status || '未返回'}</p>
          <Button className="mt-4" variant="secondary" onClick={nextQuestion}>下一题</Button>
        </div>
      )}
    </section>
  );
}
