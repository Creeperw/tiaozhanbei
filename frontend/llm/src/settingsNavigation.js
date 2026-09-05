const SETTINGS_VIEWS = new Set(['governance', 'conflicts']);
const LEGACY_PERSONALIZATION_VIEWS = {
  governance: 'governance',
  conflicts: 'conflicts',
};

export function normalizeSettingsView(value) {
  return SETTINGS_VIEWS.has(value) ? value : 'governance';
}

export function legacyPersonalizationSettingsView(value) {
  return LEGACY_PERSONALIZATION_VIEWS[value] || null;
}
