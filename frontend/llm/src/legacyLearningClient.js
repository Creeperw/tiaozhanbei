import { API_BASE, fetchWithAuth, readJsonResponse } from './utils/api';

export const LEGACY_HISTORY_SECTIONS = Object.freeze(['attempts', 'mastery', 'plans', 'sessions']);
export const LEGACY_HISTORY_PAGE_SIZE = 100;

const MAX_HISTORY_PAGES = 10000;

const isPlainObject = (value) => (
  value !== null
  && typeof value === 'object'
  && !Array.isArray(value)
);

const historyPagePath = (section, offset) => (
  `${API_BASE}/learning-activity/history?section=${encodeURIComponent(section)}`
  + `&offset=${offset}&limit=${LEGACY_HISTORY_PAGE_SIZE}`
);

function responseError(response, payload) {
  const detail = isPlainObject(payload) && typeof payload.detail === 'string'
    ? payload.detail.trim()
    : '';
  return new Error(
    `${response?.status || '请求'}: ${detail || '历史学习记录读取失败'}`,
  );
}

export function validateLegacyHistoryPage(payload, {
  section,
  offset,
  limit = LEGACY_HISTORY_PAGE_SIZE,
} = {}) {
  if (!isPlainObject(payload)) throw new Error('历史学习记录响应格式无效');
  if (payload.section !== section) throw new Error('历史学习记录 section 不匹配');
  if (payload.record_scope !== 'legacy_archive') {
    throw new Error('历史学习记录缺少 legacy_archive 记录范围');
  }
  if (payload.audit_status !== 'not_evaluated') {
    throw new Error('历史学习记录审核状态无效');
  }
  if (!Number.isInteger(payload.total) || payload.total < 0) {
    throw new Error('历史学习记录 total 无效');
  }
  if (!Number.isInteger(payload.offset) || payload.offset !== offset || payload.offset < 0) {
    throw new Error('历史学习记录 offset 无效');
  }
  if (!Number.isInteger(payload.limit) || payload.limit < 1 || payload.limit > LEGACY_HISTORY_PAGE_SIZE) {
    throw new Error('历史学习记录 limit 无效');
  }
  if (payload.limit !== limit) throw new Error('历史学习记录 limit 与请求不一致');
  if (typeof payload.has_more !== 'boolean') throw new Error('历史学习记录 has_more 无效');
  if (!Array.isArray(payload.items)) throw new Error('历史学习记录 items 无效');
  if (payload.items.length > payload.limit) throw new Error('历史学习记录分页数量超出 limit');
  if (payload.offset > payload.total || payload.offset + payload.items.length > payload.total) {
    throw new Error('历史学习记录分页范围无效');
  }
  if (!payload.items.every(isPlainObject)) throw new Error('历史学习记录条目格式无效');

  const expectedHasMore = payload.offset + payload.items.length < payload.total;
  if (payload.has_more !== expectedHasMore) throw new Error('历史学习记录 has_more 与 total 不一致');
  if (payload.has_more && payload.items.length === 0) {
    throw new Error('历史学习记录分页在 has_more 时不得为空');
  }
  return payload;
}

export async function loadAllLearningHistory(section, { signal, sessionId } = {}) {
  if (!LEGACY_HISTORY_SECTIONS.includes(section)) {
    throw new Error(`不支持的历史学习记录类型：${section}`);
  }
  if (sessionId !== undefined && (section !== 'sessions' || !String(sessionId).trim())) {
    throw new Error('历史会话标识无效');
  }

  const items = [];
  let offset = 0;
  let total = null;
  let pageCount = 0;
  let firstPage = null;

  while (true) {
    pageCount += 1;
    if (pageCount > MAX_HISTORY_PAGES) throw new Error('历史学习记录分页次数超出限制');

    const path = sessionId === undefined ? historyPagePath(section, offset)
      : `${API_BASE}/learning-activity/history/sessions/${encodeURIComponent(sessionId)}/messages?offset=${offset}&limit=${LEGACY_HISTORY_PAGE_SIZE}`;
    const response = await fetchWithAuth(path, signal ? { signal } : {});
    const payload = await readJsonResponse(response, null);
    if (!response?.ok) throw responseError(response, payload);
    // The owner-scoped message endpoint has a pagination-only response.
    // Source metadata here is assigned by this fixed endpoint, not inferred
    // from message content or passed to the formal conversation API.
    const pagePayload = sessionId === undefined ? payload : {
      ...payload, section: 'sessions', record_scope: 'legacy_archive', audit_status: 'not_evaluated',
    };
    const page = validateLegacyHistoryPage(pagePayload, {
      section,
      offset,
      limit: LEGACY_HISTORY_PAGE_SIZE,
    });

    if (total === null) {
      total = page.total;
      firstPage = page;
    } else if (page.total !== total) {
      throw new Error('历史学习记录分页 total 在请求期间发生变化');
    }

    items.push(...page.items);
    if (!page.has_more) {
      if (items.length !== total) throw new Error('历史学习记录分页未完整返回 total 条记录');
      return {
        ...firstPage,
        items,
        offset: 0,
        limit: LEGACY_HISTORY_PAGE_SIZE,
        has_more: false,
      };
    }

    const nextOffset = offset + page.items.length;
    if (nextOffset <= offset) throw new Error('历史学习记录分页 offset 未前进');
    offset = nextOffset;
  }
}

export function loadLegacySessionMessages(sessionId, options = {}) {
  return loadAllLearningHistory('sessions', { ...options, sessionId });
}