const SETTINGS_VIEWS = new Set(['memory', 'governance', 'conflicts']);
const LEGACY_PERSONALIZATION_VIEWS = {
  profile: 'memory',
  memory: 'memory',
  governance: 'governance',
  conflicts: 'conflicts',
};

export function normalizeSettingsView(value) {
  if (value === 'profile') return 'memory';
  return SETTINGS_VIEWS.has(value) ? value : 'memory';
}

export function legacyPersonalizationSettingsView(value) {
  return LEGACY_PERSONALIZATION_VIEWS[value] || null;
}
