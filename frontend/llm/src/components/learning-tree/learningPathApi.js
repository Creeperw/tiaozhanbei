import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from '../../utils/api';

export async function loadPlannedLearningPath(parentId = '') {
  const query = parentId ? `?parent_id=${encodeURIComponent(parentId)}` : '';
  const response = await fetchWithAuth(`${MAIN_API_BASE}/learning-path${query}`);
  const payload = await readJsonResponse(response, {});
  if (!response.ok) {
    const detail = payload?.detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.message || '学习路径加载失败');
  }
  if (payload?.schema_version !== '1.0' || !Array.isArray(payload?.nodes)) {
    throw new Error('学习路径数据格式不兼容');
  }
  return payload;
}

function normalizeGoalName(value) {
  return String(value || '')
    .replace(/\s+/g, '')
    .replace('中医类别', '中医')
    .trim();
}

function currentPlanMatchesTarget(context, target) {
  const route = context?.long_term_plan?.planning_route;
  if (!route || !target) return true;

  // 教材路线 ID（如 textbook_tcm_physician）是系统生成的权威标识，前后端
  // 处于同一 ID 空间。两侧都有 ID 时，由 ID 直接决定，不做任何文本猜测——
  // 文本子串匹配可能因 goal_name 写法差异（任务前缀、别名、措辞不同）推翻
  // ID 的权威结论，把已发布的路径误判为“没有个性化路径”。
  const plannedTextbookRoute = String(route.textbook_route?.route?.route_id || '');
  const selectedTextbookRoute = String(target.textbook_route_id || '');
  if (plannedTextbookRoute && selectedTextbookRoute) {
    return plannedTextbookRoute === selectedTextbookRoute;
  }

  // 仅当一侧缺失 ID（如 provisional 计划或旧数据）时，才退化到目标名宽松
  // 兜底：双向包含 + 归一化。方向是“尽量匹配”，宁可多展示（用户可再制定）
  // 也不误藏已发布路径。
  const plannedGoal = normalizeGoalName(route.goal_name);
  const selectedGoal = normalizeGoalName(target.official_name || target.name);
  if (plannedGoal && selectedGoal) {
    return plannedGoal === selectedGoal
      || plannedGoal.includes(selectedGoal)
      || selectedGoal.includes(plannedGoal);
  }

  return true;
}

export async function loadPlannedLearningPathForTarget(target) {
  const [payload, context] = await Promise.all([
    loadPlannedLearningPath(),
    fetchWithAuth(`${MAIN_API_BASE}/learning-plans/current/context`)
      .then((response) => readJsonResponse(response, {}))
      .catch(() => null),
  ]);
  if (currentPlanMatchesTarget(context, target)) return payload;
  return {
    ...payload,
    current_node_id: null,
    nodes: [],
    total: 0,
    has_more: false,
    availability: 'requires_target_plan',
    message: '当前考试还没有个性化学习路径。',
  };
}

async function loadLearningRoutePayload(path) {
  const response = await fetchWithAuth(`${MAIN_API_BASE}${path}`);
  const payload = await readJsonResponse(response, {});
  if (!response.ok) {
    const detail = payload?.detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.message || '经典路线加载失败');
  }
  if (payload?.schema_version !== '1.0') throw new Error('经典路线数据格式不兼容');
  return payload;
}

export async function loadClassicLearningRoutes(query = '') {
  const payload = await loadLearningRoutePayload('/qualification-targets');
  if (payload?.target_kind !== 'qualification_exam' || !Array.isArray(payload?.items)) {
    throw new Error('经典路线列表格式不兼容');
  }
  const keyword = query.trim().toLocaleLowerCase();
  const items = payload.items
    .map((target) => ({
      ...target,
      route_id: String(target?.target_id || ''),
      goal_name: String(target?.official_name || ''),
      textbook_route_id: String(target?.textbook_route_id || ''),
    }))
    .filter((target) => (
      target.route_id
      && target.goal_name
      && target.textbook_route_id
      && (!keyword || target.goal_name.toLocaleLowerCase().includes(keyword))
    ));
  return { ...payload, items, total: items.length };
}

export async function loadClassicLearningRoute(routeId) {
  if (!String(routeId || '').trim()) throw new Error('经典路线 ID 不能为空');
  const payload = await loadLearningRoutePayload(`/learning-routes/${encodeURIComponent(routeId)}`);
  if (!payload?.route || !Array.isArray(payload.route.stages)) throw new Error('经典路线详情格式不兼容');
  return payload;
}

export function adaptPlannedPathNode(node) {
  const mastery = node.mastery == null ? null : Number(node.mastery) * 100;
  return {
    ...node,
    membership_id: node.node_id,
    parent_membership_id: node.parent_id,
    child_count: Number(node.child_count || 0),
    total_count: Number(node.child_count || 0),
    completed_count: node.status === 'completed' ? Number(node.child_count || 0) : 0,
    incomplete_count: node.status === 'completed' ? 0 : Number(node.child_count || 0),
    average_mastery: mastery,
  };
}

export function adaptClassicRouteStage(route, stage) {
  const routeId = String(route?.route_id || 'classic');
  const stageId = String(stage?.stage_id || `stage-${stage?.order || 0}`);
  return {
    ...stage,
    node_id: `classic:${routeId}:stage:${stageId}`,
    membership_id: `classic:${routeId}:stage:${stageId}`,
    parent_membership_id: null,
    node_type: 'stage',
    title: stage?.name || `第 ${stage?.order || ''} 阶段`,
    description: stage?.objective || '',
    order: Number(stage?.order || 0),
    status: 'unassessed',
    child_count: Array.isArray(stage?.books) ? stage.books.length : 0,
    total_count: Array.isArray(stage?.books) ? stage.books.length : 0,
    completed_count: 0,
    incomplete_count: Array.isArray(stage?.books) ? stage.books.length : 0,
    navigation: { action: 'expand_classic_stage', stage_id: stageId },
  };
}

export function adaptClassicRouteBooks(route, stage, atlasRouteId = 'textbook_14_5') {
  const parent = adaptClassicRouteStage(route, stage);
  return (Array.isArray(stage?.books) ? stage.books : []).map((book, index) => {
    const normalizedBook = String(book || '').replace(/[《》]/g, '').trim();
    const id = `${parent.node_id}:book:${index + 1}`;
    return {
      node_id: id,
      membership_id: id,
      parent_id: parent.node_id,
      parent_membership_id: parent.node_id,
      node_type: 'book',
      title: `《${normalizedBook}》`,
      description: stage?.objective || '',
      order: index + 1,
      status: 'unassessed',
      child_count: 0,
      total_count: 0,
      completed_count: 0,
      incomplete_count: 0,
      source_refs: stage?.source_refs || [],
      navigation: {
        action: 'open_knowledge_atlas',
        route_id: atlasRouteId,
        book: normalizedBook,
      },
    };
  });
}
