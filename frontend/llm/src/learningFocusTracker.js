const HEARTBEAT_INTERVAL_MS = 30_000;
const INTERACTION_EVENTS = ['pointerdown', 'keydown', 'scroll'];

export function createLearningFocusTracker({
  request,
  resourceType,
  resourceId,
  taskId = null,
  documentRef = document,
  windowRef = window,
  setIntervalFn = setInterval,
  clearIntervalFn = clearInterval,
}) {
  let focusSessionId = null;
  let interacted = false;
  let intervalId = null;
  let stopped = false;

  const markInteraction = () => {
    interacted = true;
  };

  const heartbeat = async () => {
    if (!focusSessionId || stopped) return;
    const body = {
      visible: documentRef.visibilityState === 'visible',
      interacted,
    };
    interacted = false;
    await request(`/learning-activity/focus-sessions/${focusSessionId}/heartbeat`, body);
  };

  const visibilityChanged = async () => {
    if (documentRef.visibilityState === 'hidden') interacted = false;
    await heartbeat();
  };

  const start = async () => {
    const focus = await request('/learning-activity/focus-sessions', {
      task_id: taskId,
      resource_type: resourceType,
      resource_id: resourceId,
    });
    focusSessionId = focus.focus_session_id;
    if (stopped) {
      await request(`/learning-activity/focus-sessions/${focusSessionId}/end`);
      return;
    }
    INTERACTION_EVENTS.forEach((event) => documentRef.addEventListener(event, markInteraction));
    documentRef.addEventListener('visibilitychange', visibilityChanged);
    windowRef.addEventListener('pagehide', stop);
    intervalId = setIntervalFn(heartbeat, HEARTBEAT_INTERVAL_MS);
  };

  const stop = async () => {
    if (stopped) return;
    stopped = true;
    if (intervalId !== null) clearIntervalFn(intervalId);
    INTERACTION_EVENTS.forEach((event) => documentRef.removeEventListener(event, markInteraction));
    documentRef.removeEventListener('visibilitychange', visibilityChanged);
    windowRef.removeEventListener('pagehide', stop);
    if (focusSessionId) {
      await request(`/learning-activity/focus-sessions/${focusSessionId}/end`);
    }
  };

  return { start, stop };
}
