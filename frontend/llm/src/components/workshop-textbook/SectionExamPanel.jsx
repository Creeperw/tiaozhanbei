import React, { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Eye, EyeOff, FileQuestion, LoaderCircle, XCircle } from 'lucide-react';
import { loadSectionQuestions } from './textbookChapterApi';

const LABELS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

function isMultipleChoice(questionType) {
  return /multiple|多选|多项/i.test(String(questionType || ''));
}

function isTrueFalse(questionType) {
  return /true_false|判断/i.test(String(questionType || ''));
}

function optionsFor(question) {
  if (isTrueFalse(question.question_type)) {
    return [{ key: '正确', text: '正确' }, { key: '错误', text: '错误' }];
  }
  return (question.options || []).map((option, index) => ({
    key: LABELS[index] || String(index),
    text: String(option),
  }));
}

function normalized(value) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, '');
}

function answersMatch(answer, referenceAnswer, questionType) {
  if (!answer) return null;
  if (isMultipleChoice(questionType)) {
    return normalized(answer).split(',').sort().join(',')
      === normalized(referenceAnswer).split(',').sort().join(',');
  }
  return normalized(answer) === normalized(referenceAnswer);
}

export default function SectionExamPanel({ sectionName, kpIds = [], onBack }) {
  const uniqueKpIds = useMemo(() => [...new Set(kpIds.filter(Boolean))], [kpIds]);
  const [questions, setQuestions] = useState([]);
  const [answers, setAnswers] = useState({});
  const [revealed, setRevealed] = useState({});
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!uniqueKpIds.length) {
      setQuestions([]);
      return undefined;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setQuestions([]);
    setAnswers({});
    setRevealed({});
    loadSectionQuestions(uniqueKpIds, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setQuestions(result.items || []);
      })
      .catch((reason) => {
        if (reason.name !== 'AbortError' && !controller.signal.aborted) setError(reason.message || '题目加载失败');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [uniqueKpIds.join(',')]);

  if (loading) return <p className="section-exam-loading" role="status"><LoaderCircle size={18} />正在加载本小节题目…</p>;
  if (error) return <p className="section-exam-error" role="alert">{error}</p>;
  if (!uniqueKpIds.length) return <p className="section-exam-empty">该小节暂无关联知识点，无法加载题目。</p>;
  if (!questions.length) return <p className="section-exam-empty"><FileQuestion size={22} />该小节暂未匹配到题目。</p>;

  return <section className="section-exam-panel" aria-label={`${sectionName}小节作答`}>
    <header className="section-exam-header">
      <div><span>作业与考试</span><h2>{sectionName}</h2></div>
      <button type="button" onClick={onBack}>返回小节目录</button>
    </header>
    <div className="section-exam-worksheet">
      {questions.map((question, index) => {
        const questionId = question.question_id;
        const answer = answers[questionId] || '';
        const isRevealed = Boolean(revealed[questionId]);
        const correct = isRevealed
          ? answersMatch(answer, question.reference_answer, question.question_type)
          : null;
        const options = optionsFor(question);
        return <article key={questionId} className="section-exam-question-card">
          <p className="section-exam-q-stem"><strong>{index + 1}.</strong>{question.stem}</p>
          {options.length ? <fieldset disabled={isRevealed}>
            <legend className="sr-only">选项</legend>
            {options.map((option) => {
              const selected = isMultipleChoice(question.question_type)
                ? answer.split(',').filter(Boolean).includes(option.key)
                : answer === option.key;
              return <label key={option.key} className="section-exam-option">
                <input
                  type={isMultipleChoice(question.question_type) ? 'checkbox' : 'radio'}
                  name={questionId}
                  checked={selected}
                  onChange={() => setAnswers((current) => {
                    const next = isMultipleChoice(question.question_type)
                      ? (selected ? answer.split(',').filter((item) => item !== option.key) : [...answer.split(',').filter(Boolean), option.key]).sort().join(',')
                      : option.key;
                    return { ...current, [questionId]: next };
                  })}
                />
                <span>{option.key}. {option.text}</span>
              </label>;
            })}
          </fieldset> : <textarea value={answer} disabled={isRevealed} onChange={(event) => setAnswers((current) => ({ ...current, [questionId]: event.target.value }))} placeholder="请输入你的答案" />}
          <div className="section-exam-q-actions">
            {isRevealed ? <>
              <button type="button" onClick={() => setRevealed((current) => ({ ...current, [questionId]: false }))}><EyeOff size={14} />隐藏答案</button>
              {correct === true && <span className="section-exam-selfcheck is-correct"><CheckCircle2 size={14} />回答正确</span>}
              {correct === false && <span className="section-exam-selfcheck is-incorrect"><XCircle size={14} />回答错误</span>}
            </> : <button type="button" onClick={() => setRevealed((current) => ({ ...current, [questionId]: true }))} disabled={!answer}><Eye size={14} />查看答案</button>}
          </div>
          {isRevealed && <div className="section-exam-revealed">
            <p><strong>参考答案：</strong>{question.reference_answer || '暂无参考答案'}</p>
            {question.analysis && <p><strong>解析：</strong>{question.analysis}</p>}
          </div>}
        </article>;
      })}
    </div>
  </section>;
}
