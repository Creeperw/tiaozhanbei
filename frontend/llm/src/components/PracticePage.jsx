import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ClipboardCheck,
  FileText,
} from 'lucide-react';
import { createLearningFocusTracker } from '../learningFocusTracker.js';
import { fetchJsonWithAuthFallback } from '../utils/api';
import PaperGenerationPanel from './PaperGenerationPanel';
import QuestionTrainingPanel from './QuestionTrainingPanel';
import KnowledgeCardLibrary from './KnowledgeCardLibrary';
import {
  loadPracticeAgentContext,
  loadTrainingWorkspaceModules,
  isTrainingTaskResultApproved,
} from '../pageDataLoaders.js';
import { practiceContextFromIntent } from './exam-atlas/examAtlasPageContext';

const fallbackPracticeModule = {
  key: 'question_training',
  label: '题目训练',
  description: '集中完成练习批改、案例训练和错题变式。',
  enabled: true,
  badge: '可用',
  recommended: true,
};

const workshopModuleKey = (value) => {
  if (['practice_grading', 'case_training', 'mistake_variation', 'question_training'].includes(value)) return 'question_training';
  if (['paper_generation', 'paper_workspace'].includes(value)) return 'paper_workspace';
  if (['knowledge_card_generation', 'knowledge_cards'].includes(value)) return 'knowledge_cards';
  return value || 'question_training';
};

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
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">
              {displayValue(grading.question_explanation)}
            </p>
            <p className="mt-2 text-xs text-slate-500">
              解析来源：{grading.explanation_source === 'generated_on_first_attempt'
                ? '首次作答自动生成并保存'
                : '题目解析库'}
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
  const [modules, setModules] = useState([]);
  const [activeTaskType, setActiveTaskType] = useState(() => workshopModuleKey(navigationContext.taskType));
  const [taskResult, setTaskResult] = useState(null);
  const [modulesLoading, setModulesLoading] = useState(true);
  const [moduleError, setModuleError] = useState('');
  const [contextBrief, setContextBrief] = useState(null);
  const [mobilePage, setMobilePage] = useState('task');
  const [practiceScope, setPracticeScope] = useState('public');
  const [statusMessage, setStatusMessage] = useState('正在加载训练模块。');
  const mountedRef = useRef(true);

  const visibleModules = useMemo(
    () => (modules.length > 0 ? modules : [fallbackPracticeModule]),
    [modules],
  );
  const activeModule = useMemo(
    () => visibleModules.find((module) => module.key === activeTaskType) || fallbackPracticeModule,
    [activeTaskType, visibleModules],
  );
  const activeTaskReady = !modulesLoading && activeModule.key === activeTaskType && activeModule.enabled;
  const requestedTaskType = workshopModuleKey(navigationContext.taskType || '');

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

  useEffect(() => {
    mountedRef.current = true;
    let cancelled = false;
    const loadWorkspace = async () => {
      const [moduleResult, contextResult] = await Promise.all([
        loadTrainingWorkspaceModules({ fetcher: fetchJsonWithAuthFallback }),
        loadPracticeAgentContext({ fetcher: fetchJsonWithAuthFallback }),
      ]);
      if (cancelled || !mountedRef.current) return;
      const loadedModules = moduleResult.workspace.modules;
      const requestedModule = requestedTaskType && loadedModules.find((module) => module.key === requestedTaskType);
      const fallbackModule = loadedModules.find((module) => module.enabled) || fallbackPracticeModule;
      const requestedModuleUnavailable = requestedTaskType && !requestedModule?.enabled;

      if (requestedModuleUnavailable) {
        setActiveTaskType(fallbackModule.key);
        setTaskResult(null);
      }
      setModules(moduleResult.workspace.modules);
      setModuleError(moduleResult.error);
      setModulesLoading(false);
      setContextBrief(contextResult.contextBrief);
      setStatusMessage(requestedModuleUnavailable
        ? '请求的训练模块暂未开放，已切换到可用训练模块。'
        : moduleResult.error ? '训练模块加载失败，已保留题目训练入口。' : '训练模块加载完成。');
    };

    loadWorkspace();
    return () => {
      cancelled = true;
      mountedRef.current = false;
    };
  }, [requestedTaskType]);

  const selectModule = (taskType) => {
    const targetModule = visibleModules.find((module) => module.key === taskType);
    if (!targetModule?.enabled) return;
    setActiveTaskType(taskType);
    setTaskResult(null);
  };

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
    setStatusMessage(grading.is_correct ? '练习已完成。' : '练习已完成并记录错题。');
  };

  const handleNextAction = (action) => {
    const targetModule = visibleModules.find((module) => module.key === workshopModuleKey(action?.task_type));
    if (!targetModule?.enabled) return;
    selectModule(targetModule.key);
  };

  const taskResultApproved = isTrainingTaskResultApproved(taskResult);

  return (
    <div className="practice-page space-y-5 text-slate-800">
      <header className="border-b border-slate-200 pb-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-sm font-medium text-emerald-800">
              <ClipboardCheck size={17} aria-hidden="true" />
              循证训练台
            </div>
            <h2 className="mt-2 text-2xl font-bold text-slate-950">训练工坊</h2>
          </div>
          <div className="flex max-w-xl flex-col items-end gap-3">
            <p className="break-words text-sm leading-6 text-slate-600">
              当前目标：{contextBrief?.goal || (modulesLoading ? '正在加载学习上下文…' : '暂无全局学习目标，仍可直接开始训练。')}
            </p>
          </div>
        </div>
      </header>

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

      <div
        className="sr-only"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {statusMessage}
      </div>

      {moduleError && (
        <div role="alert" className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800">
          {moduleError}。已保留题目训练入口。
        </div>
      )}

      <div className="practice-workspace-grid grid gap-5 xl:grid-cols-[220px_minmax(0,1fr)]">
        <nav data-mobile-active={String(mobilePage === 'task')} aria-busy={modulesLoading} aria-label="训练模块" className="practice-module-nav rounded-[24px] border border-slate-200 bg-white p-3 shadow-sm shadow-slate-200/50">
          <div className="px-2 py-2 text-sm font-semibold text-slate-900">训练模块</div>
          <div className="space-y-1">
            {modulesLoading ? [0, 1, 2, 3, 4, 5].map((item) => <div key={item} className="h-14 animate-pulse rounded-xl bg-slate-100" />) : visibleModules.map((module) => {
              const selected = module.key === activeTaskType;
              const moduleButtonClass = selected
                ? 'border-emerald-200 bg-emerald-50 text-emerald-950'
                : 'border-transparent text-slate-700 hover:border-slate-200 hover:bg-slate-50';
              return (
                <button
                  key={module.key}
                  type="button"
                  onClick={() => selectModule(module.key)}
                  disabled={!module.enabled}
                  aria-current={selected ? 'page' : undefined}
                  className={`w-full break-words rounded-xl border px-3 py-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2 ${moduleButtonClass} disabled:cursor-not-allowed disabled:opacity-65 disabled:hover:border-transparent disabled:hover:bg-transparent`}
                >
                  <span className="flex items-center justify-between gap-2 text-sm font-semibold">
                    <span>{module.label}</span>
                    <span className={`text-xs font-medium ${selected ? 'text-emerald-800' : module.enabled ? 'text-slate-500' : 'text-slate-700'}`}>{module.enabled ? module.badge : '未开放'}</span>
                  </span>
                  <span className={`mt-1 block text-xs leading-5 ${selected ? 'text-emerald-800' : 'text-slate-500'}`}>{module.description}</span>
                </button>
              );
            })}
          </div>
        </nav>

        <div className="min-w-0 space-y-5">
          <section data-mobile-active={String(mobilePage === 'task')} className="practice-task-panel rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/50">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold text-slate-950">{activeModule.label}</h2>
                <p className="mt-1 break-words text-sm leading-6 text-slate-600">{activeModule.description}</p>
              </div>
              {activeModule.recommended && <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-800">推荐</span>}
            </div>

            {!activeTaskReady ? (
              <div className="mt-5 rounded-xl bg-slate-50 px-4 py-6 text-sm text-slate-600" role="status">
                正在准备可用训练模块。
              </div>
            ) : activeTaskType === 'paper_workspace' ? (
              <PaperGenerationPanel enabled={activeModule.enabled} paperId={navigationContext.paperId || navigationContext.paper_id || ''} />
            ) : activeTaskType === 'knowledge_cards' ? (
              <KnowledgeCardLibrary cardId={navigationContext.cardId || navigationContext.card_id || ''} kpId={navigationContext.kpId || navigationContext.kp_id || ''} />
            ) : activeTaskType === 'question_training' ? (
              <QuestionTrainingPanel
                enabled={activeModule.enabled}
                selectedKnowledgePoint={selectedKnowledgePoint}
                practiceScope={practiceScope}
                onPracticeScopeChange={setPracticeScope}
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
            {taskResultApproved && Array.isArray(taskResult?.next_actions) && taskResult.next_actions.length > 0 && (
              <div className="mt-5 border-t border-slate-200 pt-4">
                <h3 className="text-sm font-semibold text-slate-900">下一步</h3>
                <div className="mt-3 flex flex-wrap gap-2">
                  {taskResult.next_actions.slice(0, 6).map((action, index) => {
                    const targetModule = visibleModules.find((module) => module.key === workshopModuleKey(action?.task_type));
                    const enabled = Boolean(targetModule?.enabled);
                    return (
                      <button
                        key={`${action?.task_type || 'action'}-${index}`}
                        type="button"
                        disabled={!enabled}
                        onClick={() => handleNextAction(action)}
                        title={enabled ? `切换至${targetModule.label}` : '该后续模块暂未开放'}
                        className="break-words rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-950"
                      >
                        {action?.label || '后续任务'}{enabled ? '' : '（未开放）'}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </section>
        </div>

      </div>
    </div>
  );
}
