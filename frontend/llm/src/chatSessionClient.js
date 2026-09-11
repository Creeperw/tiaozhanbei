import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from './utils/api';
import { removeTraceEventsFromContent } from './chatProtocol';
import { notifyAssistantWorkflowCompleted } from './assistantWorkflowEvents';
import {
  createWorkflowRunId,
  getWorkflowRun,
  getWorkflowRunWithStartupGrace,
  getResumableWorkflowRunId,
  streamWorkflowTurn,
} from './workflowChatClient';
export { resolveAssistantSessionId } from './assistantDockModel';

const PENDING_RUNS_STORAGE_KEY = 'assistantPendingWorkflowRuns';

// Progress-only surfaces must not render model output, reasoning or error
// payloads. Keep this allowlist independent of the full chat trace renderer.
const PROGRESS_MESSAGES = new Map(Object.entries({
  run_started: '正在理解你的需求…',
  run_resumed: '已收到补充信息，正在继续处理…',
  graph_resumed: '已收到补充信息，正在继续处理…',
  graph_compiled: '已确定处理步骤，正在执行…',
  step_started: '正在处理当前环节…',
  step_completed: '当前环节已处理完成…',
  model_input: '正在结合任务信息准备处理…',
  model_transport: '正在处理，请稍候…',
  model_output: '本轮处理已完成，正在整理结果…',
  system_output: '已整理阶段结果，正在继续处理…',
  handoff_prepared: '正在整理下一环节所需信息…',
  handoff_consumed: '已接收上一环节结果，正在继续处理…',
  step_retrying: '当前环节正在重试，请稍候…',
  audit_revision_started: '正在根据审核意见修订…',
  audit_revision_completed: '修订复核已完成…',
  repair_planned: '正在安排需要修订的环节…',
  repair_step_started: '正在修订相关内容…',
  repair_step_completed: '相关内容已修订，正在继续检查…',
  repair_reaudit_started: '正在复核修订后的内容…',
  repair_completed: '修订审核已完成…',
  repair_stopped: '修订已停止，正在整理处理结果…',
  web_search_status: '正在整理资料检索进展…',
  run_interrupted: '需要你补充信息，正在整理问题…',
  graph_interrupted: '需要你补充信息，正在整理问题…',
  run_waiting_human_review: '正在等待人工复核…',
  step_failed: '当前环节未完成，正在整理处理结果…',
  run_failed: '本次处理未完成，正在整理说明…',
  run_cancelled: '本次处理已停止。',
  run_completed: '本阶段处理已完成。',
}));

function readPendingRuns() {
  try {
    const value = JSON.parse(localStorage.getItem(PENDING_RUNS_STORAGE_KEY) || '{}');
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
}

function rememberPendingRun(sessionId, runId) {
  const runs = readPendingRuns();
  if (runId) runs[sessionId] = runId;
  else delete runs[sessionId];
  localStorage.setItem(PENDING_RUNS_STORAGE_KEY, JSON.stringify(runs));
}

async function jsonRequest(path, options) {
  const response = await fetchWithAuth(`${MAIN_API_BASE}${path}`, options);
  const payload = await readJsonResponse(response, {});
  if (!response.ok) throw new Error(payload.detail || '智能助教暂时不可用');
  return payload;
}

export function compactAssistantContent(content = '') {
  let text = String(content || '');
  const rollbacks = [...text.matchAll(/<<ROLLBACK:.*?>>/gs)];
  const lastRollback = rollbacks.at(-1);
  if (lastRollback) text = text.slice((lastRollback.index || 0) + lastRollback[0].length);
  text = text.replace(/<think>[\s\S]*?<\/think>/g, '');
  text = text.replace(/<think>[\s\S]*$/g, '');
  text = removeTraceEventsFromContent(text);
  text = text.replace(/<<(?:STATUS|REFS|VIDEOS|PLAN|EXEC):[\s\S]*?>>/g, '');
  return text.trim();
}

export function listAssistantSessions() {
  return jsonRequest('/conversations');
}

export function createAssistantSession(title = '新对话', { source = 'user' } = {}) {
  return jsonRequest('/conversations', {
    method: 'POST',
    body: JSON.stringify({ title, source }),
  });
}

export function loadAssistantMessages(sessionId) {
  return jsonRequest(`/conversations/${encodeURIComponent(sessionId)}/messages`);
}

export async function getAssistantPendingRun(sessionId) {
  const runId = readPendingRuns()[sessionId] || null;
  if (!runId) return null;
  // Streaming responses may become readable a few milliseconds before the
  // server-owned run record is queryable.  Treat the initial 404 as a startup
  // race instead of immediately discarding the checkpoint needed for resume.
  const run = await getWorkflowRunWithStartupGrace(runId);
  notifyAssistantWorkflowCompleted(sessionId, runId, run?.status);
  if (!run || ['completed', 'failed', 'waiting_human_review', 'cancelled'].includes(run.status)) {
    rememberPendingRun(sessionId, null);
  }
  return run ? { ...run, runId } : null;
}

export async function streamAssistantMessageOutcome(sessionId, content, {
  onUpdate,
  onProgress,
  signal,
  currentPage = null,
  conversationSurface = null,
} = {}) {
  const storedRunId = readPendingRuns()[sessionId] || null;
  let pending = null;
  if (storedRunId) {
    const run = await getWorkflowRun(storedRunId);
    if (['running', 'cancellation_requested'].includes(run?.status)) {
      const error = new Error('当前会话已有任务在后台运行，请等待完成后再发送。');
      error.code = 'workflow_running';
      throw error;
    }
    pending = run?.status === 'interrupted'
      ? await getResumableWorkflowRunId(storedRunId)
      : null;
    if (!pending) {
      rememberPendingRun(sessionId, null);
    }
  }
  const runId = pending || createWorkflowRunId();
  rememberPendingRun(sessionId, runId);
  const progress = [];
  let lastProgress = '';
  let outcome;
  try {
    outcome = await streamWorkflowTurn({
    conversationId: sessionId,
    runId,
    answer: content,
    currentPage,
    // 系统自有枚举：产品内置向导（学习路径规划）自带进度对话框，不是用户的
    // 聊天记录，后端据此把它排除在 AI 助手历史之外。用户自由文本不参与判定。
    conversationSurface,
    signal,
    resume: Boolean(pending),
    onEvent: (event, traceEvent) => {
      const status = PROGRESS_MESSAGES.get(event?.event);
      if (status && status !== lastProgress) {
        lastProgress = status;
        onProgress?.(status);
      }
      const text = String(traceEvent?.text || '').trim();
      if (!text || progress.at(-1) === text) return;
      progress.push(text);
      onUpdate?.(text);
    },
    });
  } catch (reason) {
    // A disconnected response is not evidence that the server stopped.
    const error = reason instanceof Error ? reason : new Error(String(reason));
    error.sessionId = sessionId;
    error.runId = runId;
    throw error;
  }
  // A background status poll may have observed the short startup 404 and
  // cleared the local entry while this stream was still active.  Reassert the
  // authoritative interrupted run id at the terminal boundary so the user's
  // next answer resumes the same checkpoint rather than creating a new run.
  rememberPendingRun(sessionId, outcome.status === 'interrupted' ? runId : null);
  notifyAssistantWorkflowCompleted(sessionId, runId, outcome.status);
  const visible = compactAssistantContent(outcome.message);
  onUpdate?.(visible);
  return { ...outcome, visible, runId };
}

export async function streamAssistantMessage(sessionId, content, options = {}) {
  const outcome = await streamAssistantMessageOutcome(sessionId, content, options);
  return outcome.visible;
}
