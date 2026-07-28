import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowRight,
  BookOpenText,
  CalendarCheck2,
  Check,
  ChevronLeft,
  ChevronRight,
  Circle,
  Clock3,
  ListChecks,
  PlayCircle,
  Route,
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

function formatReviewDate(value) {
  if (!value) return '最近';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '最近';
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

const DEFAULT_EXAM_DATE = '2026-11-29T23:59:59+08:00';
const LEARNING_TARGET_CHANGED_EVENT = 'shizhen:learning-target-changed';

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

  useEffect(() => {
    let cancelled = false;
    let timer;
    let cursor = 0;
    const word = String(title || '').trim();

    const tick = () => {
      if (cancelled) return;
      cursor += 1;
      setTypedText(word.slice(0, cursor));
      if (cursor < word.length) timer = window.setTimeout(tick, 180);
    };

    timer = window.setTimeout(tick, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [title]);

  const fullWord = String(title || '').trim();
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

function activityDate(item) {
  const value = item?.dateKey
    || item?.raw?.completed_at
    || item?.raw?.ended_at
    || item?.raw?.created_at
    || item?.raw?.started_at;
  return value ? localDateKey(value) : '';
}

function isCompletedPlanItem(item) {
  const status = String(item?.raw?.status || item?.raw?.completion_status || '').toLowerCase();
  return ['completed', 'complete', 'done', 'finished'].includes(status)
    || Number(item?.progress) >= 100;
}

function planItemPresentation(item) {
  const kind = String(item?.raw?.item_type || item?.raw?.task_type || '').toLowerCase();
  if (kind === 'video_section' || kind.includes('video')) {
    return { label: '章节视频', icon: PlayCircle };
  }
  if (kind === 'knowledge_practice' || kind.includes('question') || kind.includes('training')) {
    return { label: '知识点训练', icon: ListChecks };
  }
  return { label: item?.source === 'workshop_history' ? '学习记录' : '学习任务', icon: BookOpenText };
}

function CurrentLearningPlan({
  currentTask,
  items,
  stageName,
  timer,
  onExpire,
  onOpenItem,
  onShowPath,
}) {
  const today = localDateKey();
  const [selectedDate, setSelectedDate] = useState(today);
  const [visibleMonth, setVisibleMonth] = useState(() => startOfMonth(new Date()));
  const calendarDays = useMemo(() => calendarDaysForMonth(visibleMonth), [visibleMonth]);
  const entriesByDate = useMemo(() => {
    const grouped = new Map();
    items.forEach((item) => {
      const key = activityDate(item);
      if (!key) return;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(item);
    });
    return grouped;
  }, [items]);
  const selectedItems = entriesByDate.get(selectedDate) || [];
  const taskProgress = readProgress(currentTask?.progress, readProgress(currentTask, null));
  const progress = taskProgress ?? (
    selectedDate === today && selectedItems.length
      ? (selectedItems.filter(isCompletedPlanItem).length / selectedItems.length) * 100
      : 0
  );
  const chapter = currentTask?.learning_chapter || {};
  const totalMinutes = selectedItems.reduce(
    (sum, item) => sum + Math.max(0, Number(item?.raw?.estimated_minutes || 0)),
    0,
  );
  const selectedDateLabel = selectedDate === today
    ? '今日任务'
    : new Date(`${selectedDate}T00:00:00`).toLocaleDateString('zh-CN', {
      month: 'long',
      day: 'numeric',
      weekday: 'short',
    });

  const changeMonth = (offset) => {
    setVisibleMonth((current) => new Date(current.getFullYear(), current.getMonth() + offset, 1));
  };

  return (
    <div className="home-plan" aria-label="当前学习计划">
      <header className="home-plan__summary">
        <div>
          <span>{stageName || '当前学习阶段'}</span>
          <h3>{chapter.title || currentTask?.title || '等待生成今日任务'}</h3>
          <p>
            {chapter.book ? `《${String(chapter.book).replace(/[《》]/g, '')}》` : '教材待规划'}
            {currentTask?.completion_criteria ? ` · 验收：${currentTask.completion_criteria}` : ''}
          </p>
        </div>
        <div className="home-plan__progress" aria-label={`当前计划完成度 ${Math.round(progress)}%`}>
          <strong>{Math.round(progress)}%</strong>
          <span><i style={{ width: `${progress}%` }} /></span>
          <small>{currentTask?.progress?.completed || 0} / {currentTask?.progress?.total || selectedItems.length || 0} 项</small>
        </div>
      </header>

      <div className="home-plan__body">
        <section className="home-plan__tasks" aria-label={selectedDateLabel}>
          <header>
            <div>
              <span>{selectedDateLabel}</span>
              <h4>{selectedDate === today ? (currentTask?.title || '今日学习安排') : '学习记录'}</h4>
            </div>
            <small>{selectedItems.length} 项{totalMinutes > 0 ? ` · 约 ${totalMinutes} 分钟` : ''}</small>
          </header>

          <div className="home-plan__task-list">
            {selectedItems.map((item) => {
              const completed = isCompletedPlanItem(item);
              const presentation = planItemPresentation(item);
              const Icon = presentation.icon;
              return (
                <button
                  key={item.id}
                  type="button"
                  className="home-plan__task"
                  data-status={completed ? 'completed' : 'pending'}
                  onClick={() => onOpenItem?.(item)}
                >
                  <span className="home-plan__task-check" aria-label={completed ? '已完成' : '未完成'}>
                    {completed ? <Check aria-hidden="true" size={15} /> : <Circle aria-hidden="true" size={15} />}
                  </span>
                  <span className="home-plan__task-copy">
                    <strong>{item.title}</strong>
                    <small><Icon aria-hidden="true" size={13} />{presentation.label}{item.detail ? ` · ${item.detail}` : ''}</small>
                  </span>
                  <span className="home-plan__task-meta">
                    {item.raw?.estimated_minutes ? <small><Clock3 aria-hidden="true" size={12} />{item.raw.estimated_minutes} 分钟</small> : null}
                    <em>{completed ? '已完成' : '开始学习'} <ArrowRight aria-hidden="true" size={13} /></em>
                  </span>
                </button>
              );
            })}
            {selectedItems.length === 0 && (
              <div className="home-plan__empty">
                <BookOpenText aria-hidden="true" size={24} />
                <strong>{selectedDate === today ? '今天还没有学习任务' : '这一天没有任务记录'}</strong>
                <p>{selectedDate === today ? '请先制定短期计划，再让智能助教安排今日任务。' : '只有实际产生的学习与完成记录才会显示在日历中。'}</p>
              </div>
            )}
          </div>

          <footer>
            <button type="button" onClick={onShowPath}><Route aria-hidden="true" size={15} />查看阶段路径</button>
            {currentTask?.expected_output && <p>学习产出：{currentTask.expected_output}</p>}
          </footer>
        </section>

        <aside className="home-plan__calendar" aria-label="学习计划日历">
          <header>
            <strong>我的计划</strong>
            <div>
              <button type="button" aria-label="上个月" onClick={() => changeMonth(-1)}><ChevronLeft aria-hidden="true" size={16} /></button>
              <span>{visibleMonth.getFullYear()}年{visibleMonth.getMonth() + 1}月</span>
              <button type="button" aria-label="下个月" onClick={() => changeMonth(1)}><ChevronRight aria-hidden="true" size={16} /></button>
            </div>
          </header>
          <div className="home-plan__weekdays" aria-hidden="true">
            {WEEKDAY_LABELS.map((label) => <span key={label}>{label}</span>)}
          </div>
          <div className="home-plan__calendar-grid">
            {calendarDays.map((date) => {
              const key = localDateKey(date);
              const dateItems = entriesByDate.get(key) || [];
              const hasTasks = dateItems.length > 0;
              const completed = hasTasks && dateItems.every(isCompletedPlanItem);
              const state = hasTasks ? (completed ? 'completed' : 'pending') : 'empty';
              const outside = date.getMonth() !== visibleMonth.getMonth();
              const label = `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日${hasTasks ? `，${completed ? '任务已完成' : '任务未完成'}` : '，无任务记录'}`;
              return (
                <button
                  key={key}
                  type="button"
                  data-state={state}
                  data-today={String(key === today)}
                  data-outside={String(outside)}
                  aria-label={label}
                  aria-pressed={selectedDate === key}
                  onClick={() => setSelectedDate(key)}
                >
                  {date.getDate()}
                </button>
              );
            })}
          </div>
          <div className="home-plan__calendar-legend">
            <span><i data-state="completed" />任务完成</span>
            <span><i data-state="pending" />任务未完成</span>
          </div>
          <div className="home-plan__calendar-footer">
            <strong>{selectedDateLabel}</strong>
            <span>{selectedItems.filter(isCompletedPlanItem).length} / {selectedItems.length} 项完成</span>
          </div>
          <DailyTaskCountdown timer={timer} onExpire={onExpire} />
        </aside>
      </div>
    </div>
  );
}

function HomeLearningRoute({
  onNavigate,
  onCurrentProgress,
  selectedTarget,
}) {
  const [routeView, setRouteView] = useState('orbit');
  const [routeState, setRouteState] = useState({
    loading: true,
    error: '',
    nodes: [],
    stages: [],
    classicRoute: null,
    atlasRouteId: 'textbook_14_5',
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
    const activeNode = routeState.nodes.find((node) => node.status === 'in_progress')
      || routeState.nodes.find((node) => node.status === 'next')
      || routeState.nodes[0];
    onCurrentProgress?.(String(activeNode?.title || ''));
  }, [onCurrentProgress, routeState.nodes]);

  useEffect(() => {
    let cancelled = false;
    const routeLoader = selectedTarget?.textbook_route_id
      ? loadClassicLearningRoute(selectedTarget.textbook_route_id)
      : loadPlannedLearningPath();
    setRouteView('orbit');
    setSelectedNode(null);
    setRouteState((current) => ({ ...current, loading: true, error: '' }));
    routeLoader
      .then((payload) => {
        if (cancelled) return;
        const classicRoute = payload?.route && Array.isArray(payload.route.stages) ? payload.route : null;
        const nodes = classicRoute
          ? classicRoute.stages.map((stage) => adaptClassicRouteStage(classicRoute, stage))
          : Array.isArray(payload.nodes) ? payload.nodes.map(adaptPlannedPathNode) : [];
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
        setRouteState({
          loading: false,
          error: '',
          nodes,
          stages,
          classicRoute,
          atlasRouteId: payload?.navigation?.atlas_route_id || 'textbook_14_5',
        });
      })
      .catch((error) => {
        if (!cancelled) setRouteState({
          loading: false,
          error: error.message || '学习路径暂时无法读取',
          nodes: [],
          stages: [],
          classicRoute: null,
          atlasRouteId: 'textbook_14_5',
        });
      });
    return () => { cancelled = true; };
  }, [selectedTarget?.textbook_route_id]);

  const edges = routeState.nodes.slice(1).map((node, index) => ({
    from: routeState.nodes[index].membership_id,
    to: node.membership_id,
    kind: 'spine',
  }));

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
    setRouteView('details');
    if (planningDetails.loaded || planningDetails.loading) return;
    setPlanningDetails((current) => ({ ...current, loading: true, error: '' }));
    try {
      const response = await fetchWithAuth(`${MAIN_API_BASE}/learning-context`);
      const payload = await readJsonResponse(response, {});
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
    setRouteView('orbit');
    setSelectedNode(null);
  };

  const handleOrbitWheel = (event) => {
    if (!event.target.closest?.('.learning-path-orbit')) return;
    const pageScroller = event.currentTarget.closest('.app-shell__main');
    if (!pageScroller || pageScroller.scrollHeight <= pageScroller.clientHeight) return;
    pageScroller.scrollTop += event.deltaY;
    event.preventDefault();
  };

  return (
      <section className="home-portal__route" data-view={routeView} aria-label="学习路径规划">
      <header className="home-portal__route-header">
        <div>
          <div className="home-portal__route-kicker">
            <h2>学习路径规划</h2>
            {routeView !== 'details' && <button type="button" onClick={showPlanningDetails}>了解详情</button>}
          </div>
        </div>
        {routeView === 'orbit' && <button type="button" className="home-portal__route-full-link" onClick={() => setRouteView('cards')}>查看阶段卡片 <ArrowRight aria-hidden="true" size={14} /></button>}
        {routeView === 'cards' && <button type="button" onClick={returnToPath}>返回学习路径</button>}
        {routeView === 'details' && <button type="button" onClick={returnToPath}>返回学习路径</button>}
      </header>
      {routeView === 'orbit' && (
        <div className="home-portal__route-orbit-layout" onWheelCapture={handleOrbitWheel}>
          {routeState.loading && <div className="home-portal__route-state">正在读取学习路径…</div>}
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
              summaryLabel="短期学习路径"
              homeCompact
            />
          )}
        </div>
      )}
      {!routeState.loading && !routeState.error && routeState.stages.length > 0 && routeView === 'cards' && (
        <LearningStageLanding
          compact
          stages={routeState.stages}
          onStageSelect={() => setRouteView('orbit')}
          onCreatePlan={() => onNavigate?.({ page: 'assistant', params: { context: '请结合我的学习状态，给我制定一份长期学习规划。' } })}
        />
      )}
      {routeView === 'details' && (
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
    </section>
  );
}

export default function QualificationRoutePage({ currentUser, onNavigate }) {
  const [payload, setPayload] = useState(EMPTY_HOME_PAYLOAD);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [checkinLoading, setCheckinLoading] = useState(false);
  const [checkinMessage, setCheckinMessage] = useState('');
  const [currentProgress, setCurrentProgress] = useState('');
  const [learningTarget, setLearningTarget] = useState({ name: '中医执业医师资格考试', examDate: '' });
  const [summaryRevision, setSummaryRevision] = useState(0);
  const [, setCountdownTick] = useState(0);
  const homeState = useMemo(() => buildHomePortalState(payload), [payload]);

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
      if (selected) setLearningTarget({
        ...selected,
        name: selected.official_name,
        examDate: examDateForTarget(selected),
        targetId: selected.target_id,
      });
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const handleTargetChanged = (event) => {
      const selected = event?.detail;
      if (!selected?.target_id) return;
      setLearningTarget({
        ...selected,
        name: selected.official_name,
        examDate: examDateForTarget(selected),
        targetId: selected.target_id,
      });
    };
    window.addEventListener(LEARNING_TARGET_CHANGED_EVENT, handleTargetChanged);
    return () => window.removeEventListener(LEARNING_TARGET_CHANGED_EVENT, handleTargetChanged);
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setCountdownTick((value) => value + 1), 60 * 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;

    const loadSummary = async () => {
      setLoading(true);
      setError('');
      try {
        const response = await fetchWithAuth(`${MAIN_API_BASE}/dashboard/home`);
        const result = await readJsonResponse(response, {});
        if (!response.ok) throw new Error(result.detail || '首页数据暂不可用');
        if (!result || typeof result !== 'object' || Array.isArray(result)) {
          throw new Error('首页数据暂不可用');
        }
        if (!cancelled) setPayload(result);
      } catch (requestError) {
        if (!cancelled) setError(requestError.message || '首页数据暂不可用');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    loadSummary();
    return () => { cancelled = true; };
  }, [summaryRevision]);

  const checkinStatus = payload.checkin_status || EMPTY_HOME_PAYLOAD.checkin_status;
  const submitCheckin = async () => {
    if (checkinStatus.checked_in_today || checkinLoading) return;
    setCheckinLoading(true);
    setCheckinMessage('');
    try {
      const response = await fetchWithAuth(`${MAIN_API_BASE}/checkin`, { method: 'POST' });
      const result = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(result.detail || '签到失败');
      setPayload((current) => ({ ...current, checkin_status: result.status || current.checkin_status }));
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

  const showLearningPath = () => {
    document.querySelector('.home-portal__route')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  };
  const countdown = examCountdown(learningTarget.examDate);
  const displayName = String(currentUser?.display_name || currentUser?.username || '同学').trim() || '同学';
  const heroTitle = `早上好，${displayName}，今天继续学习${currentProgress || '当前学习阶段'}`;
  const featureCards = [
    { key: 'assistant', title: '智能问答', detail: '随时提问，获得针对性讲解', image: '/design-images/home/ai-qa.png', intent: { page: 'assistant', params: { newConversation: true } } },
    { key: 'knowledge-graph', title: '知识图谱', detail: '从考点关系中建立整体理解', image: '/design-images/home/knowledge-graph.png', intent: { page: 'practice', params: { view: 'overview' } } },
    { key: 'resource-search', title: '资料检索', detail: '查找教材、经典与权威资料', image: '/design-images/home/resource-search.png', intent: { page: 'practice', params: { view: 'overview', libraryOnly: true, expandAll: true, hidePlan: true } } },
    { key: 'topic-training', title: '专题练习', detail: '针对薄弱点进行集中训练', image: '/design-images/home/focused-practice.png', intent: { page: 'practice', params: { view: 'workspace', taskType: 'topic_training' } } },
  ];

  return (
    <div className="home-portal" aria-busy={loading}>
      <section className="home-portal__hero" aria-labelledby="home-portal-title">
        <div className="home-portal__hero-actions">
          <button type="button" className="home-portal__checkin" onClick={submitCheckin} disabled={checkinLoading || checkinStatus.checked_in_today} aria-label={checkinStatus.checked_in_today ? `今日已签到，连续${checkinStatus.streak || 0}天` : '今日签到'}>
            <CalendarCheck2 aria-hidden="true" size={18} />{checkinStatus.checked_in_today ? `已签到 ${checkinStatus.streak || 0} 天` : checkinLoading ? '签到中…' : '签到'}
          </button>
        </div>
        <HeroTypewriter
          key={heroTitle}
          title={heroTitle}
          subtitle={`距离${learningTarget.name}还有 ${countdown ?? 126} 天，保持稳定节奏。`}
        />
      </section>

      {error && <div className="home-portal__notice" role="alert">{error}</div>}
      {checkinMessage && <div className="home-portal__notice" role="status">{checkinMessage}</div>}
      {!error && homeState.announcements[0] && (
        <div className="home-portal__notice" role="status">{homeState.announcements[0]}</div>
      )}

      <section className="home-portal__learning-area" aria-label="学习路线与学习进度">
        <HomeLearningRoute
          onNavigate={onNavigate}
          onCurrentProgress={setCurrentProgress}
          selectedTarget={learningTarget}
        />
        <aside className="home-portal__plan-rail" aria-label="今日学习计划">
          <CurrentLearningPlan
            currentTask={currentTask}
            items={planItems}
            stageName={currentProgress}
            timer={payload.daily_task_timer}
            onExpire={refreshDailyTask}
            onOpenItem={openLearningItem}
            onShowPath={showLearningPath}
          />
        </aside>
      </section>

      <section className="home-portal__feature-grid" aria-label="学习功能">
        {featureCards.map((card) => (
          <button key={card.key} type="button" className="home-portal__feature-card" onClick={() => onNavigate?.(card.intent)}>
            <img src={card.image} alt="" />
            <span><strong>{card.title}</strong><small>{card.detail}</small><em>进入功能 <ArrowRight aria-hidden="true" size={14} /></em></span>
          </button>
        ))}
      </section>

    </div>
  );
}
