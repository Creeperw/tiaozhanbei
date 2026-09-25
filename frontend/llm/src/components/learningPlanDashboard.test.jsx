import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

import { loadAtlasNodes } from './knowledge-atlas/knowledgeAtlasApi';
import { loadTextbookProgress } from './workshop-textbook/textbookChapterApi';
import { clearTextbookSnapshotCache, loadTextbookLearningSummary, useLearningPlanMetrics } from './learningPlanDashboard';

vi.mock('./knowledge-atlas/knowledgeAtlasApi', () => ({
  loadAtlasNodes: vi.fn(),
}));

vi.mock('./workshop-textbook/textbookChapterApi', () => ({
  loadTextbookProgress: vi.fn(),
}));

describe('lightweight textbook learning summary', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearTextbookSnapshotCache();
  });
  afterEach(() => vi.unstubAllGlobals());

  it.each(['statistics', 'policy'])('publishes %s without waiting for the other metric', async (first) => {
    const responses = {};
    vi.stubGlobal('fetch', vi.fn((url) => new Promise((resolve) => {
      responses[String(url).includes('/task-load-policy') ? 'policy' : 'statistics'] = resolve;
    })));
    const books = [];
    const { result } = renderHook(() => useLearningPlanMetrics({ books, cacheKey: 'metrics-user' }));
    await act(async () => {
      responses[first]({ ok: true, text: async () => JSON.stringify(
        first === 'statistics' ? { lifetime: { focus_minutes: 120 } } : { recommended_minutes: 25 },
      ) });
    });
    await waitFor(() => expect(result.current[first === 'statistics' ? 'totalFocusMinutes' : 'recommendedMinutes'])
      .toBe(first === 'statistics' ? 120 : 25));
    expect(result.current[first === 'statistics' ? 'statisticsLoading' : 'policyLoading']).toBe(false);
    expect(result.current[first === 'statistics' ? 'policyLoading' : 'statisticsLoading']).toBe(true);
    const second = first === 'statistics' ? 'policy' : 'statistics';
    await act(async () => {
      responses[second]({ ok: true, text: async () => JSON.stringify(
        second === 'statistics' ? { lifetime: { focus_minutes: 120 } } : { recommended_minutes: 25 },
      ) });
    });
    expect(result.current).toMatchObject({ totalFocusMinutes: 120, recommendedMinutes: 25, loading: false });
  });

  it('keeps previous values on failure without inventing zero for unknown statistics', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('offline'))));
    const books = [];
    const first = renderHook(() => useLearningPlanMetrics({ books, cacheKey: 'metrics-errors' }));
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    expect(first.result.current.totalFocusMinutes).toBeNull();
    first.unmount();
    globalThis.fetch.mockImplementation((url) => Promise.resolve({
      ok: true, text: async () => JSON.stringify(String(url).includes('/task-load-policy')
        ? { recommended_minutes: 25 } : { lifetime: { focus_minutes: 120 } }),
    }));
    const second = renderHook(() => useLearningPlanMetrics({ books, cacheKey: 'metrics-errors' }));
    await waitFor(() => expect(second.result.current.totalFocusMinutes).toBe(120));
    globalThis.fetch.mockRejectedValue(new Error('offline'));
    await act(async () => { window.dispatchEvent(new Event('focus')); });
    expect(second.result.current).toMatchObject({ totalFocusMinutes: 120, recommendedMinutes: 25, loading: false });
  });

  it('ignores superseded focus, account and unmounted responses', async () => {
    const requests = [];
    vi.stubGlobal('fetch', vi.fn((url, options) => new Promise((resolve) => {
      requests.push({ url: String(url), signal: options.signal, resolve });
    })));
    const finish = (request, value) => request.resolve({
      ok: true, text: async () => JSON.stringify(request.url.includes('/task-load-policy')
        ? { recommended_minutes: value } : { lifetime: { focus_minutes: value } }),
    });
    const books = [];
    const { result, rerender, unmount } = renderHook(({ cacheKey }) => useLearningPlanMetrics({ books, cacheKey }), {
      initialProps: { cacheKey: 'account-a' },
    });
    act(() => window.dispatchEvent(new Event('focus')));
    expect(requests.slice(0, 2).every(({ signal }) => signal.aborted)).toBe(true);
    await act(async () => { requests.slice(2, 4).forEach((request) => finish(request, 20)); });
    await act(async () => { requests.slice(0, 2).forEach((request) => finish(request, 999)); });
    expect(result.current).toMatchObject({ totalFocusMinutes: 20, recommendedMinutes: 20 });
    act(() => window.dispatchEvent(new Event('focus')));
    rerender({ cacheKey: 'account-b' });
    expect(result.current).toMatchObject({ totalFocusMinutes: null, recommendedMinutes: null, loading: true });
    expect(requests.slice(4, 6).every(({ signal }) => signal.aborted)).toBe(true);
    await act(async () => { requests.slice(4, 6).forEach((request) => finish(request, 888)); });
    expect(result.current.totalFocusMinutes).toBeNull();
    unmount();
    expect(requests.slice(6, 8).every(({ signal }) => signal.aborted)).toBe(true);
    await act(async () => { requests.slice(6, 8).forEach((request) => finish(request, 777)); });
    const count = requests.length;
    act(() => window.dispatchEvent(new Event('focus')));
    expect(requests).toHaveLength(count);
    const remounted = renderHook(() => useLearningPlanMetrics({ books, cacheKey: 'account-b' }));
    expect(remounted.result.current.totalFocusMinutes).toBeNull();
  });

  it('uses chapter child counts without loading every chapter section page', async () => {
    loadAtlasNodes.mockResolvedValue({
      nodes: [
        { id: 'CH_1', name: '第一章', children_count: 2 },
        { id: 'CH_2', name: '第二章', children_count: 3 },
      ],
    });
    loadTextbookProgress.mockResolvedValue({
      completed_section_ids: ['S_1', 'S_2'],
      last_section_id: 'S_2',
      history: [{ chapter_name: '第一章', timestamp: '2026-08-15T00:00:00Z' }],
    });

    const summary = await loadTextbookLearningSummary('《中医学基础》');

    expect(loadAtlasNodes).toHaveBeenCalledOnce();
    expect(loadAtlasNodes).toHaveBeenCalledWith(expect.objectContaining({
      level: 2,
      lv1: '中医学基础',
    }));
    expect(summary).toMatchObject({
      completedSections: 2,
      totalSections: 5,
      progress: 0.4,
      lastSectionId: 'S_2',
      lastChapterName: '第一章',
    });
    expect(summary.nextSectionId).toBe('');
  });
});
