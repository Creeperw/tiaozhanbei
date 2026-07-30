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

  // 并行请求数据库和图谱两个数据源
  const dbPromise = (async () => {
    try {
      const dbParams = new URLSearchParams({ kp_ids: filtered.join(','), limit: '200' });
      const res = await fetchWithAuth(
        `${API_BASE}/training/workspace/questions-by-kp-ids?${dbParams}`,
        signal ? { signal } : {},
      );
      const payload = await readJsonResponse(res, {});
      return (res.ok && Array.isArray(payload.items)) ? payload.items : [];
    } catch (_) { return []; }
  })();

  const atlasPromise = (async () => {
    try {
      const atlasParams = new URLSearchParams({ mode: 'lexical', limit: '100' });
      for (const kpId of filtered) atlasParams.append('kp_id', kpId);
      const res = await fetchWithAuth(
        `${API_BASE}/knowledge/atlas/questions/search?${atlasParams}`,
        signal ? { signal } : {},
      );
      const payload = await readJsonResponse(res, {});
      return (res.ok && Array.isArray(payload.items)) ? payload.items : [];
    } catch (_) { return []; }
  })();

  const [dbItems, atlasItems] = await Promise.all([dbPromise, atlasPromise]);

  // 以数据库题目为主（有 analysis），图谱题目补充数据库没有的
  const dbIds = new Set(dbItems.map((q) => q.question_id));
  const merged = [...dbItems];
  for (const q of atlasItems) {
    if (!dbIds.has(q.question_id)) {
      merged.push(q);
    }
  }
  return { items: merged, total: merged.length };
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