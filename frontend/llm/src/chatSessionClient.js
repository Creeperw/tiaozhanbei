import { API_BASE, fetchWithAuth, readJsonResponse } from './utils/api';

async function jsonRequest(path, options) {
  const response = await fetchWithAuth(`${API_BASE}${path}`, options);
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

export function resolveAssistantSessionId(sessions, preferredId, savedId) {
  if (preferredId && sessions.some((session) => session.id === preferredId)) return preferredId;
  if (savedId && sessions.some((session) => session.id === savedId)) return savedId;
  return sessions[0]?.id || null;
}

export function listAssistantSessions() {
  return jsonRequest('/sessions');
}

export function createAssistantSession(title = '新对话') {
  return jsonRequest('/sessions', {
    method: 'POST',
    body: JSON.stringify({ title }),
  });
}

export function loadAssistantMessages(sessionId) {
  return jsonRequest(`/sessions/${encodeURIComponent(sessionId)}/messages`);
}

export async function streamAssistantMessage(sessionId, content, {
  onUpdate,
  signal,
  toolsEnabled = false,
} = {}) {
  const response = await fetchWithAuth(`${API_BASE}/chat/${encodeURIComponent(sessionId)}`, {
    method: 'POST',
    body: JSON.stringify({
      role: 'user',
      content,
      files: [],
      tools_enabled: toolsEnabled,
      web_search: toolsEnabled,
      rag_search: toolsEnabled,
    }),
    signal,
  });
  if (!response.ok) throw new Error('智能助教回复失败');
  if (!response.body?.getReader) {
    const raw = await response.text();
    const visible = compactAssistantContent(raw);
    onUpdate?.(visible);
    return visible;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let raw = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    raw += decoder.decode(value, { stream: true });
    onUpdate?.(compactAssistantContent(raw));
  }
  raw += decoder.decode();
  const visible = compactAssistantContent(raw);
  onUpdate?.(visible);
  return visible;
}
