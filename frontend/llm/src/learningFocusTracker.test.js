import test from 'node:test';
import assert from 'node:assert/strict';

import { createLearningFocusTracker } from './learningFocusTracker.js';

class EventTargetStub {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  removeEventListener(type) {
    this.listeners.delete(type);
  }

  dispatch(type) {
    this.listeners.get(type)?.();
  }
}

test('reports visibility and real interaction separately from periodic heartbeat', async () => {
  const documentRef = new EventTargetStub();
  documentRef.visibilityState = 'visible';
  const windowRef = new EventTargetStub();
  const calls = [];
  let tick;
  const tracker = createLearningFocusTracker({
    request: async (path, body) => {
      calls.push({ path, body });
      if (path === '/learning-activity/focus-sessions') {
        return { focus_session_id: 'FOCUS_1' };
      }
      return {};
    },
    documentRef,
    windowRef,
    setIntervalFn: (callback) => {
      tick = callback;
      return 1;
    },
    clearIntervalFn: () => {},
    resourceType: 'training_workspace',
    resourceId: 'practice',
  });

  await tracker.start();
  documentRef.dispatch('pointerdown');
  await tick();
  await tick();
  documentRef.visibilityState = 'hidden';
  await documentRef.listeners.get('visibilitychange')();
  await tracker.stop();

  assert.deepEqual(calls, [
    {
      path: '/learning-activity/focus-sessions',
      body: { task_id: null, resource_type: 'training_workspace', resource_id: 'practice' },
    },
    {
      path: '/learning-activity/focus-sessions/FOCUS_1/heartbeat',
      body: { visible: true, interacted: true },
    },
    {
      path: '/learning-activity/focus-sessions/FOCUS_1/heartbeat',
      body: { visible: true, interacted: false },
    },
    {
      path: '/learning-activity/focus-sessions/FOCUS_1/heartbeat',
      body: { visible: false, interacted: false },
    },
    { path: '/learning-activity/focus-sessions/FOCUS_1/end', body: undefined },
  ]);
});
