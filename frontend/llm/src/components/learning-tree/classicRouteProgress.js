import {
  loadCachedTextbookLearningSnapshot,
  normalizeBookName,
} from '../learningPlanDashboard';
import {
  adaptClassicRouteBooks,
  adaptPlannedPathNode,
  loadPlannedLearningPath,
} from './learningPathApi';

function normalizedProgress(snapshot) {
  const value = Number(snapshot?.progress);
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : null;
}

function sequencedStatuses(progressValues, preferredCurrentIndex = -1) {
  const statuses = progressValues.map((progress) => (progress === 1 ? 'completed' : 'locked'));
  const activeIndex = progressValues.findIndex((progress) => progress != null && progress > 0 && progress < 1);
  const currentIndex = preferredCurrentIndex >= 0 && statuses[preferredCurrentIndex] !== 'completed'
    ? preferredCurrentIndex
    : activeIndex >= 0
    ? activeIndex
    : statuses.findIndex((status) => status !== 'completed');
  if (currentIndex >= 0) {
    statuses[currentIndex] = 'in_progress';
    if (currentIndex + 1 < statuses.length) statuses[currentIndex + 1] = 'next';
  }
  return statuses;
}

export function applyProgressToBookNodes(nodes, snapshotsByBook = {}) {
  const progressValues = nodes.map((node) => (
    normalizedProgress(snapshotsByBook[normalizeBookName(node?.navigation?.book || node?.title)])
  ));
  const statuses = sequencedStatuses(progressValues);
  return nodes.map((node, index) => {
    const progress = progressValues[index];
    return {
      ...node,
      status: statuses[index] || 'unassessed',
      completed_count: progress === 1 ? 1 : 0,
      incomplete_count: progress === 1 ? 0 : 1,
      average_mastery: progress == null ? null : Math.round(progress * 100),
    };
  });
}

export const applyProgressToClassicBooks = applyProgressToBookNodes;

export function classicBookNodesWithProgress(
  route,
  stage,
  atlasRouteId,
  snapshotsByBook = {},
) {
  return applyProgressToBookNodes(
    adaptClassicRouteBooks(route, stage, atlasRouteId),
    snapshotsByBook,
  );
}

function stageMetric(bookNodes, snapshotsByBook) {
  const values = bookNodes.map((book) => (
    normalizedProgress(snapshotsByBook[normalizeBookName(book?.navigation?.book || book?.title)])
  ));
  const completed = values.filter((progress) => progress === 1).length;
  const measured = values.filter((progress) => progress != null);
  return {
    total: values.length,
    completed,
    average: measured.length
      ? Math.round((measured.reduce((sum, progress) => sum + progress, 0) / measured.length) * 100)
      : null,
    complete: values.length > 0 && completed === values.length,
  };
}

function stagesWithMetrics(routeState, stageMetrics, snapshotsByBook) {
  const progressValues = routeState.nodes.map((node, index) => {
    const metric = stageMetrics[index];
    if (node.status === 'completed' || metric?.complete) return 1;
    if (metric?.average > 0) return metric.average / 100;
    if (node.status === 'in_progress') return 0.001;
    return 0;
  });
  const plannedCurrentIndex = routeState.classicRoute
    ? -1
    : routeState.nodes.findIndex((node) => node.status === 'in_progress');
  const stageStatuses = sequencedStatuses(progressValues, plannedCurrentIndex);
  const nodes = routeState.nodes.map((node, index) => {
    const metric = stageMetrics[index] || { total: 0, completed: 0, average: null };
    return {
      ...node,
      status: stageStatuses[index] || node.status || 'unassessed',
      completed_count: metric.completed,
      incomplete_count: Math.max(0, metric.total - metric.completed),
      average_mastery: metric.average,
    };
  });
  const statusById = new Map(nodes.map((node) => [String(node.node_id), node]));
  const stages = routeState.stages.map((stage) => {
    const node = statusById.get(String(stage.node_id)) || stage;
    return {
      ...stage,
      ...node,
      level: node.status === 'completed'
        ? '已完成'
        : node.status === 'in_progress'
          ? '当前阶段'
          : node.status === 'next'
            ? '下一阶段'
            : '待学习',
    };
  });
  return { ...routeState, nodes, stages, bookProgress: snapshotsByBook };
}

export function applyClassicRouteProgress(routeState, snapshotsByBook = {}) {
  const classicStages = routeState?.classicRoute?.stages || [];
  const stageMetrics = classicStages.map((stage) => {
    const values = (stage.books || []).map((book) => (
      normalizedProgress(snapshotsByBook[normalizeBookName(book)])
    ));
    const completed = values.filter((progress) => progress === 1).length;
    const measured = values.filter((progress) => progress != null);
    return {
      total: values.length,
      completed,
      average: measured.length
        ? Math.round((measured.reduce((sum, progress) => sum + progress, 0) / measured.length) * 100)
        : null,
      complete: values.length > 0 && completed === values.length,
    };
  });
  return stagesWithMetrics(routeState, stageMetrics, snapshotsByBook);
}

export function applyPlannedRouteProgress(
  routeState,
  stageBooksById = {},
  snapshotsByBook = {},
) {
  const stageMetrics = routeState.nodes.map((node) => (
    stageMetric(stageBooksById[String(node.node_id)] || [], snapshotsByBook)
  ));
  return stagesWithMetrics(routeState, stageMetrics, snapshotsByBook);
}

async function loadSnapshotsForBookNodes(
  bookNodes,
  snapshotsByBook,
  { signal, userCacheKey, force, concurrency },
) {
  let nextIndex = 0;
  const worker = async () => {
    while (nextIndex < bookNodes.length && !signal?.aborted) {
      const node = bookNodes[nextIndex];
      nextIndex += 1;
      const book = normalizeBookName(node?.navigation?.book || node?.title);
      if (!book) continue;
      try {
        snapshotsByBook[book] = await loadCachedTextbookLearningSnapshot(
          book,
          node?.navigation?.route_id || 'textbook_14_5',
          { signal, cacheKey: userCacheKey, force },
        );
      } catch (error) {
        if (error?.name === 'AbortError' || signal?.aborted) return;
      }
    }
  };
  await Promise.all(Array.from(
    { length: Math.min(Math.max(1, concurrency), Math.max(1, bookNodes.length)) },
    () => worker(),
  ));
}

export async function loadPlannedRouteProgress(
  routeState,
  { signal, userCacheKey = 'anonymous', force = true, concurrency = 4 } = {},
) {
  if (routeState?.classicRoute) return routeState;
  const stageBooksById = {};
  await Promise.all(routeState.nodes.map(async (stage) => {
    try {
      const payload = await loadPlannedLearningPath(stage.node_id);
      stageBooksById[String(stage.node_id)] = (payload.nodes || [])
        .filter((node) => node.node_type === 'book')
        .map(adaptPlannedPathNode);
    } catch {
      stageBooksById[String(stage.node_id)] = [];
    }
  }));
  if (signal?.aborted) return routeState;
  const bookNodes = Object.values(stageBooksById).flat();
  const snapshotsByBook = { ...(routeState.bookProgress || {}) };
  await loadSnapshotsForBookNodes(bookNodes, snapshotsByBook, {
    signal,
    userCacheKey,
    force,
    concurrency,
  });
  if (signal?.aborted) return routeState;
  return applyPlannedRouteProgress(routeState, stageBooksById, snapshotsByBook);
}

export async function loadClassicRouteProgress(
  routeState,
  { signal, userCacheKey = 'anonymous', force = true, concurrency = 4 } = {},
) {
  const route = routeState?.classicRoute;
  if (!route) return routeState;
  const atlasRouteId = routeState.atlasRouteId || 'textbook_14_5';
  const books = [...new Set(
    route.stages.flatMap((stage) => stage.books || []).map(normalizeBookName).filter(Boolean),
  )];
  const snapshotsByBook = { ...(routeState.bookProgress || {}) };
  let nextIndex = 0;
  const worker = async () => {
    while (nextIndex < books.length && !signal?.aborted) {
      const book = books[nextIndex];
      nextIndex += 1;
      try {
        snapshotsByBook[book] = await loadCachedTextbookLearningSnapshot(book, atlasRouteId, {
          signal,
          cacheKey: userCacheKey,
          force,
        });
      } catch (error) {
        if (error?.name === 'AbortError' || signal?.aborted) return;
      }
    }
  };
  await Promise.all(Array.from(
    { length: Math.min(Math.max(1, concurrency), Math.max(1, books.length)) },
    () => worker(),
  ));
  if (signal?.aborted) return routeState;
  return applyClassicRouteProgress(routeState, snapshotsByBook);
}
