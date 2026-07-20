import test from 'node:test';
import assert from 'node:assert/strict';

import { dashboardModuleTarget } from './dashboardRouting.js';

test('routes legacy personalization module keys into the personalization hub', () => {
  assert.equal(dashboardModuleTarget('planning'), 'personalization');
  assert.equal(dashboardModuleTarget('reports'), 'personalization');
  assert.equal(dashboardModuleTarget('profile'), 'personalization');
});

test('keeps standalone dashboard module keys unchanged', () => {
  assert.equal(dashboardModuleTarget('assistant'), 'assistant');
  assert.equal(dashboardModuleTarget('practice'), 'practice');
  assert.equal(dashboardModuleTarget('knowledge'), 'knowledge');
});
