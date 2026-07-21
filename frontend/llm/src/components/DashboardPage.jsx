import React, { useEffect, useMemo, useState } from 'react';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import CompactAssistant from './CompactAssistant';
import DashboardDailyWorkspace from './dashboard/DashboardDailyWorkspace';
import {
  buildDailyFeedback,
  buildDailyFocus,
  buildDailySchedule,
} from './dashboard/dashboardDailyModel';
import {
  loadExamNodes,
  loadExamTracks,
  loadLearningTarget,
  loadNodeLearnerSummary,
} from './exam-atlas/examAtlasApi';
import LearningPathOverview from './learning-tree/LearningPathOverview';
import LearningPlanRail from './learning-tree/LearningPlanRail';
import LearningPathTrainingModules from './learning-tree/LearningPathTrainingModules';
import KnowledgeTreeDrilldown from './learning-tree/KnowledgeTreeDrilldown';
import { resolveKnowledgeAtlasEnabled } from './knowledge-atlas/knowledgeAtlasFeature';
import {
  adaptPlannedPathNode,
  loadPlannedLearningPath,
} from './learning-tree/learningPathApi';

const EMPTY_DASHBOARD = {
  hero: {
    greeting: '学习数据暂未加载',
    goal: '请稍后重新进入训练工坊。',
    focus: '开始今日学习',
  },
  today_tasks: [],
  yesterday_feedback: { metrics: [] },
};

function hasDashboardShape(value) {
  return value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length > 0;
}

function getTrackId(target, tracks, requestedTrackId) {
  if (requestedTrackId) return requestedTrackId;
  if (target?.exam_track_id) return target.exam_track_id;
  return tracks?.[0]?.track_id || '';
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

function buildPathEdges(nodes) {
  return nodes.slice(1).map((node, index) => ({
    from: nodes[index].membership_id,
    to: node.membership_id,
    kind: 'spine',
  }));
}

export default function DashboardPage({
  currentUser,
  navigationContext = {},
  onNavigate,
  onKnowledgeContextChange,
}) {
  const [dashboard, setDashboard] = useState(EMPTY_DASHBOARD);
  const [error, setError] = useState('');
  const [track, setTrack] = useState({ id: '', label: '' });
  const [nodes, setNodes] = useState([]);
  const [selectedNode, setSelectedNode] = useState(null);
  const [assistantCollapsed, setAssistantCollapsed] = useState(false);
  const [assistantDocked, setAssistantDocked] = useState(true);
  const [legacyDrilldown, setLegacyDrilldown] = useState(null);
  const [plannedPath, setPlannedPath] = useState(null);
  const [pathParent, setPathParent] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const loadDashboard = async () => {
      setError('');
      try {
        const response = await fetchWithAuth(`${API_BASE}/dashboard/home`);
        const payload = await readJsonResponse(response, {});
        if (!response.ok) throw new Error(payload.detail || '首页数据加载失败');
        if (!hasDashboardShape(payload)) throw new Error('首页数据解析失败');
        if (!cancelled) setDashboard({ ...EMPTY_DASHBOARD, ...payload });
      } catch (loadError) {
        if (!cancelled) {
          setDashboard(EMPTY_DASHBOARD);
          setError(loadError.message || '首页数据加载失败');
        }
      }
    };
    loadDashboard();
    return () => { cancelled = true; };
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
          setTrack({ id: trackId, label: getTrackLabel(target, tracks, trackId) });
          setNodes(planned.nodes.map(adaptPlannedPathNode));
          setPlannedPath(planned);
          setPathParent(null);
          setSelectedNode(null);
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
        setPlannedPath(null);
        setPathParent(null);
        setSelectedNode(null);
        setLegacyDrilldown(null);
        onKnowledgeContextChange?.({ trackId });
      } catch {
        if (!cancelled) {
          setNodes([]);
          setSelectedNode(null);
        }
      }
    };
    loadPath();
    return () => { cancelled = true; };
  }, [navigationContext.trackId, onKnowledgeContextChange]);

  const focus = useMemo(() => buildDailyFocus(dashboard), [dashboard]);
  const schedule = useMemo(() => buildDailySchedule(dashboard), [dashboard]);
  const feedback = useMemo(() => buildDailyFeedback(dashboard), [dashboard]);
  const pathEdges = useMemo(() => buildPathEdges(nodes), [nodes]);
  const greeting = dashboard.hero?.greeting || EMPTY_DASHBOARD.hero.greeting;

  const openKnowledgePlanet = async (node) => {
    if (plannedPath && node.node_type === 'stage') {
      try {
        const childPage = await loadPlannedLearningPath(node.node_id);
        setNodes(childPage.nodes.map(adaptPlannedPathNode));
        setPlannedPath(childPage);
        setPathParent(node);
        setSelectedNode(null);
      } catch (loadError) {
        setError(loadError.message || '教材路径加载失败');
      }
      return;
    }
    if (plannedPath && node.node_type === 'book') {
      const navigation = node.navigation || {};
      onNavigate?.({
        page: 'knowledge',
        params: {
          view: 'atlas',
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

  const selectPathNode = (node) => {
    if (plannedPath && ['stage', 'book'].includes(node.node_type)) {
      openKnowledgePlanet(node);
      return;
    }
    setSelectedNode(node);
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
        showFocus={false}
        fullscreen
        greeting={greeting}
        focus={focus}
        schedule={schedule}
        feedback={feedback}
        assistantCollapsed={assistantCollapsed}
        assistantDocked={assistantDocked}
        pathTopContent={<LearningPathTrainingModules trackId={track.id} onNavigate={onNavigate} />}
        pathHint={plannedPath ? '阶段 → 教材 → 知识点，单击继续' : undefined}
        pathContent={(
          <>
            <div className="learning-path-content">
              {pathParent && (
                <button
                  type="button"
                  className="learning-path-content__back"
                  onClick={async () => {
                    try {
                      const rootPage = await loadPlannedLearningPath();
                      setNodes(rootPage.nodes.map(adaptPlannedPathNode));
                      setPlannedPath(rootPage);
                      setPathParent(null);
                      setSelectedNode(null);
                    } catch (loadError) {
                      setError(loadError.message || '阶段路径加载失败');
                    }
                  }}
                >
                  返回阶段
                </button>
              )}
              {nodes.length > 0 ? (
                <LearningPathOverview
                  nodes={nodes}
                  edges={pathEdges}
                  selectedId={selectedNode?.membership_id}
                  onSelect={selectPathNode}
                  onClearSelection={() => setSelectedNode(null)}
                  onDrill={openKnowledgePlanet}
                  directDrill={Boolean(plannedPath)}
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
              {selectedNode && (
                <LearningPlanRail
                  layout="overlay"
                  node={selectedNode}
                  summary={selectedNode}
                  routeNodes={nodes}
                  onClose={() => setSelectedNode(null)}
                  onStartLearning={(node) => onNavigate?.({
                    page: 'practice',
                    params: {
                      view: 'workspace',
                      trackId: track.id,
                      membershipId: node.membership_id,
                    },
                  })}
                />
              )}
            </div>
          </>
        )}
        assistantContent={(
          <CompactAssistant
            currentUser={currentUser?.username || 'User'}
            dailyGoal={dashboard.hero?.goal || ''}
            dailyFocus={focus.title}
            onCollapsedChange={setAssistantCollapsed}
            onFloatingDockChange={setAssistantDocked}
            onOpenFull={(sessionId) => onNavigate?.('assistant', sessionId)}
          />
        )}
      />
    </>
  );
}
