import test from 'node:test';
import assert from 'node:assert/strict';

import { dashboardModuleTarget } from './dashboardRouting.js';

test('routes report-related module keys into the personalization hub', () => {
  assert.equal(dashboardModuleTarget('planning'), 'personalization');
  assert.equal(dashboardModuleTarget('reports'), 'personalization');
});

test('routes moved learning settings module keys into user settings', () => {
  assert.equal(dashboardModuleTarget('profile'), 'settings');
  assert.equal(dashboardModuleTarget('memory'), 'settings');
  assert.equal(dashboardModuleTarget('governance'), 'settings');
  assert.equal(dashboardModuleTarget('conflicts'), 'settings');
});

test('keeps standalone dashboard module keys unchanged', () => {
  assert.equal(dashboardModuleTarget('assistant'), 'assistant');
  assert.equal(dashboardModuleTarget('practice'), 'practice');
  assert.equal(dashboardModuleTarget('knowledge'), 'knowledge');
});
