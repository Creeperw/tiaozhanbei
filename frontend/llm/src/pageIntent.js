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
