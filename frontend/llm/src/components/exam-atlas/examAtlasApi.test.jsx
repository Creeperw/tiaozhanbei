import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { enrollLearningTargets, invalidateLearningTargetRead, loadAllNodeKnowledgePoints, loadLearningTarget, saveLearningTarget } from './examAtlasApi';

vi.mock('../../utils/api', () => ({
  API_BASE: '/api',
  fetchWithAuth: vi.fn(),
  readJsonResponse: vi.fn(async (response) => response.payload),
}));

import { fetchWithAuth } from '../../utils/api';

describe('examAtlasApi', () => {
  beforeEach(() => {
    invalidateLearningTargetRead();
    fetchWithAuth.mockReset();
  });
  afterEach(() => invalidateLearningTargetRead());

  it('shares only an in-flight target read, then fetches again after settlement', async () => {
    let resolve;
    fetchWithAuth.mockReturnValue(new Promise((done) => { resolve = done; }));
    const first = loadLearningTarget();
    const second = loadLearningTarget();
    expect(fetchWithAuth).toHaveBeenCalledTimes(1);
    resolve({ ok: true, payload: { target: { exam_track_id: 'track-a' } } });
    expect(await first).toEqual(await second);
    fetchWithAuth.mockResolvedValueOnce({ ok: true, payload: { target: { exam_track_id: 'track-b' } } });
    expect(await loadLearningTarget()).toEqual({ target: { exam_track_id: 'track-b' } });
    expect(fetchWithAuth).toHaveBeenCalledTimes(2);
  });

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

  it('does not retain a rejected target request', async () => {
    fetchWithAuth.mockRejectedValueOnce(new Error('network unavailable'));
    await expect(loadLearningTarget()).rejects.toThrow('network unavailable');
    fetchWithAuth.mockResolvedValueOnce({ ok: true, payload: { target: null } });
    await expect(loadLearningTarget()).resolves.toEqual({ target: null });
    expect(fetchWithAuth).toHaveBeenCalledTimes(2);
  });

  it('rejects an old-session response without clearing a newer in-flight read', async () => {
    let resolveOld;
    let resolveNew;
    fetchWithAuth.mockReturnValueOnce(new Promise((done) => { resolveOld = done; }));
    const old = loadLearningTarget();
    const oldRejected = expect(old).rejects.toMatchObject({ name: 'AbortError' });
    const oldSignal = fetchWithAuth.mock.calls[0][1].signal;
    invalidateLearningTargetRead();
    expect(oldSignal.aborted).toBe(true);
    fetchWithAuth.mockReturnValueOnce(new Promise((done) => { resolveNew = done; }));
    const current = loadLearningTarget();
    resolveOld({ ok: true, payload: { target: { exam_track_id: 'old-user-track' } } });
    await oldRejected;
    expect(loadLearningTarget()).toBe(current);
    resolveNew({ ok: true, payload: { target: { exam_track_id: 'new-user-track' } } });
    await expect(current).resolves.toEqual({ target: { exam_track_id: 'new-user-track' } });
    expect(fetchWithAuth).toHaveBeenCalledTimes(2);
  });

  it.each(['save', 'enroll'])('invalidates reads before and during %s', async (kind) => {
    const reads = [];
    let finishWrite;
    fetchWithAuth.mockImplementation((url, options = {}) => {
      if (options.method) return new Promise((resolve) => { finishWrite = resolve; });
      return new Promise((resolve) => reads.push({ resolve, signal: options.signal }));
    });
    const before = loadLearningTarget();
    const beforeRejected = expect(before).rejects.toMatchObject({ name: 'AbortError' });
    const write = kind === 'save' ? saveLearningTarget('track-b') : enrollLearningTargets(['track-b'], 'track-b');
    expect(reads[0].signal.aborted).toBe(true);
    const during = loadLearningTarget();
    const duringRejected = expect(during).rejects.toMatchObject({ name: 'AbortError' });
    finishWrite({ ok: true, payload: { target: { exam_track_id: 'track-b' } } });
    await write;
    expect(reads[1].signal.aborted).toBe(true);
    reads.forEach(({ resolve }) => resolve({ ok: true, payload: { target: { exam_track_id: 'track-a' } } }));
    await Promise.all([beforeRejected, duringRejected]);
    const after = loadLearningTarget();
    reads[2].resolve({ ok: true, payload: { target: { exam_track_id: 'track-b' } } });
    await expect(after).resolves.toEqual({ target: { exam_track_id: 'track-b' } });
  });

  it('does not cancel a new-session read when an old write finishes', async () => {
    let finishWrite;
    let finishRead;
    fetchWithAuth.mockReturnValueOnce(new Promise((resolve) => { finishWrite = resolve; }));
    const write = saveLearningTarget('old-track');
    invalidateLearningTargetRead();
    fetchWithAuth.mockReturnValueOnce(new Promise((resolve) => { finishRead = resolve; }));
    const read = loadLearningTarget();
    const signal = fetchWithAuth.mock.calls[1][1].signal;
    finishWrite({ ok: true, payload: {} });
    await write;
    expect(signal.aborted).toBe(false);
    expect(loadLearningTarget()).toBe(read);
    finishRead({ ok: true, payload: { target: { exam_track_id: 'new-track' } } });
    await expect(read).resolves.toEqual({ target: { exam_track_id: 'new-track' } });
  });

  it('allows a fresh target read after a failed target update', async () => {
    fetchWithAuth.mockRejectedValueOnce(new Error('write failed'));
    await expect(saveLearningTarget('track-b')).rejects.toThrow('write failed');
    fetchWithAuth.mockResolvedValueOnce({ ok: true, payload: { target: { exam_track_id: 'track-a' } } });
    await expect(loadLearningTarget()).resolves.toEqual({ target: { exam_track_id: 'track-a' } });
  });
});
