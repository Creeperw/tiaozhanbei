import test from 'node:test';
import assert from 'node:assert/strict';

import { buildHomePortalState, getHomeActionIntent } from './homePortal.js';

test('maps dashboard summary data without inventing progress', () => {
  const state = buildHomePortalState({
    continue_learning: [{ session_id: 's-1', title: '方剂学辨证训练' }],
    today_tasks: [{ title: '完成一次短练', duration: '20 分钟' }],
    status_cards: [{ key: 'accuracy', value: '82%' }],
  });

  assert.equal(state.continueLearning.title, '方剂学辨证训练');
  assert.equal(state.continueLearning.progress, 82);
  assert.equal(state.pendingTasks.count, 1);
  assert.equal(state.pendingTasks.duration, '20 分钟');
});

test('keeps honest fallbacks for sparse dashboard data', () => {
  const state = buildHomePortalState({});

  assert.equal(state.continueLearning.progress, null);
  assert.equal(state.pendingTasks.count, 0);
  assert.equal(state.pendingTasks.duration, '打开训练工坊查看安排');
});

test('maps home feature actions to existing first- and second-level pages', () => {
  assert.deepEqual(getHomeActionIntent('pending-tasks'), {
    page: 'practice',
    params: { view: 'overview' },
  });
  assert.deepEqual(getHomeActionIntent('mistake-reinforcement'), {
    page: 'practice',
    params: { view: 'workspace', taskType: 'mistake_variation' },
  });
  assert.deepEqual(getHomeActionIntent('knowledge-graph'), {
    page: 'knowledge',
    params: { view: 'atlas', source: 'dashboard' },
  });
});
