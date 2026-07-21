import { beforeEach, describe, expect, it, vi } from 'vitest';
import { loadPlannedLearningPath } from './learningPathApi';


describe('planned learning path API', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ schema_version: '1.0', nodes: [] }),
    })));
  });

  it('uses the main backend namespace and encodes parent IDs', async () => {
    await loadPlannedLearningPath('plan:stage/1');

    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/learning-path?parent_id=plan%3Astage%2F1',
      expect.objectContaining({ credentials: 'include' }),
    );
  });
});
