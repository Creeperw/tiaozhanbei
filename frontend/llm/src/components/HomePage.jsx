import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, CalendarCheck2 } from 'lucide-react';
import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import LearningStageLanding from './learning-stage/LearningStageLanding';
import LearningPathOverview from './learning-tree/LearningPathOverview';
import { adaptPlannedPathNode, loadPlannedLearningPath } from './learning-tree/learningPathApi';
import { loadLearningTarget, saveLearningTarget } from './exam-atlas/examAtlasApi';
import {
  EMPTY_HOME_PAYLOAD,
  buildHomePortalState,
} from '../homePortal';

function formatReviewDate(value) {
  if (!value) return '待安排';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '待安排';
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

function reviewEntryTitle(entry) {
  const unit = entry?.memory_unit || {};
  return unit.prompt_abstract || entry?.task?.title || unit.kp_id || '待复习知识点';
}

const DEFAULT_EXAM_DATE = '2026-11-29T23:59:59+08:00';
function splitHeroTitle(value) {
  const text = String(value || '');
  const marker = '\u7EE7\u7EED\u5B66\u4E60';
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
      if (cursor < word.length) {
        timer = window.setTimeout(tick, 180);
      }
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

function TaskProgress({ value }) {
  const percent = Math.max(0, Math.min(100, Number(value) || 0));
  const tone = percent < 35 ? 'coral' : percent < 70 ? 'amber' : percent < 100 ? 'teal' : 'green';
  return <span className={`home-portal__task-progress home-portal__task-progress--${tone}`} style={{ '--task-progress': `${percent}%` }} aria-hidden="true" />;
}

function ReviewTaskRail({ items, learningItems, onOpen, onOpenLearning }) {
  const [activeTab, setActiveTab] = useState('review');
  return (
    <section className="home-portal__review-rail" aria-label="复习任务">
      <div className="home-portal__activity-tabs" role="tablist" aria-label="任务类型">
        <span className="home-portal__activity-indicator" data-active-tab={activeTab} aria-hidden="true" />
        <button type="button" className={activeTab === 'learning' ? 'is-active' : ''} role="tab" aria-selected={activeTab === 'learning'} onClick={() => setActiveTab('learning')}>学习任务</button>
        <button type="button" className={activeTab === 'review' ? 'is-active' : ''} role="tab" aria-selected={activeTab === 'review'} onClick={() => setActiveTab('review')}>复习任务</button>
      </div>
      <header><strong>{activeTab === 'review' ? '复习任务' : '学习任务'}</strong><small>{activeTab === 'review' ? `${items.length} 题` : `${learningItems.length} 项`}</small></header>
      {activeTab === 'review' && <p className="home-portal__review-hint">根据掌握度与遗忘曲线智能安排</p>}
      <div className="home-portal__review-list">
        {activeTab === 'review' && items.map((entry, index) => {
          const unit = entry.memory_unit || {};
          const mastery = normalizePercent(unit.mastery_score) ?? 0;
          return <button key={`${unit.memory_unit_id || unit.kp_id || index}`} type="button" className="home-portal__task-item" onClick={() => onOpen(entry)}><TaskProgress value={mastery} /><span><strong>{reviewEntryTitle(entry)}</strong><small>{entry.is_due ? '已到期 · 建议优先复习' : `掌握度 ${Math.round(mastery)}%`}</small></span><time>{formatReviewDate(unit.next_review_at)}</time></button>;
        })}
        {activeTab === 'learning' && learningItems.map((item) => <button key={item.id} type="button" className="home-portal__task-item" onClick={() => onOpenLearning(item)}><TaskProgress value={item.progress} /><span><strong>{item.title}</strong><small>{item.detail}</small></span><time>{item.meta || '最近'}</time></button>)}
        {((activeTab === 'review' && !items.length) || (activeTab === 'learning' && !learningItems.length)) && <p className="home-portal__task-empty">{activeTab === 'review' ? '当前没有复习任务。' : '完成一次学习后，近期记录会显示在这里。'}</p>}
      </div>
      {activeTab === 'review' && <p className="home-portal__review-note">完成知识点配套题并通过批改后，系统会自动加入复习队列。</p>}
    </section>
  );
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

function HomeLearningRoute({ onNavigate, onCurrentProgress }) {
  const [routeView, setRouteView] = useState('orbit');
  const [routeState, setRouteState] = useState({ loading: true, error: '', nodes: [], stages: [] });
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
    loadPlannedLearningPath()
      .then((payload) => {
        if (cancelled) return;
        const nodes = Array.isArray(payload.nodes) ? payload.nodes.map(adaptPlannedPathNode) : [];
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
        setRouteState({ loading: false, error: '', nodes, stages });
      })
      .catch((error) => {
        if (!cancelled) setRouteState({ loading: false, error: error.message || '学习路径暂时无法读取', nodes: [], stages: [] });
      });
    return () => { cancelled = true; };
  }, []);

  const edges = routeState.nodes.slice(1).map((node, index) => ({
    from: routeState.nodes[index].membership_id,
    to: node.membership_id,
    kind: 'spine',
  }));

  const openNode = async (node) => {
    if (node?.node_type === 'stage') {
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

  const returnToOrbit = () => {
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
        {routeView === 'orbit' ? (
          <button type="button" className="home-portal__route-full-link" onClick={() => setRouteView('cards')}>查看完整学习路径 <ArrowRight aria-hidden="true" size={14} /></button>
        ) : (
          <button type="button" onClick={returnToOrbit}>返回短期学习路径</button>
        )}
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

export default function HomePage({ currentUser, onNavigate }) {
  const [payload, setPayload] = useState(EMPTY_HOME_PAYLOAD);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [checkinLoading, setCheckinLoading] = useState(false);
  const [checkinMessage, setCheckinMessage] = useState('');
  const [currentProgress, setCurrentProgress] = useState('');
  const [learningTarget, setLearningTarget] = useState({ name: '中医执业医师资格考试', examDate: '' });
  const [targetOptions, setTargetOptions] = useState([]);
  const [, setCountdownTick] = useState(0);
  const targetSelectRef = useRef(null);
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
      setTargetOptions(options);
      const target = targetPayload?.target || targetPayload || {};
      const selected = options.find((item) => item.exam_track_id === target?.exam_track_id)
        || options.find((item) => item.official_name === context?.onboarding?.survey_answers?.target_exam_or_course)
        || options[0];
      if (selected) setLearningTarget({ name: selected.official_name, examDate: examDateForTarget(selected), targetId: selected.target_id });
    }).catch(() => {});
    return () => { cancelled = true; };
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
  }, []);

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

  const progressFallback = payload?.status_cards
    ?.map((card) => normalizePercent(card?.value))
    .find((value) => value !== null) ?? null;

  const currentTask = payload.current_learning_task;
  const currentTaskItems = Array.isArray(currentTask?.items) && currentTask.items.length
    ? currentTask.items.map((item) => ({
      id: `task-item:${item.task_item_id || item.title}`,
      title: item.title || currentTask.title || '今日学习任务',
      detail: item.kp_name || currentTask.learning_chapter?.title || currentTask.description || '继续完成当前学习任务',
      meta: item.estimated_minutes ? `${item.estimated_minutes} 分钟` : currentTask.duration || '',
      progress: readProgress(item.progress, readProgress(item, readProgress(currentTask, progressFallback))),
      source: 'daily_task',
      raw: item,
      intent: { page: 'practice', params: { view: 'workspace', taskType: 'question_training', taskItemId: item.task_item_id || '' } },
    }))
    : currentTask ? [{
      id: `task:${currentTask.task_id || currentTask.title}`,
      title: currentTask.title || '今日学习任务',
      detail: currentTask.learning_chapter?.title || currentTask.description || '继续完成当前学习任务',
      meta: currentTask.duration || '',
      progress: readProgress(currentTask, progressFallback),
      source: 'daily_task',
      raw: currentTask,
      intent: { page: 'practice', params: { view: 'workspace', taskType: 'question_training' } },
    }] : [];
  const progressItems = [
    ...currentTaskItems,
    ...(Array.isArray(payload.learning_activity?.recent_activities)
      ? payload.learning_activity.recent_activities
        .filter((item) => item?.activity_type === 'training_workspace_task')
        .map((item) => ({
        id: `workshop:${item.activity_id || item.resource_id || item.created_at}`,
        title: item.title || item.resource_id || '学习工坊记录',
        detail: item.task_type || '学习工坊训练记录',
        meta: item.created_at ? formatReviewDate(item.created_at) : '最近',
        progress: item.completion_status === 'completed' ? 100 : 0,
        source: 'workshop_history',
        raw: item,
        intent: { page: 'practice', params: { view: 'workspace', taskType: item.task_type || 'question_training', taskId: item.resource_id || '' } },
        }))
      : []),
  ].filter((item, index, items) => items.findIndex((candidate) => candidate.id === item.id) === index).slice(0, 5);
  const reviewItems = (Array.isArray(payload.review_queue?.entries) ? payload.review_queue.entries : [])
    .filter((entry) => entry?.memory_unit || entry?.task)
    .slice(0, 8);

  const openActivityItem = (item) => {
    if (!item?.intent) return;
    onNavigate?.(item.intent);
  };

  const openLearningItem = (item) => {
    const action = item?.source === 'daily_task' ? item.raw?.action : null;
    if (action?.destination === 'workshop.practice') {
      onNavigate?.({ page: 'practice', params: { view: 'workspace', taskType: 'question_training', ...(action.params || {}) } });
      return;
    }
    if (item?.source === 'workshop_history' && item.raw?.resource_id) {
      onNavigate?.({ page: 'practice', params: { view: 'workspace', taskType: item.raw.task_type || 'question_training', taskId: item.raw.resource_id } });
      return;
    }
    openActivityItem(item);
  };

  const openReviewItem = (entry) => onNavigate?.({
    page: 'practice',
    params: {
      view: 'workspace',
      taskType: 'question_training',
      kpId: entry?.memory_unit?.kp_id || '',
      reviewTaskId: entry?.task?.review_task_id || '',
    },
  });

  const selectTarget = async (event) => {
    const selected = targetOptions.find((item) => item.target_id === event.target.value);
    if (!selected) return;
    setLearningTarget({ name: selected.official_name, examDate: examDateForTarget(selected), targetId: selected.target_id });
    try {
      await saveLearningTarget(selected.exam_track_id);
    } catch { /* keep the local selection visible while the profile request retries */ }
  };

  const taskItems = progressItems;
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
          <label className="home-portal__target-select" onClick={(event) => {
            if (event.target === targetSelectRef.current) return;
            const select = targetSelectRef.current;
            select?.focus();
            select?.showPicker?.();
          }}>
            <span>学习目标</span>
            <select ref={targetSelectRef} aria-label="学习目标" value={learningTarget.targetId || ''} onChange={selectTarget}>
              {!targetOptions.length && <option value="">{learningTarget.name}</option>}
              {targetOptions.map((item) => <option key={item.target_id} value={item.target_id}>{item.official_name}</option>)}
            </select>
          </label>
          <button type="button" className="home-portal__checkin" onClick={submitCheckin} disabled={checkinLoading || checkinStatus.checked_in_today} aria-label={checkinStatus.checked_in_today ? `今日已签到，连续${checkinStatus.streak || 0}天` : '今日签到'}>
            <CalendarCheck2 aria-hidden="true" size={18} />{checkinStatus.checked_in_today ? `已签到 ${checkinStatus.streak || 0} 天` : checkinLoading ? '签到中…' : '签到'}
          </button>
        </div>
        <HeroTypewriter key={heroTitle} title={heroTitle} subtitle={`距离${learningTarget.name}还有 ${countdown ?? 126} 天，保持稳定节奏。`} />
      </section>

      {error && <div className="home-portal__notice" role="alert">{error}</div>}
      {checkinMessage && <div className="home-portal__notice" role="status">{checkinMessage}</div>}
      {!error && homeState.announcements[0] && (
        <div className="home-portal__notice" role="status">{homeState.announcements[0]}</div>
      )}

      <section className="home-portal__learning-area" aria-label="学习路线与学习进度">
        <HomeLearningRoute onNavigate={onNavigate} onCurrentProgress={setCurrentProgress} />
        <ReviewTaskRail items={reviewItems} learningItems={taskItems} onOpen={openReviewItem} onOpenLearning={openLearningItem} />
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
