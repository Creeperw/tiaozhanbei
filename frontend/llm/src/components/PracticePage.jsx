import React, { useEffect, useState } from 'react';
import { FileText } from 'lucide-react';
import { createLearningFocusTracker } from '../learningFocusTracker.js';
import { fetchJsonWithAuthFallback } from '../utils/api';
import QuestionTrainingPanel from './QuestionTrainingPanel';
import CaseTrainingPanel from './CaseTrainingPanel';
import MistakeVariationPanel from './MistakeVariationPanel';
import PaperGenerationPanel from './PaperGenerationPanel';
import { isTrainingTaskResultApproved } from '../pageDataLoaders.js';
import { practiceContextFromIntent } from './exam-atlas/examAtlasPageContext';

const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

function displayValue(value, depth = 0) {
  if (value === null || value === undefined || value === '') return '暂无';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (depth >= 2) return '已省略嵌套内容';
  if (Array.isArray(value)) {
    if (value.length === 0) return '暂无';
    const visibleItems = value.slice(0, 5).map((item) => displayValue(item, depth + 1));
    return `${visibleItems.join('；')}${value.length > 5 ? '；等' : ''}`;
  }
  if (isRecord(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return '暂无';
    const visibleEntries = entries.slice(0, 5)
      .map(([key, item]) => `${key}：${displayValue(item, depth + 1)}`);
    return `${visibleEntries.join('；')}${entries.length > 5 ? '；等' : ''}`;
  }
  return '暂无';
}

function contentSections(content) {
  if (!isRecord(content)) return [];
  const sections = Array.isArray(content.sections) ? content.sections : content.cards;
  if (!Array.isArray(sections)) return [];
  return sections.slice(0, 8).map((section, index) => ({
    key: section?.id || section?.key || `${index}-${section?.title || 'section'}`,
    title: typeof section?.title === 'string' ? section.title : `内容 ${index + 1}`,
    body: displayValue(section?.body ?? section?.content ?? section?.full ?? section),
  }));
}

function EmptyState({ children }) {
  return <p className="[overflow-wrap:anywhere] py-5 text-sm leading-6 text-slate-500">{children}</p>;
}

function KnowledgeCardContent({ content }) {
  const front = content.front;
  const back = content.back;
  const memoryAnchor = content.memory_anchor;
  const hasCardFields = front !== undefined || back !== undefined || memoryAnchor !== undefined;

  if (!hasCardFields) return null;
  return (
    <div className="space-y-4">
      {front !== undefined && <div><h4 className="text-sm font-semibold text-slate-900">正面</h4><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(front)}</p></div>}
      {back !== undefined && <div><h4 className="text-sm font-semibold text-slate-900">背面</h4><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(back)}</p></div>}
      {memoryAnchor !== undefined && <div className="border-l-2 border-emerald-300 pl-4"><h4 className="text-sm font-semibold text-slate-900">记忆锚点</h4><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(memoryAnchor)}</p></div>}
    </div>
  );
}

function ArtifactResult({ taskResult }) {
  const artifact = isRecord(taskResult?.artifact) ? taskResult.artifact : {};
  const content = artifact.content;
  const artifactType = artifact.artifact_type;

  if (!artifactType) {
    return <EmptyState>提交任务后，训练产物会在这里显示。</EmptyState>;
  }

  if (!isRecord(content)) {
    return (
      <div className="space-y-3">
        <h3 className="text-lg font-semibold text-slate-950">{displayValue(artifact.title || taskResult?.title)}</h3>
        <p className="mt-2 break-words whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(content)}</p>
      </div>
    );
  }

  if (artifactType === 'grading_result') {
    const grading = isRecord(content.grading) ? content.grading : {};
    const remediation = isRecord(content.remediation) ? content.remediation : {};
    const reviewCard = isRecord(remediation.review_card) ? remediation.review_card : {};
    const variants = Array.isArray(remediation.variant_questions) ? remediation.variant_questions.slice(0, 5) : [];
    const hasContent = Object.keys(grading).length > 0 || Object.keys(reviewCard).length > 0 || variants.length > 0;

    if (!hasContent) return <EmptyState>批改已返回，但暂未提供可展示的详细产物。</EmptyState>;

    return (
      <div className="space-y-5">
        <h3 className="text-lg font-semibold text-slate-950">{displayValue(artifact.title || taskResult?.title)}</h3>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200 pb-4 text-sm text-slate-700">
          <span className="font-semibold text-slate-950">得分：{displayValue(grading.score)} / 100</span>
          <span>{grading.is_correct ? '判定：回答正确' : '判定：需要复盘'}</span>
          {grading.error_type && <span>错因：{displayValue(grading.error_type)}</span>}
        </div>
        <div>
          <h4 className="text-sm font-semibold text-slate-900">分析</h4>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(grading.analysis)}</p>
        </div>
        {grading.question_explanation && (
          <div className="border-l-2 border-sky-300 pl-4">
            <h4 className="text-sm font-semibold text-slate-900">题目解析</h4>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(grading.question_explanation)}</p>
            <p className="mt-2 text-xs text-slate-500">
              解析来源：{grading.explanation_source === 'generated_on_first_attempt' ? '首次作答自动生成并保存' : '题目解析库'}
            </p>
          </div>
        )}
        {(reviewCard.title || reviewCard.content) && (
          <div className="border-l-2 border-emerald-300 pl-4">
            <h4 className="text-sm font-semibold text-slate-900">{displayValue(reviewCard.title)}</h4>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(reviewCard.content)}</p>
          </div>
        )}
        {variants.length > 0 && (
          <div>
            <h4 className="text-sm font-semibold text-slate-900">变式练习</h4>
            <ol className="mt-2 space-y-2 text-sm leading-6 text-slate-700">
              {variants.map((item, index) => <li key={item?.key || index}>{index + 1}. {displayValue(item?.stem ?? item)}</li>)}
            </ol>
          </div>
        )}
      </div>
    );
  }

  if (artifactType === 'handout' || artifactType === 'knowledge_card') {
    const sections = contentSections(content);
    const fallback = content.body ?? content.content ?? content.full ?? content.summary;
    const knowledgeCard = artifactType === 'knowledge_card' ? <KnowledgeCardContent content={content} /> : null;
    return (
      <div className="space-y-5">
        <div>
          <h3 className="text-lg font-semibold text-slate-950">{displayValue(artifact.title || taskResult?.title)}</h3>
          {taskResult?.summary && <p className="mt-2 text-sm leading-6 text-slate-600">{displayValue(taskResult.summary)}</p>}
        </div>
        {knowledgeCard || (sections.length > 0 ? sections.map((section) => (
          <div key={section.key} className="border-l-2 border-emerald-200 pl-4">
            <h4 className="text-sm font-semibold text-slate-900">{section.title}</h4>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{section.body}</p>
          </div>
        )) : fallback !== undefined ? (
          <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(fallback)}</p>
        ) : <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(content)}</p>)}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <h3 className="text-lg font-semibold text-slate-950">{displayValue(artifact.title || taskResult?.title)}</h3>
      <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(content)}</p>
    </div>
  );
}

export default function PracticePage({ navigationContext = {} }) {
  const selectedKnowledgePoint = practiceContextFromIntent(navigationContext);
  const [activeTaskType, setActiveTaskType] = useState(() => navigationContext.taskType || 'question_training');
  const [taskResult, setTaskResult] = useState(null);
  const [mobilePage, setMobilePage] = useState('task');

  const workshopModules = [
    ['question_training', '题目训练'],
    ['ai_patient_simulation', 'AI 病患模拟'],
    ['mistake_variation', '错题变式'],
    ['paper_workspace', '试卷生成'],
  ];

  useEffect(() => {
    const request = async (path, body) => {
      const result = await fetchJsonWithAuthFallback({
        paths: [path],
        options: {
          method: 'POST',
          keepalive: true,
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        },
      });
      return result.data;
    };
    const tracker = createLearningFocusTracker({
      request,
      resourceType: 'training_workspace',
      resourceId: 'practice',
    });
    tracker.start().catch(() => {});
    return () => {
      tracker.stop().catch(() => {});
    };
  }, []);

  const handlePracticeResult = (result, question) => {
    const grading = result?.grading || {};
    setTaskResult({
      task_id: result?.attempt_id || `practice-${question.question_id}`,
      task_type: 'practice_grading',
      status: 'completed',
      title: `${question.question_type}批改结果`,
      summary: grading.is_correct ? '回答正确，学习记录已更新。' : '回答错误，已进入错题记录。',
      artifact: {
        artifact_type: 'grading_result',
        title: '练习批改结果',
        content: { grading, remediation: {} },
      },
      evidence_pack: {},
      audit: { decision: 'pass', reason: '正式题库受控批改已完成' },
      trace: [],
      learning_updates: { writeback: result?.writeback || {} },
      next_actions: [],
    });
    setMobilePage('result');
  };

  const selectWorkshopModule = (taskType) => {
    setActiveTaskType(taskType);
    setTaskResult(null);
    setMobilePage('task');
  };

  const taskResultApproved = isTrainingTaskResultApproved(taskResult);

  return (
    <div className="space-y-5 text-slate-800">
      <div className="border-b border-slate-200">
        <div className="flex flex-wrap gap-1" role="tablist" aria-label="训练工坊模块">
          {workshopModules.map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={activeTaskType === key}
              onClick={() => selectWorkshopModule(key)}
              className={`border-b-2 px-4 py-3 text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2 ${activeTaskType === key ? 'border-emerald-600 text-emerald-800' : 'border-transparent text-slate-600 hover:border-slate-300 hover:text-slate-950'}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="practice-mobile-tabs" role="tablist" aria-label="移动端训练视图">
        {[
          ['task', '任务'],
          ['result', '结果'],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={mobilePage === key}
            onClick={() => setMobilePage(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {selectedKnowledgePoint && (
        <section className="border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-950" aria-label="当前考纲知识点">
          <div className="font-semibold">当前训练上下文：{selectedKnowledgePoint.kpName}</div>
          <div className="mt-1 font-mono text-xs text-emerald-800">{selectedKnowledgePoint.kpId}</div>
          <p className="mt-2 leading-6 text-emerald-900">
            该知识点已带入训练工坊；当前兼容示例题不代表该知识点的正式题目，正式题源筛选将在题库接入后启用。
          </p>
        </section>
      )}

      <div className="min-w-0 space-y-5">
          <section data-mobile-active={String(mobilePage === 'task')} className="practice-task-panel rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/50">
            {activeTaskType === 'ai_patient_simulation' ? (
              <CaseTrainingPanel enabled />
            ) : activeTaskType === 'mistake_variation' ? (
              <MistakeVariationPanel enabled />
            ) : activeTaskType === 'paper_workspace' ? (
              <PaperGenerationPanel enabled paperId={navigationContext.paperId || navigationContext.paper_id || ''} />
            ) : activeTaskType === 'question_training' ? (
              <QuestionTrainingPanel
                enabled
                selectedKnowledgePoint={selectedKnowledgePoint}
                initialMode={navigationContext.taskType || ''}
                onResult={handlePracticeResult}
              />
            ) : (
              <div className="mt-5 rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-5 text-sm leading-6 text-slate-600">
                此模块正在准备中，暂不支持提交任务。
              </div>
            )}

          </section>

          <section
            data-testid="practice-result-panel"
            data-mobile-active={String(mobilePage === 'result')}
            aria-busy="false"
            aria-labelledby="training-artifact-title"
            className="practice-result-panel rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/50"
          >
            <div className="flex items-center gap-2 text-sm font-medium text-slate-600">
              <FileText size={16} aria-hidden="true" />
              <h2 id="training-artifact-title" className="text-sm font-semibold text-slate-900">训练产物</h2>
            </div>
            {taskResult && !taskResultApproved ? (
              <div role="alert" className="mt-4 border border-rose-300 bg-rose-50 px-4 py-4 text-rose-950">
                <h3 className="text-base font-semibold">{displayValue(taskResult.title || taskResult.artifact?.title)}</h3>
                <p className="mt-2 text-sm font-semibold leading-6">审核未通过/任务未完成，该候选内容不可作为学习依据。</p>
                <p className="mt-2 text-sm leading-6">状态：{displayValue(taskResult.status)}</p>
                <p className="mt-1 text-sm leading-6">审核原因：{displayValue(taskResult.audit?.reason)}</p>
                <p className="mt-3 text-sm font-semibold">请调整输入后重试。</p>
              </div>
            ) : (
              <div className="mt-4 [overflow-wrap:anywhere]"><ArtifactResult taskResult={taskResult} /></div>
            )}
          </section>
      </div>
    </div>
  );
}
