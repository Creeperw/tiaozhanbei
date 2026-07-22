import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  BookMarked,
  ChevronRight,
  CircleHelp,
  Clapperboard,
  Grid2X2,
  ListOrdered,
  LoaderCircle,
  Orbit,
  Pause,
  Play,
  Minus,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Sparkles,
} from 'lucide-react';

import {
  loadAtlasDetail,
  loadAtlasNodes,
  loadAtlasStatus,
  resolveAtlasContext,
  warmAtlas,
} from './knowledgeAtlasApi';
import {
  arrangeAtlasNodes,
  filterAtlasNodes,
  getAtlasResourceKind,
  normalizeAtlasNode,
} from './knowledgeAtlasModel';
import KnowledgeAtlasDetail from './KnowledgeAtlasDetail';
import useKnowledgeAtlasCanvas from './useKnowledgeAtlasCanvas';
import { rememberKnowledgeAtlasRuntime } from './knowledgeAtlasFeature';
import './knowledgeAtlas.css';

const arrangements = [
  { id: 'sphere', label: '球面布局', short: '球面', Icon: Orbit },
  { id: 'sequence', label: '顺序列表', short: '顺序', Icon: ListOrdered },
  { id: 'semantic', label: '相关聚类', short: '聚类', Icon: Grid2X2 },
];

function initialPath(context) {
  return { lv1: context?.lv1 || '', lv2: context?.lv2 || '' };
}

function initialLevel(context) {
  if (context?.kpId || context?.kp_id || context?.lv2) return 3;
  if (context?.lv1) return 2;
  return 1;
}

function resourceLabel(kind) {
  return {
    both: '题目 + 视频',
    question: '含题目',
    video: '含视频',
    plain: '知识点',
  }[kind];
}

export class KnowledgeAtlasErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <section className="knowledge-atlas knowledge-atlas--crashed" role="alert">
        <Sparkles aria-hidden="true" size={28} />
        <h2>知识星球暂时无法显示</h2>
        <p>{this.state.error.message || '可视化模块发生异常，其他知识库工作区不受影响。'}</p>
        <button type="button" onClick={() => this.setState({ error: null })}>重新加载模块</button>
      </section>
    );
  }
}

export default function KnowledgeAtlas({ initialContext = {}, onOpenLegacy, onDisabled, workspaceNavigation = null }) {
  const [status, setStatus] = useState(null);
  const [route, setRoute] = useState(initialContext.route || 'textbook_14_5');
  const [level, setLevel] = useState(() => initialLevel(initialContext));
  const [path, setPath] = useState(() => initialPath(initialContext));
  const [nodes, setNodes] = useState([]);
  const [stats, setStats] = useState({});
  const [query, setQuery] = useState('');
  const [arrangement, setArrangement] = useState('sphere');
  const [autoRotate, setAutoRotate] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [retryVersion, setRetryVersion] = useState(0);
  const [selectedNode, setSelectedNode] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [detailClosing, setDetailClosing] = useState(false);
  const [pendingKpId, setPendingKpId] = useState(initialContext.kpId || initialContext.kp_id || '');
  const searchRef = useRef(null);
  const detailControllerRef = useRef(null);
  const closingTimerRef = useRef(null);
  const enterNodeRef = useRef(null);
  const arrangementRef = useRef('sphere');
  const viewPresetRef = useRef(() => {});

  useEffect(() => {
    if (initialContext.trackId || initialContext.membershipId) return undefined;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      const nextPath = { lv1: initialContext.lv1 || '', lv2: initialContext.lv2 || '' };
      const nextLevel = initialContext.kpId || initialContext.kp_id || initialContext.lv2
        ? 3
        : initialContext.lv1 ? 2 : 1;
      setRoute(initialContext.route || 'textbook_14_5');
      setPath(nextPath);
      setLevel(nextLevel);
      setPendingKpId(initialContext.kpId || initialContext.kp_id || '');
    });
    return () => { cancelled = true; };
  }, [initialContext.kpId, initialContext.kp_id, initialContext.lv1, initialContext.lv2, initialContext.membershipId, initialContext.route, initialContext.trackId]);

  useEffect(() => {
    const controller = new AbortController();
    let warmTimer = null;
    const bootstrap = async () => {
      const [statusResult] = await Promise.allSettled([
        loadAtlasStatus({ signal: controller.signal }),
      ]);
      if (controller.signal.aborted) return;
      if (statusResult.status === 'fulfilled') {
        setStatus(statusResult.value);
        rememberKnowledgeAtlasRuntime(statusResult.value?.enabled !== false);
        if (statusResult.value?.enabled === false) {
          setNotice('知识星球已由运行时开关关闭，可继续使用资料与题目工作区。');
          onDisabled?.();
        }
        else if (statusResult.value?.available === false) setNotice(statusResult.value?.errors?.[0] || '知识星球资产暂未就绪，其他模块仍可正常使用。');
      } else if (statusResult.reason?.name !== 'AbortError') {
        setNotice('状态接口暂时不可用，正在尝试直接读取知识路线。');
      }
      if (initialContext.trackId || initialContext.membershipId) {
        try {
          const resolved = await resolveAtlasContext({
            trackId: initialContext.trackId,
            membershipId: initialContext.membershipId,
            signal: controller.signal,
          });
          if (controller.signal.aborted) return;
          if (resolved.route) setRoute(resolved.route);
          setPath({ lv1: resolved.lv1 || '', lv2: resolved.lv2 || '' });
          setLevel(resolved.kp_id || resolved.lv2 ? 3 : resolved.lv1 ? 2 : 1);
          if (resolved.kp_id) setPendingKpId(resolved.kp_id);
          if (resolved.notice) setNotice(resolved.notice);
          else if (!resolved.resolved) setNotice('未能精确定位首页节点，已打开默认教材路线。');
        } catch (resolveError) {
          if (resolveError.name !== 'AbortError') setNotice('首页节点未能精确映射，已打开默认教材路线。');
        }
      }
      warmTimer = window.setTimeout(() => warmAtlas({ signal: controller.signal }).catch(() => {}), 800);
    };
    bootstrap();
    return () => {
      controller.abort();
      if (warmTimer) window.clearTimeout(warmTimer);
    };
  }, [initialContext.membershipId, initialContext.trackId, onDisabled]);

  useEffect(() => {
    const controller = new AbortController();
    queueMicrotask(() => {
      if (controller.signal.aborted) return;
      setLoading(true);
      setError('');
      setQuery('');
      loadAtlasNodes({ level, route, lv1: path.lv1, lv2: path.lv2, signal: controller.signal })
        .then((payload) => {
          if (controller.signal.aborted) return;
          const nextNodes = (payload.nodes || []).map(normalizeAtlasNode);
          const compact = level === 3 && nextNodes.length > 0 && nextNodes.length < 12 && arrangementRef.current !== 'sequence';
          const clustered = level === 3 && nextNodes.length >= 12 && arrangementRef.current === 'semantic';
          setNodes(nextNodes);
          setStats(payload.stats || {});
          if (compact || clustered) {
            setAutoRotate(false);
            viewPresetRef.current({ yaw: 0, pitch: 0, zoom: compact ? 1.28 : 1.14 });
          } else {
            viewPresetRef.current({ zoom: 1 });
          }
        })
        .catch((requestError) => {
          if (!controller.signal.aborted && requestError.name !== 'AbortError') {
            setNodes([]);
            setError(requestError.message || '知识节点加载失败');
          }
        })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    });
    return () => controller.abort();
  }, [level, path.lv1, path.lv2, retryVersion, route]);

  useEffect(() => () => {
    detailControllerRef.current?.abort();
    if (closingTimerRef.current) window.clearTimeout(closingTimerRef.current);
  }, []);

  useEffect(() => {
    const onShortcut = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    document.addEventListener('keydown', onShortcut);
    return () => document.removeEventListener('keydown', onShortcut);
  }, []);

  const filteredNodes = useMemo(() => filterAtlasNodes(nodes, query), [nodes, query]);
  const compactSphere = level === 3 && nodes.length > 0 && nodes.length < 12 && arrangement !== 'sequence';
  const clusterAllowed = level === 3 && nodes.length >= 12;
  const clusterSphere = compactSphere || (arrangement === 'semantic' && clusterAllowed);
  const effectiveArrangement = compactSphere
    ? 'compact'
    : arrangement === 'semantic' && !clusterAllowed ? 'sphere' : arrangement;
  const displayedArrangement = effectiveArrangement === 'compact' ? 'sphere' : effectiveArrangement;
  const arrangedNodes = useMemo(
    () => arrangeAtlasNodes(filteredNodes, effectiveArrangement, level),
    [effectiveArrangement, filteredNodes, level],
  );
  const directoryNodes = effectiveArrangement === 'sequence' ? arrangedNodes : filteredNodes;

  const openDetail = useCallback((node) => {
    const normalized = normalizeAtlasNode(node);
    detailControllerRef.current?.abort();
    const controller = new AbortController();
    detailControllerRef.current = controller;
    setSelectedNode(normalized);
    setDetail(null);
    setDetailError('');
    setDetailLoading(true);
    setDetailClosing(false);
    loadAtlasDetail(normalized.id, { questionLimit: 50, signal: controller.signal })
      .then((payload) => { if (!controller.signal.aborted) setDetail(payload); })
      .catch((requestError) => {
        if (!controller.signal.aborted && requestError.name !== 'AbortError') setDetailError(requestError.message || '详情加载失败');
      })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
  }, []);

  useEffect(() => {
    if (!pendingKpId) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      const matched = nodes.find((node) => node.id === pendingKpId);
      openDetail(matched || { id: pendingKpId, name: pendingKpId });
      setPendingKpId('');
    });
    return () => { cancelled = true; };
  }, [nodes, openDetail, pendingKpId]);

  const closeDetail = useCallback(() => {
    detailControllerRef.current?.abort();
    setDetailClosing(true);
    closingTimerRef.current = window.setTimeout(() => {
      setSelectedNode(null);
      setDetail(null);
      setDetailClosing(false);
    }, 160);
  }, []);

  const handleCanvasNodeActivate = useCallback((node) => enterNodeRef.current?.(node), []);
  const {
    canvasRef,
    bindings,
    hovered,
    resetView,
    zoomBy,
    zoom,
    reducedMotion,
    morphDuration,
    setViewPreset,
    startSpaceNavigation,
    spaceTransitionMode,
    spaceTransitionDuration,
  } = useKnowledgeAtlasCanvas({
    nodes: arrangedNodes,
    autoRotate,
    paused: Boolean(selectedNode) || effectiveArrangement === 'sequence',
    clustered: clusterSphere,
    resourceStyles: level === 3,
    loading,
    onNodeActivate: handleCanvasNodeActivate,
  });

  useEffect(() => {
    viewPresetRef.current = setViewPreset;
  }, [setViewPreset]);

  const selectArrangement = useCallback((nextArrangement) => {
    arrangementRef.current = nextArrangement;
    setArrangement(nextArrangement);
    const nextCompact = level === 3 && nodes.length > 0 && nodes.length < 12 && nextArrangement !== 'sequence';
    const nextClustered = level === 3 && nodes.length >= 12 && nextArrangement === 'semantic';
    if (nextCompact || nextClustered) {
      setAutoRotate(false);
      setViewPreset({ yaw: 0, pitch: 0, zoom: nextCompact ? 1.28 : 1.14 });
    } else {
      setViewPreset({ zoom: 1 });
    }
  }, [level, nodes.length, setViewPreset]);

  const beginLayerNavigation = useCallback((nextLevel, nextPath, direction, origin = null) => {
    if (nextLevel !== 3 && arrangementRef.current === 'semantic') {
      arrangementRef.current = 'sphere';
      setArrangement('sphere');
    }
    startSpaceNavigation(direction, origin);
    setLoading(true);
    setError('');
    setQuery('');
    setNodes([]);
    setPath(nextPath);
    setLevel(nextLevel);
  }, [startSpaceNavigation]);

  const enterNode = useCallback((node) => {
    if (level === 1) {
      beginLayerNavigation(2, { lv1: node.name, lv2: '' }, 'dive', node);
    } else if (level === 2) {
      beginLayerNavigation(3, { lv1: path.lv1, lv2: node.name }, 'dive', node);
    } else {
      openDetail(node);
    }
  }, [beginLayerNavigation, level, openDetail, path.lv1]);
  useEffect(() => {
    enterNodeRef.current = enterNode;
  }, [enterNode]);

  const goBack = useCallback(() => {
    if (level === 3) {
      beginLayerNavigation(2, { lv1: path.lv1, lv2: '' }, 'back');
    } else if (level === 2) {
      beginLayerNavigation(1, { lv1: '', lv2: '' }, 'back');
    }
  }, [beginLayerNavigation, level, path.lv1]);

  const unit = level === 1 ? '教材' : level === 2 ? '章节' : '知识点';

  return (
    <section className="knowledge-atlas" aria-labelledby="knowledge-atlas-title" data-level={level} data-arrangement={effectiveArrangement}>
      <header className="knowledge-atlas__header" aria-label="知识星球顶栏">
        <div className="knowledge-atlas__brand">
          <span><Orbit aria-hidden="true" size={19} /></span>
          <div><small>Knowledge atlas</small><h1 id="knowledge-atlas-title">知识星球</h1></div>
        </div>
        <div className="knowledge-atlas__context">
          <strong>{path.lv2 || path.lv1 || '教材知识目录'}</strong>
          <span>{path.lv2 ? `${path.lv1} · ${unit}` : path.lv1 ? `教材目录 · ${unit}` : '按教材、章节、知识点顺序浏览配套资源'}</span>
        </div>
        {workspaceNavigation || (onOpenLegacy && <button type="button" className="knowledge-atlas__legacy" onClick={onOpenLegacy}>旧版钻取</button>)}
      </header>

      <div className="knowledge-atlas__toolbar" role="toolbar" aria-label="知识星球视图工具">
        <nav className="knowledge-atlas__breadcrumbs" aria-label="知识星球面包屑">
          <button type="button" onClick={() => { if (level > 1) beginLayerNavigation(1, { lv1: '', lv2: '' }, 'back'); }}>教材目录</button>
          {path.lv1 && <><ChevronRight aria-hidden="true" size={13} /><button type="button" onClick={() => { if (level > 2) beginLayerNavigation(2, { lv1: path.lv1, lv2: '' }, 'back'); }}>{path.lv1}</button></>}
          {path.lv2 && <><ChevronRight aria-hidden="true" size={13} /><span aria-current="page">{path.lv2}</span></>}
        </nav>
        <div className="knowledge-atlas__search">
          <Search aria-hidden="true" size={15} />
          <input ref={searchRef} type="search" aria-label="搜索当前层" placeholder={`搜索当前${unit} · Ctrl K`} value={query} onChange={(event) => setQuery(event.target.value)} />
          {query && <kbd>{filteredNodes.length}</kbd>}
        </div>
        <div className="knowledge-atlas__arrangements" aria-label="节点布局">
          {arrangements.filter((item) => item.id !== 'semantic' || clusterAllowed).map((item) => {
            const ArrangementIcon = item.Icon;
            return (
              <button key={item.id} type="button" aria-label={item.label} aria-pressed={displayedArrangement === item.id} onClick={() => selectArrangement(item.id)} title={item.label}>
                <ArrangementIcon aria-hidden="true" size={15} /><span>{item.short}</span>
              </button>
            );
          })}
        </div>
        <button type="button" className="knowledge-atlas__icon-button" aria-label={autoRotate ? '暂停自动旋转' : '继续自动旋转'} onClick={() => setAutoRotate((value) => !value)} disabled={reducedMotion || clusterSphere || effectiveArrangement === 'sequence'}>
          {autoRotate ? <Pause aria-hidden="true" size={15} /> : <Play aria-hidden="true" size={15} />}
        </button>
        <button type="button" className="knowledge-atlas__icon-button" aria-label="放大知识星球" onClick={() => zoomBy(1.16)}><Plus aria-hidden="true" size={15} /></button>
        <button type="button" className="knowledge-atlas__icon-button" aria-label="缩小知识星球" onClick={() => zoomBy(1 / 1.16)}><Minus aria-hidden="true" size={15} /></button>
        <button type="button" className="knowledge-atlas__icon-button" aria-label="复位知识星球" onClick={() => resetView(clusterSphere ? { yaw: 0, pitch: 0, zoom: compactSphere ? 1.28 : 1.14 } : undefined)}><RotateCcw aria-hidden="true" size={15} /></button>
      </div>

      {notice && <div className="knowledge-atlas__notice" role="status">{notice}<button type="button" aria-label="关闭提示" onClick={() => setNotice('')}>×</button></div>}

      <div className={`knowledge-atlas__workspace${effectiveArrangement === 'sequence' ? ' is-sequence' : ''}`}>
        <div
          className="knowledge-atlas__stage"
          data-testid="knowledge-atlas-stage"
          data-morph-duration={morphDuration}
          data-space-transition={spaceTransitionMode || 'idle'}
          data-space-transition-duration={spaceTransitionDuration}
          aria-busy={loading || Boolean(spaceTransitionMode)}
        >
          <div className="knowledge-atlas__stage-meta">
            <div><small>LEVEL {String(level).padStart(2, '0')}</small><h2>{level === 1 ? '教材目录' : level === 2 ? path.lv1 : path.lv2}</h2><p>{level === 1 ? '选择教材，进入章节知识空间' : level === 2 ? '继续下钻，定位到可学习知识点' : '打开节点，查看视频、切片、图片、公式和题目'}</p></div>
            <span><b>{filteredNodes.length.toLocaleString('zh-CN')}</b>{unit}</span>
          </div>

          {level > 1 && <button type="button" className="knowledge-atlas__back" onClick={goBack}><ArrowLeft aria-hidden="true" size={15} />返回上一级</button>}
          {loading && <div className="knowledge-atlas__stage-state" role="status"><LoaderCircle aria-hidden="true" size={24} />正在组织知识空间</div>}
          {!loading && error && (
            <div className="knowledge-atlas__stage-state is-error" role="alert"><p>{error}</p><button type="button" onClick={() => setRetryVersion((value) => value + 1)}><RefreshCw aria-hidden="true" size={14} />重新加载当前层</button></div>
          )}
          {!loading && !error && filteredNodes.length === 0 && <div className="knowledge-atlas__stage-state"><Search aria-hidden="true" size={22} />当前层没有匹配节点</div>}
          <canvas
            ref={canvasRef}
            className={effectiveArrangement === 'sequence' ? 'is-list-mode' : ''}
            aria-label="知识星球画布"
            data-zoom={zoom.toFixed(2)}
            data-arranged-node-count={arrangedNodes.length}
            data-resource-styles={level === 3}
            {...bindings}
          />
          {effectiveArrangement === 'sequence' && (
            <div className="knowledge-atlas__sequence-panel" aria-label="当前层节点列表">
              <header>
                <div><ListOrdered aria-hidden="true" size={16} /><strong>{unit}顺序</strong></div>
                <span>{directoryNodes.length.toLocaleString('zh-CN')} 项</span>
              </header>
              <div className="knowledge-atlas__sequence-list">
                {directoryNodes.map((node, index) => {
                  const kind = getAtlasResourceKind(node);
                  const action = level === 3 ? `打开${node.name}详情` : `进入${node.name}`;
                  return (
                    <button type="button" key={node.id} aria-label={action} data-resource-kind={kind} onClick={() => enterNode(node)}>
                      <span className="knowledge-atlas__node-index">{String(index + 1).padStart(2, '0')}</span>
                      <span className="knowledge-atlas__node-copy"><strong>{node.name}</strong><small>{node.alias || `${node.count || node.children_count || 0} ${level === 3 ? '项资源' : '个下级节点'}`}</small></span>
                      <span className="knowledge-atlas__node-resource">
                        {(kind === 'question' || kind === 'both') && <CircleHelp aria-label={`${node.question_count} 道题`} size={14} />}
                        {(kind === 'video' || kind === 'both') && <Clapperboard aria-label={`${node.video_count} 个视频`} size={14} />}
                        <ChevronRight aria-hidden="true" size={14} />
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          {hovered && effectiveArrangement !== 'sequence' && (
            <div className="knowledge-atlas__tooltip" role="status">
              <strong>{hovered.name}</strong><span>{resourceLabel(getAtlasResourceKind(hovered))} · {hovered.count || 0} 项</span>
            </div>
          )}
          {level === 3 && (
            <div className="knowledge-atlas__legend" aria-label="资源节点图例">
              <span data-kind="both"><i />题目 + 视频</span>
              <span data-kind="question"><i />含题目</span>
              <span data-kind="video"><i />含视频</span>
              <span data-kind="plain"><i />纯知识点</span>
            </div>
          )}
        </div>

        {effectiveArrangement !== 'sequence' && <aside className="knowledge-atlas__node-panel" aria-label="当前层节点列表">
          <header><div><BookMarked aria-hidden="true" size={15} /><span>当前层目录</span></div><small>{effectiveArrangement === 'sequence' ? '顺序浏览' : '键盘与移动端入口'}</small></header>
          <div className="knowledge-atlas__node-list">
            {directoryNodes.map((node, index) => {
              const kind = getAtlasResourceKind(node);
              const action = level === 3 ? `打开${node.name}详情` : `进入${node.name}`;
              return (
                <button type="button" key={node.id} aria-label={action} data-resource-kind={kind} onClick={() => enterNode(node)}>
                  <span className="knowledge-atlas__node-index">{String(index + 1).padStart(2, '0')}</span>
                  <span className="knowledge-atlas__node-copy"><strong>{node.name}</strong><small>{node.alias || `${node.count || node.children_count || 0} ${level === 3 ? '项资源' : '个下级节点'}`}</small></span>
                  <span className="knowledge-atlas__node-resource">
                    {(kind === 'question' || kind === 'both') && <CircleHelp aria-label={`${node.question_count} 道题`} size={14} />}
                    {(kind === 'video' || kind === 'both') && <Clapperboard aria-label={`${node.video_count} 个视频`} size={14} />}
                    <ChevronRight aria-hidden="true" size={14} />
                  </span>
                </button>
              );
            })}
          </div>
        </aside>}
      </div>

      <footer className="knowledge-atlas__footer">
        <span>{stats.lv1 ? `${Number(stats.lv1).toLocaleString('zh-CN')} 教材` : '83 部教材'}</span>
        <span>{stats.lv2 ? `${Number(stats.lv2).toLocaleString('zh-CN')} 章节` : '4,535 个章节'}</span>
        <span>{stats.lv3 ? `${Number(stats.lv3).toLocaleString('zh-CN')} 知识点` : '73,777 个知识点'}</span>
        <span className={status?.warmed ? 'is-ready' : ''}>{status?.warmed ? '资源索引已预热' : '按需加载资源索引'}</span>
        {reducedMotion && <span>已按系统偏好减少空间运动</span>}
      </footer>

      {selectedNode && (
        <div className={detailClosing ? 'knowledge-atlas__detail-layer is-closing' : 'knowledge-atlas__detail-layer'}>
          <KnowledgeAtlasDetail node={selectedNode} detail={detail} loading={detailLoading} error={detailError} onClose={closeDetail} />
        </div>
      )}
    </section>
  );
}
