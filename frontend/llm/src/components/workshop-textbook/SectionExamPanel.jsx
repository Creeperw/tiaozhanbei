import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  CheckCircle2,
  CircleHelp,
  Eye,
  EyeOff,
  FileQuestion,
  Layers3,
  Lightbulb,
  LoaderCircle,
  XCircle,
} from 'lucide-react';
import { loadSectionQuestions } from './textbookChapterApi';

const typeLabels = {
  single_choice: '单选题',
  multiple_choice: '多选题',
  true_false: '判断题',
};

function isMultipleChoice(qType) {
  const t = String(qType || '').toLowerCase();
  return t.includes('multiple') || t.includes('多选') || t.includes('多项');
}

function isTrueFalse(qType) {
  const t = String(qType || '').toLowerCase();
  return t.includes('true_false') || t.includes('判断');
}

const TRUE_FALSE_OPTIONS = [
  { key: '正确', text: '正确', tf: true },
  { key: '错误', text: '错误', tf: true },
];

function normalizeTrueFalseAnswer(value) {
  const s = String(value || '').trim();
  // 正确类
  if (/^[√✓✔☑⊤⊨]$/.test(s)) return '正确';
  if (/^(正确|对|true|yes|是|a|right|correct)$/i.test(s)) return '正确';
  // 错误类
  if (/^[×✗✘☒⊥⊭]$/.test(s)) return '错误';
  if (/^(错误|错|false|no|否|b|wrong|incorrect)$/i.test(s)) return '错误';
  return s;
}

function questionTypeBadge(qType) {
  return typeLabels[qType] || qType || '简答题';
}

function difficultyLabel(level) {
  const num = Number(level || 3);
  if (num <= 1) return '简单';
  if (num === 2) return '较易';
  if (num === 3) return '中等';
  if (num === 4) return '较难';
  return '困难';
}

function difficultyColor(level) {
  const num = Number(level || 3);
  if (num <= 2) return '#5b9e7d';
  if (num === 3) return '#b58a3c';
  return '#c2574b';
}

const LABELS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

function parseOptions(raw) {
  // 对象格式: {"A": "...", "B": "..."}
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    return Object.entries(raw).map(([key, value], index) => ({
      key: key || LABELS[index] || String(index),
      text: typeof value === 'string' ? value : String(value),
    }));
  }
  if (!Array.isArray(raw) || raw.length === 0) return [];
  return raw.map((option, index) => {
    if (typeof option === 'string') {
      return { key: LABELS[index] || String(index), text: option };
    }
    if (option && typeof option === 'object') {
      const key = option.key || option.option_id || option.label || LABELS[index] || String(index);
      const text = option.value ?? option.content ?? option.text ?? String(option);
      return { key, text };
    }
    return { key: LABELS[index] || String(index), text: String(option) };
  });
}

function normalizeAnswer(value) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, '');
}

function checkAnswer(userAnswer, correctAnswer, questionType) {
  const user = normalizeAnswer(userAnswer);
  const correct = normalizeAnswer(correctAnswer);
  if (!user) return null;

  // 判断题：统一归化为 正确/错误 再比较
  if (isTrueFalse(questionType)) {
    return normalizeTrueFalseAnswer(userAnswer) === normalizeTrueFalseAnswer(correctAnswer);
  }

  if (isMultipleChoice(questionType)) {
    const userSet = new Set(user.split(',').map((s) => s.trim()).filter(Boolean).sort());
    const correctSet = new Set(correct.split(',').map((s) => s.trim()).filter(Boolean).sort());
    if (userSet.size !== correctSet.size) return false;
    for (const val of userSet) {
      if (!correctSet.has(val)) return false;
    }
    return true;
  }

  // 单选或文本题
  return user === correct || correct.includes(user) || user.includes(correct);
}

export default function SectionExamPanel({ sectionName, kpIds = [], onBack }) {
  const uniqueKpIds = useMemo(() => [...new Set(kpIds.filter(Boolean))], [kpIds]);
  const [questions, setQuestions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // Per-question state: { [questionId]: { answer: string, revealed: boolean, selectedOptions: string[] } }
  const [questionState, setQuestionState] = useState({});

  useEffect(() => {
    if (!uniqueKpIds.length) {
      setQuestions([]);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setQuestionState({});
    loadSectionQuestions(uniqueKpIds, { signal: controller.signal })
      .then((result) => setQuestions(result.items))
      .catch((err) => {
        if (err.name !== 'AbortError') setError(err.message || '题目加载失败');
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [uniqueKpIds.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  const setAnswer = (questionId, value) => {
    setQuestionState((prev) => ({
      ...prev,
      [questionId]: { ...(prev[questionId] || {}), answer: value },
    }));
  };

  const toggleOption = (questionId, optionKey) => {
    setQuestionState((prev) => {
      const current = prev[questionId] || {};
      const selected = current.selectedOptions || [];
      const next = selected.includes(optionKey)
        ? selected.filter((k) => k !== optionKey)
        : [...selected, optionKey];
      return {
        ...prev,
        [questionId]: { ...current, selectedOptions: next, answer: next.sort().join(',') },
      };
    });
  };

  const setSingleOption = (questionId, optionKey) => {
    setQuestionState((prev) => ({
      ...prev,
      [questionId]: { ...(prev[questionId] || {}), selectedOptions: [optionKey], answer: optionKey },
    }));
  };

  const revealAnswer = (questionId) => {
    setQuestionState((prev) => {
      const current = prev[questionId] || {};
      return { ...prev, [questionId]: { ...current, revealed: true } };
    });
  };

  const hideAnswer = (questionId) => {
    setQuestionState((prev) => {
      const current = prev[questionId] || {};
      return { ...prev, [questionId]: { ...current, revealed: false } };
    });
  };

  if (loading) {
    return (
      <div className="section-exam-loading" role="status">
        <LoaderCircle aria-hidden="true" size={20} />正在加载本小节题目…
      </div>
    );
  }

  if (error) {
    return (
      <div className="section-exam-error" role="alert">
        <p>{error}</p>
      </div>
    );
  }

  if (!uniqueKpIds.length) {
    return (
      <div className="section-exam-empty">
        <Layers3 size={36} className="mx-auto mb-3 text-slate-300" />
        <p>该小节暂无关联知识点，无法加载题目。</p>
      </div>
    );
  }

  if (!questions.length) {
    return (
      <div className="section-exam-empty">
        <FileQuestion size={36} className="mx-auto mb-3 text-slate-300" />
        <h3>{sectionName || '本节'} · 题目列表</h3>
        <p>该小节知识点暂未匹配到题目，题库补充后会在此展示。</p>
      </div>
    );
  }

  // Global question index
  let globalIndex = 0;

  return (
    <div className="section-exam-panel">
      <header className="section-exam-header">
        <button type="button" className="section-exam-back" onClick={onBack}>
          <ArrowLeft size={14} aria-hidden="true" />返回小节目录
        </button>
        <em>{questions.length} 道题目</em>
      </header>

      <div className="section-exam-worksheet">
        {questions.map((q) => {
          globalIndex += 1;
          const qNumber = globalIndex;
          const qState = questionState[q.question_id] || {};
          const isTF = isTrueFalse(q.question_type);
          const rawOptions = parseOptions(Array.isArray(q.options) ? q.options : []);
          // 判断题始终提供"正确/错误"两个选项
          const options = isTF && rawOptions.length === 0 ? TRUE_FALSE_OPTIONS : rawOptions;
          const isMultiple = isMultipleChoice(q.question_type);
          const hasOptions = options.length > 0;
          const userAnswer = qState.answer || '';
          const revealed = qState.revealed || false;
          const isCorrect = revealed ? checkAnswer(userAnswer, q.answer, q.question_type) : null;

          return (
            <article key={q.question_id} className={`section-exam-question-card ${revealed ? 'is-revealed' : ''}`}>
              {/* Question header */}
              <div className="section-exam-q-header">
                <span className="section-exam-q-number">{qNumber}</span>
                <span className="section-exam-q-badges">
                  <b>{questionTypeBadge(q.question_type)}</b>
                  <i style={{ color: difficultyColor(q.difficulty) }}>
                    {difficultyLabel(q.difficulty)}
                  </i>
                </span>
              </div>

              {/* Stem */}
              <p className="section-exam-q-stem">{q.stem || '（题目内容暂缺）'}</p>

              {/* Options for choice questions */}
              {hasOptions && options.length > 0 && (
                <fieldset className="section-exam-options" disabled={revealed}>
                  <legend className="sr-only">选项</legend>
                  {options.map((opt) => {
                    const checked = isMultiple
                      ? (qState.selectedOptions || []).includes(opt.key)
                      : userAnswer === opt.key;
                    const isCorrectOption = revealed && (
                      isTF
                        ? normalizeTrueFalseAnswer(q.answer) === opt.key
                        : String(q.answer || '').toLowerCase().includes(opt.key.toLowerCase())
                    );
                    return (
                      <label
                        key={opt.key}
                        className={`section-exam-option ${revealed && isCorrectOption ? 'is-correct-option' : ''} ${revealed && checked && !isCorrectOption ? 'is-wrong-option' : ''}`}
                      >
                        <input
                          type={isMultiple ? 'checkbox' : 'radio'}
                          name={`q-${q.question_id}`}
                          checked={checked}
                          onChange={() => (isMultiple ? toggleOption(q.question_id, opt.key) : setSingleOption(q.question_id, opt.key))}
                        />
                        {!opt.tf && <strong>{opt.key}</strong>}
                        <span>{opt.text}</span>
                      </label>
                    );
                  })}
                </fieldset>
              )}

              {/* Free text input for non-choice questions */}
              {!hasOptions && (
                <textarea
                  className="section-exam-textarea"
                  value={userAnswer}
                  onChange={(e) => setAnswer(q.question_id, e.target.value)}
                  placeholder="请在此输入你的答案…"
                  disabled={revealed}
                  rows={3}
                />
              )}

              {/* Action buttons */}
              <div className="section-exam-q-actions">
                {!revealed ? (
                  <button
                    type="button"
                    className="section-exam-reveal-btn"
                    onClick={() => revealAnswer(q.question_id)}
                    disabled={!userAnswer}
                  >
                    <Eye size={14} aria-hidden="true" />查看答案
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      className="section-exam-hide-btn"
                      onClick={() => hideAnswer(q.question_id)}
                    >
                      <EyeOff size={14} aria-hidden="true" />隐藏答案
                    </button>
                    {userAnswer && (
                      <span className={`section-exam-selfcheck ${isCorrect ? 'is-correct' : 'is-incorrect'}`}>
                        {isCorrect ? (
                          <><CheckCircle2 size={14} aria-hidden="true" />回答正确</>
                        ) : (
                          <><XCircle size={14} aria-hidden="true" />回答错误</>
                        )}
                      </span>
                    )}
                  </>
                )}
              </div>

              {/* Revealed answer section */}
              {revealed && (
                <div className="section-exam-revealed">

                  <div className="section-exam-answer">
                    <h4><CircleHelp size={14} aria-hidden="true" />参考答案</h4>
                    <p>{q.answer || '暂无参考答案'}</p>
                  </div>

                  {(q.analysis || q.explanation) && (
                    <div className="section-exam-analysis">
                      <h4><Lightbulb size={14} aria-hidden="true" />解析</h4>
                      <p>{q.analysis || q.explanation}</p>
                    </div>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
