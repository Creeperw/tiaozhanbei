import { API_BASE, fetchWithAuth, readJsonResponse } from '../../utils/api';

async function request(path, options) {
  const response = await fetchWithAuth(`${API_BASE}${path}`, options);
  const payload = await readJsonResponse(response, {});
  if (!response.ok) {
    throw new Error(payload.detail || '考纲数据加载失败');
  }
  return payload;
}

export function loadLearningTarget() {
  return request('/personalization/learning-target');
}

export function saveLearningTarget(examTrackId) {
  return request('/personalization/learning-target', {
    method: 'PUT',
    body: JSON.stringify({
      target_type: 'certification',
      exam_track_id: examTrackId,
      is_locked: true,
      lock_reason: '用户手动选择',
    }),
  });
}

export function loadExamTracks() {
  return request('/exam-learning/tracks');
}

export function loadExamNodes(trackId, parentMembershipId = null) {
  const query = parentMembershipId
    ? `?parent_membership_id=${encodeURIComponent(parentMembershipId)}`
    : '';
  return request(`/exam-learning/tracks/${encodeURIComponent(trackId)}/nodes${query}`);
}

export function loadExamNode(trackId, membershipId) {
  return request(
    `/exam-learning/tracks/${encodeURIComponent(trackId)}/nodes/${encodeURIComponent(membershipId)}`,
  );
}

export function loadNodeLearnerSummary(trackId, membershipId) {
  return request(
    `/exam-learning/tracks/${encodeURIComponent(trackId)}`
      + `/nodes/${encodeURIComponent(membershipId)}/learner-summary`,
  );
}

export function loadNodeLearnerStates(trackId, membershipIds) {
  return request(
    `/exam-learning/tracks/${encodeURIComponent(trackId)}/nodes/learner-states`,
    {
      method: 'POST',
      body: JSON.stringify({ membership_ids: membershipIds }),
    },
  );
}

export function loadNodeKnowledgePoints(trackId, membershipId, offset = 0, limit = 50) {
  return request(
    `/exam-learning/tracks/${encodeURIComponent(trackId)}`
      + `/nodes/${encodeURIComponent(membershipId)}`
      + `/knowledge-points?offset=${offset}&limit=${limit}`,
  );
}

export async function loadAllNodeKnowledgePoints(trackId, membershipId, limit = 50) {
  const items = [];
  let offset = 0;
  let page;
  do {
    page = await loadNodeKnowledgePoints(trackId, membershipId, offset, limit);
    items.push(...(Array.isArray(page.items) ? page.items : []));
    offset += Number(page.limit || limit);
  } while (page.has_more);
  return { ...page, items, offset: 0, limit, has_more: false };
}

export function loadLearnerKnowledgePointState(kpId) {
  return request(
    `/exam-learning/knowledge-points/${encodeURIComponent(kpId)}/learner-state`,
  );
}
