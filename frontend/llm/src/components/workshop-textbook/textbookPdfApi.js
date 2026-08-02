import { fetchWithAuth, readJsonResponse } from '../../utils/api';

async function request(path, options = {}) {
  const response = await fetchWithAuth(`/api/v1${path}`, options);
  const data = await readJsonResponse(response, {});
  if (!response.ok) throw new Error(data.detail || '请求失败');
  return data;
}

const json = (method, body) => ({ method, body: JSON.stringify(body) });

export const resolveTextbookPdf = (book, { signal } = {}) => request(
  `/textbooks/pdfs/resolve?book=${encodeURIComponent(book)}`,
  { signal },
);

export const loadTextbookPdfCatalog = ({ signal } = {}) => request(
  '/textbooks/pdfs/catalog',
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
