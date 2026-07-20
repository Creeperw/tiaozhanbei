const PERSONALIZATION_MODULE_KEYS = new Set(['planning', 'reports', 'profile']);

export function dashboardModuleTarget(key) {
  return PERSONALIZATION_MODULE_KEYS.has(key) ? 'personalization' : key;
}
