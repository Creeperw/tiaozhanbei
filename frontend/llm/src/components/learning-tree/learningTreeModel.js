const nodeId = (node) => node.membership_id || node.id;
const parentId = (node) => node.parent_membership_id || node.parentId || null;

export function buildTreeEdges(nodes) {
  const ids = new Set(nodes.map(nodeId));
  return nodes
    .filter((node) => parentId(node) && ids.has(parentId(node)))
    .map((node) => ({ from: parentId(node), to: nodeId(node) }));
}

export function buildFishboneEdges(nodes) {
  const ids = new Set(nodes.map(nodeId));
  const ribs = nodes
    .filter((node) => parentId(node) && ids.has(parentId(node)))
    .map((node) => ({ from: parentId(node), to: nodeId(node), kind: 'rib' }));
  const ribIds = new Set(ribs.map((edge) => edge.to));
  const spineNodes = nodes.filter((node) => !ribIds.has(nodeId(node)));
  const spine = spineNodes.slice(1).map((node, index) => ({
    from: nodeId(spineNodes[index]),
    to: nodeId(node),
    kind: 'spine',
  }));
  return [...spine, ...ribs];
}

function graphDepths(nodes, edges) {
  const depths = Object.fromEntries(nodes.map((node) => [nodeId(node), 0]));
  const incoming = new Map(nodes.map((node) => [nodeId(node), []]));
  edges.forEach(({ from, to }) => {
    if (incoming.has(to)) incoming.get(to).push(from);
  });
  for (let pass = 0; pass < nodes.length; pass += 1) {
    let changed = false;
    nodes.forEach((node) => {
      const id = nodeId(node);
      const parents = incoming.get(id) || [];
      if (!parents.length) return;
      const nextDepth = Math.max(...parents.map((parent) => depths[parent] ?? 0)) + 1;
      if (nextDepth > depths[id]) {
        depths[id] = nextDepth;
        changed = true;
      }
    });
    if (!changed) break;
  }
  return depths;
}

export function layoutDependencyTree(nodes, edges = buildTreeEdges(nodes), bounds = {}) {
  const {
    width = 960,
    height = 520,
    paddingX = 96,
    paddingY = 72,
    axisGap: requestedAxisGap,
    branchGap: requestedBranchGap,
    branchAngle: requestedBranchAngle,
  } = bounds;
  const ids = nodes.map(nodeId);
  const idSet = new Set(ids);
  const order = new Map(ids.map((id, index) => [id, index]));
  const children = new Map(ids.map((id) => [id, []]));
  const incoming = new Set();
  edges.forEach(({ from, to }) => {
    if (!idSet.has(from) || !idSet.has(to) || from === to) return;
    children.get(from).push(to);
    incoming.add(to);
  });
  children.forEach((items) => items.sort((a, b) => order.get(a) - order.get(b)));

  const longestPathFrom = (id, visiting = new Set()) => {
    if (visiting.has(id)) return [id];
    const nextVisiting = new Set(visiting).add(id);
    const candidates = (children.get(id) || []).map((child) => longestPathFrom(child, nextVisiting));
    if (!candidates.length) return [id];
    candidates.sort((a, b) => b.length - a.length || order.get(a[0]) - order.get(b[0]));
    return [id, ...candidates[0]];
  };

  const roots = ids.filter((id) => !incoming.has(id));
  const spine = [];
  const spineSet = new Set();
  const appendSpineId = (id) => {
    if (!idSet.has(id) || spineSet.has(id)) return;
    spine.push(id);
    spineSet.add(id);
  };
  const explicitSpineEdges = edges.filter((edge) => edge.kind === 'spine');
  if (explicitSpineEdges.length) {
    explicitSpineEdges.forEach(({ from, to }) => {
      appendSpineId(from);
      appendSpineId(to);
    });
  } else {
    roots.forEach((root) => {
      longestPathFrom(root).forEach((id) => {
        appendSpineId(id);
      });
    });
    ids.forEach((id) => {
      if (!spineSet.has(id) && !incoming.has(id)) appendSpineId(id);
    });
  }
  if (!spine.length && ids.length) {
    appendSpineId(ids[0]);
  }

  const positions = {};
  const centerY = height / 2;
  const spineOffset = Math.min(112, Math.max(88, (height - paddingY * 2) / 4));
  const axisGap = Number(requestedAxisGap);
  const axisSpan = Number.isFinite(axisGap) && axisGap > 0
    ? axisGap * Math.max(0, spine.length - 1)
    : width - paddingX * 2;
  const axisStart = Number.isFinite(axisGap) && axisGap > 0
    ? Math.max(paddingX, (width - axisSpan) / 2)
    : paddingX;
  const axisStep = spine.length <= 1 ? 0 : axisSpan / (spine.length - 1);
  spine.forEach((id, index) => {
    const sign = index % 2 === 0 ? -1 : 1;
    positions[id] = {
      x: spine.length === 1 ? width / 2 : axisStart + axisStep * index,
      y: centerY + sign * spineOffset,
      axisY: centerY,
      depth: index,
      lane: sign < 0 ? 'upper-spine' : 'lower-spine',
    };
  });

  const placed = new Set(spine);
  const placeRib = (id, anchor, sign, branchDepth, laneIndex, anchorIndex) => {
    if (placed.has(id)) return;
    placed.add(id);
    const verticalStep = Math.max(72, Math.min(88, (height - paddingY * 2) / 4.4));
    const minY = paddingY * 0.65;
    const maxY = height - paddingY * 0.65;
    const siblingRow = Math.floor(laneIndex / 2);
    const siblingSign = laneIndex % 2 === 0 ? sign : -sign;
    const fanDirection = anchor.x > width / 2 ? -1 : 1;
    const branchGap = Number.isFinite(Number(requestedBranchGap))
      ? Number(requestedBranchGap)
      : 96;
    const branchAngle = Number(requestedBranchAngle);
    const hasBranchAngle = Number.isFinite(branchAngle) && branchAngle > 0 && branchAngle < 90;
    const angleRadians = branchAngle * Math.PI / 180;
    const branchLead = hasBranchAngle
      ? Math.cos(angleRadians) * verticalStep / Math.max(Math.sin(angleRadians), 0.1)
      : 0;
    const fanOffset = branchLead + siblingRow * branchGap;
    const rawY = anchor.y
      + siblingSign * verticalStep * (branchDepth + siblingRow * 0.7);
    positions[id] = {
      x: Math.max(paddingX * 0.55, Math.min(width - paddingX * 0.55,
        anchor.x + fanDirection * fanOffset)),
      y: Math.max(minY, Math.min(maxY, rawY)),
      axisY: centerY,
      depth: anchorIndex,
      lane: siblingSign < 0 ? 'upper-rib' : 'lower-rib',
    };
    (children.get(id) || []).forEach((child, childIndex) => {
      placeRib(child, positions[id], sign, 1, childIndex, anchorIndex);
    });
  };

  spine.forEach((id, anchorIndex) => {
    const nextSpineId = spine[anchorIndex + 1];
    const ribs = (children.get(id) || []).filter((child) => child !== nextSpineId && !spineSet.has(child));
    ribs.forEach((child, index) => {
      const sign = positions[id].y < centerY ? -1 : 1;
      placeRib(child, positions[id], sign, 1, index, anchorIndex);
    });
  });

  ids.forEach((id, index) => {
    if (placed.has(id)) return;
    const anchorIndex = Math.min(index, spine.length - 1);
    const anchor = positions[spine[anchorIndex]] || { x: width / 2, y: centerY };
    const sign = anchor.y < centerY ? -1 : 1;
    placeRib(id, anchor, sign, 1, Math.floor(index / 2), anchorIndex);
  });
  return positions;
}

const clampScale = (value) => Math.max(0.55, Math.min(1.8, Number(value) || 1));

export const clampSemanticScale = clampScale;

export function getSemanticFishboneMetrics(value) {
  const scale = clampSemanticScale(value);
  const density = Math.sqrt(scale);
  return {
    axisGap: Math.round(184 / density),
    branchGap: Math.round(96 / density),
    branchAngle: Math.round(Math.max(34, Math.min(58, 50 - (scale - 1) * 14))),
  };
}

export function centerViewportOnFocus(viewport, focus, bounds = {}) {
  const scale = clampScale(viewport?.scale);
  const width = Number(bounds.width) || 960;
  const height = Number(bounds.height) || 640;
  const current = focus?.current;
  const next = focus?.next;
  const points = [current, next].filter(Boolean);
  if (!points.length) return { scale, x: Number(viewport?.x) || 0, y: Number(viewport?.y) || 0 };
  const center = points.reduce((total, point) => ({
    x: total.x + point.x,
    y: total.y + point.y,
  }), { x: 0, y: 0 });
  return {
    scale,
    x: width / 2 - (center.x / points.length) * scale,
    y: height / 2 - (center.y / points.length) * scale,
  };
}

export function zoomViewportAtPoint(viewport, requestedScale, point) {
  const currentScale = clampScale(viewport?.scale);
  const scale = clampScale(requestedScale);
  const ratio = scale / currentScale;
  const x = Number(viewport?.x) || 0;
  const y = Number(viewport?.y) || 0;
  const anchorX = Number(point?.x) || 0;
  const anchorY = Number(point?.y) || 0;
  return {
    scale,
    x: anchorX - (anchorX - x) * ratio,
    y: anchorY - (anchorY - y) * ratio,
  };
}

export function panViewport(viewport, deltaX, deltaY) {
  return {
    scale: clampScale(viewport?.scale),
    x: (Number(viewport?.x) || 0) + (Number(deltaX) || 0),
    y: (Number(viewport?.y) || 0) + (Number(deltaY) || 0),
  };
}

export function resetViewport() {
  return { scale: 1, x: 0, y: 0 };
}

export function layoutRadialTree(rootId, nodes, bounds = {}) {
  const { width = 900, height = 620 } = bounds;
  const edges = buildTreeEdges(nodes);
  const depths = graphDepths(nodes, edges);
  const maxDepth = Math.max(1, ...Object.values(depths));
  const centerX = width / 2;
  const centerY = height / 2;
  const radiusStep = Math.min(width, height) * 0.4 / maxDepth;
  const layers = new Map();
  nodes.forEach((node) => {
    const id = nodeId(node);
    const depth = id === rootId ? 0 : depths[id] || 0;
    if (!layers.has(depth)) layers.set(depth, []);
    layers.get(depth).push(node);
  });
  const positions = {};
  [...layers.entries()].forEach(([depth, layer]) => {
    if (depth === 0) {
      layer.forEach((node) => {
        positions[nodeId(node)] = { x: centerX, y: centerY, depth: 0 };
      });
      return;
    }
    layer.forEach((node, index) => {
      const angle = -Math.PI / 2 + (Math.PI * 2 * index) / layer.length;
      const radius = radiusStep * depth;
      positions[nodeId(node)] = {
        x: centerX + Math.cos(angle) * radius,
        y: centerY + Math.sin(angle) * radius,
        depth,
      };
    });
  });
  return positions;
}

export function isDoubleActivation(previous, id, timestamp, threshold = 350) {
  return Boolean(previous && previous.id === id && timestamp - previous.at <= threshold);
}
