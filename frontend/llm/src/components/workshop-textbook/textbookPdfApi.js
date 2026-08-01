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
