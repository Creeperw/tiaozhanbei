import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  LEGACY_HISTORY_PAGE_SIZE,
  loadAllLearningHistory,
  loadLegacySessionMessages,
  validateLegacyHistoryPage,
} from './legacyLearningClient.js';

function page(section, offset, items, total = items.length, hasMore = offset + items.length < total) {
  return {
    section,
    total,
    offset,
    limit: LEGACY_HISTORY_PAGE_SIZE,
    has_more: hasMore,
    items,
    record_scope: 'legacy_archive',
    audit_status: 'not_evaluated',
  };
}

function response(payload, ok = true, status = 200) {
  return {
    ok,
    status,
    text: async () => (payload === undefined ? '' : JSON.stringify(payload)),
  };
}

describe('legacyLearningClient', () => {
  it('loads all owner-scoped message pages without using formal conversations', async () => {
    const urls = [];
    vi.stubGlobal('fetch', async url => {
      urls.push(url);
      const offset = Number(new URL(url, 'http://localhost').searchParams.get('offset'));
      return response({ total: 2, offset, limit: 100, has_more: offset === 0, items: [{ id: offset + 1, content: `消息${offset}` }] });
    });
    const result = await loadLegacySessionMessages('old/id');
    expect(result.items).toHaveLength(2);
    expect(urls).toHaveLength(2);
    expect(urls.every(url => url.includes('/history/sessions/old%2Fid/messages'))).toBe(true);
  });

  it('propagates a denied historical message request', async () => {
    vi.stubGlobal('fetch', async () => response({ detail: 'Not found' }, false, 404));
    await expect(loadLegacySessionMessages('not-owned')).rejects.toThrow('404');
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('loads every legacy history page and preserves the archive contract', async () => {
    const requests = [];
    vi.stubGlobal('fetch', async (url, options) => {
      requests.push([url, options]);
      const offset = new URL(url, 'http://localhost').searchParams.get('offset');
      return response(offset === '0'
        ? page('attempts', 0, [{ id: 1, answer: 'A' }], 2)
        : page('attempts', 1, [{ id: 2, answer: 'B' }], 2));
    });
    const result = await loadAllLearningHistory('attempts');
    expect(result.items.map((item) => item.id)).toEqual([1, 2]);
    expect(result.total).toBe(2);
    expect(result.record_scope).toBe('legacy_archive');
    expect(result.audit_status).toBe('not_evaluated');
    expect(requests).toHaveLength(2);
    expect(requests[0][0]).toMatch(/section=attempts/);
    expect(requests[0][0]).toMatch(/limit=100/);
    expect(requests[1][0]).toMatch(/offset=1/);
  });

  it('does not treat an empty page as a successful complete history', async () => {
    vi.stubGlobal('fetch', async () => response(page('mastery', 0, [], 1, true)));
    await expect(
      loadAllLearningHistory('mastery'),
    ).rejects.toThrow('has_more 时不得为空');
  });

  it('accepts a legitimate empty archived history', async () => {
    vi.stubGlobal('fetch', async () => response(page('plans', 0, [], 0, false)));

    await expect(loadAllLearningHistory('plans')).resolves.toMatchObject({
      total: 0,
      items: [],
      record_scope: 'legacy_archive',
      audit_status: 'not_evaluated',
    });
  });

  it('rejects malformed or incomplete pagination metadata', () => {
    expect(() => validateLegacyHistoryPage(page('plans', 0, [{ id: 1 }], 2, false), {
      section: 'plans',
      offset: 0,
      limit: LEGACY_HISTORY_PAGE_SIZE,
    })).toThrow('has_more 与 total 不一致');
    expect(() => validateLegacyHistoryPage({ ...page('plans', 0, [{ id: 1 }]), audit_status: 'completed' }, {
      section: 'plans',
      offset: 0,
      limit: LEGACY_HISTORY_PAGE_SIZE,
    })).toThrow('审核状态无效');
  });

  it('exposes a server failure instead of returning an empty array', async () => {
    vi.stubGlobal('fetch', async () => response({ detail: '历史接口不可用' }, false, 503));
    await expect(loadAllLearningHistory('attempts')).rejects.toThrow('503: 历史接口不可用');
  });

  it('passes the caller AbortSignal to every request', async () => {
    const controller = new AbortController();
    let receivedOptions;
    vi.stubGlobal('fetch', async (_url, options) => {
      receivedOptions = options;
      return response(page('plans', 0, [], 0, false));
    });
    await loadAllLearningHistory('plans', { signal: controller.signal });
    expect(receivedOptions.signal).toBe(controller.signal);
  });
});