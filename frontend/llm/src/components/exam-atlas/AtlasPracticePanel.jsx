import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  BookOpenText,
  ChevronRight,
  CircleHelp,
  FileText,
  Lightbulb,
  PenLine,
  Send,
  SkipForward,
} from 'lucide-react';
import {
  loadDailyTaskPracticeQuestion,
  loadPracticeQuestion,
  skipPracticeQuestion,
  submitPracticeAnswer,
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

function buildGuidance({ isMultiple, isSingle, mode, kpName }) {
  const steps = [
    '先圈出题干中的限定条件，明确题目真正要求回答的对象。',
    kpName
      ? `围绕“${kpName}”回忆核心概念，再把概念与题干条件逐一对应。`
      : '先回忆相关核心概念，再把概念与题干条件逐一对应。',
  ];

  if (isMultiple) steps.push('逐项判断每个选项，不要因为某一项正确就提前结束。');
  else if (isSingle) steps.push('先排除与题干条件冲突的选项，再比较剩余选项。');
  else if (mode === 'case') steps.push('按“关键信息—辨析依据—结论”三个层次组织回答。');
  else steps.push('按“概念—依据—结论”分层表达，避免只罗列关键词。');

  return steps;
}

function historyStatusLabel(status, result) {
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
}) {
  const [question, setQuestion] = useState(null);
  const [answer, setAnswer] = useState('');
  const [selectedAnswers, setSelectedAnswers] = useState([]);
  const [result, setResult] = useState(null);
  const [hintVisible, setHintVisible] = useState(false);
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
  const operationGenerationRef = useRef(0);
  const excludedQuestionIdRef = useRef('');
  const historyRef = useRef([]);
  const historyIndexRef = useRef(-1);
  const kpId = knowledgePoint?.kpId || knowledgePoint?.kp_id || '';
  const kpName = knowledgePoint?.kpName || knowledgePoint?.kp_name || '';

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
      hintVisible,
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
    setHintVisible(Boolean(entry.hintVisible));
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
      if (generation === 0) replaceHistory([], -1);
      setQuestion(null);
      setAnswer('');
      setSelectedAnswers([]);
      setResult(null);
      setHintVisible(false);
      setSubmitting(false);
      setSkipping(false);
      setError('');
      setProgress(null);
      setLoadingQuestion(true);
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
          hintVisible: false,
          progress: loaded.practice.progress || null,
          status: 'active',
        };
        const nextHistory = [
          ...historyRef.current.slice(0, historyIndexRef.current + 1),
          entry,
        ];
        replaceHistory(nextHistory, nextHistory.length - 1);
        setQuestionStatus('active');
      }
      if (loaded.error) setError(loaded.error);
      setLoadingQuestion(false);
    };
    load();
    return () => { cancelled = true; };
  }, [generation, kpId, kpName, mode, scope, taskItemId, difficultyFilter]);

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
  const guidance = buildGuidance({ isMultiple, isSingle, mode, kpName: kpName || knowledgeLabels[0] || '' });
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
    persistCurrentHistory();
    setGeneration((value) => value + 1);
  };

  if (loadingQuestion) return <Skeleton label={`正在加载${mode === 'case' ? '案例简答题' : '客观题'}`} lines={3} />;
  if (error && !question) return <InlineError message={error} />;
  if (!question) {
    if (taskItemId && loadedProgressComplete(progress)) {
      return <p role="status">今日知识点练习已完成</p>;
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
              <small>专项特训练习</small>
            </div>
            <span>{history.length} 题</span>
          </header>
          <nav>
            {history.map((entry, index) => {
              const entryStatus = historyStatusLabel(entry.status, entry.result);
              return (
                <button
                  key={`${entry.question?.question_id || 'question'}:${entry.question?.request_id || index}`}
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

        <div className="practice-question-grid" data-hint-visible={String(hintVisible)}>
        <div className="practice-answer-workspace">
          <header className="practice-section-heading">
            <span className="practice-section-heading__icon" aria-hidden="true"><PenLine size={20} /></span>
            <div>
              <h3>题目内容</h3>
              <p>认真阅读题目，完成本次练习</p>
            </div>
          </header>

          {difficultyAvailable && !taskItemId && (
            <section className="practice-difficulty-filter" aria-label="难度筛选">
              <span className="practice-difficulty-filter__label">难度</span>
              <div className="practice-difficulty-filter__options">
                <button
                  type="button"
                  className={difficultyFilter === null ? 'is-active' : ''}
                  onClick={() => setDifficultyFilter(null)}
                >
                  不限
                </button>
                {[1, 2, 3, 4, 5].map((level) => (
                  <button
                    key={level}
                    type="button"
                    className={difficultyFilter === level ? 'is-active' : ''}
                    disabled={availableDifficulties.length > 0 && !availableDifficulties.includes(level)}
                    onClick={() => setDifficultyFilter(level)}
                    title={`难度 ${level}${availableDifficulties.length > 0 && !availableDifficulties.includes(level) ? '（当前题库暂无该难度标注）' : ''}`}
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
            </div>
            <p id="practice-question">{question.stem}</p>
          </article>

          <button
            type="button"
            className="practice-hint-trigger"
            aria-label={hintVisible ? '收起答题提示' : '查看答题提示'}
            aria-expanded={hintVisible}
            aria-controls="practice-hint-panel"
            onClick={() => setHintVisible((visible) => !visible)}
          >
            <CircleHelp size={21} aria-hidden="true" />
            <span>
              <strong>{hintVisible ? '收起答题提示' : '查看答题提示'}</strong>
              <small>提示只提供解题方向，不会直接显示答案</small>
            </span>
            <ChevronRight className={hintVisible ? 'is-expanded' : ''} size={19} aria-hidden="true" />
          </button>

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
              <Button
                aria-label="跳过该题"
                className="practice-skip-button"
                variant="secondary"
                onClick={skipQuestion}
                disabled={submitting}
                loading={skipping}
              >
                <SkipForward size={17} aria-hidden="true" />跳过该题
              </Button>
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
          {(result || questionStatus === 'skipped') && (
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
                <Button variant="secondary" onClick={nextQuestion}>下一题</Button>
              </div>
            </div>
          )}
        </div>

        {hintVisible && <aside
          id="practice-hint-panel"
          data-testid="practice-hint-panel"
          data-visible={String(hintVisible)}
          className="practice-hint-panel"
          aria-live="polite"
        >
          <header className="practice-hint-panel__heading">
            <span aria-hidden="true"><Lightbulb size={21} /></span>
            <div>
              <h3>答题提示</h3>
              <p>需要时再展开，保留独立思考空间</p>
            </div>
          </header>

            <div className="practice-hint-content">
              <section>
                <h4>思路引导</h4>
                <ol>
                  {guidance.map((item, index) => (
                    <li key={item}><span>{index + 1}</span><p>{item}</p></li>
                  ))}
                </ol>
              </section>
              <section className="practice-hint-note">
                <h4>作答建议</h4>
                <p>先写出你的判断，再补充一至两个关键依据。提交后系统才会展示批改反馈。</p>
              </section>
              <section className="practice-question-clues">
                <h4>题目线索</h4>
                <dl>
                  <div><dt>题型</dt><dd>{typeLabel}</dd></div>
                  <div><dt>来源</dt><dd>{question.source_scope === 'user' ? '我的题目' : '正式题库'}</dd></div>
                </dl>
              </section>
            </div>
        </aside>}
        </div>
      </div>
    </section>
  );
}

function loadedProgressComplete(value) {
  return Number(value?.required || 0) > 0
    && Number(value?.reviewed || 0) >= Number(value.required);
}
