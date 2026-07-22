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
  const suffix = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : '';
  const payload = await loadLearningRoutePayload(`/learning-routes${suffix}`);
  if (!Array.isArray(payload?.items)) throw new Error('经典路线列表格式不兼容');
  return payload;
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
