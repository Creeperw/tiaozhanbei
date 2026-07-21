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
