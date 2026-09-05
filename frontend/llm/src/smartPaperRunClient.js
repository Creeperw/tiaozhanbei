import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from './utils/api';
import {
  cancelWorkflowRun,
  createWorkflowRunId,
  getWorkflowRun,
  streamWorkflowTurn,
} from './workflowChatClient';

const STORAGE_KEY = 'smartPaperPendingWorkflowRun';

function rememberRun(runId) {
  if (runId) localStorage.setItem(STORAGE_KEY, runId);
  else localStorage.removeItem(STORAGE_KEY);
}

function paperIdFromResult(result) {
  const action = (result?.ui_actions || []).find(item => item?.destination === 'workshop.paper');
  return action?.params?.paper_id || action?.params?.paperId || '';
}

export async function getPendingSmartPaperRun() {
  const runId = localStorage.getItem(STORAGE_KEY) || '';
  if (!runId) return null;
  const run = await getWorkflowRun(runId);
  if (!run) {
    rememberRun('');
    return null;
  }
  const paperId = paperIdFromResult(run.result);
  if (['completed', 'failed', 'human_review_rejected', 'cancelled'].includes(run.status)) {
    rememberRun('');
  }
  return { ...run, runId, paperId, reviewPreview: run.result?.preview || null };
}

export async function startSmartPaperRun({
  topic,
  distribution,
  answerMode = 'practice',
  durationMinutes = null,
  difficulty = null,
  paperKind = 'special',
  focusTopics = [],
  onEvent,
  signal,
}) {
  const existing = await getPendingSmartPaperRun();
  if (['running', 'cancellation_requested'].includes(existing?.status)) {
    const error = new Error('已有一份试卷正在后台生成，请等待完成。');
    error.code = 'smart_paper_running';
    throw error;
  }
  const typeLabels = {
    single_choice: '单选题', multiple_choice: '多选题', fill_blank: '填空题',
    short_answer: '简答题',
  };
  const activeDistribution = Object.fromEntries(
    Object.entries(distribution || {}).filter(([, count]) => Number.isInteger(count) && count > 0),
  );
  const questionCount = Object.values(activeDistribution).reduce((sum, count) => sum + count, 0);
  const typeRequirement = Object.entries(activeDistribution)
    .map(([type, count]) => `${typeLabels[type] || type}${count}题`).join('、');
  const difficultyRequirement = Number.isInteger(difficulty) ? `，全部题目要求难度${difficulty}星` : '';
  const runId = createWorkflowRunId();
  rememberRun(runId);
  try {
    const outcome = await streamWorkflowTurn({
      conversationId: runId,
      runId,
      answer: `请围绕“${topic.trim()}”生成一份${answerMode === 'test' ? `测试模式（${durationMinutes}分钟）` : '练习模式'}试卷，共${questionCount}题，其中${typeRequirement}${difficultyRequirement}。完成审核后发布到学习工坊，不要在对话中展开试卷正文。`,
      ...(answerMode === 'test' && Number.isFinite(durationMinutes)
        ? { availableMinutes: durationMinutes }
        : {}),
      currentPage: { path: '/practice/smart-paper', product_surface: 'smart_paper' },
      examConstraints: {
          question_count: questionCount,
          question_types: Object.keys(activeDistribution).map(type => typeLabels[type] || type),
          question_type_distribution: activeDistribution,
          answer_mode: answerMode,
          duration_minutes: answerMode === 'test' ? durationMinutes : null,
          difficulty: Number.isInteger(difficulty) ? difficulty : null,
          paper_kind: paperKind,
          topic: paperKind === 'special' ? topic.trim() : '',
          focus_topics: paperKind === 'adaptive'
            ? focusTopics.map(item => String(item || '').trim()).filter(Boolean).slice(0, 8)
            : [],
      },
      endpointOverride: `${MAIN_API_BASE}/workshop/smart-papers/stream`,
      onEvent,
      signal,
    });
    const paperId = paperIdFromResult(outcome.result);
    if (outcome.status === 'waiting_human_review') {
      return {
        ...outcome,
        runId,
        paperId: '',
        reviewPreview: outcome.result?.preview || null,
      };
    }
    if (outcome.status !== 'completed' || !paperId) {
      throw new Error('试卷生成完成，但未能发布到答题工作区。');
    }
    rememberRun('');
    return { ...outcome, runId, paperId };
  } catch (error) {
    if (error?.code === 'smart_paper_running' && error?.activeThreadId) {
      rememberRun(error.activeThreadId);
    }
    // A disconnected browser does not cancel the server workflow. Keep the ID
    // so a remount/refresh can recover its authoritative state.
    if (error?.name !== 'AbortError') {
      const run = await getWorkflowRun(runId).catch(() => null);
      if (run && ['completed', 'failed', 'human_review_rejected'].includes(run.status)) rememberRun('');
    }
    throw error;
  }
}

export async function cancelSmartPaperRun(runId) {
  const payload = await cancelWorkflowRun(runId);
  if (payload.status === 'cancelled') rememberRun('');
  return payload;
}
