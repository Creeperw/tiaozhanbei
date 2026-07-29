export function createPageIntent(destination, params = {}) {
  if (typeof destination === 'string') {
    return { page: destination, params: { ...params } };
  }

  return {
    page: destination?.page || 'dashboard',
    params: { ...(destination?.params || {}), ...params },
  };
}

export function mergePageIntent(current, destination) {
  const base = createPageIntent(current);
  const next = createPageIntent(destination);
  return {
    page: next.page,
    params: { ...base.params, ...next.params },
  };
}

export function getIntentPage(intent) {
  return createPageIntent(intent).page;
}

const WORKSHOP_DESTINATIONS = {
  'workshop.paper': { taskType: 'paper_workspace' },
  'workshop.knowledge_card': { taskType: 'knowledge_cards', resourceView: 'explanation' },
  'workshop.knowledge_video': { taskType: 'knowledge_cards', resourceView: 'videos' },
  'workshop.question_training': { taskType: 'question_training' },
  'workshop.practice': { taskType: 'question_training' },
};

const definedEntries = (value) => Object.fromEntries(
  Object.entries(value).filter(([, item]) => item !== undefined && item !== null && item !== ''),
);

/**
 * Converts backend workflow/daily-task actions into one stable frontend route.
 * Both snake_case persisted actions and camelCase live actions are accepted.
 */
export function workshopActionIntent(action, defaults = {}) {
  const destination = String(action?.destination || '');
  const target = WORKSHOP_DESTINATIONS[destination];
  if (!target) return null;

  const rawParams = {
    ...defaults,
    ...(action?.params || {}),
  };
  const params = definedEntries({
    ...rawParams,
    view: 'workspace',
    taskType: target.taskType,
    resourceView: rawParams.resourceView || rawParams.resource_view || target.resourceView,
    paperId: rawParams.paperId || rawParams.paper_id,
    cardId: rawParams.cardId || rawParams.card_id,
    kpId: rawParams.kpId || rawParams.kp_id,
    kpName: rawParams.kpName || rawParams.kp_name,
    taskItemId: rawParams.taskItemId || rawParams.task_item_id,
    reviewTaskId: rawParams.reviewTaskId || rawParams.review_task_id,
    directVideo: rawParams.directVideo || rawParams.video,
  });

  return createPageIntent('practice', params);
}
