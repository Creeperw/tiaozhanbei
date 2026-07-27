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