import { API_BASE, fetchWithAuth } from '../../utils/api';

function atlasErrorMessage(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object') return detail.message || detail.code || fallback;
  return payload?.error || fallback;
}

async function readAtlasResponse(path, { signal, method = 'GET' } = {}) {
  const options = signal ? { signal } : {};
  if (method !== 'GET') options.method = method;
  const response = await fetchWithAuth(`${API_BASE}${path}`, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload?.ok === false) {
    throw new Error(atlasErrorMessage(payload, `知识星球请求失败 (${response.status || 'unknown'})`));
  }
  return payload;
}

export async function loadAtlasStatus({ signal } = {}) {
  return readAtlasResponse('/knowledge/atlas/status', { signal });
}

export async function loadAtlasRoutes({ signal } = {}) {
  const payload = await readAtlasResponse('/knowledge/atlas/routes', { signal });
  return Array.isArray(payload) ? payload : (payload.routes || payload.items || []);
}

export async function loadAtlasNodes({ level = 1, route = 'textbook_14_5', lv1 = '', lv2 = '', signal } = {}) {
  const params = new URLSearchParams({ level: String(level), route });
  if (lv1) params.set('lv1', lv1);
  if (lv2) params.set('lv2', lv2);
  const payload = await readAtlasResponse(`/knowledge/atlas/nodes?${params}`, { signal });
  if (Array.isArray(payload)) return { nodes: payload, count: payload.length, stats: {} };
  return {
    ...payload,
    nodes: payload.nodes || payload.items || [],
  };
}

export async function loadAtlasDetail(kpId, { questionLimit = 50, signal } = {}) {
  const params = new URLSearchParams({ question_limit: String(questionLimit) });
  return readAtlasResponse(`/knowledge/atlas/detail/${encodeURIComponent(kpId)}?${params}`, { signal });
}

export async function resolveAtlasContext({ trackId, membershipId, signal } = {}) {
  const params = new URLSearchParams();
  if (trackId) params.set('track_id', trackId);
  if (membershipId) params.set('membership_id', membershipId);
  return readAtlasResponse(`/knowledge/atlas/resolve-context?${params}`, { signal });
}

export async function warmAtlas({ signal } = {}) {
  return readAtlasResponse('/knowledge/atlas/warm', { signal, method: 'POST' });
}

export function atlasImageUrl(filename) {
  return `${API_BASE}/knowledge/atlas/images/${encodeURIComponent(filename)}`;
}

export async function loadAtlasImage(filename, { signal } = {}) {
  const response = await fetchWithAuth(atlasImageUrl(filename), signal ? { signal } : {});
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(atlasErrorMessage(payload, '教材图片加载失败'));
  }
  return response.blob();
}
