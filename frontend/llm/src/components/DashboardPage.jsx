import React, { useEffect, useMemo, useState } from 'react';
import { ArrowRight, BookOpenCheck, Clock3, Route, Sparkles } from 'lucide-react';
import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import DashboardDailyWorkspace from './dashboard/DashboardDailyWorkspace';
import {
  loadExamNodes,
  loadExamTracks,
  loadLearningTarget,
  loadNodeLearnerSummary,
} from './exam-atlas/examAtlasApi';
import KnowledgeTreeDrilldown from './learning-tree/KnowledgeTreeDrilldown';
import LearningPathOverview from './learning-tree/LearningPathOverview';
import PageLoadingSpinner from './PageLoadingSpinner';
import TextbookLibrary from './workshop-textbook/TextbookLibrary';
import { loadTextbookPdfCatalog } from './workshop-textbook/textbookPdfApi';
import { resolveKnowledgeAtlasEnabled } from './knowledge-atlas/knowledgeAtlasFeature';
import { loadAtlasNodes } from './knowledge-atlas/knowledgeAtlasApi';
import {
  adaptClassicRouteBooks,
  adaptPlannedPathNode,
  loadClassicLearningRoute,
  loadClassicLearningRoutes,
  loadPlannedLearningPath,
} from './learning-tree/learningPathApi';
import { loadSectionLearningDetail } from './workshop-textbook/textbookChapterApi';
import {
  formatLearningDuration,
  normalizeBookName,
  selectNextKnowledgePoint,
  useLearningPlanMetrics,
} from './learningPlanDashboard';
import {
  readTeachingResourcesPageCache,
  updateTeachingResourcesPageCache,
} from './teachingResourcesPageCache';
const WORKSHOP_PREFERENCES_KEY = 'learning-workshop.preferences';
const VALID_PATH_MODES = new Set(['personalized', 'classic']);

function normalizedBookName(item) {
  return String(item?.navigation?.book || item?.book || item?.name || item?.title || '')
    .replace(/[《》]/g, '')
    .trim();
}

function readWorkshopPreferences() {
  try {
    const value = JSON.parse(localStorage.getItem(WORKSHOP_PREFERENCES_KEY) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function preferredPathMode(navigationContext, preferences) {
  if (VALID_PATH_MODES.has(navigationContext.pathMode)) return navigationContext.pathMode;
  if (navigationContext.view === 'path') return 'classic';
  if (VALID_PATH_MODES.has(preferences.pathMode)) return preferences.pathMode;
  return 'personalized';
}

function preferredStageId(navigationContext, preferences) {
  return String(
    navigationContext.currentStageId
    || navigationContext.stageId
    || preferences.currentStageId
    || '',
  );
}

function teachingResourcesPageCacheKey(currentUser, navigationContext, pathMode) {
  const userKey = String(
    currentUser?.id
    || currentUser?.user_id
    || currentUser?.username
    || currentUser?.display_name
    || 'anonymous',
  );
  return JSON.stringify([
    userKey,
    pathMode,
    navigationContext.trackId || '',
    navigationContext.currentStageId || navigationContext.stageId || '',
    navigationContext.classicRouteId || navigationContext.routeId || '',
  ]);
}

function selectCurrentStageId(stages, preferredId = '') {
  if (stages.some((stage) => stage.node_id === preferredId)) return preferredId;
  return (
    stages.find((stage) => ['in_progress', 'current'].includes(stage.status))?.node_id
    || stages.find((stage) => stage.status !== 'completed')?.node_id
    || stages[0]?.node_id
    || ''
  );
}

function getTrackId(target, tracks, requestedTrackId) {
  if (requestedTrackId) return requestedTrackId;
  if (target?.exam_track_id) return target.exam_track_id;
  return tracks?.[0]?.track_id || '';
}

export function visibleWorkshopTextbooks({ allTextbooks = [], plannedBooks = [], remainingTextbooks = [], showAllTextbooks = false } = {}) {
  if (!plannedBooks.length) return allTextbooks;
  return showAllTextbooks ? [...plannedBooks, ...remainingTextbooks] : plannedBooks;
}

function getTrackLabel(target, tracks, trackId) {
  return target?.exam_name
    || tracks?.find((track) => track.track_id === trackId)?.title_normalized
    || '';
}

function learnerStatus(summary) {
  if (summary?.status) return summary.status;
  const total = Number(summary?.total_count || 0);
  const completed = Number(summary?.completed_count || 0);
  if (total > 0 && completed >= total) return 'completed';
  if (completed > 0) return 'in_progress';
  return 'next';
}

function buildPathNodes(items, summaries) {
  return items.map((item, index) => {
    const summary = summaries[index] || {};
    return {
      ...item,
      total_count: summary.total_count ?? item.child_count ?? 0,
      completed_count: summary.completed_count ?? 0,
      incomplete_count: summary.incomplete_count ?? 0,
      review_due_count: summary.review_due_count ?? 0,
      average_mastery: summary.average_mastery,
      status: learnerStatus(summary),
    };
  });
}

export default function DashboardPage({
  currentUser,
  navigationContext = {},
  onNavigate,
  onKnowledgeContextChange,
}) {
  const [initialPreferences] = useState(readWorkshopPreferences);
  const [pathMode] = useState(() => preferredPathMode(navigationContext, initialPreferences));
  const teachingResourcesCacheKey = teachingResourcesPageCacheKey(
    currentUser,
    navigationContext,
    pathMode,
  );
  const initialTeachingResourcesCache = readTeachingResourcesPageCache(teachingResourcesCacheKey);
  const hasInitialCurrentTask = Object.prototype.hasOwnProperty.call(
    initialTeachingResourcesCache || {},
    'currentLearningTask',
  );
  const error = '';
  const [track, setTrack] = useState(() => initialTeachingResourcesCache?.track || { id: '', label: '' });
  const [nodes, setNodes] = useState(() => initialTeachingResourcesCache?.nodes || []);
  const [legacyDrilldown, setLegacyDrilldown] = useState(null);
  const [plannedPath, setPlannedPath] = useState(() => initialTeachingResourcesCache?.plannedPath || null);
  const [classicRoutes, setClassicRoutes] = useState(() => initialTeachingResourcesCache?.classicRoutes || []);
  const [classicRouteId, setClassicRouteId] = useState(() => (
    navigationContext.classicRouteId
    || navigationContext.routeId
    || initialTeachingResourcesCache?.classicRouteId
    || initialPreferences.classicRouteId
    || ''
  ));
  const [currentStageId, setCurrentStageId] = useState(() => (
    preferredStageId(navigationContext, initialPreferences)
    || initialTeachingResourcesCache?.currentStageId
    || ''
  ));
  const [classicRoutePayload, setClassicRoutePayload] = useState(
    () => initialTeachingResourcesCache?.classicRoutePayload || null,
  );
  const [plannedBooks, setPlannedBooks] = useState(() => initialTeachingResourcesCache?.plannedBooks || []);
  const [, setClassicBooks] = useState([]);
  const [classicError, setClassicError] = useState('');
  const [allTextbooks, setAllTextbooks] = useState(
    () => initialTeachingResourcesCache?.allTextbooks || [],
  );
  const [uploadedTextbooks, setUploadedTextbooks] = useState(
    () => initialTeachingResourcesCache?.uploadedTextbooks || [],
  );
  const [textbookError, setTextbookError] = useState('');
  const [textbooksLoading, setTextbooksLoading] = useState(
    () => !Array.isArray(initialTeachingResourcesCache?.allTextbooks),
  );
  const [currentLearningTask, setCurrentLearningTask] = useState(
    () => initialTeachingResourcesCache?.currentLearningTask || null,
  );
  const [currentTaskLoading, setCurrentTaskLoading] = useState(() => !hasInitialCurrentTask);
  const [pathLoading, setPathLoading] = useState(
    () => !initialTeachingResourcesCache?.pathReady,
  );
  const [fallbackKnowledgePoint, setFallbackKnowledgePoint] = useState({ sectionId: '', value: '' });  const [showAllTextbooks, setShowAllTextbooks] = useState(
    () => Boolean(navigationContext.expandAll),
  );
  const hidePlan = Boolean(navigationContext.hidePlan || navigationContext.libraryOnly);

  useEffect(() => {
    let cancelled = false;
    const cached = readTeachingResourcesPageCache(teachingResourcesCacheKey);
    const hasCachedCurrentTask = Object.prototype.hasOwnProperty.call(
      cached || {},
      'currentLearningTask',
    );
    if (hasCachedCurrentTask) {
      setCurrentLearningTask(cached.currentLearningTask);
      setCurrentTaskLoading(false);
    } else {
      setCurrentLearningTask(null);
      setCurrentTaskLoading(true);
    }
    fetchWithAuth(`${MAIN_API_BASE}/dashboard/home`)
      .then(async (response) => {
        const payload = await readJsonResponse(response, {});
        if (!response.ok) throw new Error(payload.detail || '学习任务加载失败');
        if (!cancelled) {
          const nextCurrentLearningTask = payload.current_learning_task || null;
          setCurrentLearningTask(nextCurrentLearningTask);
          updateTeachingResourcesPageCache(teachingResourcesCacheKey, {
            currentLearningTask: nextCurrentLearningTask,
          });
          setCurrentTaskLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled && !hasCachedCurrentTask) {
          setCurrentLearningTask(null);
          setCurrentTaskLoading(false);
        }      });
    return () => { cancelled = true; };
  }, [teachingResourcesCacheKey]);

  useEffect(() => {
    const controller = new AbortController();
    const cached = readTeachingResourcesPageCache(teachingResourcesCacheKey);
    if (Array.isArray(cached?.uploadedTextbooks)) {
      setUploadedTextbooks(cached.uploadedTextbooks);
    } else {
      setUploadedTextbooks([]);
    }
    loadTextbookPdfCatalog({ signal: controller.signal })
      .then((payload) => {
        const items = (payload.items || []).filter((item) => item.origin === 'user_upload');
        const nextUploadedTextbooks = items.map((item) => ({
          ...item,
          id: item.book_id,
          book: item.title,
          node_type: 'book',
          title: `《${item.title}》`,
          stage_title: item.category || '用户教材',
          navigation: { book: item.title, book_id: item.book_id, route_id: 'user_textbooks' },
        }));
        setUploadedTextbooks(nextUploadedTextbooks);
        updateTeachingResourcesPageCache(teachingResourcesCacheKey, {
          uploadedTextbooks: nextUploadedTextbooks,
        });
      })
      .catch(() => {});
    return () => controller.abort();
  }, [teachingResourcesCacheKey]);

  useEffect(() => {
    const controller = new AbortController();
    const cached = readTeachingResourcesPageCache(teachingResourcesCacheKey);
    const hasCachedCatalog = Array.isArray(cached?.allTextbooks);
    if (hasCachedCatalog) {
      setAllTextbooks(cached.allTextbooks);
      setTextbookError('');
      setTextbooksLoading(false);
    } else {
      setAllTextbooks([]);
      setTextbookError('');
      setTextbooksLoading(true);
    }
    loadAtlasNodes({ level: 1, route: 'textbook_14_5', signal: controller.signal })
      .then((payload) => {
        const books = (payload.nodes || []).map((node) => ({
          ...node,
          node_type: 'book',
          title: `《${node.name}》`,
          stage_title: '十四五规划教材',
          navigation: {
            action: 'open_textbook_chapters',
            route_id: payload.route || 'textbook_14_5',
            book: node.name,
          },
        }));
        setAllTextbooks(books);
        updateTeachingResourcesPageCache(teachingResourcesCacheKey, { allTextbooks: books });
        setTextbookError('');
        setTextbooksLoading(false);
      })
      .catch((loadError) => {
        if (loadError?.name !== 'AbortError') {
          if (!hasCachedCatalog) {
            setAllTextbooks([]);
            setTextbookError(loadError.message || '教材目录加载失败');
          }
          setTextbooksLoading(false);
        }
      });
    return () => controller.abort();
  }, [teachingResourcesCacheKey]);

  useEffect(() => {
    let cancelled = false;
    const cached = readTeachingResourcesPageCache(teachingResourcesCacheKey);
    const hasCachedPath = Boolean(cached?.pathReady);
    if (hasCachedPath) {
      setTrack(cached.track || { id: '', label: '' });
      setNodes(cached.nodes || []);
      setPlannedBooks(cached.plannedBooks || []);
      setPlannedPath(cached.plannedPath || null);
      setCurrentStageId(cached.currentStageId || '');
      setPathLoading(false);
      if (cached.track?.id) {
        onKnowledgeContextChange?.({
          trackId: cached.track.id,
          ...(cached.plannedPath?.plan_ref?.plan_id
            ? { planId: cached.plannedPath.plan_ref.plan_id }
            : {}),
        });
      }
    } else {
      setTrack({ id: '', label: '' });
      setNodes([]);
      setPlannedBooks([]);
      setPlannedPath(null);
      setPathLoading(true);
    }
    const loadPath = async () => {
      try {
        const [targetRequest, tracksRequest] = await Promise.allSettled([
          loadLearningTarget(),
          loadExamTracks(),
        ]);
        const targetResult = targetRequest.status === 'fulfilled' ? targetRequest.value : {};
        const tracksResult = tracksRequest.status === 'fulfilled' ? tracksRequest.value : {};
        const tracks = Array.isArray(tracksResult?.items) ? tracksResult.items : [];
        const target = targetResult?.target || {};
        const trackId = getTrackId(target, tracks, navigationContext.trackId);
        try {
          const planned = await loadPlannedLearningPath();
          if (cancelled) return;
          const rootNodes = planned.nodes.map(adaptPlannedPathNode);
          const rootStages = rootNodes.filter((node) => node.node_type === 'stage');
          if (cancelled) return;
          const stageBookPages = await Promise.all(rootStages.map(async (stage) => {
            try {
              const page = await loadPlannedLearningPath(stage.node_id);
              return page.nodes.filter((node) => node.node_type === 'book').map((node) => ({
                ...adaptPlannedPathNode(node), stage_title: stage.title, stage_order: stage.order,
              }));
            } catch {
              return [];
            }
          }));
          if (cancelled) return;
          const nextTrack = { id: trackId, label: getTrackLabel(target, tracks, trackId) };
          const nextPlannedBooks = stageBookPages.flat();
          const nextCurrentStageId = selectCurrentStageId(
            rootStages,
            preferredStageId(navigationContext, initialPreferences) || cached?.currentStageId || '',
          );
          setTrack(nextTrack);
          setPlannedBooks(nextPlannedBooks);
          setCurrentStageId(nextCurrentStageId);
          setNodes(rootNodes);
          setPlannedPath(planned);
          updateTeachingResourcesPageCache(teachingResourcesCacheKey, {
            pathReady: true,
            track: nextTrack,
            nodes: rootNodes,
            plannedBooks: nextPlannedBooks,
            plannedPath: planned,
            currentStageId: nextCurrentStageId,
          });
          setLegacyDrilldown(null);
          onKnowledgeContextChange?.({ trackId, planId: planned.plan_ref?.plan_id });
          return;
        } catch {
          // Existing exam-tree data remains a compatibility fallback for users
          // who have not generated a long-term plan yet.
        }
        if (!trackId) {
          setTrack({ id: '', label: '' });
          setNodes([]);
          setPlannedBooks([]);
          setPlannedPath(null);
          setCurrentStageId('');
          updateTeachingResourcesPageCache(teachingResourcesCacheKey, {
            pathReady: true,
            track: { id: '', label: '' },
            nodes: [],
            plannedBooks: [],
            plannedPath: null,
            currentStageId: '',
          });
          return;
        }
        const rootResult = await loadExamNodes(trackId);
        const roots = Array.isArray(rootResult?.items) ? rootResult.items : [];
        const children = (await Promise.all(roots.map(async (root) => {
          const result = await loadExamNodes(trackId, root.membership_id);
          return Array.isArray(result?.items) ? result.items : [];
        }))).flat();
        const summaries = await Promise.all(children.map((node) => loadNodeLearnerSummary(trackId, node.membership_id)));
        if (cancelled) return;
        const nextTrack = { id: trackId, label: getTrackLabel(target, tracks, trackId) };
        const nextNodes = buildPathNodes(children, summaries);
        const nextCurrentStageId = selectCurrentStageId(
          nextNodes,
          preferredStageId(navigationContext, initialPreferences) || cached?.currentStageId || '',
        );
        setTrack(nextTrack);
        setNodes(nextNodes);
        setPlannedBooks([]);
        setPlannedPath(null);
        setCurrentStageId(nextCurrentStageId);
        updateTeachingResourcesPageCache(teachingResourcesCacheKey, {
          pathReady: true,
          track: nextTrack,
          nodes: nextNodes,
          plannedBooks: [],
          plannedPath: null,
          currentStageId: nextCurrentStageId,
        });
        setLegacyDrilldown(null);
        onKnowledgeContextChange?.({ trackId });
      } catch {
        if (!cancelled && !hasCachedPath) {
          setNodes([]);
          setPlannedBooks([]);
        }
      }
    };
    loadPath();
    return () => { cancelled = true; };
  }, [
    initialPreferences,
    onKnowledgeContextChange,
    teachingResourcesCacheKey,
  ]);

  useEffect(() => {
    let cancelled = false;
    const cached = readTeachingResourcesPageCache(teachingResourcesCacheKey);
    if (Array.isArray(cached?.classicRoutes)) {
      setClassicRoutes(cached.classicRoutes);
      setClassicRouteId(cached.classicRouteId || '');
    }
    loadClassicLearningRoutes()
      .then((payload) => {
        if (cancelled) return;
        const routes = payload.items || [];
        setClassicRoutes(routes);
        setClassicRouteId((current) => {
          const nextClassicRouteId = routes.some((route) => route.route_id === current)
            ? current
            : routes[0]?.route_id || '';
          updateTeachingResourcesPageCache(teachingResourcesCacheKey, {
            classicRoutes: routes,
            classicRouteId: nextClassicRouteId,
          });
          return nextClassicRouteId;
        });
      })
      .catch((loadError) => {
        if (!cancelled && !Array.isArray(cached?.classicRoutes)) {
          setClassicError(loadError.message || '经典路线列表加载失败');
        }
      });
    return () => { cancelled = true; };
  }, [teachingResourcesCacheKey]);

  useEffect(() => {
    const selectedRoute = classicRoutes.find((route) => route.route_id === classicRouteId);
    const textbookRouteId = selectedRoute?.textbook_route_id;
    if (!textbookRouteId) return undefined;
    let cancelled = false;
    const cached = readTeachingResourcesPageCache(teachingResourcesCacheKey);
    if (cached?.classicRouteId === classicRouteId && cached?.classicRoutePayload) {
      setClassicRoutePayload(cached.classicRoutePayload);
    }
    loadClassicLearningRoute(textbookRouteId)
      .then((payload) => {
        if (cancelled) return;
        setClassicRoutePayload(payload);
        updateTeachingResourcesPageCache(teachingResourcesCacheKey, {
          classicRouteId,
          classicRoutePayload: payload,
        });
        setClassicBooks(payload.route.stages.flatMap((stage) => adaptClassicRouteBooks(
          payload.route, stage, payload.navigation?.atlas_route_id,
        ).map((book) => ({ ...book, stage_title: stage.name, stage_order: stage.order }))));
      })
      .catch((loadError) => {
        if (cancelled) return;
        if (!(cached?.classicRouteId === classicRouteId && cached?.classicRoutePayload)) {
          setClassicRoutePayload(null);
          setClassicBooks([]);
          setClassicError(loadError.message || '经典路线详情加载失败');
        }
      });
    return () => { cancelled = true; };
  }, [classicRouteId, classicRoutes, teachingResourcesCacheKey]);

  useEffect(() => {
    try {
      localStorage.setItem(WORKSHOP_PREFERENCES_KEY, JSON.stringify({
        pathMode,
        classicRouteId,
        currentStageId,
      }));
    } catch {
      // Storage is optional; private browsing must not block the workshop.
    }
  }, [pathMode, classicRouteId, currentStageId]);

  const legacyPathEdges = useMemo(() => nodes.slice(1).map((node, index) => ({
    from: nodes[index].membership_id, to: node.membership_id, kind: 'spine',
  })), [nodes]);
  const currentStage = useMemo(() => (
    nodes.find((node) => node.node_id === currentStageId)
    || nodes.find((node) => ['in_progress', 'current'].includes(node.status))
    || nodes[0]
    || null
  ), [currentStageId, nodes]);
  const taskBookName = currentLearningTask?.learning_chapter?.book || '';
  const currentPlanBook = useMemo(() => (
    plannedBooks.find((book) => normalizedBookName(book) === taskBookName)
    || plannedBooks.find((book) => ['in_progress', 'current'].includes(book.status))
    || plannedBooks[0]
    || null
  ), [plannedBooks, taskBookName]);
  const planBookNames = useMemo(() => new Set(plannedBooks.map(normalizedBookName)), [plannedBooks]);
  const libraryTextbooks = useMemo(() => {
    const uploadedNames = new Set(uploadedTextbooks.map(normalizedBookName));
    return [...uploadedTextbooks, ...allTextbooks.filter((book) => !uploadedNames.has(normalizedBookName(book)))];
  }, [allTextbooks, uploadedTextbooks]);
  const remainingTextbooks = useMemo(() => (
    libraryTextbooks.filter((book) => !planBookNames.has(normalizedBookName(book)))
  ), [libraryTextbooks, planBookNames]);
  const visibleTextbooks = useMemo(() => visibleWorkshopTextbooks({
    allTextbooks: libraryTextbooks, plannedBooks, remainingTextbooks, showAllTextbooks,
  }), [libraryTextbooks, plannedBooks, remainingTextbooks, showAllTextbooks]);
  const currentStageBooks = useMemo(() => (
    currentStage
      ? plannedBooks.filter((book) => book.parent_id === currentStage.node_id)
      : []
  ), [currentStage, plannedBooks]);
  const snapshotBooks = useMemo(() => (
    pathLoading ? currentStageBooks : allTextbooks
  ), [allTextbooks, currentStageBooks, pathLoading]);
  const learningMetrics = useLearningPlanMetrics({
    books: snapshotBooks,
    taskBook: taskBookName,
    cacheKey: teachingResourcesCacheKey,
  });
  const mostRecentlyStudiedBook = useMemo(() => currentStageBooks.reduce((latest, book) => {
    const snapshot = learningMetrics.snapshots.byBook[normalizedBookName(book)];
    if (!snapshot?.lastActivityAt) return latest;
    return !latest || snapshot.lastActivityAt > latest.lastActivityAt
      ? { book, lastActivityAt: snapshot.lastActivityAt }
      : latest;
  }, null)?.book || null, [currentStageBooks, learningMetrics.snapshots.byBook]);
  const currentBookName = taskBookName || normalizedBookName(currentPlanBook);
  const currentChapter = currentLearningTask?.learning_chapter?.title || '';
  const currentBookProgress = Number(currentPlanBook?.progress || 0);
  const currentBookStatus = currentBookProgress > 0
    ? `${Math.round(currentBookProgress * 100)}%`
    : currentPlanBook?.status === 'completed' ? '已完成' : '学习中';

  const openKnowledgePlanet = async (node) => {
    if (pathMode === 'classic') {
      if (node.node_type === 'book') {
        onNavigate?.({
          page: 'practice',
          params: {
            view: 'textbook-chapters',
            route: node.navigation?.route_id || 'textbook_14_5',
            lv1: node.navigation?.book || node.title.replace(/[《》]/g, ''),
            source: 'classic-learning-route',
            routeId: classicRoutePayload?.route?.route_id || classicRouteId,
          },
        });
      }
      return;
    }
    if (plannedPath && node.node_type === 'book') {
      const navigation = node.navigation || {};
      onNavigate?.({
        page: 'practice',
        params: {
          view: 'textbook-chapters',
          route: navigation.route_id || 'textbook_14_5',
          lv1: navigation.book || node.title.replace(/[《》]/g, ''),
          source: 'learning-plan',
        },
      });
      return;
    }
    if (!track.id) return;
    if (await resolveKnowledgeAtlasEnabled()) {
      onNavigate?.({
        page: 'knowledge',
        params: {
          view: 'atlas',
          trackId: track.id,
          membershipId: node.membership_id,
          source: 'dashboard',
        },
      });
      return;
    }
    setLegacyDrilldown(node);
  };

  const openTextbook = (node) => {
    const name = normalizedBookName(node);
    onNavigate?.({
      page: 'practice',
      params: {
        view: 'textbook-chapters',
        // 学习工坊教材目录来自全量教材路线，不能沿用考试路线的筛选 route。
        route: 'textbook_14_5',
        lv1: name,
        bookId: node.book_id || node.navigation?.book_id || '',
        uploaded: node.origin === 'user_upload',
        source: 'textbook-library',
      },
    });
  };

  const continueCurrentPlan = () => {
    if (currentPlanBook) {
      openTextbook(currentPlanBook);
      return;
    }
    if (taskBookName) {
      openTextbook({ name: taskBookName, navigation: { route_id: 'textbook_14_5', book: taskBookName } });
    }
  };

  if (legacyDrilldown) {
    return (
      <KnowledgeTreeDrilldown
        trackId={track.id}
        rootNode={legacyDrilldown}
        onBack={() => setLegacyDrilldown(null)}
        onNavigate={onNavigate}
      />
    );
  }

  return (
    <>
      {error && <div role="alert" className="dashboard-daily__error">{error}</div>}
      <DashboardDailyWorkspace
        libraryOnly
        pathContent={(
          <div className="workshop-library-page">
              {textbooksLoading ? (
                <PageLoadingSpinner className="workshop-library-page__loading" label="正在加载教材目录" />
              ) : libraryTextbooks.length > 0 ? (
                <>
                  {!hidePlan && <section className="workshop-plan" aria-label="当前学习计划">
                    <div className="workshop-plan__summary">
                      <span><Route aria-hidden="true" size={15} />Learning plan</span>
                      <h1>学习计划</h1>
                      <p>{currentStage?.description || plannedPath?.message || '结合你的长期目标，按计划教材循序推进学习。'}</p>
                      <div className="workshop-plan__meta">
                        <span>{plannedPath?.plan_ref?.plan_id ? `计划 ${plannedPath.plan_ref.plan_id}` : '当前学习计划'}</span>
                        <span>{currentStage ? `当前阶段：${currentStage.title}` : '等待生成学习阶段'}</span>
                        <span>{plannedBooks.length} 本计划教材</span>
                      </div>
                    </div>
                    <div className={`workshop-plan__focus${currentBookName ? '' : ' is-awaiting-plan'}`}>
                      <span><Sparkles aria-hidden="true" size={14} />现在继续</span>
                      {currentBookName ? (
                        <>
                          <h2>该继续学习《{currentBookName}》</h2>
                          <p>{currentChapter ? `当前任务：${currentChapter}` : currentLearningTask?.title || currentPlanBook?.description || '从当前计划教材继续学习。'}</p>
                          <div className="workshop-plan__progress">
                            <div><i style={{ width: currentBookProgress > 0 ? `${Math.round(currentBookProgress * 100)}%` : '12%' }} /></div>
                            <strong>{currentBookStatus}</strong>
                            {currentLearningTask?.duration && <small><Clock3 aria-hidden="true" size={13} />预计 {currentLearningTask.duration}</small>}
                          </div>
                          <button type="button" onClick={continueCurrentPlan}>
                            <BookOpenCheck aria-hidden="true" size={17} />点击继续学习<ArrowRight aria-hidden="true" size={16} />
                          </button>
                        </>
                      ) : (
                        <>
                          <h2>先制定你的长期学习计划</h2>
                          <p>完成计划后，这里会提醒当前阶段和下一本教材。</p>
                          <button type="button" onClick={() => onNavigate?.({ page: 'assistant', params: { context: '请结合我的学习状态，给我制定一份长期学习规划。' } })}>去制定学习计划</button>
                        </>
                      )}
                    </div>
                  </section>}
                  <TextbookLibrary
                    books={visibleTextbooks}
                    emptyText="当前计划暂未匹配到教材"
                    onOpen={openTextbook}
                    remainingCount={plannedBooks.length > 0 && !showAllTextbooks ? remainingTextbooks.length : 0}
                    onExpandAll={() => setShowAllTextbooks(true)}
                    onUploaded={(bookItem) => {
                      if (!bookItem) return;
                      setUploadedTextbooks((current) => [{
                        ...bookItem,
                        id: bookItem.book_id,
                        book: bookItem.title,
                        node_type: 'book',
                        title: `《${bookItem.title}》`,
                        stage_title: bookItem.category || '用户教材',
                        navigation: { book: bookItem.title, book_id: bookItem.book_id, route_id: 'user_textbooks' },
                      }, ...current.filter((item) => item.book_id !== bookItem.book_id)]);
                      setShowAllTextbooks(true);
                    }}
                  />
                </>
              ) : textbookError ? (
                <div className="dashboard-daily__path-empty"><p>{textbookError}</p></div>
              ) : pathMode === 'classic' ? (
                <div className="dashboard-daily__path-empty" data-state="classic-route-unavailable">
                  <p>{classicError || '经典路线正在准备中。'}</p>
                </div>
              ) : !plannedPath && nodes.length > 0 ? (
                <LearningPathOverview
                  nodes={nodes}
                  edges={legacyPathEdges}
                  onSelect={openKnowledgePlanet}
                  onDrill={openKnowledgePlanet}
                />
              ) : plannedPath?.availability === 'requires_long_term_plan' ? (
                <div className="dashboard-daily__path-empty" data-state="requires-long-term-plan">
                  <p>{plannedPath.message || '请先完成长期学习规划，再生成阶段、教材和知识点路径。'}</p>
                  <button
                    type="button"
                    onClick={() => onNavigate?.({
                      page: 'assistant',
                      params: { context: '请结合我的学习状态，给我制定一份长期学习规划。' },
                    })}
                  >
                    去制定长期规划
                  </button>
                </div>
              ) : <div className="dashboard-daily__path-empty">知识路径正在准备中</div>}
          </div>
        )}
      />
    </>
  );
}
