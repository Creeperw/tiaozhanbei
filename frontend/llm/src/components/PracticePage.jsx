import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ClipboardCheck,
  FileText,
  Loader2,
} from 'lucide-react';
import { formatSystemDataMetrics } from '../systemDataDisplay.js';
import { createLearningFocusTracker } from '../learningFocusTracker.js';
import { fetchJsonWithAuthFallback } from '../utils/api';
import CaseTrainingPanel from './CaseTrainingPanel';
import MistakeVariationPanel from './MistakeVariationPanel';
import PaperGenerationPanel from './PaperGenerationPanel';
import {
  loadPracticeAgentContext,
  loadTrainingWorkspaceModules,
  isTrainingTaskResultApproved,
  submitTrainingWorkspaceTask,
} from '../pageDataLoaders.js';
import AtlasPracticePanel from './exam-atlas/AtlasPracticePanel';
import { practiceContextFromIntent } from './exam-atlas/examAtlasPageContext';

const demoQuestion = {
  question_id: 'demo-sijunzi-001',
  question_type: 'short_answer',
  stem: '四君子汤主治的核心证型是什么？请简要说明。',
  standard_answer: '脾胃气虚证',
  rubric: '答出脾胃气虚证并能说明气虚、纳差、乏力等证据为满分。',
  knowledge_points: ['四君子汤', '脾胃气虚证'],
  difficulty: 2,
};

const fallbackPracticeModule = {
  key: 'practice_grading',
  label: '练习批改',
  description: '提交练习并获得批改与复盘建议。',
  enabled: true,
  badge: '可用',
  recommended: true,
};

const inspectorTabs = [
  { key: 'evidence', label: '证据' },
  { key: 'audit', label: '审核' },
  { key: 'trace', label: '轨迹' },
];

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

function EvidencePanel({ evidencePack }) {
  const evidence = isRecord(evidencePack) ? evidencePack : {};
  const items = Array.isArray(evidence.items) ? evidence.items.slice(0, 8) : [];
  if (items.length === 0 && !evidence.pack_id) return <EmptyState>暂无证据包；提交任务后会显示来源与知识点对齐信息。</EmptyState>;

  return (
    <div className="space-y-4">
      <div className="text-sm leading-6 text-slate-700">
        <p>来源范围：{displayValue(evidence.source_scope)}</p>
        <p>已对齐知识点：{displayValue(evidence.resolved_kp_ids ?? evidence.kp_ids)}</p>
      </div>
      {items.length > 0 ? items.map((item, index) => (
        <div key={item?.source_id || index} className="border-t border-slate-200 pt-3 text-sm leading-6 text-slate-700">
          <p className="font-medium text-slate-900">{displayValue(item?.source_scope || item?.source_id || `证据 ${index + 1}`)}</p>
          <p className="mt-1">{displayValue(item?.summary)}</p>
        </div>
      )) : <EmptyState>证据包已创建，暂未返回条目。</EmptyState>}
    </div>
  );
}

function AuditPanel({ audit }) {
  const auditData = isRecord(audit) ? audit : {};
  if (Object.keys(auditData).length === 0) return <EmptyState>暂无审核信息。</EmptyState>;
  return (
    <div className="space-y-3 text-sm leading-6 text-slate-700">
      <p><span className="font-medium text-slate-900">决策：</span>{displayValue(auditData.decision)}</p>
      <p><span className="font-medium text-slate-900">风险：</span>{displayValue(auditData.safety_risk)}</p>
      <p><span className="font-medium text-slate-900">说明：</span>{displayValue(auditData.reason)}</p>
    </div>
  );
}

function TracePanel({ trace }) {
  const entries = Array.isArray(trace) ? trace.slice(0, 12) : [];
  if (entries.length === 0) return <EmptyState>暂无执行轨迹；提交后会按步骤展示处理状态。</EmptyState>;
  return (
    <ol className="space-y-3">
      {entries.map((entry, index) => (
        <li key={entry?.step_id || index} className="border-l-2 border-cyan-200 pl-3 text-sm leading-6 text-slate-700">
          <p className="font-medium text-slate-900">{displayValue(entry?.status)} · {displayValue(entry?.agent)}</p>
          <p>动作：{displayValue(entry?.action)}</p>
          <p className="text-slate-500">{displayValue(entry?.summary)}</p>
        </li>
      ))}
    </ol>
  );
}

export default function PracticePage({ navigationContext = {} }) {
  const selectedKnowledgePoint = practiceContextFromIntent(navigationContext);
  const [modules, setModules] = useState([]);
  const [activeTaskType, setActiveTaskType] = useState(() => navigationContext.taskType || 'practice_grading');
  const [answer, setAnswer] = useState('中焦虚寒证');
  const [topic, setTopic] = useState('围绕四君子汤与脾胃气虚证完成 15 分钟复习');
  const [taskResult, setTaskResult] = useState(null);
  const [systemData, setSystemData] = useState({});
  const [loading, setLoading] = useState(false);
  const [modulesLoading, setModulesLoading] = useState(true);
  const [error, setError] = useState('');
  const [moduleError, setModuleError] = useState('');
  const [contextBrief, setContextBrief] = useState(null);
  const [recentTrace, setRecentTrace] = useState([]);
  const [activeInspectorTab, setActiveInspectorTab] = useState('evidence');
  const [mobilePage, setMobilePage] = useState('task');
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [practiceScope, setPracticeScope] = useState('public');
  const [statusMessage, setStatusMessage] = useState('正在加载训练模块。');
  const mountedRef = useRef(true);
  const requestSequenceRef = useRef(0);
  const submittingRef = useRef(false);
  const tabRefs = useRef([]);

  const visibleModules = useMemo(
    () => (modules.length > 0 ? modules : [fallbackPracticeModule]),
    [modules],
  );
  const activeModule = useMemo(
    () => visibleModules.find((module) => module.key === activeTaskType) || fallbackPracticeModule,
    [activeTaskType, visibleModules],
  );
  const activeTaskReady = !modulesLoading && activeModule.key === activeTaskType && activeModule.enabled;
  const generationTask = activeTaskType === 'handout_generation' || activeTaskType === 'knowledge_card_generation';
  const requestedTaskType = navigationContext.taskType || '';

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
    if (modulesLoading || !requestedTaskType) return;

    const requestedModule = visibleModules.find((module) => module.key === requestedTaskType);
    const fallbackModule = visibleModules.find((module) => module.enabled) || fallbackPracticeModule;
    const nextTaskType = requestedModule?.enabled ? requestedModule.key : fallbackModule.key;

    setActiveTaskType(nextTaskType);
    setTaskResult(null);
    setError('');
    if (!requestedModule?.enabled) {
      setStatusMessage('请求的训练模块暂未开放，已切换到可用训练模块。');
    }
  }, [modulesLoading, requestedTaskType, visibleModules]);

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
        setError('');
      }
      setModules(moduleResult.workspace.modules);
      setModuleError(moduleResult.error);
      setModulesLoading(false);
      setContextBrief(contextResult.contextBrief);
      setRecentTrace(contextResult.recentTrace);
      setStatusMessage(requestedModuleUnavailable
        ? '请求的训练模块暂未开放，已切换到可用训练模块。'
        : moduleResult.error ? '训练模块加载失败，已保留练习批改入口。' : '训练模块加载完成。');
    };

    loadWorkspace();
    return () => {
      cancelled = true;
      mountedRef.current = false;
      requestSequenceRef.current += 1;
    };
  }, []);

  const selectModule = (taskType) => {
    if (loading || submittingRef.current) return;
    const targetModule = visibleModules.find((module) => module.key === taskType);
    if (!targetModule?.enabled) return;
    setActiveTaskType(taskType);
    setTaskResult(null);
    setError('');
  };

  const submitTask = async () => {
    if (loading || submittingRef.current || !activeModule.enabled) return;
    const normalizedTopic = topic.trim();
    if (generationTask && !normalizedTopic) {
      setError('请填写生成主题后再提交。');
      return;
    }

    const submittedTaskType = activeTaskType;
    const requestSequence = requestSequenceRef.current + 1;
    requestSequenceRef.current = requestSequence;
    submittingRef.current = true;
    const task = submittedTaskType === 'practice_grading'
      ? {
        task_type: 'practice_grading',
        title: '四君子汤练习批改',
        query: demoQuestion.stem,
        inputs: { ...demoQuestion, student_answer: answer },
        options: { save_activity: true, need_audit: true },
      }
      : {
        task_type: submittedTaskType,
        title: submittedTaskType === 'handout_generation' ? '个性化讲义' : '知识卡片',
        query: normalizedTopic,
        inputs: {
          knowledge_points: demoQuestion.knowledge_points,
          difficulty: 2,
          duration_minutes: 15,
        },
        options: { save_activity: true, need_audit: true },
      };

    setLoading(true);
    setError('');
    setTaskResult(null);
    setStatusMessage('正在提交训练任务。');
    try {
      const result = await submitTrainingWorkspaceTask({ fetcher: fetchJsonWithAuthFallback, task });
      if (!mountedRef.current
        || requestSequence !== requestSequenceRef.current
        || submittedTaskType !== activeTaskType) return;
      if (result.error) {
        setError(result.error);
        setStatusMessage('训练任务提交失败。');
        return;
      }
      setTaskResult(result.taskResult);
      setMobilePage('result');
      if (result.taskResult.system_data) {
        setSystemData(result.taskResult.system_data);
      }
      if (!isTrainingTaskResultApproved(result.taskResult)) {
        setActiveInspectorTab('audit');
        setStatusMessage('训练任务未完成或审核未通过。');
        return;
      }
      setActiveInspectorTab('evidence');
      setStatusMessage('训练任务已完成。');
    } catch (submissionError) {
      if (mountedRef.current
        && requestSequence === requestSequenceRef.current
        && submittedTaskType === activeTaskType) {
        setError(submissionError.message || '训练任务请求失败');
        setStatusMessage('训练任务提交失败。');
      }
    } finally {
      if (mountedRef.current && requestSequence === requestSequenceRef.current) {
        submittingRef.current = false;
        setLoading(false);
      }
    }
  };

  const handleNextAction = (action) => {
    if (loading || submittingRef.current) return;
    const targetModule = visibleModules.find((module) => module.key === action?.task_type);
    if (!targetModule?.enabled) return;
    selectModule(targetModule.key);
  };

  const handleInspectorTabKeyDown = (event) => {
    const currentIndex = inspectorTabs.findIndex((tab) => tab.key === activeInspectorTab);
    let nextIndex = currentIndex;
    if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % inspectorTabs.length;
    if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + inspectorTabs.length) % inspectorTabs.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = inspectorTabs.length - 1;
    if (nextIndex === currentIndex) return;
    event.preventDefault();
    const nextTab = inspectorTabs[nextIndex];
    setActiveInspectorTab(nextTab.key);
    tabRefs.current[nextIndex]?.focus();
  };

  const inspectorContent = taskResult || { trace: recentTrace };
  const taskResultApproved = isTrainingTaskResultApproved(taskResult);

  return (
    <div className="space-y-5 text-slate-800">
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
            <button type="button" className="practice-inspector-toggle button button--secondary" onClick={() => setInspectorOpen(true)}>打开证据检查器</button>
          </div>
        </div>
      </header>

      <div className="practice-mobile-tabs" role="tablist" aria-label="移动端训练视图">
        {[
          ['task', '任务'],
          ['result', '结果'],
          ['evidence', '证据'],
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
          {moduleError}。已保留练习批改入口。
        </div>
      )}

      <div className="practice-workspace-grid grid gap-5 xl:grid-cols-[220px_minmax(0,1fr)_300px]">
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
                  disabled={loading || !module.enabled}
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
            ) : activeTaskType === 'case_training' ? (
              <CaseTrainingPanel enabled={activeModule.enabled} />
            ) : activeTaskType === 'mistake_variation' ? (
              <MistakeVariationPanel enabled={activeModule.enabled} />
            ) : activeTaskType === 'paper_generation' ? (
              <PaperGenerationPanel enabled={activeModule.enabled} />
            ) : activeTaskType === 'practice_grading' ? (
              selectedKnowledgePoint ? (
                <div className="mt-5">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <span className="text-xs font-semibold text-slate-600">题目范围</span>
                    <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-1" role="group" aria-label="题目范围">
                      {[
                        ['public', '正式题库'],
                        ['user', '我的题目'],
                        ['all', '全部题目'],
                      ].map(([value, label]) => (
                        <button
                          key={value}
                          type="button"
                          aria-pressed={practiceScope === value}
                          className={`min-h-10 rounded-md px-3 text-xs font-semibold transition ${practiceScope === value ? 'bg-white text-emerald-800 shadow-sm' : 'text-slate-600 hover:text-slate-900'}`}
                          onClick={() => setPracticeScope(value)}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <AtlasPracticePanel knowledgePoint={selectedKnowledgePoint} scope={practiceScope} />
                </div>
              ) : (
                <div className="mt-5">
                  <p className="text-sm font-medium leading-6 text-slate-900">{demoQuestion.stem}</p>
                  <p className="mt-2 text-sm text-slate-500">知识点：{demoQuestion.knowledge_points.join('、')}</p>
                  <label htmlFor="practice-answer" className="mt-5 block text-sm font-medium text-slate-700">你的答案</label>
                  <textarea
                    id="practice-answer"
                    value={answer}
                    onChange={(event) => setAnswer(event.target.value)}
                    className="mt-2 min-h-32 w-full rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm leading-6 text-slate-800 outline-none transition focus:border-emerald-400 focus:bg-white focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
                    disabled={loading}
                  />
                </div>
              )
            ) : generationTask ? (
              <div className="mt-5">
                <label htmlFor="training-topic" className="block text-sm font-medium text-slate-700">训练主题</label>
                <textarea
                  id="training-topic"
                  value={topic}
                  onChange={(event) => setTopic(event.target.value)}
                  placeholder="例如：围绕四君子汤的辨证要点进行复习"
                  className="mt-2 min-h-32 w-full rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm leading-6 text-slate-800 outline-none transition focus:border-emerald-400 focus:bg-white focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
                  disabled={loading}
                />
                <p className="mt-2 text-xs leading-5 text-slate-500">将按 15 分钟、难度 2 生成；当前示例知识点仅作为兼容上下文，不代表正式知识点 ID。</p>
              </div>
            ) : (
              <div className="mt-5 rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-5 text-sm leading-6 text-slate-600">
                此模块正在准备中，暂不支持提交任务。
              </div>
            )}

            {activeTaskType !== 'case_training' && activeTaskType !== 'mistake_variation' && activeTaskType !== 'paper_generation' && !(selectedKnowledgePoint && activeTaskType === 'practice_grading') && <div className="mt-5 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={submitTask}
                disabled={loading || !activeModule.enabled}
                className="inline-flex items-center gap-2 rounded-2xl bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-emerald-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-55"
              >
                {loading && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
                {loading ? '正在处理…' : activeModule.enabled ? '提交训练任务' : '模块暂未开放'}
              </button>
              {!activeModule.enabled && <span className="text-sm text-slate-500">当前模块不可提交。</span>}
            </div>}
            {error && <div role="alert" className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm leading-6 text-rose-700">{error}</div>}
          </section>

          <section
            data-testid="practice-result-panel"
            data-mobile-active={String(mobilePage === 'result')}
            aria-busy={loading}
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
            {taskResultApproved && formatSystemDataMetrics(systemData).length > 0 && (
              <div className="mt-5 grid gap-3 border-t border-emerald-100 pt-4 sm:grid-cols-2">
                {formatSystemDataMetrics(systemData).map((metric) => (
                  <div key={metric.key} className="border-l-2 border-emerald-300 pl-3">
                    <div className="text-xs font-medium text-emerald-800">{metric.label}</div>
                    <div className="mt-1 text-sm text-emerald-950">{metric.value}</div>
                  </div>
                ))}
              </div>
            )}
            {taskResultApproved && Array.isArray(taskResult?.next_actions) && taskResult.next_actions.length > 0 && (
              <div className="mt-5 border-t border-slate-200 pt-4">
                <h3 className="text-sm font-semibold text-slate-900">下一步</h3>
                <div className="mt-3 flex flex-wrap gap-2">
                  {taskResult.next_actions.slice(0, 6).map((action, index) => {
                    const targetModule = visibleModules.find((module) => module.key === action?.task_type);
                    const enabled = Boolean(targetModule?.enabled);
                    return (
                      <button
                        key={`${action?.task_type || 'action'}-${index}`}
                        type="button"
                        disabled={loading || !enabled}
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

        <button
          type="button"
          aria-label="关闭证据检查器"
          aria-hidden={inspectorOpen ? undefined : true}
          tabIndex={inspectorOpen ? 0 : -1}
          data-open={String(inspectorOpen)}
          className="practice-inspector-backdrop"
          onClick={() => setInspectorOpen(false)}
        />
        <aside data-testid="practice-inspector" data-open={String(inspectorOpen)} data-mobile-active={String(mobilePage === 'evidence')} className="practice-inspector rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm shadow-slate-200/50">
          <div className="practice-inspector__heading">
            <strong>证据检查器</strong>
            <button type="button" className="practice-inspector-close icon-button" aria-label="收起证据检查器" onClick={() => setInspectorOpen(false)}>×</button>
          </div>
          <div role="tablist" aria-label="训练检查器" className="flex border-b border-slate-200">
            {inspectorTabs.map((tab, index) => (
              <button
                key={tab.key}
                ref={(element) => { tabRefs.current[index] = element; }}
                id={`inspector-tab-${tab.key}`}
                type="button"
                role="tab"
                tabIndex={activeInspectorTab === tab.key ? 0 : -1}
                aria-selected={activeInspectorTab === tab.key}
                aria-controls={`inspector-panel-${tab.key}`}
                onClick={() => setActiveInspectorTab(tab.key)}
                onKeyDown={handleInspectorTabKeyDown}
                className={`flex-1 border-b-2 px-2 py-2 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-inset ${activeInspectorTab === tab.key ? 'border-emerald-600 text-emerald-800' : 'border-transparent text-slate-500 hover:text-slate-800'}`}
              >
                {tab.label}
              </button>
            ))}
          </div>
      <div id={`inspector-panel-${activeInspectorTab}`} role="tabpanel" aria-labelledby={`inspector-tab-${activeInspectorTab}`} className="break-words pt-4">
            {activeInspectorTab === 'evidence' && <EvidencePanel evidencePack={inspectorContent.evidence_pack} />}
            {activeInspectorTab === 'audit' && <AuditPanel audit={inspectorContent.audit} />}
            {activeInspectorTab === 'trace' && <TracePanel trace={inspectorContent.trace} />}
          </div>
        </aside>
      </div>
    </div>
  );
}
