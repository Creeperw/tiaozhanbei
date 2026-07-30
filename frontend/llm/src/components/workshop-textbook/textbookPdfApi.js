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
