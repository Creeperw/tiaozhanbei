import { useEffect, useMemo, useState } from 'react';
import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import { loadAtlasNodes } from './knowledge-atlas/knowledgeAtlasApi';
import { loadTextbookProgress } from './workshop-textbook/textbookChapterApi';

export function normalizeBookName(value) {
  return String(value || '').replace(/[《》]/g, '').trim();
}

export function focusMinutesFromStatistics(payload) {
  const rawValue = payload?.lifetime?.focus_minutes;
  if (rawValue === null || rawValue === undefined || rawValue === '') return null;
  const value = Number(rawValue);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

export function formatLearningDuration(minutes) {
  if (!Number.isFinite(minutes)) return '待统计';
  if (minutes < 60) return `${Math.round(minutes)} 分钟`;
  const hours = Math.round((minutes / 60) * 10) / 10;
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} 小时`;
}

export function selectNextKnowledgePoint(task, fallback = '') {
  const pendingItem = (Array.isArray(task?.items) ? task.items : [])
    .find((item) => item?.status !== 'completed' && String(item?.kp_name || '').trim());
  return pendingItem?.kp_name
    || task?.focus_knowledge_points?.[0]
    || fallback
    || '';
}

export function visibleWorkshopTextbooks({ allTextbooks = [], plannedBooks = [], remainingTextbooks = [], showAllTextbooks = false } = {}) {
  if (!plannedBooks.length) return allTextbooks;
  const textbookByName = new Map(allTextbooks.map((book) => [normalizeBookName(book?.name || book?.title), book]));
  const visiblePlannedBooks = [];
  const seen = new Set();
  plannedBooks.forEach((book) => {
    const name = normalizeBookName(book?.navigation?.book || book?.book || book?.name || book?.title);
    const textbook = textbookByName.get(name);
    if (textbook && !seen.has(name)) {
      visiblePlannedBooks.push(textbook);
      seen.add(name);
    }
  });
  if (!showAllTextbooks) return visiblePlannedBooks;
  const rest = remainingTextbooks.filter((book) => !seen.has(normalizeBookName(book?.name || book?.title)));
  return [...visiblePlannedBooks, ...rest];
}

function responseError(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === 'string') return detail;
  return detail?.message || payload?.error || fallback;
}

async function loadMainApi(path, signal) {
  const response = await fetchWithAuth(`${MAIN_API_BASE}${path}`, { signal });
  const payload = await readJsonResponse(response, {});
  if (!response.ok) throw new Error(responseError(payload, '学习计划数据加载失败'));
  return payload;
}

function historyTimestamp(item) {
  const timestamp = Date.parse(item?.timestamp || item?.created_at || item?.completed_at || item?.updated_at || '');
  return Number.isFinite(timestamp) ? timestamp : 0;
}

export async function loadTextbookLearningSnapshot(book, route = 'textbook_14_5', { signal } = {}) {
  const normalizedBook = normalizeBookName(book);
  if (!normalizedBook) throw new Error('教材名称不能为空');

  const [chapterPayload, progressPayload] = await Promise.all([
    loadAtlasNodes({ level: 2, route, lv1: normalizedBook, signal }),
    loadTextbookProgress(normalizedBook, { signal }),
  ]);
  const chapters = Array.isArray(chapterPayload?.nodes) ? chapterPayload.nodes : [];
  const sectionPages = await Promise.all(chapters.map(async (chapter) => {
    const payload = await loadAtlasNodes({
      level: 3,
      route,
      lv1: normalizedBook,
      chapter: chapter.name,
      chapterId: chapter.id,
      signal,
    });
    return {
      chapter,
      sections: Array.isArray(payload?.nodes) ? payload.nodes : [],
    };
  }));
  const validSectionIds = new Set(sectionPages.flatMap((page) => page.sections.map((section) => String(section.id))));
  const completedIds = new Set(
    (Array.isArray(progressPayload?.completed_section_ids) ? progressPayload.completed_section_ids : [])
      .map(String)
      .filter((id) => validSectionIds.has(id)),
  );
  const totalSections = validSectionIds.size;
  const progress = totalSections > 0 ? completedIds.size / totalSections : null;
  const nextPage = sectionPages.find((page) => page.sections.some((section) => !completedIds.has(String(section.id))));
  const nextSection = nextPage?.sections.find((section) => !completedIds.has(String(section.id))) || null;
  const history = Array.isArray(progressPayload?.history) ? progressPayload.history : [];
  const latestHistory = [...history].sort((a, b) => historyTimestamp(b) - historyTimestamp(a))[0] || null;

  return {
    book: normalizedBook,
    route,
    progress,
    completedSections: completedIds.size,
    totalSections,
    lastSectionId: progressPayload?.last_section_id || '',
    lastChapterName: latestHistory?.chapter_name || latestHistory?.chapter || '',
    lastActivityAt: historyTimestamp(latestHistory),
    nextChapterName: nextPage?.chapter?.name || '',
    nextSectionId: nextSection?.id || '',
  };
}

const TEXTBOOK_SNAPSHOT_FRESH_MS = 60 * 1000;
const textbookSnapshotCache = new Map();

function snapshotTarget(book) {
  const name = normalizeBookName(book?.navigation?.book || book?.book || book?.name || book?.title);
  const route = book?.navigation?.route_id || book?.routeId || 'textbook_14_5';
  return { name, route, key: `${route}:${name}` };
}

export function clearTextbookSnapshotCache() {
  textbookSnapshotCache.clear();
}

export function useTextbookLearningSnapshots({ books = [], taskBook = '' } = {}) {
  const [refreshVersion, setRefreshVersion] = useState(0);
  const targets = useMemo(() => {
    const unique = new Map();
    books.forEach((book) => {
      const target = snapshotTarget(book);
      if (target.name) unique.set(target.key, target);
    });
    const normalizedTaskBook = normalizeBookName(taskBook);
    if (normalizedTaskBook) {
      const target = snapshotTarget({ name: normalizedTaskBook });
      if (![...unique.values()].some((item) => item.name === normalizedTaskBook)) unique.set(target.key, target);
    }
    return [...unique.values()];
  }, [books, taskBook]);
  const targetKey = targets.map((target) => target.key).join('|');
  const [snapshots, setSnapshots] = useState({ loading: false, byBook: {} });

  useEffect(() => {
    const refresh = () => setRefreshVersion((version) => version + 1);
    window.addEventListener('focus', refresh);
    return () => window.removeEventListener('focus', refresh);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      await Promise.resolve();
      if (controller.signal.aborted) return;
      const now = Date.now();
      const cachedByBook = {};
      const refreshTargets = [];
      let missingCount = 0;

      targets.forEach((target) => {
        const cached = textbookSnapshotCache.get(target.key);
        if (cached?.data) cachedByBook[target.name] = cached.data;
        if (!cached?.data) missingCount += 1;
        if (!cached?.data || now - cached.updatedAt >= TEXTBOOK_SNAPSHOT_FRESH_MS) refreshTargets.push(target);
      });
      setSnapshots({ loading: missingCount > 0, byBook: cachedByBook });
      if (!refreshTargets.length) return;

      let nextIndex = 0;
      const worker = async () => {
        while (nextIndex < refreshTargets.length && !controller.signal.aborted) {
          const target = refreshTargets[nextIndex];
          nextIndex += 1;
          try {
            const snapshot = await loadTextbookLearningSnapshot(target.name, target.route, { signal: controller.signal });
            textbookSnapshotCache.set(target.key, { data: snapshot, updatedAt: Date.now() });
            if (!controller.signal.aborted) {
              setSnapshots((current) => ({
                ...current,
                byBook: { ...current.byBook, [target.name]: snapshot },
              }));
            }
          } catch (error) {
            if (error?.name === 'AbortError' || controller.signal.aborted) return;
            if (!textbookSnapshotCache.get(target.key)?.data) {
              setSnapshots((current) => ({
                ...current,
                byBook: {
                  ...current.byBook,
                  [target.name]: { book: target.name, route: target.route, progress: null, unavailable: true },
                },
              }));
            }
          }
        }
      };

      await Promise.all(Array.from({ length: Math.min(6, refreshTargets.length) }, () => worker()));
      if (!controller.signal.aborted) setSnapshots((current) => ({ ...current, loading: false }));
    };
    load();
    return () => controller.abort();
  }, [refreshVersion, targetKey, targets]);

  return snapshots;
}

export function useLearningPlanMetrics({ books = [], taskBook = '' } = {}) {
  const [overview, setOverview] = useState({
    loading: true,
    totalFocusMinutes: null,
    recommendedMinutes: null,
  });
  const snapshots = useTextbookLearningSnapshots({ books, taskBook });

  useEffect(() => {
    let controller = null;
    let requestVersion = 0;
    const load = async () => {
      controller?.abort();
      controller = new AbortController();
      const version = ++requestVersion;
      const [statistics, policy] = await Promise.allSettled([
        loadMainApi('/learning-statistics/overview?days=30', controller.signal),
        loadMainApi('/task-load-policy', controller.signal),
      ]);
      if (controller.signal.aborted || version !== requestVersion) return;
      const minutes = Number(policy.status === 'fulfilled' ? policy.value?.recommended_minutes : NaN);
      setOverview({
        loading: false,
        totalFocusMinutes: statistics.status === 'fulfilled'
          ? focusMinutesFromStatistics(statistics.value)
          : null,
        recommendedMinutes: Number.isFinite(minutes) && minutes > 0 ? minutes : null,
      });
    };
    load();
    window.addEventListener('focus', load);
    return () => {
      requestVersion += 1;
      controller?.abort();
      window.removeEventListener('focus', load);
    };
  }, []);

  return { ...overview, snapshots };
}
