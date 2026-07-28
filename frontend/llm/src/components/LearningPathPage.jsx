import React, { useEffect, useId, useRef, useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import LearningStageLanding from './learning-stage/LearningStageLanding';
import LearningPathOverview from './learning-tree/LearningPathOverview';
import { adaptPlannedPathNode, loadPlannedLearningPath } from './learning-tree/learningPathApi';
import './LearningPathPage.css';

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
  for (const key of ['progress_percent', 'progress', 'completion_rate', 'completion', 'rate']) {
    const value = normalizePercent(item?.[key]);
    if (value !== null) return value;
  }
  return fallback;
}

function TaskProgress({ value }) {
  const percent = Math.max(0, Math.min(100, Number(value) || 0));
  const tone = percent < 35 ? 'coral' : percent < 70 ? 'amber' : percent < 100 ? 'teal' : 'green';
  return (
    <span
      className={`learning-path-page__task-progress learning-path-page__task-progress--${tone}`}
      style={{ '--task-progress': `${percent}%` }}
      aria-hidden="true"
    />
  );
}

function ReviewTaskRail({ items, learningItems, onOpen, onOpenLearning }) {
  const [activeTab, setActiveTab] = useState('review');
  const tabId = useId();
  const learningTabRef = useRef(null);
  const reviewTabRef = useRef(null);
  const learningTabId = `${tabId}-learning-tab`;
  const reviewTabId = `${tabId}-review-tab`;
  const learningPanelId = `${tabId}-learning-panel`;
  const reviewPanelId = `${tabId}-review-panel`;

  const activateTab = (tab, focus = false) => {
    setActiveTab(tab);
    if (focus) {
      const target = tab === 'learning' ? learningTabRef.current : reviewTabRef.current;
      target?.focus();
    }
  };

  const handleTabKeyDown = (event) => {
    const tabs = ['learning', 'review'];
    const currentIndex = tabs.indexOf(activeTab);
    let nextTab = null;
    if (event.key === 'ArrowRight') nextTab = tabs[(currentIndex + 1) % tabs.length];
    if (event.key === 'ArrowLeft') nextTab = tabs[(currentIndex - 1 + tabs.length) % tabs.length];
    if (event.key === 'Home') nextTab = tabs[0];
    if (event.key === 'End') nextTab = tabs[tabs.length - 1];
    if (!nextTab) return;
    event.preventDefault();
    activateTab(nextTab, true);
  };

  return (
    <section className="learning-path-page__review-rail" aria-label="学习与复习任务">
      <div className="learning-path-page__activity-tabs" role="tablist" aria-label="任务类型">
        <span className="learning-path-page__activity-indicator" data-active-tab={activeTab} aria-hidden="true" />
        <button
          ref={learningTabRef}
          id={learningTabId}
          type="button"
          className={activeTab === 'learning' ? 'is-active' : ''}
          role="tab"
          aria-selected={activeTab === 'learning'}
          aria-controls={learningPanelId}
          tabIndex={activeTab === 'learning' ? 0 : -1}
          onClick={() => activateTab('learning')}
          onKeyDown={handleTabKeyDown}
        >
          学习任务
        </button>
        <button
          ref={reviewTabRef}
          id={reviewTabId}
          type="button"
          className={activeTab === 'review' ? 'is-active' : ''}
          role="tab"
          aria-selected={activeTab === 'review'}
          aria-controls={reviewPanelId}
          tabIndex={activeTab === 'review' ? 0 : -1}
          onClick={() => activateTab('review')}
          onKeyDown={handleTabKeyDown}
        >
          复习任务
        </button>
      </div>
      <header>
        <strong>{activeTab === 'review' ? '复习任务' : '学习任务'}</strong>
        <small>{activeTab === 'review' ? `${items.length} 题` : `${learningItems.length} 项`}</small>
      </header>
      {activeTab === 'review' && (
        <p className="learning-path-page__review-hint">根据掌握度与遗忘曲线智能安排</p>
      )}
      <div
        id={reviewPanelId}
        className="learning-path-page__review-list"
        role="tabpanel"
        aria-labelledby={reviewTabId}
        hidden={activeTab !== 'review'}
      >
        {items.map((entry, index) => {
          const unit = entry.memory_unit || {};
          const mastery = normalizePercent(unit.mastery_score) ?? 0;
          return (
            <button
              key={unit.memory_unit_id || unit.kp_id || index}
              type="button"
              className="learning-path-page__task-item"
              onClick={() => onOpen(entry)}
            >
              <TaskProgress value={mastery} />
              <span>
                <strong>{reviewEntryTitle(entry)}</strong>
                <small>{entry.is_due ? '已到期 · 建议优先复习' : `掌握度 ${Math.round(mastery)}%`}</small>
              </span>
              <time>{formatReviewDate(unit.next_review_at)}</time>
            </button>
          );
        })}
        {!items.length && <p className="learning-path-page__task-empty">当前没有复习任务。</p>}
      </div>
      <div
        id={learningPanelId}
        className="learning-path-page__review-list"
        role="tabpanel"
        aria-labelledby={learningTabId}
        hidden={activeTab !== 'learning'}
      >
        {learningItems.map((item) => (
          <button
            key={item.id}
            type="button"
            className="learning-path-page__task-item"
            onClick={() => onOpenLearning(item)}
          >
            <TaskProgress value={item.progress} />
            <span>
              <strong>{item.title}</strong>
              <small>{item.detail}</small>
            </span>
            <time>{item.meta || '最近'}</time>
          </button>
        ))}
        {!learningItems.length && (
          <p className="learning-path-page__task-empty">完成一次学习后，近期记录会显示在这里。</p>
        )}
      </div>
      {activeTab === 'review' && (
        <p className="learning-path-page__review-note">
          完成知识点配套题并通过批改后，系统会自动加入复习队列。
        </p>
      )}
    </section>
  );
}

function LearningRoute({ onNavigate }) {
  const [routeView, setRouteView] = useState('orbit');
  const [routeState, setRouteState] = useState({
    loading: true,
    error: '',
    nodes: [],
    stages: [],
  });
  const [selectedNode, setSelectedNode] = useState(null);
  const [planningDetails, setPlanningDetails] = useState({
    loaded: false,
    loading: false,
    error: '',
    longTerm: '',
    shortTerm: '',
  });
  const mountedRef = useRef(true);
  const drillGenerationRef = useRef(0);
  const pendingDrillsRef = useRef(new Map());
  const planningGenerationRef = useRef(0);
  const planningRequestRef = useRef(null);

  useEffect(() => {
    const pendingDrills = pendingDrillsRef.current;
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      drillGenerationRef.current += 1;
      planningGenerationRef.current += 1;
      pendingDrills.clear();
      planningRequestRef.current = null;
    };
  }, []);

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
            level: node.status === 'in_progress'
              ? '当前阶段'
              : node.status === 'completed'
                ? '已完成'
                : '待学习',
            duration: node.child_count ? `${node.child_count} 本教材` : '教材待规划',
            tasks: [node.description || '阶段目标待补充'],
            resources: [],
          }));
        setRouteState({ loading: false, error: '', nodes, stages });
      })
      .catch((error) => {
        if (!cancelled) {
          setRouteState({
            loading: false,
            error: error.message || '学习路径暂时无法读取',
            nodes: [],
            stages: [],
          });
        }
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
      const stageId = String(node.node_id || '');
      const pending = pendingDrillsRef.current.get(stageId);
      if (pending) return pending;

      const generation = drillGenerationRef.current + 1;
      drillGenerationRef.current = generation;
      const request = loadPlannedLearningPath(stageId)
        .then((childPage) => {
          if (!mountedRef.current || drillGenerationRef.current !== generation) return;
          const childNodes = Array.isArray(childPage.nodes)
            ? childPage.nodes.map(adaptPlannedPathNode)
            : [];
          setRouteState((current) => ({ ...current, nodes: childNodes, error: '' }));
          setSelectedNode(null);
        })
        .catch((error) => {
          if (!mountedRef.current || drillGenerationRef.current !== generation) return;
          setRouteState((current) => ({
            ...current,
            error: error.message || '教材路径暂时无法读取',
          }));
        })
        .finally(() => {
          if (pendingDrillsRef.current.get(stageId) === request) {
            pendingDrillsRef.current.delete(stageId);
          }
        });
      pendingDrillsRef.current.set(stageId, request);
      return request;
    }

    const navigation = node?.navigation || {};
    onNavigate?.({
      page: 'practice',
      params: {
        view: 'textbook-chapters',
        route: navigation.route_id || 'textbook_14_5',
        lv1: navigation.book || String(node?.title || '').replace(/[《》]/g, ''),
        source: 'learning-plan',
      },
    });
  };

  const showPlanningDetails = async () => {
    setRouteView('details');
    if (planningDetails.loaded || planningRequestRef.current) return;
    setPlanningDetails((current) => ({ ...current, loading: true, error: '' }));
    const generation = planningGenerationRef.current + 1;
    planningGenerationRef.current = generation;
    const request = fetchWithAuth(`${MAIN_API_BASE}/learning-context`)
      .then(async (response) => ({
        response,
        payload: await readJsonResponse(response, {}),
      }));
    planningRequestRef.current = request;
    try {
      const { response, payload } = await request;
      if (!response.ok) {
        const detail = payload?.detail;
        throw new Error(typeof detail === 'string' ? detail : detail?.message || '学习规划暂时无法读取');
      }
      if (!mountedRef.current || planningGenerationRef.current !== generation) return;
      setPlanningDetails({
        loaded: true,
        loading: false,
        error: '',
        longTerm: String(payload?.long_term_plan?.content || ''),
        shortTerm: String(payload?.short_term_plan?.content || ''),
      });
    } catch (error) {
      if (!mountedRef.current || planningGenerationRef.current !== generation) return;
      setPlanningDetails((current) => ({
        ...current,
        loaded: false,
        loading: false,
        error: error.message || '学习规划暂时无法读取',
      }));
    } finally {
      if (planningRequestRef.current === request) planningRequestRef.current = null;
    }
  };

  const returnToOrbit = () => {
    planningGenerationRef.current += 1;
    planningRequestRef.current = null;
    setRouteView('orbit');
    setSelectedNode(null);
  };

  return (
    <section className="learning-path-page__route" data-view={routeView} aria-label="学习路径规划">
      <header className="learning-path-page__route-header">
        <div className="learning-path-page__route-kicker">
          <h2>学习路径规划</h2>
          {routeView !== 'details' && (
            <button type="button" onClick={showPlanningDetails}>了解详情</button>
          )}
        </div>
        {routeView === 'orbit' ? (
          <button
            type="button"
            className="learning-path-page__route-full-link"
            onClick={() => setRouteView('cards')}
          >
            查看完整学习路径 <ArrowRight aria-hidden="true" size={14} />
          </button>
        ) : (
          <button type="button" onClick={returnToOrbit}>返回短期学习路径</button>
        )}
      </header>

      {routeView === 'orbit' && (
        <div className="learning-path-page__route-orbit-layout">
          <h3 className="learning-path-page__route-summary">短期学习路径</h3>
          {routeState.loading && <div className="learning-path-page__route-state">正在读取学习路径…</div>}
          {!routeState.loading && routeState.error && (
            <div className="learning-path-page__route-state" role="alert">{routeState.error}</div>
          )}
          {!routeState.loading && !routeState.error && routeState.stages.length === 0 && (
            <div className="learning-path-page__route-state">尚未生成学习路径</div>
          )}
          {!routeState.loading && !routeState.error && routeState.stages.length > 0 && (
            <LearningPathOverview
              nodes={routeState.nodes}
              edges={edges}
              selectedId={selectedNode?.membership_id}
              onSelect={(node) => {
                setSelectedNode(node);
                openNode(node);
              }}
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
          onCreatePlan={() => onNavigate?.({
            page: 'assistant',
            params: { context: '请结合我的学习状态，给我制定一份长期学习规划。' },
          })}
        />
      )}

      {routeView === 'details' && (
        <div
          className="learning-path-page__route-details"
          role="region"
          aria-label="长期规划和短期规划说明"
          tabIndex="0"
        >
          {planningDetails.loading && (
            <div className="learning-path-page__route-details-state" role="status">正在读取规划说明…</div>
          )}
          {!planningDetails.loading && planningDetails.error && (
            <div className="learning-path-page__route-details-state" role="alert">{planningDetails.error}</div>
          )}
          {!planningDetails.loading && !planningDetails.error && (
            <div className="learning-path-page__route-details-grid">
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

export default function LearningPathPage({ currentUser, onNavigate }) {
  const [payload, setPayload] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const loadSummary = async () => {
      setLoading(true);
      setError('');
      try {
        const response = await fetchWithAuth(`${MAIN_API_BASE}/dashboard/home`);
        const result = await readJsonResponse(response, {});
        if (!response.ok) throw new Error(result.detail || '学习数据暂不可用');
        if (!result || typeof result !== 'object' || Array.isArray(result)) {
          throw new Error('学习数据暂不可用');
        }
        if (!cancelled) setPayload(result);
      } catch (requestError) {
        if (!cancelled) setError(requestError.message || '学习数据暂不可用');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    loadSummary();
    return () => { cancelled = true; };
  }, []);

  const currentTask = payload.current_learning_task;
  const learningItems = Array.isArray(currentTask?.items) && currentTask.items.length
    ? currentTask.items.map((item) => ({
      id: `task-item:${item.task_item_id || item.title}`,
      title: item.title || currentTask.title || '今日学习任务',
      detail: item.kp_name || currentTask.description || '继续完成当前学习任务',
      meta: item.estimated_minutes ? `${item.estimated_minutes} 分钟` : currentTask.duration || '',
      progress: readProgress(item.progress, readProgress(item, readProgress(currentTask, 0))),
      raw: item,
    }))
    : currentTask
      ? [{
        id: `task:${currentTask.task_id || currentTask.title}`,
        title: currentTask.title || '今日学习任务',
        detail: currentTask.description || '继续完成当前学习任务',
        meta: currentTask.duration || '',
        progress: readProgress(currentTask, 0),
        raw: currentTask,
      }]
      : [];

  const reviewItems = (Array.isArray(payload.review_queue?.entries) ? payload.review_queue.entries : [])
    .filter((entry) => entry?.memory_unit || entry?.task)
    .slice(0, 8);

  const openLearningItem = (item) => {
    const action = item?.raw?.action;
    if (action?.destination === 'workshop.practice') {
      onNavigate?.({
        page: 'practice',
        params: {
          view: 'workspace',
          taskType: 'question_training',
          ...(action.params || {}),
        },
      });
      return;
    }
    onNavigate?.({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'question_training',
        taskItemId: item?.raw?.task_item_id || '',
      },
    });
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

  const displayName = String(currentUser?.display_name || currentUser?.username || '同学').trim() || '同学';

  return (
    <div className="learning-path-page" aria-busy={loading}>
      <header className="learning-path-page__intro">
        <div>
          <h1>{displayName}，按计划稳步推进</h1>
        </div>
      </header>

      {error && <div className="learning-path-page__notice" role="alert">{error}</div>}

      <section className="learning-path-page__learning-area" aria-label="学习路线与学习进度">
        <LearningRoute onNavigate={onNavigate} />
        <ReviewTaskRail
          items={reviewItems}
          learningItems={learningItems}
          onOpen={openReviewItem}
          onOpenLearning={openLearningItem}
        />
      </section>
    </div>
  );
}
