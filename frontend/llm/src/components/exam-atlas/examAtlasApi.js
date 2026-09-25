import { API_BASE, fetchWithAuth, readJsonResponse } from '../../utils/api';

async function request(path, options) {
  const response = await fetchWithAuth(`${API_BASE}${path}`, options);
  const payload = await readJsonResponse(response, {});
  if (!response.ok) {
    throw new Error(payload.detail || '考纲数据加载失败');
  }
  return payload;
}

let pendingLearningTarget = null;
let learningTargetReadVersion = 0;

export function invalidateLearningTargetRead() {
  learningTargetReadVersion += 1;
  const pending = pendingLearningTarget;
  pendingLearningTarget = null;
  pending?.controller.abort();
}

export function loadLearningTarget() {
  if (pendingLearningTarget) return pendingLearningTarget.promise;
  const controller = new AbortController();
  const entry = { controller, promise: null };
  entry.promise = request('/personalization/learning-target', { signal: controller.signal })
    .then((payload) => {
      // A response from a previous login or target selection must never be
      // delivered, even if the transport finished before abort took effect.
      if (controller.signal.aborted) throw new DOMException('学习目标读取已失效', 'AbortError');
      return payload;
    })
    .finally(() => {
      if (pendingLearningTarget === entry) pendingLearningTarget = null;
    });
  pendingLearningTarget = entry;
  return entry.promise;
}

async function updateLearningTargets(path, options) {
  invalidateLearningTargetRead();
  const version = learningTargetReadVersion;
  try {
    return await request(path, options);
  } finally {
    // Reads started while a write was pending may still contain the old target.
    if (version === learningTargetReadVersion) invalidateLearningTargetRead();
  }
}

export function loadLearningTargets() {
  return request('/personalization/learning-targets');
}

export function enrollLearningTargets(examTrackIds, currentExamTrackId) {
  return updateLearningTargets('/personalization/learning-targets', {
    method: 'POST',
    body: JSON.stringify({
      exam_track_ids: examTrackIds,
      current_exam_track_id: currentExamTrackId,
    }),
  });
}

export function saveLearningTarget(examTrackId) {
  return updateLearningTargets('/personalization/learning-target', {
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
