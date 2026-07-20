const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

export function stableHash(value) {
  const text = String(value ?? '');
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash = Math.imul(hash ^ text.charCodeAt(index), 16777619);
  }
  return hash >>> 0;
}

export function distributeSphere(nodes) {
  const count = nodes.length;
  return nodes.map((node, index) => {
    const y = count === 1 ? 0 : 1 - (index / (count - 1)) * 2;
    const radius = Math.sqrt(Math.max(0, 1 - y * y));
    const id = node.membership_id || node.kp_id || node.id || String(index);
    const theta = GOLDEN_ANGLE * index + ((stableHash(id) % 100) / 100) * 0.18;
    return {
      ...node,
      id,
      px: Math.cos(theta) * radius,
      py: y,
      pz: Math.sin(theta) * radius,
    };
  });
}

export function rotatePoint(point, { yaw = 0, pitch = 0 } = {}) {
  const cosYaw = Math.cos(yaw);
  const sinYaw = Math.sin(yaw);
  const cosPitch = Math.cos(pitch);
  const sinPitch = Math.sin(pitch);
  const x = point.px * cosYaw - point.pz * sinYaw;
  const z = point.px * sinYaw + point.pz * cosYaw;
  return {
    x,
    y: point.py * cosPitch - z * sinPitch,
    z: point.py * sinPitch + z * cosPitch,
  };
}

export function projectPoint(point, {
  centerX,
  centerY,
  radius,
  zoom = 1,
} = {}) {
  const perspective = 1 / (1.12 - point.z * 0.16);
  const depth = (point.z + 1) / 2;
  return {
    ...point,
    sx: centerX + point.x * radius * perspective * zoom,
    sy: centerY + point.y * radius * perspective * zoom,
    depth,
    perspective,
  };
}

export function hitTestProjected(projected, point) {
  let best = null;
  let bestDistance = Infinity;
  projected.forEach((item) => {
    if (item.z < -0.35) return;
    const distance = Math.hypot(item.sx - point.x, item.sy - point.y);
    const hitRadius = Math.max(15, (item.radius || 0) + 9);
    if (
      distance <= hitRadius
      && (distance < bestDistance || item.z > (best?.z ?? -2) + 0.15)
    ) {
      best = item;
      bestDistance = distance;
    }
  });
  return best;
}

function easeOutCubic(value) {
  return 1 - ((1 - clamp(value, 0, 1)) ** 3);
}

export function transitionValues({ mode, progress, origin, width = 0, height = 0 }) {
  const eased = easeOutCubic(progress);
  if (mode === 'dive-out') {
    return {
      alpha: 1 - eased,
      scale: 1 + eased * 4.5,
      offsetX: origin ? (width / 2 - origin.sx) * eased : 0,
      offsetY: origin ? (height / 2 - origin.sy) * eased : 0,
    };
  }
  if (mode === 'back-out') {
    return { alpha: 1 - eased, scale: 1 - eased * 0.76, offsetX: 0, offsetY: 0 };
  }
  return { alpha: eased, scale: 0.18 + eased * 0.82, offsetX: 0, offsetY: 0 };
}

function overlaps(first, second) {
  return first.x < second.x + second.width
    && first.x + first.width > second.x
    && first.y < second.y + second.height
    && first.y + first.height > second.y;
}

export function placeVisibleLabels(projected, {
  measureText,
  threshold = -0.34,
  fontSize = 13,
} = {}) {
  const occupied = [];
  const labels = [];
  [...projected]
    .filter((item) => item.z > threshold)
    .sort((first, second) => second.z - first.z)
    .forEach((item) => {
      const label = item.label || item.title || item.node?.title || '';
      const box = {
        x: item.sx + (item.radius || 0) + 8,
        y: item.sy - fontSize / 2,
        width: measureText(label) + 7,
        height: fontSize + 6,
      };
      if (occupied.some((other) => overlaps(box, other))) return;
      occupied.push(box);
      labels.push({ ...item, label, box });
    });
  return labels;
}

function searchableText(node) {
  const path = node.node?.path || node.path || [];
  const rawPath = Array.isArray(path) ? path.join(' ') : String(path);
  return `${node.title || ''} ${rawPath}`.normalize('NFKC').toLocaleLowerCase('zh-CN');
}

export function filterAtlasNodes(nodes, query) {
  const normalized = String(query || '').trim().normalize('NFKC').toLocaleLowerCase('zh-CN');
  if (!normalized) return [...nodes];
  return nodes.filter((node) => searchableText(node).includes(normalized));
}

function conceptKey(name) {
  return String(name || '').normalize('NFKC').replace(/\s+/g, '').toLocaleLowerCase('zh-CN');
}

export function groupKnowledgePoints(items) {
  const concepts = new Map();
  items.forEach((item) => {
    const key = conceptKey(item.name || item.kp_id);
    if (!concepts.has(key)) {
      concepts.set(key, {
        conceptKey: key,
        name: item.name || item.kp_id,
        path: [...(item.path || [])],
        acceptedCount: 0,
        variants: [],
      });
    }
    const concept = concepts.get(key);
    concept.acceptedCount += Number(item.accepted_count || 0);
    concept.variants.push({ ...item, path: [...(item.path || [])] });
  });
  return [...concepts.values()].map((concept) => ({
    ...concept,
    variants: [...concept.variants].sort((first, second) => (
      String(first.kp_id).localeCompare(String(second.kp_id), 'zh-CN')
    )),
  }));
}

function publicContext(context) {
  return {
    trackId: context.trackId,
    membershipId: context.membershipId,
    kpId: context.kpId,
  };
}

export function buildPracticeIntent(context) {
  return {
    page: 'practice',
    params: { ...publicContext(context), kpName: context.kpName },
  };
}

export function buildKnowledgeIntent(context) {
  return {
    page: 'knowledge',
    params: { ...publicContext(context), query: context.kpName },
  };
}

export function buildAssistantIntent(context) {
  const readablePath = (context.path || []).join(' / ') || context.kpName;
  return {
    page: 'assistant',
    params: {
      ...publicContext(context),
      context: `请围绕知识点“${context.kpName}”进行讲解。考纲路径：${readablePath}`,
    },
  };
}
