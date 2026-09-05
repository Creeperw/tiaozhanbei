import { describe, expect, it, vi } from 'vitest';
import { loadAllNodeKnowledgePoints } from './examAtlasApi';

vi.mock('../../utils/api', () => ({
  API_BASE: '/api',
  fetchWithAuth: vi.fn(),
  readJsonResponse: vi.fn(async (response) => response.payload),
}));

import { fetchWithAuth } from '../../utils/api';

describe('examAtlasApi', () => {
  it('loads every accepted KP page until has_more is false', async () => {
    fetchWithAuth
      .mockResolvedValueOnce({
        ok: true,
        payload: {
          items: [{ kp_id: 'kp-1', name: '知识点一' }],
          total: 2,
          offset: 0,
          limit: 1,
          has_more: true,
        },
      })
      .mockResolvedValueOnce({
        ok: true,
        payload: {
          items: [{ kp_id: 'kp-2', name: '知识点二' }],
          total: 2,
          offset: 1,
          limit: 1,
          has_more: false,
        },
      });

    const result = await loadAllNodeKnowledgePoints('track-a', 'node-a', 1);

    expect(result.items.map((item) => item.kp_id)).toEqual(['kp-1', 'kp-2']);
    expect(fetchWithAuth).toHaveBeenNthCalledWith(
      2,
      '/api/exam-learning/tracks/track-a/nodes/node-a/knowledge-points?offset=1&limit=1',
      undefined,
    );
  });
});
