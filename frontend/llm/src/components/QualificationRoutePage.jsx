import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BookOpenText,
  CalendarDays,
  CalendarCheck2,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Plus,
} from 'lucide-react';
import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import DailyTaskCountdown from './daily-task/DailyTaskCountdown';
import LearningStageLanding from './learning-stage/LearningStageLanding';
import LearningPathOverview from './learning-tree/LearningPathOverview';
import {
  adaptClassicRouteBooks,
  adaptClassicRouteStage,
  adaptPlannedPathNode,
  loadClassicLearningRoute,
  loadPlannedLearningPath,
} from './learning-tree/learningPathApi';
import { loadLearningTarget } from './exam-atlas/examAtlasApi';
import {
  EMPTY_HOME_PAYLOAD,
  buildHomePortalState,
} from '../homePortal';
import { workshopActionIntent } from '../pageIntent';
import {
  readQualificationPageCache,
  readQualificationRouteCache,
  updateQualificationPageCache,
  updateQualificationRouteCache,
} from './qualificationRoutePageCache';

function formatReviewDate(value) {
  if (!value) return '最近';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '最近';
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

const DEFAULT_EXAM_DATE = '2026-11-29T23:59:59+08:00';
const LEARNING_TARGET_CHANGED_EVENT = 'shizhen:learning-target-changed';
const CONTENT_FADE_DURATION = 480;

function currentUserCacheKey(currentUser) {
  return String(currentUser?.id || currentUser?.user_id || currentUser?.username || currentUser?.display_name || 'anonymous');
}

function updatePageCache(key, patch) {
  updateQualificationPageCache(key, patch);
}

function routeCacheKey(userKey, routeKey) {
  return `${userKey}:${routeKey}`;
}

function samePayload(left, right) {
  try {
    return JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
}


function splitHeroTitle(value) {
  const text = String(value || '');
  const marker = '继续学习';
  const markerIndex = text.indexOf(marker);
  if (markerIndex < 0) return { lead: text, title: '' };
  return {
    lead: text.slice(0, markerIndex + marker.length),
    title: text.slice(markerIndex + marker.length).trim(),
  };
}

function HeroTypewriter({ title, subtitle }) {
  const [typedText, setTypedText] = useState('');
  const typedTextRef = useRef('');
  const fullWord = String(title || '').trim();

  useEffect(() => {
    let cancelled = false;
    let timer;
    let cursor = 0;
    const previousText = typedTextRef.current;
    const limit = Math.min(previousText.length, fullWord.length);
    while (cursor < limit && previousText[cursor] === fullWord[cursor]) cursor += 1;
    if (!fullWord) return undefined;
    const remainingLength = fullWord.length - cursor;
    const stepDelay = Math.max(12, Math.floor(850 / Math.max(remainingLength - 1, 1)));
    const tick = () => {
      if (cancelled) return;
      cursor += 1;
      const nextText = fullWord.slice(0, cursor);
      typedTextRef.current = nextText;
      setTypedText(nextText);
      if (cursor < fullWord.length) timer = window.setTimeout(tick, stepDelay);
    };

    timer = window.setTimeout(tick, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [fullWord]);

  const isTyping = typedText.length < fullWord.length;
  const typedSegments = splitHeroTitle(typedText);
  return (
    <div className="home-portal__hero-copy">
      <h1 className="home-portal__hero-title" id="home-portal-title" aria-label={fullWord}>
        <span className="home-portal__hero-title-lead">{typedSegments.lead}</span>
        <span className="home-portal__hero-title-accent">{typedSegments.title}</span>
        {isTyping && <span className="home-portal__hero-caret" aria-hidden="true" />}
      </h1>
      <span className="home-portal__hero-desc">{subtitle}</span>
    </div>
  );
}

function examCountdown(targetDate) {
  const parsed = targetDate ? new Date(targetDate) : null;
  if (!parsed || Number.isNaN(parsed.getTime())) return null;
  return Math.max(0, Math.ceil((parsed.getTime() - Date.now()) / 86400000));
}

function examDateForTarget(target) {
  return target?.exam_date || target?.examDate || DEFAULT_EXAM_DATE;
}

function normalizePercent(value) {
  if (typeof value === 'string' && value.trim().endsWith('%')) {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? Math.max(0, Math.min(100, parsed)) : null;
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  const percent = parsed >= 0 && parsed <= 1 ? parsed * 100 : parsed;
  return Math.max(0, Math.min(100, percent));
}

function readProgress(item, fallback = null) {
  for (const key of ['progress_percent', 'progress', 'completion_rate', 'completion', 'rate', 'coverage_rate', 'coverage', 'video_coverage']) {
    const value = normalizePercent(item?.[key]);
    if (value !== null) return value;
  }
  return fallback;
}

const WEEKDAY_LABELS = ['日', '一', '二', '三', '四', '五', '六'];

function localDateKey(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function startOfMonth(value) {
  const date = value instanceof Date ? value : new Date(value);
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function calendarDaysForMonth(monthDate) {
  const first = startOfMonth(monthDate);
  const gridStart = new Date(first.getFullYear(), first.getMonth(), 1 - first.getDay());
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(gridStart);
    date.setDate(gridStart.getDate() + index);
    return date;
  });
}

function isCompletedPlanItem(item) {
  const status = String(item?.raw?.status || item?.raw?.completion_status || '').toLowerCase();
  return ['completed', 'complete', 'done', 'finished'].includes(status)
    || Number(item?.progress) >= 100;
}

function CurrentLearningPlan({
  currentTask,
  items,
  studyDays,
  timer,
  onExpire,
  onOpenItem,
  onAddTask,
}) {
  const today = localDateKey();
  const [visibleMonth, setVisibleMonth] = useState(() => startOfMonth(new Date()));
  const calendarDays = useMemo(() => calendarDaysForMonth(visibleMonth), [visibleMonth]);
  const todayItems = items.filter((item) => item.source === 'daily_task');
  const completedFromItems = todayItems.filter(isCompletedPlanItem).length;
  const total = Math.max(Number(currentTask?.progress?.total || 0), todayItems.length);
  const completed = Math.min(total, Math.max(Number(currentTask?.progress?.completed || 0), completedFromItems));
  const progress = total > 0 ? (completed / total) * 100 : 0;
  const learnedDates = useMemo(() => new Set(studyDays), [studyDays]);

  const changeMonth = (offset) => {
    setVisibleMonth((current) => new Date(current.getFullYear(), current.getMonth() + offset, 1));
  };

  return (
    <div className="home-plan" aria-label="当前学习计划">
      <aside className="home-study-calendar" aria-label="学习日历">
        <header>
          <h3><CalendarDays aria-hidden="true" size={22} />学习日历</h3>
          <div>
            <button type="button" aria-label="上个月" onClick={() => changeMonth(-1)}><ChevronLeft aria-hidden="true" size={17} /></button>
            <strong>{visibleMonth.getFullYear()}年{visibleMonth.getMonth() + 1}月</strong>
            <button type="button" aria-label="下个月" onClick={() => changeMonth(1)}><ChevronRight aria-hidden="true" size={17} /></button>
          </div>
        </header>
        <div className="home-study-calendar__weekdays" aria-hidden="true">
          {WEEKDAY_LABELS.map((label) => <span key={label}>{label}</span>)}
        </div>
        <div className="home-study-calendar__grid">
          {calendarDays.map((date) => {
            const key = localDateKey(date);
            const learned = learnedDates.has(key);
            const isToday = key === today;
            const outside = date.getMonth() !== visibleMonth.getMonth();
            const label = `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日${isToday ? '，今天' : learned ? '，已学习' : ''}`;
            return (
              <span
                key={key}
                data-learned={String(learned)}
                data-today={String(isToday)}
                data-outside={String(outside)}
                aria-label={label}
              >
                {date.getDate()}
              </span>
            );
          })}
        </div>
        <footer className="home-study-calendar__legend">
          <span><i data-state="today" />今天</span>
          <span><i data-state="learned" />已学习</span>
        </footer>
      </aside>

      <section className="home-today-card" aria-label="今日任务" data-task-count={todayItems.length}>
        <header className="home-today-card__header">
          <h3><CalendarCheck2 aria-hidden="true" size={22} />今日任务</h3>
          <div className="home-today-card__progress" aria-label={`今日任务完成 ${completed}/${total}`}>
            <strong>{completed}/{total}</strong>
            <span><i style={{ width: `${progress}%` }} /></span>
          </div>
        </header>

        <div className="home-today-card__list">
          {todayItems.map((item) => {
            const itemCompleted = isCompletedPlanItem(item);
            const minutes = Number(item.raw?.estimated_minutes || 0);
            return (
              <button
                key={item.id}
                type="button"
                className="home-today-card__task"
                data-status={itemCompleted ? 'completed' : 'pending'}
                onClick={() => onOpenItem?.(item)}
              >
                <span className="home-today-card__check" aria-label={itemCompleted ? '已完成' : '未完成'}>
                  {itemCompleted ? <Check aria-hidden="true" size={15} /> : null}
                </span>
                <strong>{item.title}</strong>
                <small><Clock3 aria-hidden="true" size={14} />{minutes > 0 ? `${minutes}分钟` : item.meta || '待安排'}</small>
              </button>
            );
          })}
          {todayItems.length === 0 && (
            <div className="home-today-card__empty">
              <BookOpenText aria-hidden="true" size={22} />
              <strong>今天还没有学习任务</strong>
              <p>可让智能助教结合当前阶段安排任务。</p>
            </div>
          )}
        </div>

        <button type="button" className="home-today-card__add" onClick={onAddTask}>
          <Plus aria-hidden="true" size={17} />添加新任务
        </button>
        <DailyTaskCountdown timer={timer} onExpire={onExpire} className="home-plan__refresh-timer" />
      </section>
    </div>
  );
}

function buildLearningRouteState(payload) {
  const classicRoute = payload?.route && Array.isArray(payload.route.stages) ? payload.route : null;
  const nodes = classicRoute
    ? classicRoute.stages.map((stage) => adaptClassicRouteStage(classicRoute, stage))
    : Array.isArray(payload?.nodes) ? payload.nodes.map(adaptPlannedPathNode) : [];
  const stages = nodes
    .filter((node) => node.node_type === 'stage')
    .map((node) => ({
      ...node,
      id: node.node_id,
      nodeId: node.node_id,
      level: node.status === 'in_progress' ? '当前阶段' : node.status === 'completed' ? '已完成' : '待学习',
      title: node.title,
      duration: node.child_count ? `${node.child_count} 本教材` : '教材待规划',
      tasks: [node.description || '阶段目标待补充'],
      resources: [],
    }));
  return {
    loading: false,
    error: '',
    nodes,
    stages,
    classicRoute,
    atlasRouteId: payload?.navigation?.atlas_route_id || 'textbook_14_5',
  };
}

function HomeLearningRoute({
  onNavigate,
  onCurrentProgress,
  onReadyChange,
  contentReady,
  userCacheKey,
  selectedTarget,
}) {
  const [routeView, setRouteView] = useState('cards');
  const [renderedRouteView, setRenderedRouteView] = useState('cards');
  const [routeTransitionPhase, setRouteTransitionPhase] = useState('idle');
  const routeTransitionTimerRef = useRef(null);
  const [routeState, setRouteState] = useState(() => {
    const initialRouteKey = selectedTarget?.textbook_route_id || '__planned__';
    const cached = readQualificationRouteCache(routeCacheKey(userCacheKey, initialRouteKey));
    return cached?.state || {
      loading: true,
      error: '',
      nodes: [],
      stages: [],
      classicRoute: null,
      atlasRouteId: 'textbook_14_5',
    };
  });
  const [selectedNode, setSelectedNode] = useState(null);
  const [planningDetails, setPlanningDetails] = useState({
    loaded: false,
    loading: false,
    error: '',
    longTerm: '',
    shortTerm: '',
  });

  useEffect(() => {
    if (routeState.loading) return;
    const activeNode = routeState.nodes.find((node) => node.status === 'in_progress')
      || routeState.nodes.find((node) => node.status === 'next')
      || routeState.nodes[0];
    onCurrentProgress?.(String(activeNode?.title || '当前学习阶段'));
  }, [onCurrentProgress, routeState.loading, routeState.nodes]);

  useEffect(() => {
    let cancelled = false;
    const routeKey = selectedTarget?.textbook_route_id || '__planned__';
    const cacheKey = routeCacheKey(userCacheKey, routeKey);
    const cached = readQualificationRouteCache(cacheKey);
    const routeLoader = selectedTarget?.textbook_route_id
      ? loadClassicLearningRoute(selectedTarget.textbook_route_id)
      : loadPlannedLearningPath();
    if (cached?.state) {
      setRouteState(cached.state);
      onReadyChange?.(routeKey);
    } else {
      onReadyChange?.('');
    }
    setRouteView('cards');
    setRenderedRouteView('cards');
    setRouteTransitionPhase('idle');
    setSelectedNode(null);
    if (!cached?.state) setRouteState((current) => ({ ...current, loading: true, error: '' }));
    routeLoader
      .then((payload) => {
        if (cancelled) return;
        const nextState = buildLearningRouteState(payload);
        updateQualificationRouteCache(cacheKey, { payload, state: nextState });
        if (!cached || !samePayload(cached.payload, payload)) setRouteState(nextState);
        onReadyChange?.(routeKey);
      })
      .catch((error) => {
        if (!cancelled) {
          if (!cached?.state) {
            setRouteState({
              loading: false,
              error: error.message || '学习路径暂时无法读取',
              nodes: [],
              stages: [],
              classicRoute: null,
              atlasRouteId: 'textbook_14_5',
            });
          }
          onReadyChange?.(routeKey);
        }
      });
    return () => { cancelled = true; };
  }, [onReadyChange, selectedTarget?.textbook_route_id, userCacheKey]);

  useEffect(() => () => window.clearTimeout(routeTransitionTimerRef.current), []);

  const edges = routeState.nodes.slice(1).map((node, index) => ({
    from: routeState.nodes[index].membership_id,
    to: node.membership_id,
    kind: 'spine',
  }));

  const changeRouteView = (nextView) => {
    if (nextView === routeView) return;
    window.clearTimeout(routeTransitionTimerRef.current);
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (!reduceMotion && typeof document.startViewTransition === 'function') {
      document.startViewTransition(() => {
        setRouteView(nextView);
        setRenderedRouteView(nextView);
      });
      return;
    }
    setRouteView(nextView);
    if (reduceMotion) {
      setRenderedRouteView(nextView);
      setRouteTransitionPhase('idle');
      return;
    }
    setRouteTransitionPhase('exiting');
    routeTransitionTimerRef.current = window.setTimeout(() => {
      setRenderedRouteView(nextView);
      setRouteTransitionPhase('entering');
      window.requestAnimationFrame(() => setRouteTransitionPhase('idle'));
    }, 85);
  };

  const openNode = async (node) => {
    if (node?.node_type === 'stage') {
      if (routeState.classicRoute) {
        const stageId = node?.navigation?.stage_id;
        const stage = routeState.classicRoute.stages.find((item) => String(item.stage_id) === String(stageId));
        const childNodes = stage
          ? adaptClassicRouteBooks(routeState.classicRoute, stage, routeState.atlasRouteId)
          : [];
        setRouteState((current) => ({ ...current, nodes: childNodes, error: '' }));
        setSelectedNode(null);
        return;
      }
      try {
        const childPage = await loadPlannedLearningPath(node.node_id);
        const childNodes = Array.isArray(childPage.nodes) ? childPage.nodes.map(adaptPlannedPathNode) : [];
        setRouteState((current) => ({ ...current, nodes: childNodes, error: '' }));
        setSelectedNode(null);
      } catch (error) {
        setRouteState((current) => ({ ...current, error: error.message || '教材路径暂时无法读取' }));
      }
      return;
    }
    const navigation = node?.navigation || {};
    onNavigate?.({ page: 'practice', params: {
      view: 'textbook-chapters',
      route: navigation.route_id || 'textbook_14_5',
      lv1: navigation.book || String(node?.title || '').replace(/[《》]/g, ''),
      source: 'learning-plan',
    } });
  };

  const showPlanningDetails = async () => {
    changeRouteView('details');
    if (planningDetails.loaded || planningDetails.loading) return;
    setPlanningDetails((current) => ({ ...current, loading: true, error: '' }));
    try {
      let response = await fetchWithAuth(`${MAIN_API_BASE}/learning-plans/current/context`);
      let payload = await readJsonResponse(response, {});
      if (!response.ok) {
        response = await fetchWithAuth(`${MAIN_API_BASE}/learning-context`);
        payload = await readJsonResponse(response, {});
      }
      if (!response.ok) {
        const detail = payload?.detail;
        throw new Error(typeof detail === 'string' ? detail : detail?.message || '学习规划暂时无法读取');
      }
      setPlanningDetails({
        loaded: true,
        loading: false,
        error: '',
        longTerm: String(payload?.long_term_plan?.content || ''),
        shortTerm: String(payload?.short_term_plan?.content || ''),
      });
    } catch (error) {
      setPlanningDetails((current) => ({
        ...current,
        loaded: false,
        loading: false,
        error: error.message || '学习规划暂时无法读取',
      }));
    }
  };

  const returnToPath = () => {
    changeRouteView('orbit');
    setSelectedNode(null);
  };

  return (
      <section
        className="home-portal__route"
        data-view={routeView}
        data-content-ready={String(contentReady)}
        aria-busy={!contentReady}
        aria-label={`${selectedTarget?.name || '当前考证'}学习路径`}
      >
      <header className="home-portal__route-header">
        <div>
          <div className="home-portal__route-kicker">
            <h2>{selectedTarget?.name || '当前考证'}</h2>
            <button type="button" className="home-portal__route-detail-toggle" onClick={routeView === 'details' ? returnToPath : showPlanningDetails}>
              {routeView === 'details' ? '返回' : '了解详情'}
            </button>
          </div>
        </div>
        <div
          className="home-portal__route-switch"
          data-view={routeView === 'cards' ? 'cards' : 'orbit'}
          role="group"
          aria-label="学习路径视图"
        >
          <span className="home-portal__route-switch-indicator" aria-hidden="true" />
          <button
            type="button"
            className={routeView === 'cards' ? 'is-active' : ''}
            aria-pressed={routeView === 'cards'}
            onClick={() => changeRouteView('cards')}
          >
            学习阶段
          </button>
          <button
            type="button"
            className={routeView !== 'cards' ? 'is-active' : ''}
            aria-pressed={routeView !== 'cards'}
            onClick={returnToPath}
          >
            学习路径
          </button>
        </div>
      </header>
      <div className="home-portal__route-view-content" data-view={renderedRouteView} data-phase={routeTransitionPhase}>
      {renderedRouteView === 'orbit' && (
        <div className="home-portal__route-orbit-layout">
          {routeState.loading && <div className="home-portal__route-state" aria-hidden="true" />}
          {!routeState.loading && routeState.error && <div className="home-portal__route-state">{routeState.error}</div>}
          {!routeState.loading && !routeState.error && routeState.stages.length === 0 && <div className="home-portal__route-state">尚未生成学习路径</div>}
          {!routeState.loading && !routeState.error && routeState.stages.length > 0 && (
            <LearningPathOverview
              nodes={routeState.nodes}
              edges={edges}
              selectedId={selectedNode?.membership_id}
              onSelect={(node) => { setSelectedNode(node); openNode(node); }}
              onDrill={openNode}
              onClearSelection={() => setSelectedNode(null)}
              directDrill
              summaryLabel=""
              homeCompact
            />
          )}
        </div>
      )}
      {!routeState.loading && !routeState.error && routeState.stages.length > 0 && renderedRouteView === 'cards' && (
        <LearningStageLanding
          compact
          stages={routeState.stages}
          onStageSelect={() => changeRouteView('orbit')}
          onCreatePlan={() => onNavigate?.({ page: 'assistant', params: { context: '请结合我的学习状态，给我制定一份长期学习规划。' } })}
        />
      )}
      {renderedRouteView === 'details' && (
        <div className="home-portal__route-details" role="region" aria-label="长期规划和短期规划说明" tabIndex="0">
          {planningDetails.loading && <div className="home-portal__route-details-state" role="status">正在读取规划说明…</div>}
          {!planningDetails.loading && planningDetails.error && <div className="home-portal__route-details-state" role="alert">{planningDetails.error}</div>}
          {!planningDetails.loading && !planningDetails.error && (
            <div className="home-portal__route-details-grid">
              <article>
                <h3>长期规划说明</h3>
                <div>{planningDetails.longTerm || '尚未制定长期规划。'}</div>
              </article>
              <article>
                <h3>短期规划说明</h3>
                <div>{planningDetails.shortTerm || '尚未制定短期规划。'}</div>
              </article>
            </div>
          )}
        </div>
      )}
      </div>
    </section>
  );
}

export default function QualificationRoutePage({ currentUser, onNavigate }) {
  const userCacheKey = currentUserCacheKey(currentUser);
  const initialPageCacheRef = useRef(readQualificationPageCache(userCacheKey));
  const initialPageCache = initialPageCacheRef.current;
  const initialTarget = initialPageCache?.learningTarget || { name: '中医执业医师资格考试', examDate: '' };
  const initialRouteKey = initialTarget.textbook_route_id || '__planned__';
  const hasInitialRoute = Boolean(readQualificationRouteCache(routeCacheKey(userCacheKey, initialRouteKey)));
  const hasSummaryRef = useRef(Boolean(initialPageCache?.payload));
  const [payload, setPayload] = useState(initialPageCache?.payload || EMPTY_HOME_PAYLOAD);
  const [loading, setLoading] = useState(!initialPageCache?.payload);
  const [error, setError] = useState('');
  const [checkinLoading, setCheckinLoading] = useState(false);
  const [checkinMessage, setCheckinMessage] = useState('');
  const [currentProgress, setCurrentProgress] = useState(initialPageCache?.currentProgress || '');
  const [learningTarget, setLearningTarget] = useState(initialTarget);
  const [learningTargetReady, setLearningTargetReady] = useState(Boolean(initialPageCache?.learningTarget));
  const [readyRouteKey, setReadyRouteKey] = useState(hasInitialRoute ? initialRouteKey : '');
  const [welcomeRevealReady, setWelcomeRevealReady] = useState(false);
  const [summaryRevision, setSummaryRevision] = useState(0);
  const [, setCountdownTick] = useState(0);
  const homeState = useMemo(() => buildHomePortalState(payload), [payload]);
  const updateCurrentProgress = useCallback((nextProgress) => {
    setCurrentProgress(nextProgress);
    updatePageCache(userCacheKey, { currentProgress: nextProgress });
  }, [userCacheKey]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetchWithAuth(`${MAIN_API_BASE}/qualification-targets`).then((response) => readJsonResponse(response, { items: [] })),
      loadLearningTarget(),
      fetchWithAuth(`${MAIN_API_BASE}/learning-context`).then((response) => readJsonResponse(response, {})),
    ]).then(([catalog, targetPayload, context]) => {
      if (cancelled) return;
      const options = Array.isArray(catalog?.items) ? catalog.items : [];
      const target = targetPayload?.target || targetPayload || {};
      const selected = options.find((item) => item.exam_track_id === target?.exam_track_id)
        || options.find((item) => item.official_name === context?.onboarding?.survey_answers?.target_exam_or_course)
        || options[0];
      if (selected) {
        const nextTarget = {
          ...selected,
          name: selected.official_name,
          examDate: examDateForTarget(selected),
          targetId: selected.target_id,
        };
        setLearningTarget((current) => (samePayload(current, nextTarget) ? current : nextTarget));
        updatePageCache(userCacheKey, { learningTarget: nextTarget });
      }
    }).catch(() => {}).finally(() => {
      if (!cancelled) setLearningTargetReady(true);
    });
    return () => { cancelled = true; };
  }, [userCacheKey]);

  useEffect(() => {
    const handleTargetChanged = (event) => {
      const selected = event?.detail;
      if (!selected?.target_id) return;
      const nextTarget = {
        ...selected,
        name: selected.official_name,
        examDate: examDateForTarget(selected),
        targetId: selected.target_id,
      };
      setLearningTarget(nextTarget);
      updatePageCache(userCacheKey, { learningTarget: nextTarget });
    };
    window.addEventListener(LEARNING_TARGET_CHANGED_EVENT, handleTargetChanged);
    return () => window.removeEventListener(LEARNING_TARGET_CHANGED_EVENT, handleTargetChanged);
  }, [userCacheKey]);

  useEffect(() => {
    const timer = window.setInterval(() => setCountdownTick((value) => value + 1), 60 * 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;

    const loadSummary = async () => {
      if (!hasSummaryRef.current) setLoading(true);
      setError('');
      try {
        const response = await fetchWithAuth(`${MAIN_API_BASE}/dashboard/home`);
        const result = await readJsonResponse(response, {});
        if (!response.ok) throw new Error(result.detail || '首页数据暂不可用');
        if (!result || typeof result !== 'object' || Array.isArray(result)) {
          throw new Error('首页数据暂不可用');
        }
        if (!cancelled) {
          hasSummaryRef.current = true;
          setPayload((current) => (samePayload(current, result) ? current : result));
          updatePageCache(userCacheKey, { payload: result });
        }
      } catch (requestError) {
        if (!cancelled && !hasSummaryRef.current) setError(requestError.message || '首页数据暂不可用');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    loadSummary();
    return () => { cancelled = true; };
  }, [summaryRevision, userCacheKey]);

  const checkinStatus = payload.checkin_status || EMPTY_HOME_PAYLOAD.checkin_status;
  const submitCheckin = async () => {
    if (checkinStatus.checked_in_today || checkinLoading) return;
    setCheckinLoading(true);
    setCheckinMessage('');
    try {
      const response = await fetchWithAuth(`${MAIN_API_BASE}/checkin`, { method: 'POST' });
      const result = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(result.detail || '签到失败');
      setPayload((current) => {
        const nextPayload = { ...current, checkin_status: result.status || current.checkin_status };
        updatePageCache(userCacheKey, { payload: nextPayload });
        return nextPayload;
      });
      setCheckinMessage(result.message || '今日签到成功');
    } catch (requestError) {
      setCheckinMessage(requestError.message || '签到失败');
    } finally {
      setCheckinLoading(false);
    }
  };

  const refreshDailyTask = async () => {
    try {
      await fetchWithAuth(`${MAIN_API_BASE}/learning-tasks/current/refresh`, { method: 'POST' });
    } finally {
      setSummaryRevision((value) => value + 1);
    }
  };

  const progressFallback = payload?.status_cards
    ?.map((card) => normalizePercent(card?.value))
    .find((value) => value !== null) ?? null;

  const currentTask = payload.current_learning_task;
  const currentTaskItems = Array.isArray(currentTask?.items) && currentTask.items.length
    ? currentTask.items.map((item) => {
      const inferredAction = item.action || (
        item.item_type === 'video_section'
          ? {
            destination: 'workshop.knowledge_video',
            params: { video: item.resource_ref || {} },
          }
          : { destination: 'workshop.practice', params: {} }
      );
      return {
        id: `task-item:${item.task_item_id || item.title}`,
        title: item.title || currentTask.title || '今日学习任务',
        detail: item.kp_name || currentTask.learning_chapter?.title || currentTask.description || '继续完成当前学习任务',
        meta: item.estimated_minutes ? `${item.estimated_minutes} 分钟` : currentTask.duration || '',
        progress: readProgress(item.progress, readProgress(item, readProgress(currentTask, progressFallback))),
        source: 'daily_task',
        dateKey: localDateKey(),
        raw: item,
        intent: workshopActionIntent(inferredAction, {
          taskItemId: item.task_item_id || '',
          kpId: item.kp_id || '',
          kpName: item.kp_name || '',
          directTitle: item.item_type === 'video_section'
            ? item.title || currentTask.title || '今日学习视频'
            : '',
          returnTo: { page: 'qualification-route', params: {} },
        }),
      };
    })
    : currentTask ? [{
      id: `task:${currentTask.task_id || currentTask.title}`,
      title: currentTask.title || '今日学习任务',
      detail: currentTask.learning_chapter?.title || currentTask.description || '继续完成当前学习任务',
      meta: currentTask.duration || '',
      progress: readProgress(currentTask, progressFallback),
      source: 'daily_task',
      dateKey: localDateKey(),
      raw: currentTask,
      intent: { page: 'practice', params: { view: 'workspace', taskType: 'question_training' } },
    }] : [];
  const activityItems = Array.isArray(payload.learning_activity?.recent_activities)
    ? payload.learning_activity.recent_activities
      .filter((item) => item?.activity_type === 'training_workspace_task')
      .map((item) => {
        const recordedAt = item.completed_at || item.ended_at || item.created_at || item.started_at;
        return {
          id: `workshop:${item.activity_id || item.resource_id || item.created_at}`,
          title: item.title || item.resource_id || '学习工坊记录',
          detail: item.task_type || '学习工坊训练记录',
          meta: item.created_at ? formatReviewDate(item.created_at) : '最近',
          progress: item.completion_status === 'completed' ? 100 : 0,
          source: 'workshop_history',
          dateKey: recordedAt ? localDateKey(recordedAt) : '',
          raw: item,
          intent: { page: 'practice', params: { view: 'workspace', taskType: item.task_type || 'question_training', taskId: item.resource_id || '' } },
        };
      })
    : [];
  const planItems = [...currentTaskItems, ...activityItems]
    .filter((item, index, items) => items.findIndex((candidate) => candidate.id === item.id) === index);
  const studyDays = Array.isArray(payload.learning_activity?.trends?.series)
    ? payload.learning_activity.trends.series
      .filter((day) => Number(day?.login_days || 0) > 0
        || Number(day?.focus_minutes || 0) > 0
        || Number(day?.task_completion_rate || 0) > 0
        || Number(day?.daily_atomic_task_completion_rate || 0) > 0)
      .map((day) => String(day.date || ''))
      .filter(Boolean)
    : [];

  const openActivityItem = (item) => {
    if (!item?.intent) return;
    onNavigate?.(item.intent);
  };

  const openLearningItem = (item) => {
    if (item?.source === 'workshop_history' && item.raw?.resource_id) {
      onNavigate?.({ page: 'practice', params: { view: 'workspace', taskType: item.raw.task_type || 'question_training', taskId: item.raw.resource_id } });
      return;
    }
    openActivityItem(item);
  };

  const countdown = examCountdown(learningTarget.examDate);
  const learningRouteKey = learningTarget.textbook_route_id || '__planned__';
  const pageContentReady = !loading
    && learningTargetReady
    && Boolean(currentProgress)
    && readyRouteKey === learningRouteKey;
  useEffect(() => {
    if (!pageContentReady) {
      setWelcomeRevealReady(false);
      return undefined;
    }
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const timer = window.setTimeout(() => setWelcomeRevealReady(true), reduceMotion ? 0 : CONTENT_FADE_DURATION);
    return () => window.clearTimeout(timer);
  }, [pageContentReady]);
  const displayName = String(currentUser?.display_name || currentUser?.username || '同学').trim() || '同学';
  const hour = new Date().getHours();
  const greeting = hour >= 5 && hour < 11
    ? '早上好'
    : hour >= 11 && hour < 14
      ? '中午好'
      : hour >= 14 && hour < 18
        ? '下午好'
        : '晚上好';
  const heroTitle = currentProgress
    ? `${greeting}，${displayName}\n今天继续学习${currentProgress}`
    : '';

  return (
    <div className="home-portal" aria-busy={loading}>
      <section className="home-portal__learning-area" aria-label="学习路线与学习进度">
        <div className="home-portal__main-column">
          <section className="home-portal__hero" aria-labelledby="home-portal-title">
            <div className="home-portal__hero-primary" data-content-ready={String(pageContentReady)}>
              <div className="home-portal__hero-actions">
                <button type="button" className="home-portal__checkin" onClick={submitCheckin} disabled={checkinLoading || checkinStatus.checked_in_today} aria-label={checkinStatus.checked_in_today ? `今日已签到，连续${checkinStatus.streak || 0}天` : '今日签到'}>
                  <CalendarCheck2 aria-hidden="true" size={18} />{checkinStatus.checked_in_today ? `已签到 ${checkinStatus.streak || 0} 天` : checkinLoading ? '签到中…' : '签到'}
                </button>
              </div>
              <HeroTypewriter
                title={welcomeRevealReady ? heroTitle : ''}
                subtitle={learningTargetReady && countdown !== null
                  ? `距离${learningTarget.name}还有 ${countdown} 天，保持稳定节奏。`
                  : ''}
              />
            </div>
            <div className="home-portal__hero-notices">
              {error && <div className="home-portal__notice" role="alert">{error}</div>}
              {checkinMessage && <div className="home-portal__notice" role="status">{checkinMessage}</div>}
              {!error && homeState.announcements[0] && (
                <div className="home-portal__notice" role="status">{homeState.announcements[0]}</div>
              )}
            </div>
          </section>

          <HomeLearningRoute
            onNavigate={onNavigate}
            onCurrentProgress={updateCurrentProgress}
            onReadyChange={setReadyRouteKey}
            contentReady={pageContentReady}
            userCacheKey={userCacheKey}
            selectedTarget={learningTarget}
          />
        </div>
        <aside className="home-portal__plan-rail" data-content-ready={String(pageContentReady)} aria-busy={!pageContentReady} aria-label="今日学习计划">
          <CurrentLearningPlan
            currentTask={currentTask}
            items={planItems}
            studyDays={studyDays}
            timer={payload.daily_task_timer}
            onExpire={refreshDailyTask}
            onOpenItem={openLearningItem}
            onAddTask={() => onNavigate?.({
              page: 'assistant',
              params: {
                newConversation: true,
                context: `请结合我的学习目标和当前阶段${currentProgress ? `“${currentProgress}”` : ''}，为今天添加一项可执行的学习任务。`,
              },
            })}
          />
        </aside>
      </section>
    </div>
  );
}
