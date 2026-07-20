const nodeId = (node) => node.membership_id || node.id;

function officialOrder(node, state, fallback) {
  const direct = state?.display_order ?? node?.display_order ?? state?.sort_index ?? node?.sort_index;
  if (Number.isFinite(Number(direct))) return Number(direct);
  const path = state?.order_path ?? node?.order_path;
  if (Array.isArray(path)) {
    const last = Number(path.at(-1));
    if (Number.isFinite(last)) return last;
  }
  const match = String(path || '').match(/\d+(?:\.\d+)?/g)?.at(-1);
  return Number.isFinite(Number(match)) ? Number(match) : fallback;
}

function spiralPosition(side, index, gap) {
  const radius = 1.05 + 2.8 * (1 - Math.exp(-index * Math.max(0.1, gap) * 0.12));
  const angle = 0.45 + index * 1.72;
  const sign = side === 'history' ? -1 : 1;
  const vector = {
    x: sign * (0.18 + Math.abs(Math.cos(angle)) * 0.74),
    y: Math.sin(angle) * 0.76,
    z: sign * (0.42 + Math.cos(angle + 0.9) * 0.18),
  };
  const length = Math.hypot(vector.x, vector.y, vector.z) || 1;
  return {
    x: radius * vector.x / length,
    y: radius * vector.y / length,
    z: radius * vector.z / length,
    radius,
  };
}

export function layoutKnowledgePlanet(nodes, learnerStates = [], options = {}) {
  const { rootId, spiralGap = 0.8 } = options;
  const stateMap = new Map(learnerStates.map((state) => [state.membership_id, state]));
  const indexed = nodes.map((node, index) => ({
    node,
    index,
    state: stateMap.get(nodeId(node)) || null,
  }));
  const history = indexed
    .filter(({ node, state }) => nodeId(node) !== rootId && state?.status === 'completed')
    .sort((a, b) => {
      const aTime = Date.parse(a.state?.last_assessed_at || '') || 0;
      const bTime = Date.parse(b.state?.last_assessed_at || '') || 0;
      return bTime - aTime || officialOrder(a.node, a.state, a.index) - officialOrder(b.node, b.state, b.index);
    });
  const future = indexed
    .filter(({ node, state }) => nodeId(node) !== rootId && state?.status !== 'completed')
    .sort((a, b) => {
      const aPriority = a.state?.status === 'in_progress' ? -1 : 0;
      const bPriority = b.state?.status === 'in_progress' ? -1 : 0;
      return aPriority - bPriority
        || officialOrder(a.node, a.state, a.index) - officialOrder(b.node, b.state, b.index);
    });

  const positions = {};
  const root = indexed.find(({ node }) => nodeId(node) === rootId) || indexed[0];
  if (root) {
    positions[nodeId(root.node)] = {
      x: 0, y: 0, z: 0, radius: 0, side: 'current', material: 'current',
      timelineOrder: 0,
      lastAssessedAt: root.state?.last_assessed_at || null,
    };
  }
  history.forEach(({ node, state }, index) => {
    positions[nodeId(node)] = {
      ...spiralPosition('history', index, spiralGap),
      side: 'history',
      timelineOrder: -(index + 1),
      material: state?.review_due ? 'review_due' : 'mastered',
      lastAssessedAt: state?.last_assessed_at || null,
    };
  });
  let unlearnedIndex = 0;
  future.forEach(({ node, state }, index) => {
    const inProgress = state?.status === 'in_progress';
    positions[nodeId(node)] = {
      ...spiralPosition('future', index, spiralGap),
      side: 'future',
      timelineOrder: index + 1,
      material: inProgress ? 'in_progress' : unlearnedIndex++ === 0 ? 'next' : 'unlearned',
      lastAssessedAt: state?.last_assessed_at || null,
    };
  });
  return positions;
}

export function buildKnowledgePlanetEdges(nodes, explicitRelations = [], positions = null) {
  const ids = new Set(nodes.map(nodeId));
  const edges = nodes
    .filter((node) => node.parent_membership_id && ids.has(node.parent_membership_id))
    .map((node) => ({ from: node.parent_membership_id, to: nodeId(node), kind: 'hierarchy' }));
  const seen = new Set(edges.map((edge) => `${edge.from}:${edge.to}`));
  const declaredRelations = nodes.flatMap((node) => {
    const from = nodeId(node);
    const raw = node.related_membership_ids || node.related_ids || node.relations || [];
    return raw.map((relation) => (
      typeof relation === 'string'
        ? { from, to: relation }
        : { from, to: relation.to || relation.membership_id || relation.id }
    ));
  });
  [...explicitRelations, ...declaredRelations].forEach((relation) => {
    if (!ids.has(relation.from) || !ids.has(relation.to) || relation.from === relation.to) return;
    const key = `${relation.from}:${relation.to}`;
    if (seen.has(key)) return;
    seen.add(key);
    edges.push({ from: relation.from, to: relation.to, kind: 'relation' });
  });
  if (positions) {
    const timelineNodes = nodes
      .filter((node) => positions[nodeId(node)])
      .sort((a, b) => (
        (positions[nodeId(a)].timelineOrder ?? positions[nodeId(a)].x)
        - (positions[nodeId(b)].timelineOrder ?? positions[nodeId(b)].x)
      ));
    timelineNodes.slice(1).forEach((node, index) => {
      edges.push({
        from: nodeId(timelineNodes[index]),
        to: nodeId(node),
        kind: 'timeline',
      });
    });
  }
  return edges;
}

export function mergeExpandedPlanetBranch(currentNodes, _parent, children) {
  const seen = new Set(currentNodes.map(nodeId));
  return [
    ...currentNodes,
    ...children.filter((node) => {
      const id = nodeId(node);
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    }),
  ];
}
