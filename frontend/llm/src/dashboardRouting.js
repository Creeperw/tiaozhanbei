const PERSONALIZATION_MODULE_KEYS = new Set(['planning', 'reports']);
const SETTINGS_MODULE_KEYS = new Set(['profile', 'memory', 'governance', 'conflicts']);

export function dashboardModuleTarget(key) {
  if (SETTINGS_MODULE_KEYS.has(key)) return 'settings';
  return PERSONALIZATION_MODULE_KEYS.has(key) ? 'personalization' : key;
}
