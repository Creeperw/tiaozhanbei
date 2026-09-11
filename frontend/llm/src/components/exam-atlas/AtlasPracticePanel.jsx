import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  BookOpenText,
  FileText,
  PenLine,
  Send,
  SkipForward,
} from 'lucide-react';
import {
  loadDailyTaskPracticeQuestion,
  loadPracticeQuestion,
  loadPracticeQuestionList,
  skipPracticeQuestion,
  submitPracticeAnswer,
  tagPracticeQuestionDifficulty,
} from '../../pageDataLoaders';
import { fetchJsonWithAuthFallback } from '../../utils/api';
import { Button, EmptyState, InlineError, Skeleton } from '../ui';
import { FavoriteQuestionButton, NoteQuestionButton } from '../WorkshopSaveActions';

const multipleTypes = new Set(['multiple_choice', '多选题', '多项选择题']);
const singleTypes = new Set(['single_choice', 'true_false', '单选题', '单项选择题', '判断题']);

function optionProjection(option, index) {
  if (typeof option === 'string') return { value: option, label: option };
  const key = String(option?.option_id || option?.key || option?.label || String.fromCharCode(65 + index));
  const content = String(option?.content ?? option?.value ?? option?.text ?? '').trim();
  return { value: key, label: content ? `${key}. ${content}` : key };
}

function questionTypeLabel(questionType, mode) {
  if (mode === 'case') return '案例简答';
  if (multipleTypes.has(questionType)) return '多项选择';
  if (questionType === 'true_false' || questionType === '判断题') return '判断题';
  if (singleTypes.has(questionType)) return '单项选择';
  return '简答题';
}

function formatStandardAnswer(value) {
  if (value == null) return '';
  if (Array.isArray(value)) {
    return value.map(formatStandardAnswer).filter(Boolean).join('、');
  }
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }

  const text = String(value).trim();
  if (!text) return '';
  if ((text.startsWith('[') && text.endsWith(']')) || (text.startsWith('{') && text.endsWith('}'))) {
    try {
      return formatStandardAnswer(JSON.parse(text));
    } catch {
      // Keep malformed legacy answer snapshots readable instead of hiding them.
    }
  }
  const choiceTokens = text.split(/[,，、;；\s]+/).filter(Boolean);
  if (choiceTokens.length > 1 && choiceTokens.every((token) => /^[A-Za-z]$/.test(token))) {
    return choiceTokens.map((token) => token.toUpperCase()).join('、');
  }
  return text;
}

function historyStatusLabel(status, result) {
  if (status === 'reviewed') return '已批改';
  if (status === 'skipped') return '已跳过';
  if (status === 'submitted') return result?.grading?.is_correct ? '回答正确' : '已批改';
  return '作答中';
}

export default function AtlasPracticePanel({
  knowledgePoint = null,
  scope = 'public',
  mode = 'objective',
  onResult,
  taskItemId = '',
  practiceOrigin = 'question_training',
}) {
  const [question, setQuestion] = useState(null);
  const [answer, setAnswer] = useState('');
  const [selectedAnswers, setSelectedAnswers] = useState([]);
  const [result, setResult] = useState(null);
  const [loadingQuestion, setLoadingQuestion] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState(null);
  const [generation, setGeneration] = useState(0);
  const [history, setHistory] = useState([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [questionStatus, setQuestionStatus] = useState('active');
  const [difficultyFilter, setDifficultyFilter] = useState(null);
  const [difficultyAvailable, setDifficultyAvailable] = useState(false);
  const [availableDifficulties, setAvailableDifficulties] = useState([]);
  const [taggingDifficulty, setTaggingDifficulty] = useState(false);
  const operationGenerationRef = useRef(0);
  const excludedQuestionIdRef = useRef('');
  const historyRef = useRef([]);
  const historyIndexRef = useRef(-1);
  const kpId = knowledgePoint?.kpId || knowledgePoint?.kp_id || '';
  const kpName = knowledgePoint?.kpName || knowledgePoint?.kp_name || '';
  const fixedQuestionList = Boolean(kpId || taskItemId);

  const replaceHistory = (nextHistory, nextIndex = historyIndexRef.current) => {
    historyRef.current = nextHistory;
    historyIndexRef.current = nextIndex;
    setHistory(nextHistory);
    setHistoryIndex(nextIndex);
  };

  const persistCurrentHistory = (overrides = {}) => {
    const index = historyIndexRef.current;
    if (index < 0 || !question) return historyRef.current;
    const nextHistory = [...historyRef.current];
    nextHistory[index] = {
      ...nextHistory[index],
      question,
      answer,
      selectedAnswers: [...selectedAnswers],
      result,
      progress,
      status: questionStatus,
      ...overrides,
    };
    replaceHistory(nextHistory, index);
    return nextHistory;
  };

  const restoreHistoryEntry = (targetIndex) => {
    const nextHistory = persistCurrentHistory();
    const entry = nextHistory[targetIndex];
    if (!entry) return;
    operationGenerationRef.current += 1;
    historyIndexRef.current = targetIndex;
    setHistoryIndex(targetIndex);
    setQuestion(entry.question);
    setAnswer(entry.answer || '');
    setSelectedAnswers([...(entry.selectedAnswers || [])]);
    setResult(entry.result || null);
    setProgress(entry.progress || null);
    setQuestionStatus(entry.status || 'active');
    setSubmitting(false);
    setSkipping(false);
    setLoadingQuestion(false);
    setError('');
  };

  useEffect(() => {
    const operation = operationGenerationRef.current + 1;
    operationGenerationRef.current = operation;
    const excludedQuestionId = excludedQuestionIdRef.current;
    let cancelled = false;
    const load = async () => {
      await Promise.resolve();
      if (cancelled || operation !== operationGenerationRef.current) return;
      if (generation === 0 || fixedQuestionList) replaceHistory([], -1);
      setQuestion(null);
      setAnswer('');
      setSelectedAnswers([]);
      setResult(null);
      setSubmitting(false);
      setSkipping(false);
      setError('');
      setProgress(null);
      setLoadingQuestion(true);
      if (fixedQuestionList) {
        const loaded = await loadPracticeQuestionList({
          fetcher: fetchJsonWithAuthFallback, taskItemId, kpId, mode, scope, difficulty: difficultyFilter,
        });
        if (cancelled || operation !== operationGenerationRef.current) return;
        const entries = loaded.practice.questions.map((item) => ({
          question: { ...item, listed: true },
          answer: item.submitted_answer || '',
          selectedAnswers: multipleTypes.has(item.question_type)
            ? String(item.submitted_answer || '').split(',').filter(Boolean) : [],
          result: null,
          progress: loaded.practice.progress || null,
          status: item.reviewed ? 'reviewed' : 'active',
        }));
        replaceHistory(entries, entries.length ? 0 : -1);
        const first = entries[0];
        setQuestion(first?.question || null);
        setAnswer(first?.answer || '');
        setSelectedAnswers(first?.selectedAnswers || []);
        setQuestionStatus(first?.status || 'active');
        setProgress(loaded.practice.progress || null);
        setDifficultyAvailable(false);
        setAvailableDifficulties([]);
        setError(loaded.error || '');
        setLoadingQuestion(false);
        return;
      }
      const loaded = taskItemId
        ? await loadDailyTaskPracticeQuestion({ fetcher: fetchJsonWithAuthFallback, taskItemId, excludeQuestionId: excludedQuestionId })
        : await loadPracticeQuestion({ fetcher: fetchJsonWithAuthFallback, mode, kpId, topic: kpName, scope, difficulty: difficultyFilter, excludeQuestionId: excludedQuestionId });
      if (cancelled || operation !== operationGenerationRef.current) return;
      excludedQuestionIdRef.current = '';
      const loadedQuestion = loaded.practice.available ? loaded.practice.question : null;
      setQuestion(loadedQuestion);
      setProgress(loaded.practice.progress || null);
      setDifficultyAvailable(loaded.practice.difficulty_available === true);
      if (Array.isArray(loaded.practice.available_difficulties)) {
        setAvailableDifficulties(loaded.practice.available_difficulties);
      }
      if (loadedQuestion) {
        const entry = {
          question: loadedQuestion,
          answer: '',
          selectedAnswers: [],
          result: null,
          progress: loaded.practice.progress || null,
          status: 'active',
        };
        const nextHistory = [...historyRef.current.slice(0, historyIndexRef.current + 1), entry];
        replaceHistory(nextHistory, nextHistory.length - 1);
        setQuestionStatus('active');
      }
      if (loaded.error) setError(loaded.error);
      setLoadingQuestion(false);
    };
    load();
    return () => { cancelled = true; };
  }, [generation, kpId, kpName, mode, scope, taskItemId, difficultyFilter, fixedQuestionList]);

  const questionOptions = useMemo(() => {
    const options = Array.isArray(question?.options) ? question.options : [];
    if (question?.question_type === 'true_false' && options.length === 0) return ['正确', '错误'].map(optionProjection);
    return options.map(optionProjection);
  }, [question]);
  const isMultiple = multipleTypes.has(question?.question_type);
  const isSingle = singleTypes.has(question?.question_type);
  const submittedAnswer = isMultiple ? selectedAnswers.join(',') : answer.trim();
  const typeLabel = questionTypeLabel(question?.question_type, mode);
  const standardAnswer = formatStandardAnswer(result?.grading?.standard_answer);
  const standardAnswerLabel = mode === 'case' ? '参考答案' : '正确答案';
  const knowledgeLabels = [...new Set((kpName
    ? [kpName]
    : (Array.isArray(question?.kp_names)
      ? question.kp_names.filter((label) => label && !question?.kp_ids?.includes(label))
      : []))
    .map((label) => String(label).trim())
    .filter(Boolean))].slice(0, 3);
  const favoriteQuestion = {
    resource_id: question?.question_id || question?.id || `${question?.stem || ''}`.slice(0, 80),
    title: `${typeLabel} · ${String(question?.stem || '').slice(0, 80)}`,
    content: {
      question_content: question?.stem || '',
      question_type: question?.question_type || '',
      options: question?.options || [],
      standard_answer: result?.grading?.standard_answer || [],
      explanation: result?.grading?.question_explanation || '',
      knowledge_points: knowledgeLabels,
    },
  };

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
      taskItemId,
      practiceOrigin,
    });
    if (operation === operationGenerationRef.current) {
      if (response.error) setError(response.error);
      else {
        setResult(response.result);
        setQuestionStatus('submitted');
        persistCurrentHistory({ result: response.result, status: 'submitted' });
        onResult?.(response.result, question);
      }
      setSubmitting(false);
    }
  };

  const skipQuestion = async () => {
    if (!question || submitting || skipping || result) return;
    const operation = operationGenerationRef.current;
    setSkipping(true);
    setError('');
    const response = await skipPracticeQuestion({
      fetcher: fetchJsonWithAuthFallback,
      question,
      taskItemId,
    });
    if (operation !== operationGenerationRef.current) return;
    if (response.error || !response.skipped) {
      setError(response.error || '跳过题目失败');
      setSkipping(false);
      return;
    }
    setQuestionStatus('skipped');
    persistCurrentHistory({ status: 'skipped' });
    excludedQuestionIdRef.current = question.question_id;
    setGeneration((value) => value + 1);
  };

  const tagQuestionDifficulty = async (level) => {
    if (!question || taggingDifficulty || question.difficulty != null) return;
    const operation = operationGenerationRef.current;
    setTaggingDifficulty(true);
    setError('');
    const response = await tagPracticeQuestionDifficulty({
      fetcher: fetchJsonWithAuthFallback,
      question,
      difficulty: level,
    });
    if (operation !== operationGenerationRef.current) return;
    if (response.error || !response.saved) {
      setError(response.error || '标记难度失败，请稍后重试');
      setTaggingDifficulty(false);
      return;
    }
    // The learner's tag now counts as a real label for them: show it on the
    // card and make the level selectable in the difficulty filter.
    setQuestion((current) => (current ? { ...current, difficulty: level, difficulty_source: 'user_tagged' } : current));
    setAvailableDifficulties((current) => (current.includes(level) ? current : [...current, level]));
    setDifficultyAvailable(true);
    setTaggingDifficulty(false);
  };

  const previousQuestion = () => {
    if (historyIndexRef.current <= 0 || submitting || skipping) return;
    restoreHistoryEntry(historyIndexRef.current - 1);
  };

  const nextQuestion = () => {
    if (submitting || skipping) return;
    if (historyIndexRef.current < historyRef.current.length - 1) {
      restoreHistoryEntry(historyIndexRef.current + 1);
      return;
    }
    if (fixedQuestionList) return;
    // Moving on must not re-serve the question just left. The backend treats an
    // exhausted candidate list with an explicit exclusion as "no more questions".
    excludedQuestionIdRef.current = question?.question_id || '';
    persistCurrentHistory();
    setGeneration((value) => value + 1);
  };

  if (loadingQuestion) return <Skeleton label={`正在加载${mode === 'case' ? '案例简答题' : '客观题'}`} lines={3} />;
  if (error && !question) return <InlineError message={error} />;
  if (!question) {
    if (taskItemId && loadedProgressComplete(progress)) {
      return <p role="status">今日知识点练习已完成</p>;
    }
    if (history.length > 0) {
      return (
        <EmptyState
          title={mode === 'case' ? '该知识点案例简答题已练完' : '该知识点题目已练完'}
          description={kpName ? `“${kpName}”下的题目已全部作答，可返回上一题回顾，或切换题目范围继续练习。` : '当前范围的题目已全部作答，可返回上一题回顾。'}
          action={
            <Button
              variant="secondary"
              aria-label="返回上一题"
              onClick={() => restoreHistoryEntry(historyIndexRef.current)}
              disabled={historyIndex < 0 || submitting || skipping}
            >
              <ArrowLeft size={17} aria-hidden="true" />返回上一题
            </Button>
          }
        />
      );
    }
    return (
      <EmptyState
        title={mode === 'case' ? '当前暂无可用案例简答题' : '当前暂无可用客观题'}
        description={kpName ? `“${kpName}”暂未匹配到相应题型，可切换题目范围或其他练习模式。` : '题库补齐后会在此提供受控练习。'}
      />
    );
  }

  return (
    <section className="practice-question-shell" aria-labelledby="practice-question">
      <div className="practice-session-layout">
        <aside className="practice-question-list" aria-label="题目列表">
          <header>
            <div>
              <strong>题目列表</strong>
              <small>{fixedQuestionList ? '知识点专练' : '专项特训练习'}</small>
            </div>
            <span>{history.length} 题</span>
          </header>
          <nav>
            {history.map((entry, index) => {
              const entryStatus = historyStatusLabel(entry.status, entry.result);
              return (
                <button
                  key={`${entry.question?.snapshot_id || entry.question?.question_id || 'question'}:${index}`}
                  type="button"
                  className={`${index === historyIndex ? 'is-current' : ''} is-${entry.status || 'active'}`}
                  aria-current={index === historyIndex ? 'step' : undefined}
                  aria-label={`第${index + 1}题，${entryStatus}`}
                  onClick={() => restoreHistoryEntry(index)}
                  disabled={index === historyIndex || submitting || skipping}
                  title={entry.question?.stem || `第${index + 1}题`}
                >
                  <span>{index + 1}</span>
                  <span>
                    <strong>第 {index + 1} 题</strong>
                    <small>{entryStatus}</small>
                  </span>
                </button>
              );
            })}
          </nav>
        </aside>

        <div className="practice-question-grid" data-hint-visible="false">
        <div className="practice-answer-workspace">
          <header className="practice-section-heading">
            <span className="practice-section-heading__icon" aria-hidden="true"><PenLine size={20} /></span>
            <div>
              <h3>题目内容</h3>
              <p>认真阅读题目，完成本次练习</p>
            </div>
          </header>

          {difficultyAvailable && !fixedQuestionList && (
            <section className="practice-difficulty-filter" aria-label="难度筛选">
              <span className="practice-difficulty-filter__label">难度</span>
              <div className="practice-difficulty-filter__options">
                <button
                  type="button"
                  className={difficultyFilter === null ? 'is-active' : ''}
                  onClick={() => setDifficultyFilter(null)}
                  title="显示全部难度的题目"
                >
                  全部
                </button>
                {[1, 2, 3, 4, 5].map((level) => (
                  <button
                    key={level}
                    type="button"
                    className={difficultyFilter === level ? 'is-active' : ''}
                    disabled={availableDifficulties.length > 0 && !availableDifficulties.includes(level)}
                    onClick={() => setDifficultyFilter(level)}
                    title={`只看 ${level} 星难度的题目${availableDifficulties.length > 0 && !availableDifficulties.includes(level) ? '（当前题库暂无该难度标注）' : ''}`}
                  >
                    {level}星
                  </button>
                ))}
              </div>
            </section>
          )}

          <article className="practice-question-card">
            <div className="practice-question-meta">
              <span>第 {historyIndex + 1} 题</span>
              <span>{question.source_scope === 'user' ? '我的题目' : '正式题库'}</span>
              <span>{typeLabel}</span>
              {question.difficulty != null && (
                <span className="practice-question-difficulty" data-testid="question-difficulty-label">
                  难度 {question.difficulty}星
                </span>
              )}
            </div>
            <p id="practice-question">{question.stem}</p>
          </article>

          {!taskItemId && question.difficulty == null && (
            <section className="practice-difficulty-tagging" aria-label="标记本题难度">
              <span className="practice-difficulty-tagging__label">本题暂无难度标注</span>
              <div className="practice-difficulty-tagging__options">
                {[1, 2, 3, 4, 5].map((level) => (
                  <button
                    key={level}
                    type="button"
                    className="practice-difficulty-tagging__star"
                    disabled={taggingDifficulty}
                    onClick={() => tagQuestionDifficulty(level)}
                    title={`标记本题为 ${level} 星难度`}
                  >
                    {level}星
                  </button>
                ))}
              </div>
              <small className="practice-difficulty-tagging__hint">标记后本题将参与难度筛选，仅对你可见</small>
            </section>
          )}


          <section className="practice-answer-block" aria-labelledby="practice-answer-title">
            <div className="practice-subheading" id="practice-answer-title"><FileText size={17} aria-hidden="true" />我的答案 <span>必填</span></div>
            {(isSingle || isMultiple) && questionOptions.length > 0 ? (
              <fieldset className="practice-option-list" disabled={submitting || Boolean(result) || questionStatus !== 'active'}>
                <legend className="sr-only">你的答案</legend>
                {questionOptions.map((option) => {
                  const checked = isMultiple ? selectedAnswers.includes(option.value) : answer === option.value;
                  return (
                    <label key={option.value} className="practice-option">
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
              <>
                <label htmlFor="practice-answer" className="sr-only">你的答案</label>
                <textarea
                  id="practice-answer"
                  value={answer}
                  onChange={(event) => setAnswer(event.target.value)}
                  className="practice-answer-input"
                  placeholder="请在此输入你的答案，并尽量写出判断依据……"
                  disabled={submitting || Boolean(result) || questionStatus !== 'active'}
                />
              </>
            )}
          </section>

          {questionStatus === 'skipped' && (
            <p className="practice-skipped-notice" role="status">该题已跳过，原作答凭证已失效。</p>
          )}
          {questionStatus === 'reviewed' && (
            <p role="status">该题已批改，已保留原作答记录。</p>
          )}
          {questionStatus === 'active' && !result && (
            <div className="practice-question-footer">
              <Button
                aria-label="返回上一题"
                className="practice-previous-button"
                variant="secondary"
                onClick={previousQuestion}
                disabled={historyIndex <= 0 || submitting || skipping}
              >
                <ArrowLeft size={17} aria-hidden="true" />返回上一题
              </Button>
              <div className="practice-submit-actions">
              <Button
                aria-label="提交并批改"
                className="practice-submit-button"
                onClick={submit}
                disabled={!submittedAnswer || skipping}
                loading={submitting}
              >
                <Send size={17} aria-hidden="true" />提交练习任务
              </Button>
              {!fixedQuestionList && <Button
                aria-label="跳过该题"
                className="practice-skip-button"
                variant="secondary"
                onClick={skipQuestion}
                disabled={submitting}
                loading={skipping}
              >
                <SkipForward size={17} aria-hidden="true" />跳过该题
              </Button>}
              {fixedQuestionList && historyIndex < history.length - 1 && (
                <Button variant="secondary" onClick={nextQuestion} disabled={submitting}>下一题</Button>
              )}
              </div>
            </div>
          )}
          {error && question && <InlineError message={error} />}
          {result && (
            <div className={`practice-grading-result ${result.grading?.is_correct ? 'is-correct' : 'is-incorrect'}`} role="status">
              <div className="font-semibold">
                {result.audit && result.audit.decision !== 'pass'
                  ? '等待审核，暂不写入学习状态'
                  : result.grading?.is_correct ? '回答正确' : '已记录为错题'}
                {' · '}得分 {result.grading?.score ?? '待确认'}
              </div>
              <p>{result.grading?.analysis || '批改已完成。'}</p>
              {standardAnswer && (
                <section className="practice-standard-answer" aria-label={standardAnswerLabel}>
                  <strong>{standardAnswerLabel}</strong>
                  <p>{standardAnswer}</p>
                </section>
              )}
              {(result.grading?.question_explanation || knowledgeLabels.length > 0) && (
                <div className="practice-question-explanation">
                  <strong>题目解析</strong>
                  {result.grading?.question_explanation && <p>{result.grading.question_explanation}</p>}
                  {knowledgeLabels.length > 0 && (
                    <section className="practice-explanation-knowledge" aria-label="相关知识点">
                      <div className="practice-subheading"><BookOpenText size={17} aria-hidden="true" />相关知识点</div>
                      <div className="practice-knowledge-chips">
                        {knowledgeLabels.map((label) => <span key={label}>{label}</span>)}
                      </div>
                    </section>
                  )}
                  {result.grading?.question_explanation && (
                    <small>解析来源：{result.grading.explanation_source === 'generated_on_first_attempt' ? '首次作答自动生成并保存' : '题目解析库'}</small>
                  )}
                </div>
              )}
              {result.grading?.grading_source && (
                <small>批改来源：{result.grading.grading_source === 'expert_agent_model' ? 'Expert Agent 模型' : '规则降级结果'}</small>
              )}
              {result.audit && (
                <small>Audit：{result.audit.decision} · {result.audit.reason || '无补充说明'}</small>
              )}
              {result.grading?.dimension_scores && Object.keys(result.grading.dimension_scores).length > 0 && (
                <div className="practice-grading-dimensions">
                  {Object.entries(result.grading.dimension_scores).map(([label, value]) => (
                    <small key={label}>{label}：{typeof value === 'object' ? (value.score ?? JSON.stringify(value)) : value}</small>
                  ))}
                </div>
              )}
              <small>学习写回：{result.writeback?.status || '未返回'}</small>
            </div>
          )}
          {(result || questionStatus === 'skipped' || questionStatus === 'reviewed') && (
            <div className="practice-question-footer">
              <Button
                aria-label="返回上一题"
                className="practice-previous-button"
                variant="secondary"
                onClick={previousQuestion}
                disabled={historyIndex <= 0 || submitting || skipping}
              >
                <ArrowLeft size={17} aria-hidden="true" />返回上一题
              </Button>
              <div className="practice-result-actions">
                {result && <FavoriteQuestionButton question={favoriteQuestion} source="题目练习" />}
                {result && <NoteQuestionButton question={favoriteQuestion} source="题目练习" />}
                {(!fixedQuestionList || historyIndex < history.length - 1) && (
                  <Button variant="secondary" onClick={nextQuestion}>下一题</Button>
                )}
              </div>
            </div>
          )}
        </div>

        </div>
      </div>
    </section>
  );
}

function loadedProgressComplete(value) {
  return Number(value?.required || 0) > 0
    && Number(value?.reviewed || 0) >= Number(value.required);
}
