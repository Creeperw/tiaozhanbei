import React, { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  BookOpen,
  Brain,
  Circle,
  ClipboardCheck,
  X,
} from 'lucide-react';
import {
  loadAllNodeKnowledgePoints,
  loadExamNodes,
  loadNodeLearnerStates,
} from '../exam-atlas/examAtlasApi';
import {
  buildAssistantIntent,
  buildKnowledgeIntent,
  buildPracticeIntent,
} from '../exam-atlas/examAtlasModel';
import { buildKnowledgePlanetEdges, layoutKnowledgePlanet } from './knowledgePlanetModel';

const KnowledgePlanetScene = lazy(() => import('./KnowledgePlanetScene'));

function KnowledgeNodeCard({ node, trackId, knowledgePoints, loading, error, onRetry, onClose, onNavigate }) {
  if (!node) return null;
  const primary = knowledgePoints[0] || null;
  const path = primary?.path || node.path || [node.title];
  const context = primary ? {
    trackId,
    membershipId: node.membership_id,
    kpId: primary.kp_id,
    kpName: primary.name,
    path,
  } : null;
  return (
    <div className="knowledge-node-card-backdrop" onMouseDown={onClose}>
      <section
        role="dialog"
        aria-modal="false"
        aria-label={node.title}
        className="knowledge-node-card"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span>知识卡片</span>
            <h2>{node.title}</h2>
          </div>
          <button type="button" aria-label="关闭知识卡片" onClick={onClose}><X aria-hidden="true" size={17} /></button>
        </header>
        <p className="knowledge-node-card__path">{path.join(' / ')}</p>
        {loading ? (
          <p className="knowledge-node-card__empty">正在汇总知识点…</p>
        ) : error ? (
          <div className="knowledge-node-card__error" role="alert">
            <p>{error}</p>
            <button type="button" onClick={onRetry}>重试加载知识点</button>
          </div>
        ) : knowledgePoints.length ? (
          <ul>
            {knowledgePoints.slice(0, 8).map((item) => (
              <li key={item.kp_id}>
                <Circle aria-hidden="true" size={12} />
                <span>{item.name}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="knowledge-node-card__empty">当前节点暂无已确认公共知识点，可继续查看下级结构。</p>
        )}
        {context && (
          <div className="knowledge-node-card__actions">
            <button type="button" onClick={() => onNavigate(buildPracticeIntent(context))}><ClipboardCheck aria-hidden="true" size={15} />开始练习</button>
            <button type="button" onClick={() => onNavigate(buildKnowledgeIntent(context))}><BookOpen aria-hidden="true" size={15} />查看资料</button>
            <button type="button" onClick={() => onNavigate(buildAssistantIntent(context))}><Brain aria-hidden="true" size={15} />询问助教</button>
          </div>
        )}
      </section>
    </div>
  );
}

export default function KnowledgeTreeDrilldown({ trackId, rootNode, onBack, onNavigate }) {
  const [nodes, setNodes] = useState([rootNode]);
  const [branchErrors, setBranchErrors] = useState({});
  const [selectedNode, setSelectedNode] = useState(null);
  const [learnerStates, setLearnerStates] = useState([]);
  const [knowledgePoints, setKnowledgePoints] = useState([]);
  const [loadingCard, setLoadingCard] = useState(false);
  const [cardError, setCardError] = useState('');
  const generationRef = useRef(0);
  const cardGenerationRef = useRef(0);
  const learnerStateGenerationRef = useRef(0);
  const loadedBranchesRef = useRef(new Set());
  const activationTimerRef = useRef(null);

  const loadBranch = useCallback(async (parent, generation) => {
    if (!parent || loadedBranchesRef.current.has(parent.membership_id)) return;
    loadedBranchesRef.current.add(parent.membership_id);
    try {
      const payload = await loadExamNodes(trackId, parent.membership_id);
      if (generation !== generationRef.current) return;
      const children = Array.isArray(payload.items) ? payload.items : [];
      setNodes((current) => {
        const byId = new Map(current.map((node) => [node.membership_id, node]));
        children.forEach((node) => byId.set(node.membership_id, node));
        return [...byId.values()];
      });
      setBranchErrors((current) => {
        if (!current[parent.membership_id]) return current;
        const next = { ...current };
        delete next[parent.membership_id];
        return next;
      });
    } catch (error) {
      if (generation !== generationRef.current) return;
      loadedBranchesRef.current.delete(parent.membership_id);
      setBranchErrors((current) => ({
        ...current,
        [parent.membership_id]: {
          parent,
          message: error.message || `${parent.title}分支暂时不可用`,
        },
      }));
    }
  }, [trackId]);

  useEffect(() => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    setNodes([rootNode]);
    setBranchErrors({});
    setSelectedNode(null);
    setKnowledgePoints([]);
    setLearnerStates([]);
    setCardError('');
    loadedBranchesRef.current = new Set();
    cardGenerationRef.current += 1;
    loadBranch(rootNode, generation);
    return () => {
      generationRef.current += 1;
      cardGenerationRef.current += 1;
      if (activationTimerRef.current) clearTimeout(activationTimerRef.current);
    };
  }, [loadBranch, rootNode]);

  useEffect(() => {
    const membershipIds = nodes.map((node) => node.membership_id).filter(Boolean);
    if (!membershipIds.length) {
      setLearnerStates([]);
      return undefined;
    }
    const generation = learnerStateGenerationRef.current + 1;
    learnerStateGenerationRef.current = generation;
    const chunks = [];
    for (let index = 0; index < membershipIds.length; index += 120) {
      chunks.push(membershipIds.slice(index, index + 120));
    }
    Promise.all(chunks.map((chunk) => loadNodeLearnerStates(trackId, chunk)))
      .then((payloads) => {
        if (generation !== learnerStateGenerationRef.current) return;
        setLearnerStates(payloads.flatMap((payload) => (
          Array.isArray(payload?.items) ? payload.items : []
        )));
      })
      .catch(() => {
        if (generation === learnerStateGenerationRef.current) setLearnerStates([]);
      });
    return () => {
      if (generation === learnerStateGenerationRef.current) learnerStateGenerationRef.current += 1;
    };
  }, [nodes, trackId]);

  const positions = useMemo(
    () => layoutKnowledgePlanet(nodes, learnerStates, {
      rootId: rootNode.membership_id,
      spiralGap: nodes.length > 18 ? 0.56 : 0.78,
    }),
    [learnerStates, nodes, rootNode.membership_id],
  );
  const edges = useMemo(
    () => buildKnowledgePlanetEdges(nodes, [], positions),
    [nodes, positions],
  );

  const openCard = async (node) => {
    const cardGeneration = cardGenerationRef.current + 1;
    cardGenerationRef.current = cardGeneration;
    setSelectedNode(node);
    setKnowledgePoints([]);
    setCardError('');
    setLoadingCard(true);
    try {
      const payload = await loadAllNodeKnowledgePoints(trackId, node.membership_id);
      if (cardGeneration !== cardGenerationRef.current) return;
      setKnowledgePoints(Array.isArray(payload.items) ? payload.items : []);
    } catch (error) {
      if (cardGeneration !== cardGenerationRef.current) return;
      setKnowledgePoints([]);
      setCardError(error.message || '知识点暂时无法加载');
    } finally {
      if (cardGeneration === cardGenerationRef.current) setLoadingCard(false);
    }
  };

  const scheduleOpenCard = (node) => {
    if (activationTimerRef.current) clearTimeout(activationTimerRef.current);
    activationTimerRef.current = setTimeout(() => {
      activationTimerRef.current = null;
      openCard(node);
    }, 260);
  };

  const expandBranch = (node, event) => {
    event?.preventDefault?.();
    if (activationTimerRef.current) {
      clearTimeout(activationTimerRef.current);
      activationTimerRef.current = null;
    }
    if (Number(node.child_count || 0) > 0) {
      cardGenerationRef.current += 1;
      setSelectedNode(null);
      loadBranch(node, generationRef.current);
      return;
    }
    openCard(node);
  };

  return (
    <section className="knowledge-tree-drilldown" aria-labelledby="knowledge-tree-drilldown-title">
      <header className="knowledge-tree-drilldown__header">
        <button type="button" onClick={onBack}><ArrowLeft aria-hidden="true" size={16} />返回一级路径</button>
        <div>
          <span>聚焦知识树</span>
          <h2 id="knowledge-tree-drilldown-title">{rootNode.title}</h2>
        </div>
        <p>单击打开知识卡片 · 双击展开下级</p>
      </header>

      <div className="knowledge-tree-drilldown__viewport">
        <Suspense fallback={<div className="knowledge-planet__loading" role="status">正在构建三维知识星球…</div>}>
          <KnowledgePlanetScene
            nodes={nodes}
            positions={positions}
            edges={edges}
            onNodeClick={scheduleOpenCard}
            onNodeDoubleClick={expandBranch}
          />
        </Suspense>
      </div>

      {Object.values(branchErrors).map(({ parent, message }) => (
        <div key={parent.membership_id} className="knowledge-tree-drilldown__error" role="alert">
          <span>{message}</span>
          <button type="button" onClick={() => loadBranch(parent, generationRef.current)}>
            重试{parent.title}分支
          </button>
        </div>
      ))}

      <KnowledgeNodeCard
        node={selectedNode}
        trackId={trackId}
        knowledgePoints={knowledgePoints}
        loading={loadingCard}
        error={cardError}
        onRetry={() => openCard(selectedNode)}
        onClose={() => {
          cardGenerationRef.current += 1;
          setSelectedNode(null);
        }}
        onNavigate={onNavigate}
      />
    </section>
  );
}
