import { fetchWithAuth, readJsonResponse } from '../../utils/api';

async function request(path, options = {}) {
  const response = await fetchWithAuth(`/api/v1${path}`, options);
  const data = await readJsonResponse(response, {});
  if (!response.ok) throw new Error(data.detail || '请求失败');
  return data;
}

const json = (method, body) => ({ method, body: JSON.stringify(body) });

// ── 教材目录缓存 ─────────────────────────────────────────────
// catalog 端点在服务端已做可用性预计算，但每次请求仍要序列化大列表；
// 前端用 60s 内存 + localStorage 双层缓存，切页/刷新不再重复请求。
const CATALOG_CACHE_KEY = 'textbook_pdf_catalog_cache';
const CATALOG_CACHE_TTL_MS = 60_000;
let catalogCache = null; // { cachedAt, data }

export const invalidateTextbookPdfCatalogCache = () => {
  catalogCache = null;
  try {
    localStorage.removeItem(CATALOG_CACHE_KEY);
  } catch {
    /* storage 不可用时静默忽略 */
  }
};

export const loadTextbookPdfCatalog = async ({ signal } = {}) => {
  const now = Date.now();
  if (catalogCache && now - catalogCache.cachedAt < CATALOG_CACHE_TTL_MS) {
    return catalogCache.data;
  }
  try {
    const stored = localStorage.getItem(CATALOG_CACHE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (parsed?.cachedAt && now - parsed.cachedAt < CATALOG_CACHE_TTL_MS) {
        catalogCache = parsed;
        return parsed.data;
      }
    }
  } catch {
    /* 缓存损坏时忽略，回源请求 */
  }
  const data = await request('/textbooks/pdfs/catalog', { signal });
  catalogCache = { cachedAt: now, data };
  try {
    localStorage.setItem(CATALOG_CACHE_KEY, JSON.stringify(catalogCache));
  } catch {
    /* storage 不可用时静默忽略 */
  }
  return data;
};

export const resolveTextbookPdf = (book, { signal } = {}) => request(
  `/textbooks/pdfs/resolve?book=${encodeURIComponent(book)}`,
  { signal },
);

export const loadTextbookCategories = ({ signal } = {}) => request(
  '/textbooks/categories',
  { signal },
);

export const loadTextbookPdfMetadata = (bookId, { signal } = {}) => request(
  `/textbooks/pdfs/${encodeURIComponent(bookId)}`,
  { signal },
);

export const loadBookMatchedQuestions = (bookId, { signal } = {}) => request(
  `/textbooks/pdfs/${encodeURIComponent(bookId)}/questions`,
  { signal },
);

export const deleteUploadedTextbook = async (bookId) => {
  const data = await request(
    `/textbooks/pdfs/${encodeURIComponent(bookId)}`,
    { method: 'DELETE' },
  );
  invalidateTextbookPdfCatalogCache();
  return data;
};

export const setUploadedTextbookHidden = async (bookId, hidden) => {
  const data = await request(
    `/textbooks/pdfs/${encodeURIComponent(bookId)}`,
    json('PATCH', { hidden }),
  );
  invalidateTextbookPdfCatalogCache();
  return data;
};

export const listKnowledgeGraphs = ({ signal } = {}) => request(
  '/textbooks/knowledge-graphs',
  { signal },
);

export const loadKnowledgeGraph = (bookId, { signal } = {}) => request(
  `/textbooks/knowledge-graphs/${encodeURIComponent(bookId)}`,
  { signal },
);

export const loadTextbookImportStatus = (taskId, { signal } = {}) => request(
  `/textbooks/import/${encodeURIComponent(taskId)}`,
  { signal },
);

export const uploadTextbook = async (formData, { signal } = {}) => {
  const response = await fetchWithAuth('/api/v1/textbooks/import', {
    method: 'POST',
    body: formData,
    ...(signal ? { signal } : {}),
  });
  const data = await readJsonResponse(response, {});
  if (!response.ok) {
    const detail = data.detail;
    const message = typeof detail === 'string'
      ? detail
      : detail?.message || detail?.code || '教材上传失败';
    const error = new Error(message);
    error.code = detail?.code || '';
    throw error;
  }
  invalidateTextbookPdfCatalogCache();
  return data;
};

export const loadPdfAnnotations = (bookId, pageNumber, { signal } = {}) => request(
  `/textbooks/pdfs/${encodeURIComponent(bookId)}/pages/${pageNumber}/annotations`,
  { signal },
);

export const savePdfAnnotations = (bookId, pageNumber, annotations) => request(
  `/textbooks/pdfs/${encodeURIComponent(bookId)}/pages/${pageNumber}/annotations`,
  json('PUT', { annotations }),
);

export const loadPdfReadingState = (bookId, { signal } = {}) => request(
  `/textbooks/pdfs/${encodeURIComponent(bookId)}/reading-state`,
  { signal },
);

export const savePdfReadingState = (bookId, pageNumber, zoom) => request(
  `/textbooks/pdfs/${encodeURIComponent(bookId)}/reading-state`,
  json('PUT', { page_number: pageNumber, zoom }),
);

export const streamPdfAi = async (bookId, pageNumber, { mode, question, history, sessionId, onDelta, signal }) => {
  const response = await fetchWithAuth(`/api/v1/textbooks/pdfs/${encodeURIComponent(bookId)}/pages/${pageNumber}/ai`, {
    method: 'POST',
    body: JSON.stringify({ mode, question, history: history || [], session_id: sessionId || null }),
    signal,
  });
  if (!response.ok) {
    const data = await readJsonResponse(response, {}).catch(() => ({}));
    const detail = data.detail;
    const message = typeof detail === 'string' && detail.trim()
      ? detail.trim()
      : 'AI 请求失败，请稍后重试';
    throw new Error(message);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let done = false;
  while (!done) {
    const { value, done: readerDone } = await reader.read();
    done = readerDone;
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('data: ')) continue;
      let frame;
      try { frame = JSON.parse(trimmed.slice(6)); } catch { continue; }
      if (frame.event === 'delta' && typeof frame.text === 'string') onDelta(frame.text);
      if (frame.event === 'error') throw new Error(frame.message || 'AI 生成失败');
    }
  }
};

export const loadPdfAiSessions = ({ signal } = {}) => request(
  '/textbooks/ai/sessions',
  { signal },
);

export const createPdfAiSession = (title = '新对话') => request(
  '/textbooks/ai/sessions',
  json('POST', { title }),
);

export const loadPdfAiSessionMessages = (sessionId, { signal } = {}) => request(
  `/textbooks/ai/sessions/${encodeURIComponent(sessionId)}/messages`,
  { signal },
);

export const renamePdfAiSession = (sessionId, title) => request(
  `/textbooks/ai/sessions/${encodeURIComponent(sessionId)}`,
  json('PATCH', { title }),
);

export const deletePdfAiSession = (sessionId) => request(
  `/textbooks/ai/sessions/${encodeURIComponent(sessionId)}`,
  { method: 'DELETE' },
);
