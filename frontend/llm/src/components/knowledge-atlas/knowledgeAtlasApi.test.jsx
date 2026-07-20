import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  atlasImageUrl,
  loadAtlasDetail,
  loadAtlasImage,
  loadAtlasNodes,
  loadAtlasRoutes,
  resolveAtlasContext,
} from './knowledgeAtlasApi';

vi.mock('../../utils/api', () => ({
  API_BASE: '/api',
  fetchWithAuth: vi.fn(),
}));

import { fetchWithAuth } from '../../utils/api';

describe('knowledgeAtlasApi', () => {
  beforeEach(() => vi.clearAllMocks());

  it('uses authenticated Atlas endpoints and forwards AbortSignal', async () => {
    const signal = new AbortController().signal;
    fetchWithAuth.mockResolvedValue({ ok: true, json: async () => ({ ok: true, routes: [{ id: 'textbook_14_5' }] }) });
    await expect(loadAtlasRoutes({ signal })).resolves.toEqual([{ id: 'textbook_14_5' }]);
    expect(fetchWithAuth).toHaveBeenCalledWith('/api/knowledge/atlas/routes', { signal });
  });

  it('encodes hierarchy, detail, and dashboard context parameters', async () => {
    fetchWithAuth
      .mockResolvedValueOnce({ ok: true, json: async () => ({ nodes: [{ id: 'kp-1' }] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ kp: { id: 'kp-1' }, questions: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ resolved: true, route: 'postgraduate' }) });
    await loadAtlasNodes({ level: 3, route: 'postgraduate', lv1: '药理学', lv2: '第一节 心律失常', signal: null });
    await loadAtlasDetail('kp/1', { questionLimit: 50 });
    await resolveAtlasContext({ trackId: 'track a', membershipId: 'node/1' });
    expect(fetchWithAuth.mock.calls[0][0]).toContain('level=3');
    expect(fetchWithAuth.mock.calls[0][0]).toContain('lv1=%E8%8D%AF%E7%90%86%E5%AD%A6');
    expect(fetchWithAuth.mock.calls[1][0]).toBe('/api/knowledge/atlas/detail/kp%2F1?question_limit=50');
    expect(fetchWithAuth.mock.calls[2][0]).toContain('track_id=track+a');
    expect(fetchWithAuth.mock.calls[2][0]).toContain('membership_id=node%2F1');
  });

  it('loads protected images through the authenticated client instead of a bare img request', async () => {
    const blob = new Blob(['image'], { type: 'image/png' });
    const signal = new AbortController().signal;
    fetchWithAuth.mockResolvedValue({ ok: true, blob: async () => blob });
    await expect(loadAtlasImage('figure 1.png', { signal })).resolves.toBe(blob);
    expect(atlasImageUrl('figure 1.png')).toBe('/api/knowledge/atlas/images/figure%201.png');
    expect(fetchWithAuth).toHaveBeenCalledWith('/api/knowledge/atlas/images/figure%201.png', { signal });
  });

  it('surfaces structured FastAPI availability errors as readable messages', async () => {
    fetchWithAuth.mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ detail: { code: 'knowledge_atlas_unavailable', message: 'Atlas assets missing' } }),
    });
    await expect(loadAtlasRoutes()).rejects.toThrow('Atlas assets missing');
  });
});
