import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  BookOpenText,
  ChevronRight,
  CircleHelp,
  FileText,
  Lightbulb,
  PenLine,
  Send,
} from 'lucide-react';
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

function questionTypeLabel(questionType, mode) {
  if (mode === 'case') return '案例简答';
  if (multipleTypes.has(questionType)) return '多项选择';
  if (questionType === 'true_false' || questionType === '判断题') return '判断题';
  if (singleTypes.has(questionType)) return '单项选择';
  return '简答题';
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

function difficultySourceLabel(source) {
  if (source === 'formal_question_bank') return '正式题库标注';
  if (source === 'question_bank_snapshot') return '题库快照';
  if (source === 'system_default') return '题库未标注，系统默认';
  if (source === 'variation') return '沿用原题';
  return '题目快照';
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
  const [hintVisible, setHintVisible] = useState(false);
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
      setHintVisible(false);
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
  }, [generation, kpId, kpName, mode, scope]);

  const questionOptions = useMemo(() => {
    const options = Array.isArray(question?.options) ? question.options : [];
    if (question?.question_type === 'true_false' && options.length === 0) return ['正确', '错误'].map(optionProjection);
    return options.map(optionProjection);
  }, [question]);
  const isMultiple = multipleTypes.has(question?.question_type);
  const isSingle = singleTypes.has(question?.question_type);
  const submittedAnswer = isMultiple ? selectedAnswers.join(',') : answer.trim();
  const typeLabel = questionTypeLabel(question?.question_type, mode);
  const knowledgeLabels = kpName
    ? [kpName]
    : (Array.isArray(question?.kp_names)
      ? question.kp_names.filter((label) => label && !question?.kp_ids?.includes(label)).slice(0, 3)
      : []);
  const guidance = buildGuidance({ isMultiple, isSingle, mode, kpName: kpName || knowledgeLabels[0] || '' });

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
    <section className="practice-question-shell" aria-labelledby="practice-question">
      <div className="practice-question-grid">
        <div className="practice-answer-workspace">
          <header className="practice-section-heading">
            <span className="practice-section-heading__icon" aria-hidden="true"><PenLine size={20} /></span>
            <div>
              <h3>题目内容</h3>
              <p>认真阅读题目，完成本次训练</p>
            </div>
          </header>

          <article className="practice-question-card">
            <div className="practice-question-meta">
              <span>{question.source_scope === 'user' ? '我的题目' : '正式题库'}</span>
              <span>{typeLabel}</span>
              <span title={`来源：${difficultySourceLabel(question.difficulty_source)}`}>难度 D{question.difficulty || 2}</span>
            </div>
            <p id="practice-question">{question.stem}</p>
          </article>

          {knowledgeLabels.length > 0 && (
            <section className="practice-knowledge-block" aria-label="相关知识点">
              <div className="practice-subheading"><BookOpenText size={17} aria-hidden="true" />相关知识点</div>
              <div className="practice-knowledge-chips">
                {knowledgeLabels.map((label) => <span key={label}>{label}</span>)}
              </div>
            </section>
          )}

          <button
            type="button"
            className="practice-hint-trigger"
            aria-label="若暂时没有思路，点我查看提示"
            aria-expanded={hintVisible}
            aria-controls="practice-hint-panel"
            onClick={() => setHintVisible((visible) => !visible)}
          >
            <CircleHelp size={21} aria-hidden="true" />
            <span>
              <strong>若暂时没有思路，点我查看提示</strong>
              <small>提示只提供解题方向，不会直接显示答案</small>
            </span>
            <ChevronRight className={hintVisible ? 'is-expanded' : ''} size={19} aria-hidden="true" />
          </button>

          <section className="practice-answer-block" aria-labelledby="practice-answer-title">
            <div className="practice-subheading" id="practice-answer-title"><FileText size={17} aria-hidden="true" />我的答案 <span>必填</span></div>
            {(isSingle || isMultiple) && questionOptions.length > 0 ? (
              <fieldset className="practice-option-list" disabled={submitting || Boolean(result)}>
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
                  disabled={submitting || Boolean(result)}
                />
              </>
            )}
          </section>

          {!result && (
            <Button aria-label="提交并批改" className="practice-submit-button" onClick={submit} disabled={!submittedAnswer} loading={submitting}>
              <Send size={17} aria-hidden="true" />提交训练任务
            </Button>
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
              {result.grading?.question_explanation && (
                <div className="practice-question-explanation">
                  <strong>题目解析</strong>
                  <p>{result.grading.question_explanation}</p>
                  <small>解析来源：{result.grading.explanation_source === 'generated_on_first_attempt' ? '首次作答自动生成并保存' : '题目解析库'}</small>
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
              <Button variant="secondary" onClick={nextQuestion}>下一题</Button>
            </div>
          )}
        </div>

        <aside
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

          {!hintVisible ? (
            <div className="practice-hint-locked">
              <CircleHelp size={30} aria-hidden="true" />
              <strong>提示尚未展开</strong>
              <p>先尝试独立分析题干；遇到卡点时，点击左侧提示按钮。</p>
            </div>
          ) : (
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
                  <div><dt>难度</dt><dd>D{question.difficulty || 2}（{difficultySourceLabel(question.difficulty_source)}）</dd></div>
                  <div><dt>来源</dt><dd>{question.source_scope === 'user' ? '我的题目' : '正式题库'}</dd></div>
                </dl>
              </section>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
