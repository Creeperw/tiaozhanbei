import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  ChevronRight,
  Maximize2,
  Network,
  Search,
} from 'lucide-react';
import { Button, EmptyState, InlineError, SegmentedControl, Skeleton } from '../ui';
import {
  loadExamNode,
  loadExamNodes,
  loadExamTracks,
  loadLearningTarget,
  loadAllNodeKnowledgePoints,
  saveLearningTarget,
} from './examAtlasApi';
import ExamAtlasCanvas from './ExamAtlasCanvas';
import ExamAtlasDetailDrawer from './ExamAtlasDetailDrawer';
import { filterAtlasNodes, groupKnowledgePoints } from './examAtlasModel';

function trackLabel(track) {
  return track?.title_normalized || track?.title || track?.track_id || '考试考纲';
}

function activationLabel(node) {
  const detail = Number(node.child_count || 0) > 0
    ? `${node.child_count} 个下级节点`
    : '查看已确认知识点';
  const action = Number(node.child_count || 0) > 0
    ? `进入${node.title}`
    : `查看${node.title}知识点`;
  return `${action}：${node.title}${detail}`;
}

function shouldSkipTransition() {
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const testEnvironment = import.meta.env.MODE === 'test';
  return reducedMotion || testEnvironment;
}

function transitionDelay(duration) {
  return new Promise((resolve) => window.setTimeout(resolve, duration));
}

export default function ExamAtlas({ navigationContext = {}, onNavigate }) {
  const [tracks, setTracks] = useState([]);
  const [trackId, setTrackId] = useState(navigationContext.trackId || '');
  const [stack, setStack] = useState([]);
  const [nodes, setNodes] = useState([]);
  const [query, setQuery] = useState('');
  const [view, setView] = useState(() => (
    window.matchMedia?.('(max-width: 767px)').matches ? 'list' : 'globe'
  ));
  const [status, setStatus] = useState('loading');
  const [error, setError] = useState('');
  const [knowledgePoints, setKnowledgePoints] = useState([]);
  const [selectedConcept, setSelectedConcept] = useState(null);
  const [loadingKnowledgePoints, setLoadingKnowledgePoints] = useState(false);
  const [savingTarget, setSavingTarget] = useState(false);
  const [transition, setTransition] = useState('idle');
  const searchRef = useRef(null);
  const initializeGenerationRef = useRef(0);
  const layerGenerationRef = useRef(0);
  const operationGenerationRef = useRef(0);
  const transitionGenerationRef = useRef(0);
  const savingTargetRef = useRef(false);
  const mountedRef = useRef(true);
  const latestRequestedTrackRef = useRef(navigationContext.trackId || '');
  if (navigationContext.trackId) {
    latestRequestedTrackRef.current = navigationContext.trackId;
  }

  const activeTrack = tracks.find((item) => item.track_id === trackId);
  const currentParent = stack.at(-1) || null;
  const filteredNodes = useMemo(
    () => filterAtlasNodes(nodes, query),
    [nodes, query],
  );
  const knowledgePointNodes = useMemo(
    () => knowledgePoints.map((concept) => ({
      id: `kp:${concept.conceptKey}`,
      membership_id: `kp:${concept.conceptKey}`,
      title: concept.name,
      child_count: 0,
      path: concept.path,
      concept,
    })),
    [knowledgePoints],
  );
  const filteredKnowledgePointNodes = useMemo(
    () => filterAtlasNodes(knowledgePointNodes, query),
    [knowledgePointNodes, query],
  );
  const canvasNodes = knowledgePoints.length > 0
    ? filteredKnowledgePointNodes
    : filteredNodes;

  const loadLayer = useCallback(async (nextTrackId, parentMembershipId = null) => {
    const generation = layerGenerationRef.current + 1;
    layerGenerationRef.current = generation;
    setStatus('loading');
    setError('');
    try {
      const payload = await loadExamNodes(nextTrackId, parentMembershipId);
      if (!mountedRef.current || generation !== layerGenerationRef.current) return false;
      setNodes(Array.isArray(payload.items) ? payload.items : []);
      setQuery('');
      setStatus('ready');
      return true;
    } catch (loadError) {
      if (!mountedRef.current || generation !== layerGenerationRef.current) return false;
      setNodes([]);
      setStatus('error');
      setError(loadError.message || '考纲层级加载失败');
      return false;
    }
  }, []);

  const initialize = useCallback(async () => {
    const generation = initializeGenerationRef.current + 1;
    initializeGenerationRef.current = generation;
    operationGenerationRef.current += 1;
    transitionGenerationRef.current += 1;
    layerGenerationRef.current += 1;
    setLoadingKnowledgePoints(false);
    setTransition('idle');
    setStatus('loading');
    setError('');
    try {
      const [tracksPayload, targetPayload] = await Promise.all([
        loadExamTracks(),
        loadLearningTarget().catch(() => ({ target: null })),
      ]);
      if (!mountedRef.current || generation !== initializeGenerationRef.current) return;
      const availableTracks = Array.isArray(tracksPayload.items) ? tracksPayload.items : [];
      const requestedTrackId = navigationContext.trackId || targetPayload.target?.exam_track_id;
      const nextTrackId = availableTracks.some((item) => item.track_id === requestedTrackId)
        ? requestedTrackId
        : '';
      setTracks(availableTracks);
      setSelectedConcept(null);
      setKnowledgePoints([]);
      setStack([]);
      if (!nextTrackId) {
        layerGenerationRef.current += 1;
        setTrackId('');
        setNodes([]);
        setStatus('ready');
        return;
      }
      setTrackId(nextTrackId);
      await loadLayer(nextTrackId);
    } catch (loadError) {
      if (!mountedRef.current || generation !== initializeGenerationRef.current) return;
      setStatus('error');
      setError(loadError.message || '考纲星球加载失败');
    }
  }, [loadLayer, navigationContext.trackId]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      initializeGenerationRef.current += 1;
      layerGenerationRef.current += 1;
      operationGenerationRef.current += 1;
      transitionGenerationRef.current += 1;
    };
  }, []);

  useEffect(() => {
    initialize();
    return () => {
      initializeGenerationRef.current += 1;
      layerGenerationRef.current += 1;
      operationGenerationRef.current += 1;
      transitionGenerationRef.current += 1;
    };
  }, [initialize]);

  const runLayerTransition = async (mode, operation) => {
    if (status === 'loading' || loadingKnowledgePoints || transition !== 'idle') return;
    const generation = transitionGenerationRef.current + 1;
    transitionGenerationRef.current = generation;
    if (shouldSkipTransition()) {
      await operation();
      return;
    }
    setTransition(`${mode}-out`);
    try {
      await transitionDelay(480);
      if (!mountedRef.current || generation !== transitionGenerationRef.current) return;
      await operation();
      if (!mountedRef.current || generation !== transitionGenerationRef.current) return;
      setTransition(`${mode}-in`);
      await transitionDelay(600);
    } finally {
      if (mountedRef.current && generation === transitionGenerationRef.current) {
        setTransition('idle');
      }
    }
  };

  useEffect(() => {
    const handleShortcut = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === 'Escape' && !selectedConcept && stack.length > 0) {
        const nextStack = stack.slice(0, -1);
        runLayerTransition('back', async () => {
          setStack(nextStack);
          setKnowledgePoints([]);
          await loadLayer(trackId, nextStack.at(-1)?.membership_id || null);
        });
      }
    };
    window.addEventListener('keydown', handleShortcut);
    return () => window.removeEventListener('keydown', handleShortcut);
  }, [loadLayer, selectedConcept, stack, trackId]);

  const changeTrack = async (nextTrackId) => {
    if (!nextTrackId || nextTrackId === trackId || savingTargetRef.current) return;
    savingTargetRef.current = true;
    latestRequestedTrackRef.current = nextTrackId;
    const generation = operationGenerationRef.current + 1;
    operationGenerationRef.current = generation;
    transitionGenerationRef.current += 1;
    layerGenerationRef.current += 1;
    setSavingTarget(true);
    setLoadingKnowledgePoints(false);
    setTransition('idle');
    setError('');
    try {
      await saveLearningTarget(nextTrackId);
      if (!mountedRef.current) return;
      if (generation !== operationGenerationRef.current) {
        const latestTrackId = latestRequestedTrackRef.current;
        if (latestTrackId && latestTrackId !== nextTrackId) {
          await saveLearningTarget(latestTrackId);
        }
        return;
      }
      setTrackId(nextTrackId);
      setStack([]);
      setSelectedConcept(null);
      setKnowledgePoints([]);
      await loadLayer(nextTrackId);
    } catch (saveError) {
      if (
        mountedRef.current
        && generation === operationGenerationRef.current
      ) {
        setError(saveError.message || '学习目标保存失败');
      }
    } finally {
      savingTargetRef.current = false;
      if (mountedRef.current) {
        setSavingTarget(false);
      }
    }
  };

  const enterNode = async (node) => {
    if (status === 'loading' || loadingKnowledgePoints || transition !== 'idle') return;
    if (Number(node.child_count || 0) > 0) {
      await runLayerTransition('dive', async () => {
        setStack((current) => [...current, node]);
        await loadLayer(trackId, node.membership_id);
      });
      return;
    }

    await runLayerTransition('dive', async () => {
      const generation = operationGenerationRef.current + 1;
      operationGenerationRef.current = generation;
      const requestedTrackId = trackId;
      setLoadingKnowledgePoints(true);
      setError('');
      try {
        const [detail, kpPayload] = await Promise.all([
          loadExamNode(requestedTrackId, node.membership_id),
          loadAllNodeKnowledgePoints(requestedTrackId, node.membership_id),
        ]);
        if (!mountedRef.current || generation !== operationGenerationRef.current) return;
        setStack((current) => {
          if (current.at(-1)?.membership_id === node.membership_id) return current;
          return [...current, {
            ...node,
            title: detail.breadcrumb?.at(-1)?.title || node.title,
          }];
        });
        setKnowledgePoints(groupKnowledgePoints(kpPayload.items || []));
        setView('list');
      } catch (loadError) {
        if (mountedRef.current && generation === operationGenerationRef.current) {
          setError(loadError.message || '知识点加载失败');
        }
      } finally {
        if (mountedRef.current && generation === operationGenerationRef.current) {
          setLoadingKnowledgePoints(false);
        }
      }
    });
  };

  const activateCanvasNode = (node) => {
    if (node.concept) {
      setSelectedConcept(node.concept);
      return;
    }
    enterNode(node);
  };

  const navigateToStackIndex = async (index) => {
    if (status === 'loading' || loadingKnowledgePoints || transition !== 'idle') return;
    const nextStack = index < 0 ? [] : stack.slice(0, index + 1);
    await runLayerTransition('back', async () => {
      setStack(nextStack);
      setKnowledgePoints([]);
      setSelectedConcept(null);
      await loadLayer(trackId, nextStack.at(-1)?.membership_id || null);
    });
  };

  const currentLayerTitle = currentParent?.title || trackLabel(activeTrack);
  const currentMembershipId = currentParent?.membership_id || null;

  return (
    <section className="exam-atlas" aria-labelledby="exam-atlas-title">
      <header className="exam-atlas__toolbar">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-semibold text-emerald-700">
            <Network aria-hidden="true" size={15} />
            2025 正式考试考纲
          </div>
          <h2 id="exam-atlas-title" className="mt-2 truncate text-xl font-semibold text-slate-950">
            {trackLabel(activeTrack)}
          </h2>
        </div>

        <div className="exam-atlas__controls">
          <label className="exam-atlas__track-select">
            <span className="sr-only">切换考试目标</span>
            <select
              value={trackId}
              disabled={savingTarget}
              aria-busy={savingTarget || undefined}
              onChange={(event) => changeTrack(event.target.value)}
            >
              <option value="">选择正式考试目标</option>
              {tracks.map((track) => (
                <option key={track.track_id} value={track.track_id}>{trackLabel(track)}</option>
              ))}
            </select>
          </label>
          <label className="exam-atlas__search">
            <Search aria-hidden="true" size={16} />
            <span className="sr-only">搜索当前球面</span>
            <input
              ref={searchRef}
              type="search"
              aria-label="搜索当前球面"
              placeholder="搜索当前层"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <kbd>Ctrl K</kbd>
          </label>
          <SegmentedControl
            label="考纲视图"
            value={view}
            onChange={setView}
            options={[
              { value: 'globe', label: '星球' },
              { value: 'list', label: '列表' },
            ]}
          />
        </div>
      </header>

      <nav className="exam-atlas__breadcrumbs" aria-label="考纲路径">
        <button type="button" onClick={() => navigateToStackIndex(-1)}>
          {trackLabel(activeTrack)}
        </button>
        {stack.map((item, index) => (
          <React.Fragment key={item.membership_id}>
            <ChevronRight aria-hidden="true" size={14} />
            <button type="button" onClick={() => navigateToStackIndex(index)}>{item.title}</button>
          </React.Fragment>
        ))}
      </nav>

      <div className="exam-atlas__meta">
        <div>
          <span>当前层</span>
          <strong>{currentLayerTitle}</strong>
        </div>
        <div>
          <span>节点</span>
          <strong>{nodes.length}</strong>
        </div>
        <p>层级节点继续钻取；叶子节点只显示已确认的公共知识点。</p>
      </div>

      {status === 'loading' && <Skeleton label="正在构建考纲星球" lines={5} />}
      {status === 'error' && <InlineError message={error} onRetry={initialize} />}

      {status === 'ready' && !trackId && (
        <EmptyState
          title="先选择正式考试目标"
          description="选择当前可用的 2025 医师资格考试轨道后，首页会加载该考纲的直接下一级节点。"
        />
      )}

      {status === 'ready' && trackId && nodes.length === 0 && knowledgePoints.length === 0 && (
        <EmptyState title="当前层暂无节点" description="该考纲层级尚未提供可浏览内容。" />
      )}

      {status === 'ready' && (nodes.length > 0 || knowledgePoints.length > 0) && (
        <div className={`exam-atlas__workspace exam-atlas__workspace--${transition}`} aria-busy={transition !== 'idle'}>
          <div className={view === 'globe' ? 'exam-atlas__visual is-active' : 'exam-atlas__visual'}>
            <ExamAtlasCanvas nodes={canvasNodes} onActivate={activateCanvasNode} />
            <div className="exam-atlas__canvas-caption">
              <Maximize2 aria-hidden="true" size={15} />
              拖动旋转，滚轮缩放，点击节点深入
            </div>
          </div>

          <div className={view === 'list' ? 'exam-atlas__list-panel is-active' : 'exam-atlas__list-panel'}>
            <div className="exam-atlas__list-heading">
              <h3>{knowledgePoints.length > 0 ? '已确认公共知识点' : '当前考纲层级'}</h3>
              {stack.length > 0 && (
                <Button variant="ghost" onClick={() => navigateToStackIndex(stack.length - 2)}>
                  <ArrowLeft aria-hidden="true" size={16} />
                  返回上级
                </Button>
              )}
            </div>

            {filteredNodes.length > 0 && knowledgePoints.length === 0 && (
              <ul role="tree" aria-label="当前考纲层级列表" className="exam-atlas__tree">
                {filteredNodes.map((node) => (
                  <li key={node.membership_id} role="treeitem">
                    <button type="button" aria-label={activationLabel(node)} onClick={() => enterNode(node)}>
                      <span>
                        <strong>{node.title}</strong>
                        <small>{Number(node.child_count || 0) > 0 ? `${node.child_count} 个下级节点` : '查看已确认知识点'}</small>
                      </span>
                      <ChevronRight aria-hidden="true" size={17} />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {query && filteredNodes.length === 0 && knowledgePoints.length === 0 && (
              <p className="exam-atlas__no-results">当前层没有匹配节点</p>
            )}

            {loadingKnowledgePoints && <Skeleton label="正在汇总公共知识点" lines={4} />}
            {query && knowledgePoints.length > 0 && filteredKnowledgePointNodes.length === 0 && (
              <p className="exam-atlas__no-results">当前层没有匹配知识点</p>
            )}
            {!loadingKnowledgePoints && knowledgePoints.length > 0 && (
              <ul className="exam-atlas__kp-list" aria-label="已确认公共知识点列表">
                {filteredKnowledgePointNodes.map(({ concept }) => (
                  <li key={concept.conceptKey}>
                    <button
                      type="button"
                      aria-label={`打开知识点${concept.name}`}
                      onClick={() => setSelectedConcept(concept)}
                    >
                      <span>
                        <strong>{concept.name}</strong>
                        <small>{concept.variants.length} 条记录 · {concept.acceptedCount} 个已确认关联</small>
                      </span>
                      <ChevronRight aria-hidden="true" size={17} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {error && status !== 'error' && <InlineError message={error} />}

      <ExamAtlasDetailDrawer
        concept={selectedConcept}
        trackId={trackId}
        membershipId={currentMembershipId}
        onClose={() => setSelectedConcept(null)}
        onNavigate={onNavigate}
      />
    </section>
  );
}
