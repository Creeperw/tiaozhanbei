import { fetchWithAuth, readJsonResponse } from '../utils/api';

async function request(path, options = {}) {
  const response = await fetchWithAuth(`/api/v1${path}`, options);
  if (response.status === 204) return null;
  const data = await readJsonResponse(response, {});
  if (!response.ok) throw new Error(data.detail || '请求失败');
  return data;
}

const jsonOptions = (method, body) => ({ method, body: JSON.stringify(body) });

export const loadFavoriteFolders = () => request('/workshop/favorite-folders');
export const createFavoriteFolder = (name) => request(
  '/workshop/favorite-folders',
  jsonOptions('POST', { name }),
);
export const deleteFavoriteFolder = (folderId) => request(
  `/workshop/favorite-folders/${encodeURIComponent(folderId)}`,
  { method: 'DELETE' },
);
export const loadFavorites = (folderId = '') => request(
  `/workshop/favorites${folderId ? `?folder_id=${encodeURIComponent(folderId)}` : ''}`,
);
export const saveFavorite = (favorite) => request(
  '/workshop/favorites',
  jsonOptions('POST', favorite),
);
export const deleteFavorite = (favoriteId) => request(
  `/workshop/favorites/${encodeURIComponent(favoriteId)}`,
  { method: 'DELETE' },
);

export const loadNotes = ({ source = '', noteType = '', query = '' } = {}) => {
  const params = new URLSearchParams();
  if (source) params.set('source', source);
  if (noteType) params.set('note_type', noteType);
  if (query) params.set('q', query);
  const suffix = params.toString();
  return request(`/workshop/notes${suffix ? `?${suffix}` : ''}`);
};
export const createNote = (note) => request(
  '/workshop/notes',
  jsonOptions('POST', note),
);
export const updateNote = (noteId, note) => request(
  `/workshop/notes/${encodeURIComponent(noteId)}`,
  jsonOptions('PUT', note),
);
export const deleteNote = (noteId) => request(
  `/workshop/notes/${encodeURIComponent(noteId)}`,
  { method: 'DELETE' },
);
