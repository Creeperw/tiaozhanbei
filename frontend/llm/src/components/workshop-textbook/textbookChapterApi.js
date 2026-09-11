import { API_BASE, fetchWithAuth, readJsonResponse } from '../../utils/api';

function responseMessage(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') return detail.message || detail.code || fallback;
  return payload?.error || fallback;
}

export async function loadSectionLearningDetail(sectionId, { recommendationLimit = 1, signal } = {}) {
  const params = new URLSearchParams({ recommendation_limit: String(recommendationLimit) });
  const response = await fetchWithAuth(
    `${API_BASE}/knowledge/atlas/section/${encodeURIComponent(sectionId)}?${params}`,
    signal ? { signal } : {},
  );
  const payload = await readJsonResponse(response, {});
  if (!response.ok || payload?.ok === false) {
    throw new Error(responseMessage(payload, `小节学习内容加载失败 (${response.status || 'unknown'})`));
  }
  return payload;
}

export async function loadTextbookProgress(book, { signal } = {}) {
  const params = new URLSearchParams({ book: String(book || '') });
  const response = await fetchWithAuth(
    `${API_BASE}/learning-activity/textbook-progress?${params}`,
    signal ? { signal } : {},
  );
  const payload = await readJsonResponse(response, {});
  if (!response.ok || payload?.ok === false) {
    throw new Error(responseMessage(payload, `教材学习进度加载失败 (${response.status || 'unknown'})`));
  }
  return payload;
}
export async function loadSectionQuestions(kpIds, { signal } = {}) {
  if (!Array.isArray(kpIds) || kpIds.length === 0) return { items: [], total: 0 };
  const filtered = kpIds.filter(Boolean);
  if (!filtered.length) return { items: [], total: 0 };

  const params = new URLSearchParams({ kp_ids: filtered.join(','), limit: '200' });
  const response = await fetchWithAuth(
    `${API_BASE}/training/workspace/questions-by-kp-ids?${params}`,
    signal ? { signal } : {},
  );
  const payload = await readJsonResponse(response, {});
  if (!response.ok || payload?.ok === false) {
    throw new Error(responseMessage(payload, `章节题目加载失败 (${response.status || 'unknown'})`));
  }
  return {
    items: Array.isArray(payload.items) ? payload.items : [],
    total: Number.isFinite(payload.total) ? payload.total : 0,
  };
}

export async function completeTextbookSection(payload, { signal } = {}) {
  const response = await fetchWithAuth(`${API_BASE}/learning-activity/textbook-progress`, {
    method: 'POST',
    body: JSON.stringify(payload),
    ...(signal ? { signal } : {}),
  });
  const responsePayload = await readJsonResponse(response, {});
  if (!response.ok || responsePayload?.ok === false) {
    throw new Error(responseMessage(responsePayload, `教材小节学习记录失败 (${response.status || 'unknown'})`));
  }
  return responsePayload;
}

export async function submitSectionExamAnswer(payload, { signal } = {}) {
  const response = await fetchWithAuth(`${API_BASE}/training/workspace/section-exam/answers`, {
    method: 'POST',
    body: JSON.stringify(payload),
    ...(signal ? { signal } : {}),
  });
  const responsePayload = await readJsonResponse(response, {});
  if (!response.ok || responsePayload?.ok === false) {
    throw new Error(responseMessage(responsePayload, `作答记录失败 (${response.status || 'unknown'})`));
  }
  return responsePayload;
}