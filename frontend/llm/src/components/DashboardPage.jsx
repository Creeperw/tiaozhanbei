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
import TextbookLibrary from './workshop-textbook/TextbookLibrary';
import { resolveKnowledgeAtlasEnabled } from './knowledge-atlas/knowledgeAtlasFeature';
import { loadAtlasNodes } from './knowledge-atlas/knowledgeAtlasApi';
import {
  adaptClassicRouteBooks,
  adaptPlannedPathNode,
  loadClassicLearningRoute,
  loadClassicLearningRoutes,
  loadPlannedLearningPath,
} from './learning-tree/learningPathApi';

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
  navigationContext = {},
  onNavigate,
  onKnowledgeContextChange,
}) {
  const error = '';
  const [track, setTrack] = useState({ id: '', label: '' });
  const [nodes, setNodes] = useState([]);
  const [legacyDrilldown, setLegacyDrilldown] = useState(null);
  const [plannedPath, setPlannedPath] = useState(null);
  const [initialPreferences] = useState(readWorkshopPreferences);
  const [pathMode] = useState(() => preferredPathMode(navigationContext, initialPreferences));
  const [classicRoutes, setClassicRoutes] = useState([]);
  const [classicRouteId, setClassicRouteId] = useState(() => (
    navigationContext.classicRouteId || navigationContext.routeId || initialPreferences.classicRouteId || ''
  ));
  const [currentStageId, setCurrentStageId] = useState(() => (
    preferredStageId(navigationContext, initialPreferences)
  ));
  const [classicRoutePayload, setClassicRoutePayload] = useState(null);
  const [plannedBooks, setPlannedBooks] = useState([]);
  const [, setClassicBooks] = useState([]);
  const [classicError, setClassicError] = useState('');
  const [allTextbooks, setAllTextbooks] = useState([]);
  const [textbookError, setTextbookError] = useState('');
  const [textbooksLoading, setTextbooksLoading] = useState(true);
  const [currentLearningTask, setCurrentLearningTask] = useState(null);
  const [showAllTextbooks, setShowAllTextbooks] = useState(
    () => Boolean(navigationContext.expandAll),
  );
  const hidePlan = Boolean(navigationContext.hidePlan || navigationContext.libraryOnly);

  useEffect(() => {
    let cancelled = false;
    fetchWithAuth(`${MAIN_API_BASE}/dashboard/home`)
      .then(async (response) => {
        const payload = await readJsonResponse(response, {});
        if (!response.ok) throw new Error(payload.detail || '学习任务加载失败');
        if (!cancelled) setCurrentLearningTask(payload.current_learning_task || null);
      })
      .catch(() => {
        if (!cancelled) setCurrentLearningTask(null);
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
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
        setTextbookError('');
        setTextbooksLoading(false);
      })
      .catch((loadError) => {
        if (loadError?.name !== 'AbortError') {
          setAllTextbooks([]);
          setTextbookError(loadError.message || '教材目录加载失败');
          setTextbooksLoading(false);
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    let cancelled = false;
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
          setTrack({ id: trackId, label: getTrackLabel(target, tracks, trackId) });
          setPlannedBooks(stageBookPages.flat());
          setCurrentStageId((current) => {
            if (rootStages.some((stage) => stage.node_id === current)) return current;
            return (
              rootStages.find((stage) => ['in_progress', 'current'].includes(stage.status))?.node_id
              || rootStages.find((stage) => stage.status !== 'completed')?.node_id
              || rootStages[0]?.node_id
              || ''
            );
          });
          setNodes(rootNodes);
          setPlannedPath(planned);
          setLegacyDrilldown(null);
          onKnowledgeContextChange?.({ trackId, planId: planned.plan_ref?.plan_id });
          return;
        } catch {
          // Existing exam-tree data remains a compatibility fallback for users
          // who have not generated a long-term plan yet.
        }
        if (!trackId) return;
        const rootResult = await loadExamNodes(trackId);
        const roots = Array.isArray(rootResult?.items) ? rootResult.items : [];
        const children = (await Promise.all(roots.map(async (root) => {
          const result = await loadExamNodes(trackId, root.membership_id);
          return Array.isArray(result?.items) ? result.items : [];
        }))).flat();
        const summaries = await Promise.all(children.map((node) => loadNodeLearnerSummary(trackId, node.membership_id)));
        if (cancelled) return;
        setTrack({ id: trackId, label: getTrackLabel(target, tracks, trackId) });
        setNodes(buildPathNodes(children, summaries));
        setPlannedBooks([]);
        setPlannedPath(null);
        setLegacyDrilldown(null);
        onKnowledgeContextChange?.({ trackId });
      } catch {
        if (!cancelled) {
          setNodes([]);
          setPlannedBooks([]);
        }
      }
    };
    loadPath();
    return () => { cancelled = true; };
  }, [navigationContext.stageId, navigationContext.trackId, onKnowledgeContextChange]);

  useEffect(() => {
    let cancelled = false;
    loadClassicLearningRoutes()
      .then((payload) => {
        if (cancelled) return;
        const routes = payload.items || [];
        setClassicRoutes(routes);
        setClassicRouteId((current) => (
          routes.some((route) => route.route_id === current)
            ? current
            : routes[0]?.route_id || ''
        ));
      })
      .catch((loadError) => {
        if (!cancelled) setClassicError(loadError.message || '经典路线列表加载失败');
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const selectedRoute = classicRoutes.find((route) => route.route_id === classicRouteId);
    const textbookRouteId = selectedRoute?.textbook_route_id;
    if (!textbookRouteId) return undefined;
    let cancelled = false;
    loadClassicLearningRoute(textbookRouteId)
      .then((payload) => {
        if (cancelled) return;
        setClassicRoutePayload(payload);
        setClassicBooks(payload.route.stages.flatMap((stage) => adaptClassicRouteBooks(
          payload.route, stage, payload.navigation?.atlas_route_id,
        ).map((book) => ({ ...book, stage_title: stage.name, stage_order: stage.order }))));
      })
      .catch((loadError) => {
        if (cancelled) return;
        setClassicRoutePayload(null);
        setClassicBooks([]);
        setClassicError(loadError.message || '经典路线详情加载失败');
      });
    return () => { cancelled = true; };
  }, [classicRouteId, classicRoutes]);

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
  const remainingTextbooks = useMemo(() => (
    allTextbooks.filter((book) => !planBookNames.has(normalizedBookName(book)))
  ), [allTextbooks, planBookNames]);
  const visibleTextbooks = useMemo(() => visibleWorkshopTextbooks({
    allTextbooks, plannedBooks, remainingTextbooks, showAllTextbooks,
  }), [allTextbooks, plannedBooks, remainingTextbooks, showAllTextbooks]);
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
                <div className="dashboard-daily__path-empty">教材目录正在准备中</div>
              ) : allTextbooks.length > 0 ? (
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
                    <div className="workshop-plan__focus">
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
