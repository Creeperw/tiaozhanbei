const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

export const ATLAS_ROUTES = [
  { id: 'textbook_14_5', name: '十四五教材' },
  { id: 'tcm_assistant', name: '中医助理医师' },
  { id: 'postgraduate', name: '西医考研' },
];

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

export function atlasNodeId(node) {
  return String(node?.id ?? node?.kp_id ?? node?.name ?? '');
}

export function normalizeAtlasNode(node, index = 0) {
  const name = String(node?.name ?? node?.title ?? node?.lv3 ?? node?.lv2 ?? node?.lv1 ?? `节点 ${index + 1}`);
  return {
    ...node,
    id: atlasNodeId(node) || `${index}-${name}`,
    name,
    alias: String(node?.alias ?? ''),
    count: Number(node?.count ?? node?.children_count ?? node?.child_count ?? 0),
    children_count: Number(node?.children_count ?? node?.child_count ?? node?.count ?? 0),
    question_count: Number(node?.question_count ?? node?.questions_count ?? 0),
    video_count: Number(node?.video_count ?? node?.videos_count ?? 0),
    order_index: Number(node?.order_index ?? node?.sort_index ?? index),
  };
}

export function getAtlasResourceKind(node) {
  const hasQuestions = Number(node?.question_count || 0) > 0;
  const hasVideo = Number(node?.video_count || 0) > 0;
  if (hasQuestions && hasVideo) return 'both';
  if (hasQuestions) return 'question';
  if (hasVideo) return 'video';
  return 'plain';
}

export function filterAtlasNodes(nodes, query) {
  const keyword = String(query || '').trim().toLocaleLowerCase('zh-CN');
  if (!keyword) return nodes;
  return nodes.filter((item) => {
    const node = normalizeAtlasNode(item);
    return `${node.name} ${node.alias} ${node.id}`.toLocaleLowerCase('zh-CN').includes(keyword);
  });
}

function hash(text) {
  let value = 2166136261;
  const source = String(text || '');
  for (let index = 0; index < source.length; index += 1) {
    value = Math.imul(value ^ source.charCodeAt(index), 16777619);
  }
  return value >>> 0;
}

export function atlasNodeColor(node, clusterId = null, fade = 0) {
  const base = clusterId == null ? hash(node?.name) % 42 : (clusterId * 9) % 42;
  const hue = 145 + base;
  const lightness = 40 + fade * 9;
  return {
    solid: `hsl(${hue} ${62 - fade * 7}% ${lightness}%)`,
    glow: `hsla(${hue} 68% 40% / ${0.28 - fade * 0.07})`,
  };
}

function spherePositions(count, jitter = false) {
  return Array.from({ length: count }, (_, index) => {
    const py = count === 1 ? 0 : 1 - (index / (count - 1)) * 2;
    const radius = Math.sqrt(Math.max(0, 1 - py * py));
    const theta = GOLDEN_ANGLE * index + (jitter ? ((index * 37) % 100) * 0.0018 : 0);
    return { px: Math.cos(theta) * radius, py, pz: Math.sin(theta) * radius };
  });
}

function plainLabel(value) {
  return String(value || '')
    .replace(/\\(?:mathrm|mathbf|mathit|text|operatorname)\s*\{([^{}]*)\}/g, '$1')
    .replace(/\\([A-Za-z]+)\b/g, '$1')
    .replace(/[${}]/g, '');
}

function sequenceSort(nodes, level) {
  return [...nodes].sort((left, right) => {
    if (level === 2) {
      const byOrder = Number(left.order_index || 0) - Number(right.order_index || 0);
      if (byOrder) return byOrder;
    }
    return plainLabel(left.name).localeCompare(plainLabel(right.name), 'zh-CN-u-co-pinyin');
  });
}

function naturalSort(nodes, level) {
  return [...nodes].sort((left, right) => (
    level < 3
      ? Number(right.count || 0) - Number(left.count || 0) || left.name.localeCompare(right.name, 'zh-CN')
      : String(left.order || '').localeCompare(String(right.order || '')) || left.name.localeCompare(right.name, 'zh-CN')
  ));
}

function semanticVector(node, dimensions = 72) {
  const source = plainLabel(`${node.name} ${node.alias || ''}`)
    .replace(/第[一二三四五六七八九十百零〇0-9]+[章节篇讲]/g, '')
    .replace(/[^\u3400-\u9fffA-Za-z0-9]+/g, '');
  const chars = [...source];
  const vector = new Float32Array(dimensions);
  const add = (token, weight) => {
    if (token) vector[hash(token) % dimensions] += weight;
  };
  chars.forEach((char, index) => {
    add(char, 0.55);
    add(chars.slice(index, index + 2).join(''), 1.45);
    add(chars.slice(index, index + 3).join(''), 0.7);
  });
  const norm = Math.hypot(...vector) || 1;
  for (let index = 0; index < vector.length; index += 1) vector[index] /= norm;
  return vector;
}

function similarity(left, right) {
  let score = 0;
  for (let index = 0; index < left.length; index += 1) score += left[index] * right[index];
  return score;
}

function clusterAssignments(nodes) {
  const count = nodes.length;
  if (count <= 2) return { assignments: nodes.map((_, index) => index), clusterCount: count || 1 };
  const vectors = nodes.map(semanticVector);
  const clusterCount = count <= 3 ? 1 : count < 8 ? 2 : count < 12 ? 3 : clamp(Math.round(Math.sqrt(count / 3)), 3, 8);
  const seedIndexes = [0];
  while (seedIndexes.length < clusterCount) {
    let bestIndex = 0;
    let bestDistance = -1;
    vectors.forEach((vector, index) => {
      if (seedIndexes.includes(index)) return;
      const distance = Math.min(...seedIndexes.map((seed) => 1 - similarity(vector, vectors[seed])));
      if (distance > bestDistance) {
        bestDistance = distance;
        bestIndex = index;
      }
    });
    seedIndexes.push(bestIndex);
  }
  let centers = seedIndexes.map((index) => Float32Array.from(vectors[index]));
  let assignments = new Array(count).fill(0);
  for (let iteration = 0; iteration < 6; iteration += 1) {
    assignments = vectors.map((vector) => {
      let best = 0;
      let score = -Infinity;
      centers.forEach((center, index) => {
        const candidate = similarity(vector, center);
        if (candidate > score) { score = candidate; best = index; }
      });
      return best;
    });
    const sums = Array.from({ length: clusterCount }, () => new Float32Array(vectors[0].length));
    const totals = new Array(clusterCount).fill(0);
    vectors.forEach((vector, vectorIndex) => {
      const cluster = assignments[vectorIndex];
      totals[cluster] += 1;
      for (let offset = 0; offset < vector.length; offset += 1) sums[cluster][offset] += vector[offset];
    });
    centers = sums.map((sum, index) => {
      if (!totals[index]) return centers[index];
      const norm = Math.hypot(...sum) || 1;
      for (let offset = 0; offset < sum.length; offset += 1) sum[offset] /= norm;
      return sum;
    });
  }
  return { assignments, clusterCount };
}

function semanticArrangement(nodes) {
  const ordered = sequenceSort(nodes, 3);
  const { assignments, clusterCount } = clusterAssignments(ordered);
  const groups = Array.from({ length: clusterCount }, () => []);
  ordered.forEach((node, index) => groups[assignments[index]].push(node));
  const centers = spherePositions(clusterCount);
  const arranged = [];
  groups.forEach((group, clusterId) => {
    group.forEach((node, index) => {
      const fade = group.length <= 1 ? 0 : Math.sqrt(index / (group.length - 1));
      const spread = index === 0 ? 0 : 0.05 + 0.16 * fade;
      const theta = index * GOLDEN_ANGLE;
      const center = centers[clusterId];
      const raw = {
        px: center.px * 0.68 + Math.cos(theta) * spread,
        py: center.py * 0.68 + Math.sin(theta) * spread,
        pz: 0.5 + center.pz * 0.28,
      };
      const length = Math.hypot(raw.px, raw.py, raw.pz) || 1;
      arranged.push({
        ...node,
        px: raw.px / length,
        py: raw.py / length,
        pz: raw.pz / length,
        cluster_id: clusterId,
        cluster_fade: fade,
        visualAlpha: 1 - fade * 0.18,
        color: atlasNodeColor(node, clusterId, fade),
      });
    });
  });
  return arranged;
}

export function arrangeAtlasNodes(rawNodes, mode = 'sphere', level = 1) {
  const nodes = rawNodes.map(normalizeAtlasNode);
  if (!nodes.length) return [];
  if (mode === 'semantic' || mode === 'compact') {
    return semanticArrangement(nodes);
  }
  const ordered = mode === 'sequence' ? sequenceSort(nodes, level) : naturalSort(nodes, level);
  const positions = spherePositions(ordered.length, mode === 'sphere');
  return ordered.map((node, index) => ({
    ...node,
    ...positions[index],
    cluster_id: null,
    cluster_fade: 0,
    visualAlpha: 1,
    color: atlasNodeColor(node),
  }));
}

export function projectAtlasNodes(nodes, view) {
  const width = Math.max(1, Number(view?.width || 1));
  const height = Math.max(1, Number(view?.height || 1));
  const yaw = Number(view?.yaw || 0);
  const pitch = Number(view?.pitch || 0);
  const zoom = clamp(Number(view?.zoom || 1), 0.56, 2.1);
  const cyaw = Math.cos(yaw);
  const syaw = Math.sin(yaw);
  const cpitch = Math.cos(pitch);
  const spitch = Math.sin(pitch);
  const base = Math.min(width, height) * 0.36 * zoom;
  return nodes.map((node) => {
    const x1 = node.px * cyaw - node.pz * syaw;
    const z1 = node.px * syaw + node.pz * cyaw;
    const y2 = node.py * cpitch - z1 * spitch;
    const z2 = node.py * spitch + z1 * cpitch;
    const depth = (z2 + 1) / 2;
    const perspective = 0.72 + depth * 0.42;
    return {
      ...node,
      x: width / 2 + x1 * base * perspective,
      y: height / 2 + y2 * base * perspective,
      z: z2,
      depth,
      radius: clamp((5.2 + Math.log2(Number(node.count || 0) + 2) * 1.28) * perspective, 5, 16),
      alpha: clamp(0.34 + depth * 0.72, 0.3, 1) * Number(node.visualAlpha ?? 1),
    };
  }).sort((left, right) => left.z - right.z);
}

export function interpolateAtlasPositions(previous, next, progress) {
  const eased = 1 - ((1 - clamp(progress, 0, 1)) ** 3);
  const previousById = new Map(previous.map((node) => [atlasNodeId(node), node]));
  return next.map((node) => {
    const before = previousById.get(atlasNodeId(node)) || { px: 0, py: 0, pz: -1 };
    return {
      ...node,
      px: before.px + (node.px - before.px) * eased,
      py: before.py + (node.py - before.py) * eased,
      pz: before.pz + (node.pz - before.pz) * eased,
      visualAlpha: Number(node.visualAlpha ?? 1) * eased,
    };
  });
}
