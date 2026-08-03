import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from './utils/api';
import {
  createWorkflowRunId,
  getWorkflowRun,
  getResumableWorkflowRunId,
  streamWorkflowTurn,
} from './workflowChatClient';
export { resolveAssistantSessionId } from './assistantDockModel';

const PENDING_RUNS_STORAGE_KEY = 'assistantPendingWorkflowRuns';

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
  text = text.replace(/<<(?:STATUS|EV|REFS|VIDEOS|PLAN|EXEC):[\s\S]*?>>/g, '');
  return text.trim();
}

export function listAssistantSessions() {
  return jsonRequest('/conversations');
}

export function createAssistantSession(title = '新对话') {
  return jsonRequest('/conversations', {
    method: 'POST',
    body: JSON.stringify({ title }),
  });
}

export function loadAssistantMessages(sessionId) {
  return jsonRequest(`/conversations/${encodeURIComponent(sessionId)}/messages`);
}

export async function getAssistantPendingRun(sessionId) {
  const runId = readPendingRuns()[sessionId] || null;
  if (!runId) return null;
  const run = await getWorkflowRun(runId);
  if (!run || ['completed', 'failed', 'waiting_human_review'].includes(run.status)) {
    rememberPendingRun(sessionId, null);
  }
  return run ? { ...run, runId } : null;
}

export async function streamAssistantMessageOutcome(sessionId, content, {
  onUpdate,
  signal,
  currentPage = null,
} = {}) {
  const storedRunId = readPendingRuns()[sessionId] || null;
  let pending = null;
  if (storedRunId) {
    const run = await getWorkflowRun(storedRunId);
    if (run?.status === 'running') {
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
  const outcome = await streamWorkflowTurn({
    conversationId: sessionId,
    runId,
    answer: content,
    currentPage,
    signal,
    resume: Boolean(pending),
    onEvent: (_event, traceEvent) => {
      const text = String(traceEvent?.text || '').trim();
      if (!text || progress.at(-1) === text) return;
      progress.push(text);
      onUpdate?.(text);
    },
  });
  if (outcome.status !== 'interrupted') rememberPendingRun(sessionId, null);
  const visible = compactAssistantContent(outcome.message);
  onUpdate?.(visible);
  return { ...outcome, visible, runId };
}

export async function streamAssistantMessage(sessionId, content, options = {}) {
  const outcome = await streamAssistantMessageOutcome(sessionId, content, options);
  return outcome.visible;
}
