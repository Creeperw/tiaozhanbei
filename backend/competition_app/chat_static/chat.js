const STORAGE_PREFIX = 'competition.chat.v2';
const STREAM_ENDPOINT = '/api/v1/review-cards/stream';
let storageOwnerId = sessionStorage.getItem('competition.auth.user_id') || 'pending';
const storageKey = () => `${STORAGE_PREFIX}.${storageOwnerId}`;

const elements = {
  conversation: document.querySelector('#conversation'),
  welcome: document.querySelector('#welcome'),
  list: document.querySelector('#message-list'),
  form: document.querySelector('#message-form'),
  input: document.querySelector('#message-input'),
  send: document.querySelector('#send-button'),
  newConversation: document.querySelector('#new-conversation'),
  availableMinutes: document.querySelector('#available-minutes'),
  connectionStatus: document.querySelector('#connection-status'),
  loadingTemplate: document.querySelector('#loading-template'),
  appShell: document.querySelector('.app-shell'),
  executionToggle: document.querySelector('#execution-toggle'),
  executionClose: document.querySelector('#execution-close'),
  executionOverlay: document.querySelector('#execution-overlay'),
  executionPanel: document.querySelector('#execution-panel'),
  executionGraph: document.querySelector('#execution-graph'),
  executionEngine: document.querySelector('#execution-engine'),
  executionDescription: document.querySelector('#execution-description'),
  executionStatus: document.querySelector('#execution-status'),
  executionEvents: document.querySelector('#execution-event-list'),
  learningToggle: document.querySelector('#learning-toggle'),
  learningPanel: document.querySelector('#learning-panel'),
  learningClose: document.querySelector('#learning-close'),
  learningOverlay: document.querySelector('#learning-overlay'),
  learningRefresh: document.querySelector('#learning-refresh'),
  learningSyncStatus: document.querySelector('#learning-sync-status'),
  learningStatusCode: document.querySelector('#learning-status-code'),
  learningStatusName: document.querySelector('#learning-status-name'),
  learningStatusEvidence: document.querySelector('#learning-status-evidence'),
  metricCompletion: document.querySelector('#metric-completion'),
  metricAccuracy: document.querySelector('#metric-accuracy'),
  metricClick: document.querySelector('#metric-click'),
  metricFocus: document.querySelector('#metric-focus'),
  focusClock: document.querySelector('#focus-clock'),
  focusDescription: document.querySelector('#focus-description'),
  focusStart: document.querySelector('#focus-start'),
  focusEnd: document.querySelector('#focus-end'),
  currentTaskStatus: document.querySelector('#current-task-status'),
  currentTaskContent: document.querySelector('#current-task-content'),
  currentTaskCriteria: document.querySelector('#current-task-criteria'),
  currentTaskComplete: document.querySelector('#current-task-complete'),
  learningTrend: document.querySelector('#learning-trend'),
  weakPointCount: document.querySelector('#weak-point-count'),
  weakPointList: document.querySelector('#weak-point-list'),
  reviewDueCount: document.querySelector('#review-due-count'),
  learningReviewList: document.querySelector('#learning-review-list'),
  learningDataSource: document.querySelector('#learning-data-source'),
};

let activeRequest = null;
let state = restoreState();
let executionClock = null;
let executionViewportWasMobile = window.matchMedia('(max-width: 68rem)').matches;
const executionNodeElements = new Map();
let learningContext = null;
let focusState = null;
let focusClockTimer = null;
let focusHeartbeatTimer = null;
let focusInteractionObserved = true;

function defaultExecutionTrace() {
  return {
    graph: null,
    nodeStates: {},
    events: [],
    status: 'idle',
    retryCount: 0,
    revisionCount: 0,
    startedAt: null,
    elapsedSeconds: 0,
  };
}

function normalizeExecutionTrace(value) {
  const fallback = defaultExecutionTrace();
  if (!value || typeof value !== 'object') return fallback;
  return {
    ...fallback,
    graph: value.graph && typeof value.graph === 'object' ? value.graph : null,
    nodeStates: value.nodeStates && typeof value.nodeStates === 'object' ? value.nodeStates : {},
    events: Array.isArray(value.events) ? value.events.slice(0, 8) : [],
    status: ['idle', 'running', 'interrupted', 'completed', 'failed'].includes(value.status)
      ? value.status : 'idle',
    retryCount: Number(value.retryCount) || 0,
    revisionCount: Number(value.revisionCount) || 0,
    startedAt: Number(value.startedAt) || null,
    elapsedSeconds: Number(value.elapsedSeconds) || 0,
  };
}

function createId(prefix) {
  const suffix = globalThis.crypto?.randomUUID?.() || `${Date.now()}_${Math.random().toString(16).slice(2)}`;
  return `${prefix}_${suffix}`;
}

function emptyState() {
  return {
    version: 1,
    learnerId: storageOwnerId === 'pending' ? createId('CHAT_USER') : storageOwnerId,
    availableMinutes: 60,
    messages: [],
    longTermPlan: {},
    shortTermPlan: {},
    learningTask: {},
    pendingRequest: null,
    pendingClarification: null,
    pendingThreadId: null,
    pendingInterrupt: null,
    executionTrace: defaultExecutionTrace(),
  };
}

function normalizePendingClarification(value) {
  if (!value || typeof value !== 'object') return null;
  const originalRequest = typeof value.originalRequest === 'string'
    ? value.originalRequest.trim()
    : '';
  const targetLayers = Array.isArray(value.targetLayers)
    ? value.targetLayers.filter(layer => ['long_term', 'short_term', 'daily_task'].includes(layer))
    : [];
  const answers = Array.isArray(value.answers)
    ? value.answers.filter(answer => typeof answer === 'string')
    : [];
  const resumeScope = ['long_term', 'short_term', 'daily_task', 'unspecified'].includes(value.resumeScope)
    ? value.resumeScope
    : null;
  if (!originalRequest || !targetLayers.length) return null;
  return { originalRequest, targetLayers: [...new Set(targetLayers)], answers, resumeScope };
}

function restoreState() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey()) || 'null');
    if (!saved || saved.version !== 1 || !Array.isArray(saved.messages)) return emptyState();
    return {
      ...emptyState(),
      ...saved,
      messages: saved.messages.filter(message =>
        message && ['user', 'assistant'].includes(message.role) && typeof message.content === 'string'
      ),
      pendingClarification: normalizePendingClarification(saved.pendingClarification),
      pendingThreadId: typeof saved.pendingThreadId === 'string' ? saved.pendingThreadId : null,
      pendingInterrupt: saved.pendingInterrupt && typeof saved.pendingInterrupt === 'object'
        ? saved.pendingInterrupt
        : null,
      executionTrace: normalizeExecutionTrace(saved.executionTrace),
    };
  } catch (_error) {
    return emptyState();
  }
}

function persistState() {
  try {
    localStorage.setItem(storageKey(), JSON.stringify(state));
  } catch (_error) {
    setConnectionStatus('会话仅保留在当前页面', 'offline');
  }
}

function setConnectionStatus(label, tone = '') {
  elements.connectionStatus.lastChild.textContent = label;
  elements.connectionStatus.classList.toggle('is-online', tone === 'online');
  elements.connectionStatus.classList.toggle('is-offline', tone === 'offline');
}

function setBusy(isBusy) {
  elements.send.disabled = isBusy;
  elements.input.disabled = isBusy;
  elements.newConversation.disabled = false;
}

const executionAgentLabels = {
  planner_agent: 'Planner',
  default_route_resolver: '路线解析',
  knowledge_base_agent: '知识检索',
  diagnosis_agent: '学情诊断',
  learning_plan_service: '计划落地',
  review_scheduler: '复习调度',
  expert_agent: '内容生成',
  paper_blueprint_agent: '试卷蓝图',
  paper_assembly_agent: '试卷组装',
  knowledge_explanation_agent: '知识讲解',
  audit_agent: '质量审核',
  memory_agent: '会话记忆',
};

function setExecutionPanel(open) {
  const mobile = window.matchMedia('(max-width: 68rem)').matches;
  if (mobile) {
    document.body.classList.toggle('execution-mobile-open', open);
  } else {
    elements.appShell.classList.toggle('execution-collapsed', !open);
  }
  elements.executionToggle.setAttribute('aria-expanded', String(open));
  if (open) window.requestAnimationFrame(drawExecutionEdges);
}

function setLearningPanel(open) {
  document.body.classList.toggle('learning-panel-open', open);
  elements.learningToggle.setAttribute('aria-expanded', String(open));
  elements.learningPanel.setAttribute('aria-hidden', String(!open));
  if (open) loadLearningContext();
}

function metricValue(value, nestedKey = '') {
  if (nestedKey && value?.[nestedKey]) return Number(value[nestedKey].value) || 0;
  if (value && typeof value === 'object' && 'value' in value) return Number(value.value) || 0;
  return Number(value) || 0;
}

function formatPercent(value) {
  return `${Math.round(Math.max(0, Math.min(1, Number(value) || 0)) * 100)}%`;
}

function formatSyncTime(value) {
  if (!value) return '刚刚同步';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '刚刚同步';
  return `${date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })} ${date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 同步`;
}

function renderLearningTrend(trends) {
  elements.learningTrend.replaceChildren();
  const series = Array.isArray(trends?.series) ? trends.series.slice(-7) : [];
  if (!series.length) {
    const empty = document.createElement('p');
    empty.className = 'panel-empty';
    empty.textContent = '完成一次专注学习后生成趋势。';
    elements.learningTrend.appendChild(empty);
    return;
  }
  const maxMinutes = Math.max(1, ...series.map(item => Number(item.focus_minutes) || 0));
  const today = new Date().toISOString().slice(0, 10);
  series.forEach(item => {
    const day = document.createElement('div');
    day.className = `trend-day${item.date === today ? ' is-today' : ''}`;
    day.title = `${item.date} · ${Number(item.focus_minutes) || 0} 分钟`;
    const bar = document.createElement('i');
    bar.style.height = `${Math.max(4, ((Number(item.focus_minutes) || 0) / maxMinutes) * 100)}%`;
    const label = document.createElement('span');
    label.textContent = item.date?.slice(5).replace('-', '/') || '—';
    day.append(bar, label);
    elements.learningTrend.appendChild(day);
  });
}

function renderWeakPoints(profile, mastery) {
  const weakIds = Array.isArray(profile?.weak_kp_ids) ? profile.weak_kp_ids : [];
  const weakRows = Array.isArray(mastery)
    ? mastery.filter(item => item.mastery_status === 'weak' || Number(item.mastery) < 0.6)
    : [];
  const values = [...new Set([...weakIds, ...weakRows.map(item => item.kp_id)].filter(Boolean))].slice(0, 12);
  elements.weakPointCount.textContent = `${values.length} 项`;
  elements.weakPointList.replaceChildren();
  if (!values.length) {
    const empty = document.createElement('p');
    empty.className = 'panel-empty';
    empty.textContent = '暂未识别明确薄弱点。';
    elements.weakPointList.appendChild(empty);
    return;
  }
  values.forEach(value => {
    const tag = document.createElement('span');
    tag.textContent = value;
    elements.weakPointList.appendChild(tag);
  });
}

async function submitReviewOutcome(taskId, outcome) {
  const response = await fetch(`/api/v1/review-tasks/${encodeURIComponent(taskId)}/attempts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ learner_id: state.learnerId, outcome }),
  });
  if (!response.ok) throw new Error('复习结果保存失败');
  await loadLearningContext({ quiet: true });
}

function renderReviewQueue(queue) {
  const entries = Array.isArray(queue?.entries) ? queue.entries : [];
  elements.reviewDueCount.textContent = `${Number(queue?.due_count) || 0} 项`;
  elements.learningReviewList.replaceChildren();
  if (!entries.length) {
    const empty = document.createElement('li');
    empty.className = 'panel-empty';
    empty.textContent = '当前没有到期复习任务。';
    elements.learningReviewList.appendChild(empty);
    return;
  }
  entries.slice(0, 6).forEach(entry => {
    const item = document.createElement('li');
    item.className = 'review-item';
    const title = document.createElement('strong');
    title.textContent = entry.memory_unit?.prompt_abstract || entry.memory_unit?.kp_id || '复习任务';
    const meta = document.createElement('p');
    meta.textContent = `${entry.is_due ? '已到期' : '待复习'} · 预计保持率 ${formatPercent(entry.retention_estimate)}`;
    item.append(title, meta);
    if (entry.task?.review_task_id) {
      const actions = document.createElement('div');
      actions.className = 'review-actions';
      [
        ['独立答对', 'independent_correct'],
        ['提示后答对', 'hinted_correct'],
        ['答错', 'wrong'],
        ['跳过', 'skipped'],
      ].forEach(([label, outcome]) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = label;
        button.addEventListener('click', async () => {
          button.disabled = true;
          try { await submitReviewOutcome(entry.task.review_task_id, outcome); }
          catch (_error) { elements.learningSyncStatus.textContent = '复习结果保存失败'; }
          finally { button.disabled = false; }
        });
        actions.appendChild(button);
      });
      item.appendChild(actions);
    }
    elements.learningReviewList.appendChild(item);
  });
}

function renderLearningContext(context) {
  learningContext = context || {};
  const profile = learningContext.learning_profile || {};
  const status = profile.current_status || {};
  const systemData = learningContext.system_data || {};
  const completion = metricValue(
    systemData.task_completion_rate,
    'learning_task_completion_rate',
  );
  const accuracy = metricValue(systemData.question_accuracy) || Number(profile.question_accuracy) || 0;
  const clickRate = metricValue(systemData.resource_click_rate);
  const focusMinutes = (learningContext.learning_trends?.series || [])
    .reduce((total, item) => total + (Number(item.focus_minutes) || 0), 0);

  elements.learningSyncStatus.textContent = formatSyncTime(learningContext.calculated_at);
  elements.learningStatusCode.textContent = status.status_code || 'T0';
  elements.learningStatusName.textContent = status.status_name || '稳定学习';
  elements.learningStatusEvidence.textContent = status.evidence?.[0]
    || learningContext.diagnosis?.summary
    || '当前数据较少，系统会随任务、答题和复习记录持续更新。';
  elements.metricCompletion.textContent = formatPercent(completion);
  elements.metricAccuracy.textContent = formatPercent(accuracy);
  elements.metricClick.textContent = formatPercent(clickRate);
  elements.metricFocus.textContent = `${Math.round(focusMinutes)}m`;

  const task = learningContext.learning_task;
  elements.currentTaskStatus.textContent = task?.status === 'completed' ? '已完成' : task ? '进行中' : '未安排';
  elements.currentTaskContent.textContent = task?.task_content || '对话生成当日任务后会显示在这里';
  elements.currentTaskCriteria.textContent = task?.completion_criteria || '完成标准会与任务一起保存。';
  elements.currentTaskComplete.disabled = !task || task.status === 'completed';

  renderLearningTrend(learningContext.learning_trends);
  renderWeakPoints(profile, learningContext.mastery);
  renderReviewQueue(learningContext.review_queue);
  const capabilities = learningContext.capabilities || {};
  elements.learningDataSource.textContent = capabilities.behavior_context
    ? '任务、专注、答题、掌握度与复习记录会在服务端汇总后进入智能体。'
    : '行为数据服务暂未启用，本次对话使用已有计划和会话信息。';
  elements.learningToggle.classList.toggle('is-ready', Boolean(capabilities.behavior_context));
}

async function loadLearningContext(options = {}) {
  if (!state.learnerId || state.learnerId.startsWith('CHAT_USER')) return;
  if (!options.quiet) elements.learningSyncStatus.textContent = '正在同步…';
  try {
    const response = await fetch('/api/v1/learning-context');
    if (!response.ok) throw new Error('learning context unavailable');
    renderLearningContext(await response.json());
  } catch (_error) {
    elements.learningSyncStatus.textContent = '暂时无法同步';
    elements.learningToggle.classList.remove('is-ready');
  }
}

function focusStorageKey() {
  return `competition.focus.v1.${state.learnerId}`;
}

function persistFocusState() {
  if (focusState) localStorage.setItem(focusStorageKey(), JSON.stringify(focusState));
  else localStorage.removeItem(focusStorageKey());
}

function renderFocusClock() {
  const elapsed = focusState ? Math.max(0, Math.floor((Date.now() - focusState.startedAt) / 1000)) : 0;
  const minutes = String(Math.floor(elapsed / 60)).padStart(2, '0');
  const seconds = String(elapsed % 60).padStart(2, '0');
  elements.focusClock.textContent = `${minutes}:${seconds}`;
}

function stopFocusTimers() {
  if (focusClockTimer) window.clearInterval(focusClockTimer);
  if (focusHeartbeatTimer) window.clearInterval(focusHeartbeatTimer);
  focusClockTimer = null;
  focusHeartbeatTimer = null;
}

async function sendFocusHeartbeat() {
  if (!focusState) return;
  const response = await fetch(`/learning-activity/focus-sessions/${encodeURIComponent(focusState.focusSessionId)}/heartbeat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ visible: !document.hidden, interacted: focusInteractionObserved }),
  });
  focusInteractionObserved = false;
  if (response.status === 404) clearFocusState();
}

function startFocusTimers() {
  stopFocusTimers();
  renderFocusClock();
  focusClockTimer = window.setInterval(renderFocusClock, 1000);
  focusHeartbeatTimer = window.setInterval(() => sendFocusHeartbeat().catch(() => {}), 30000);
}

function clearFocusState() {
  stopFocusTimers();
  focusState = null;
  persistFocusState();
  elements.focusStart.disabled = false;
  elements.focusEnd.disabled = true;
  elements.focusDescription.textContent = '开始后仅累计页面可见且有交互的有效时间。';
  elements.learningToggle.classList.remove('is-active');
  renderFocusClock();
}

function restoreFocusState() {
  stopFocusTimers();
  try { focusState = JSON.parse(localStorage.getItem(focusStorageKey()) || 'null'); }
  catch (_error) { focusState = null; }
  if (!focusState?.focusSessionId || !focusState?.startedAt) {
    clearFocusState();
    return;
  }
  elements.focusStart.disabled = true;
  elements.focusEnd.disabled = false;
  elements.focusDescription.textContent = '正在记录有效学习时间；离开页面不会累计。';
  elements.learningToggle.classList.add('is-active');
  startFocusTimers();
  sendFocusHeartbeat().catch(() => clearFocusState());
}

async function startFocusSession() {
  elements.focusStart.disabled = true;
  try {
    const taskResponse = await fetch('/learning-activity/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task_type: learningContext?.learning_task?.task_type || 'focused_learning',
        resource_type: learningContext?.learning_task ? 'daily_task' : 'conversation',
        resource_id: learningContext?.learning_task?.task_id || '',
      }),
    });
    if (!taskResponse.ok) throw new Error('task start failed');
    const task = await taskResponse.json();
    const focusResponse = await fetch('/learning-activity/focus-sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task_id: task.task_id,
        resource_type: learningContext?.learning_task ? 'daily_task' : 'conversation',
        resource_id: learningContext?.learning_task?.task_id || '',
      }),
    });
    if (!focusResponse.ok) throw new Error('focus start failed');
    const focus = await focusResponse.json();
    focusState = {
      focusSessionId: focus.focus_session_id,
      handoffTaskId: task.task_id,
      startedAt: Date.now(),
    };
    persistFocusState();
    elements.focusEnd.disabled = false;
    elements.focusDescription.textContent = '正在记录有效学习时间；离开页面不会累计。';
    elements.learningToggle.classList.add('is-active');
    focusInteractionObserved = true;
    startFocusTimers();
    await sendFocusHeartbeat();
  } catch (_error) {
    clearFocusState();
    elements.focusDescription.textContent = '专注记录启动失败，请刷新后重试。';
  }
}

async function endFocusSession() {
  if (!focusState) return;
  elements.focusEnd.disabled = true;
  try {
    await sendFocusHeartbeat();
    await fetch(`/learning-activity/focus-sessions/${encodeURIComponent(focusState.focusSessionId)}/end`, { method: 'POST' });
  } finally {
    clearFocusState();
    await loadLearningContext({ quiet: true });
  }
}

async function completeCurrentTask() {
  elements.currentTaskComplete.disabled = true;
  try {
    const response = await fetch('/api/v1/learning-tasks/current/complete', { method: 'POST' });
    if (!response.ok) throw new Error('task completion failed');
    if (focusState?.handoffTaskId) {
      await fetch(`/learning-activity/tasks/${encodeURIComponent(focusState.handoffTaskId)}/complete`, { method: 'POST' });
    }
    await loadLearningContext({ quiet: true });
  } catch (_error) {
    elements.learningSyncStatus.textContent = '任务状态保存失败';
    elements.currentTaskComplete.disabled = false;
  }
}

function setExecutionMetric(id, value) {
  const node = document.querySelector(`#${id}`);
  if (node) node.textContent = String(value);
}

function executionElapsedSeconds() {
  const trace = state.executionTrace;
  if (trace.status === 'running' && trace.startedAt) {
    return Math.max(0, Math.round((Date.now() - trace.startedAt) / 1000));
  }
  return trace.elapsedSeconds || 0;
}

function updateExecutionElapsed() {
  const seconds = executionElapsedSeconds();
  state.executionTrace.elapsedSeconds = seconds;
  setExecutionMetric('execution-elapsed', `${seconds}s`);
}

function startExecutionClock() {
  if (executionClock) window.clearInterval(executionClock);
  updateExecutionElapsed();
  executionClock = window.setInterval(updateExecutionElapsed, 1000);
}

function stopExecutionClock() {
  if (executionClock) window.clearInterval(executionClock);
  executionClock = null;
  updateExecutionElapsed();
}

function addExecutionEvent(message, tone = 'normal') {
  const trace = state.executionTrace;
  trace.events.unshift({ message, tone, seconds: executionElapsedSeconds() });
  trace.events = trace.events.slice(0, 8);
  renderExecutionEvents();
}

function renderExecutionEvents() {
  elements.executionEvents.replaceChildren();
  state.executionTrace.events.forEach(entry => {
    const item = document.createElement('li');
    item.className = `is-${entry.tone || 'normal'}`;
    const time = document.createElement('time');
    time.textContent = `${entry.seconds || 0}s`;
    const message = document.createElement('span');
    message.textContent = entry.message;
    item.append(time, message);
    elements.executionEvents.appendChild(item);
  });
}

function resetExecutionMonitor({ persist = false } = {}) {
  stopExecutionClock();
  state.executionTrace = defaultExecutionTrace();
  renderExecutionEmptyView();
  if (persist) persistState();
}

function renderExecutionEmptyView() {
  executionNodeElements.clear();
  elements.executionGraph.className = 'execution-graph is-empty';
  elements.executionGraph.setAttribute('aria-label', '等待生成执行图');
  elements.executionGraph.innerHTML = '<div class="execution-empty"><span aria-hidden="true">◇</span><strong>等待 Planner 编译</strong><p>每次对话都会生成一张不同的执行图。</p></div>';
  elements.executionEngine.textContent = '等待请求';
  elements.executionDescription.textContent = '发起对话后，这里会显示本次实际参与的智能体、依赖关系、重试与审核返修。';
  elements.executionStatus.textContent = '空闲';
  elements.executionToggle.className = 'execution-toggle';
  setExecutionMetric('execution-node-count', '—');
  setExecutionMetric('execution-retry-count', 0);
  setExecutionMetric('execution-revision-count', 0);
  setExecutionMetric('execution-elapsed', '0s');
  renderExecutionEvents();
}

function drawExecutionEdges() {
  const graph = state.executionTrace.graph;
  if (!graph) return;
  const surface = elements.executionGraph.querySelector('.execution-surface');
  const svg = elements.executionGraph.querySelector('.execution-edges');
  if (!surface || !svg) return;
  const surfaceRect = surface.getBoundingClientRect();
  const width = Math.max(surface.scrollWidth, surfaceRect.width);
  const height = Math.max(surface.scrollHeight, surfaceRect.height);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('width', String(width));
  svg.setAttribute('height', String(height));
  svg.replaceChildren();
  const ns = 'http://www.w3.org/2000/svg';
  const defs = document.createElementNS(ns, 'defs');
  const marker = document.createElementNS(ns, 'marker');
  marker.setAttribute('id', 'chat-graph-arrow');
  marker.setAttribute('viewBox', '0 0 10 10');
  marker.setAttribute('refX', '8');
  marker.setAttribute('refY', '5');
  marker.setAttribute('markerWidth', '5');
  marker.setAttribute('markerHeight', '5');
  marker.setAttribute('orient', 'auto');
  const arrow = document.createElementNS(ns, 'path');
  arrow.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
  marker.appendChild(arrow);
  defs.appendChild(marker);
  svg.appendChild(defs);
  [...(graph.edges || []), ...(graph.control_edges || [])].forEach(edge => {
    const source = executionNodeElements.get(edge.source);
    const target = executionNodeElements.get(edge.target);
    if (!source || !target) return;
    const sourceRect = source.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const startX = sourceRect.left - surfaceRect.left + sourceRect.width / 2;
    const startY = sourceRect.bottom - surfaceRect.top;
    const endX = targetRect.left - surfaceRect.left + targetRect.width / 2;
    const endY = targetRect.top - surfaceRect.top;
    const path = document.createElementNS(ns, 'path');
    path.classList.add('execution-edge');
    if (edge.kind === 'revision') path.classList.add('revision');
    const bend = Math.max(18, (endY - startY) * .48);
    path.setAttribute('d', `M ${startX} ${startY} C ${startX} ${startY + bend}, ${endX} ${endY - bend}, ${endX} ${endY}`);
    if (edge.kind !== 'revision') path.setAttribute('marker-end', 'url(#chat-graph-arrow)');
    svg.appendChild(path);
  });
}

function renderExecutionGraph(graph) {
  state.executionTrace.graph = graph;
  executionNodeElements.clear();
  elements.executionGraph.replaceChildren();
  elements.executionGraph.classList.remove('is-empty');
  const surface = document.createElement('div');
  surface.className = 'execution-surface';
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.classList.add('execution-edges');
  svg.setAttribute('aria-hidden', 'true');
  const levels = document.createElement('div');
  levels.className = 'execution-levels';
  const nodes = new Map((graph.nodes || []).map(node => [node.step_id, node]));
  (graph.levels || []).forEach((level, index) => {
    const row = document.createElement('section');
    row.className = 'execution-level';
    row.style.setProperty('--level-width', String(Math.max(1, level.length)));
    const label = document.createElement('small');
    label.textContent = level.length > 1 ? `parallel ${index}` : `stage ${index}`;
    row.appendChild(label);
    level.forEach(stepId => {
      const node = nodes.get(stepId) || { step_id: stepId, agent: stepId };
      const card = document.createElement('article');
      const nodeState = state.executionTrace.nodeStates[stepId]?.state
        || (stepId === 'planner' ? 'completed' : 'pending');
      card.className = `execution-node ${nodeState}`;
      card.dataset.stepId = stepId;
      const dot = document.createElement('i');
      dot.className = 'execution-node-state';
      const heading = document.createElement('h4');
      heading.textContent = executionAgentLabels[node.agent] || node.agent || stepId;
      const code = document.createElement('code');
      code.textContent = stepId;
      const status = document.createElement('span');
      status.textContent = state.executionTrace.nodeStates[stepId]?.message
        || (stepId === 'planner' ? '路由已完成' : '等待依赖');
      card.append(dot, heading, code, status);
      row.appendChild(card);
      executionNodeElements.set(stepId, card);
    });
    levels.appendChild(row);
  });
  surface.append(svg, levels);
  elements.executionGraph.appendChild(surface);
  const edgeCount = (graph.edges || []).length;
  setExecutionMetric('execution-node-count', (graph.nodes || []).length);
  elements.executionEngine.textContent = graph.engine === 'langgraph' ? '图已编译' : '流程已编排';
  elements.executionDescription.textContent = `${(graph.nodes || []).length} 个节点 · ${edgeCount} 条依赖边 · 按本次诉求动态生成`;
  elements.executionGraph.setAttribute('aria-label', `${graph.engine || '执行'}图，共 ${(graph.nodes || []).length} 个节点、${edgeCount} 条依赖边`);
  window.requestAnimationFrame(drawExecutionEdges);
}

function markExecutionNode(stepId, nodeState, message) {
  if (!stepId) return;
  state.executionTrace.nodeStates[stepId] = { state: nodeState, message };
  const node = executionNodeElements.get(stepId);
  if (!node) return;
  node.className = `execution-node ${nodeState}`;
  const status = node.querySelector('span');
  if (status) status.textContent = message;
}

function renderExecutionMonitor() {
  const trace = state.executionTrace = normalizeExecutionTrace(state.executionTrace);
  if (!trace.graph) {
    renderExecutionEmptyView();
  } else {
    renderExecutionGraph(trace.graph);
  }
  setExecutionMetric('execution-retry-count', trace.retryCount);
  setExecutionMetric('execution-revision-count', trace.revisionCount);
  setExecutionMetric('execution-elapsed', `${executionElapsedSeconds()}s`);
  elements.executionStatus.textContent = {
    idle: '空闲', running: '运行中', interrupted: '等待回答', completed: '已完成', failed: '执行失败',
  }[trace.status];
  elements.executionToggle.className = `execution-toggle${trace.status === 'running' ? ' is-running' : trace.status === 'completed' ? ' is-complete' : trace.status === 'failed' ? ' is-error' : ''}`;
  renderExecutionEvents();
  if (trace.status === 'running') startExecutionClock();
}

function handleExecutionEvent(event) {
  let trace = state.executionTrace;
  const stepId = event.step_id || event.agent;
  if (event.event === 'run_started') {
    resetExecutionMonitor();
    trace = state.executionTrace;
    trace.status = 'running';
    trace.startedAt = Date.now();
    elements.executionStatus.textContent = '正在规划';
    elements.executionEngine.textContent = '正在编译';
    elements.executionToggle.className = 'execution-toggle is-running';
    addExecutionEvent('收到请求，Planner 开始路由');
    startExecutionClock();
    if (!window.matchMedia('(max-width: 68rem)').matches) setExecutionPanel(true);
  } else if (event.event === 'run_resumed') {
    trace.status = 'running';
    trace.startedAt = Date.now() - (trace.elapsedSeconds * 1000);
    elements.executionStatus.textContent = '恢复运行';
    addExecutionEvent('从已保存检查点恢复', 'success');
    startExecutionClock();
  } else if (event.event === 'graph_compiled') {
    renderExecutionGraph(event);
    elements.executionStatus.textContent = '执行中';
    addExecutionEvent(`执行图已编译，共 ${(event.nodes || []).length} 个节点`, 'success');
  } else if (event.event === 'behavior_context_loaded') {
    addExecutionEvent(
      `已载入学习行为：${event.mastery_count || 0} 个掌握点、${event.attempt_count || 0} 次近期作答`,
      'success',
    );
    elements.learningToggle.classList.add('is-ready');
  } else if (event.event === 'behavior_context_unavailable') {
    addExecutionEvent('学习行为暂未载入，本次按已有对话与计划继续', 'warning');
  } else if (event.event === 'step_started') {
    markExecutionNode(stepId, 'running', '正在执行');
    elements.executionStatus.textContent = `${executionAgentLabels[event.agent] || event.agent}处理中`;
    addExecutionEvent(`${executionAgentLabels[event.agent] || event.agent} 开始执行`);
  } else if (event.event === 'step_completed') {
    markExecutionNode(stepId, 'completed', '已完成');
  } else if (event.event === 'step_retrying') {
    trace.retryCount += 1;
    setExecutionMetric('execution-retry-count', trace.retryCount);
    markExecutionNode(stepId, 'retrying', `第 ${event.attempt} 次重试`);
    addExecutionEvent(`${executionAgentLabels[event.agent] || stepId} 失败后重试`, 'warning');
  } else if (event.event === 'graph_interrupted' || event.event === 'run_interrupted') {
    trace.status = 'interrupted';
    markExecutionNode(event.step_id, 'interrupted', '等待用户回答');
    elements.executionStatus.textContent = '等待回答';
    elements.executionToggle.className = 'execution-toggle';
    addExecutionEvent('流程已中断并保存检查点', 'revision');
    stopExecutionClock();
  } else if (event.event === 'graph_resumed') {
    markExecutionNode(event.step_id, 'running', '从检查点继续');
    addExecutionEvent('检查点恢复完成', 'success');
  } else if (event.event === 'graph_resume_requested') {
    addExecutionEvent('正在载入上次保存的检查点');
  } else if (event.event === 'audit_revision_started') {
    trace.revisionCount += 1;
    setExecutionMetric('execution-revision-count', trace.revisionCount);
    markExecutionNode(event.audit_step_id || 'audit', 'revision', '正在受控返修');
    addExecutionEvent('审核触发受控返修', 'revision');
  } else if (event.event === 'audit_revision_completed') {
    markExecutionNode(event.audit_step_id || 'audit', event.status === 'pass' ? 'completed' : 'failed', event.status === 'pass' ? '返修复审通过' : '等待人工复核');
    addExecutionEvent(event.status === 'pass' ? '返修完成，复审通过' : '返修后转人工复核', event.status === 'pass' ? 'success' : 'warning');
  } else if (event.event === 'run_completed') {
    trace.status = 'completed';
    elements.executionStatus.textContent = '已完成';
    elements.executionEngine.textContent = '成功终态';
    elements.executionToggle.className = 'execution-toggle is-complete';
    addExecutionEvent('所有节点完成，结果已返回', 'success');
    stopExecutionClock();
  } else if (event.event === 'run_failed') {
    trace.status = 'failed';
    elements.executionStatus.textContent = '执行失败';
    elements.executionEngine.textContent = '异常终态';
    elements.executionToggle.className = 'execution-toggle is-error';
    markExecutionNode(stepId, 'failed', '执行失败');
    addExecutionEvent(event.message || '流程执行失败', 'error');
    stopExecutionClock();
  }
  persistState();
}

function scrollToLatest(behavior = 'smooth') {
  requestAnimationFrame(() => {
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior });
  });
}

function addUserMessage(content) {
  const article = document.createElement('article');
  article.className = 'message message--user';
  article.setAttribute('aria-label', '你的消息');
  const body = document.createElement('div');
  body.className = 'message-body';
  const paragraph = document.createElement('p');
  paragraph.className = 'message-text';
  paragraph.textContent = content;
  body.appendChild(paragraph);
  article.appendChild(body);
  elements.list.appendChild(article);
  return article;
}

function addAssistantShell(className = 'message--assistant') {
  const article = document.createElement('article');
  article.className = `message ${className}`;
  article.setAttribute('aria-label', className === 'message--error' ? '回复失败' : '岐黄学伴的回复');
  const seal = document.createElement('div');
  seal.className = 'assistant-seal';
  seal.setAttribute('aria-hidden', 'true');
  seal.textContent = className === 'message--error' ? '止' : '岐';
  const body = document.createElement('div');
  body.className = 'message-body';
  article.append(seal, body);
  elements.list.appendChild(article);
  return { article, body };
}

function cleanInlineText(text) {
  return text.replace(/\*\*(.*?)\*\*/g, '$1').replaceAll('`', '').trim();
}

function tableCells(line) {
  return line.replace(/^\||\|$/g, '').split('|').map(cell => cleanInlineText(cell));
}

function isTableDivider(line) {
  const cells = tableCells(line);
  return cells.length > 0 && cells.every(cell => /^:?-{3,}:?$/.test(cell));
}

function appendRichText(container, rawText) {
  const normalized = String(rawText).replace(/(【[^】]+】)(?=#{1,4}\s)/g, '$1\n');
  const lines = normalized.split(/\r?\n/);
  let index = 0;
  const isSpecialLine = (line, nextLine = '') => (
    /^#{1,4}\s+/.test(line)
    || /^【[^】]+】$/.test(line)
    || /^[-*]\s+/.test(line)
    || /^\d+[.、]\s*/.test(line)
    || (line.trim().startsWith('|') && isTableDivider(nextLine.trim()))
  );

  while (index < lines.length) {
    const line = lines[index].trim();
    if (!line) {
      index += 1;
      continue;
    }

    if (/^【[^】]+】$/.test(line)) {
      const flag = document.createElement('p');
      flag.className = 'plan-flag';
      flag.textContent = line.slice(1, -1);
      container.appendChild(flag);
      index += 1;
      continue;
    }

    const headingMatch = line.match(/^#{1,4}\s+(.+)$/);
    if (headingMatch) {
      const heading = document.createElement('h3');
      heading.className = 'rich-heading';
      heading.textContent = cleanInlineText(headingMatch[1]);
      container.appendChild(heading);
      index += 1;
      continue;
    }

    if (line.startsWith('|') && isTableDivider((lines[index + 1] || '').trim())) {
      const rows = [];
      while (index < lines.length && lines[index].trim().startsWith('|')) {
        rows.push(tableCells(lines[index].trim()));
        index += 1;
      }
      const wrapper = document.createElement('div');
      wrapper.className = 'rich-table-wrap';
      const table = document.createElement('table');
      table.className = 'rich-table';
      const head = document.createElement('thead');
      const headRow = document.createElement('tr');
      rows[0].forEach(cell => {
        const column = document.createElement('th');
        column.scope = 'col';
        column.textContent = cell;
        headRow.appendChild(column);
      });
      head.appendChild(headRow);
      const body = document.createElement('tbody');
      rows.slice(2).forEach(row => {
        const tableRow = document.createElement('tr');
        row.forEach(cell => {
          const column = document.createElement('td');
          column.textContent = cell;
          tableRow.appendChild(column);
        });
        body.appendChild(tableRow);
      });
      table.append(head, body);
      wrapper.appendChild(table);
      container.appendChild(wrapper);
      continue;
    }

    if (/^[-*]\s+/.test(line)) {
      const list = document.createElement('ul');
      while (index < lines.length && /^[-*]\s+/.test(lines[index].trim())) {
        const item = document.createElement('li');
        item.textContent = cleanInlineText(lines[index].trim().replace(/^[-*]\s+/, ''));
        list.appendChild(item);
        index += 1;
      }
      container.appendChild(list);
      continue;
    }

    if (/^\d+[.、]\s*/.test(line)) {
      const list = document.createElement('ol');
      while (index < lines.length && /^\d+[.、]\s*/.test(lines[index].trim())) {
        const item = document.createElement('li');
        item.textContent = cleanInlineText(lines[index].trim().replace(/^\d+[.、]\s*/, ''));
        list.appendChild(item);
        index += 1;
      }
      container.appendChild(list);
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (index < lines.length) {
      const nextLine = lines[index].trim();
      if (!nextLine || isSpecialLine(nextLine, (lines[index + 1] || '').trim())) break;
      paragraphLines.push(nextLine);
      index += 1;
    }
    const paragraph = document.createElement('p');
    paragraph.className = 'rich-paragraph';
    paragraph.textContent = cleanInlineText(paragraphLines.join('\n'));
    container.appendChild(paragraph);
  }
}

function appendStructuredValue(container, value) {
  if (value === null || value === undefined || value === '') return;
  if (Array.isArray(value)) {
    const list = document.createElement('ul');
    value.forEach(item => {
      const row = document.createElement('li');
      appendStructuredValue(row, item);
      list.appendChild(row);
    });
    container.appendChild(list);
    return;
  }
  if (typeof value === 'object') {
    const list = document.createElement('dl');
    Object.entries(value).forEach(([key, item]) => {
      if (item === null || item === undefined || item === '') return;
      const wrapper = document.createElement('div');
      const term = document.createElement('dt');
      const description = document.createElement('dd');
      term.textContent = humanizeLabel(key);
      appendStructuredValue(description, item);
      wrapper.append(term, description);
      list.appendChild(wrapper);
    });
    container.appendChild(list);
    return;
  }
  if (typeof value === 'string') {
    appendRichText(container, value);
  } else {
    const paragraph = document.createElement('p');
    paragraph.textContent = String(value);
    container.appendChild(paragraph);
  }
}

function humanizeLabel(label) {
  const labels = {
    task_content: '任务内容',
    completion_criteria: '完成标准',
    estimated_minutes: '预计用时（分钟）',
    knowledge_points: '知识要点',
    questions: '练习题',
    answer: '答案',
    analysis: '解析',
    title: '标题',
    content: '内容',
    stage: '阶段',
    book: '教材',
    goal: '阶段目标',
  };
  return labels[label] || label.replaceAll('_', ' ');
}

function renderAssistantMessage(presentation) {
  const { article, body } = addAssistantShell();
  const intro = document.createElement('p');
  intro.className = 'assistant-intro';
  intro.textContent = presentation.intro;
  body.appendChild(intro);

  if (presentation.questions?.length) {
    const note = document.createElement('section');
    note.className = 'clarification-note';
    const title = document.createElement('strong');
    title.textContent = '请补充以下信息';
    const list = document.createElement('ol');
    presentation.questions.forEach(question => {
      const item = document.createElement('li');
      item.textContent = question;
      list.appendChild(item);
    });
    note.append(title, list);
    body.appendChild(note);
  }

  if (presentation.sections?.length) {
    const sections = document.createElement('div');
    sections.className = 'result-sections';
    presentation.sections.forEach(section => {
      const block = document.createElement('section');
      block.className = 'result-section';
      const title = document.createElement('h2');
      title.textContent = section.title;
      block.appendChild(title);
      appendStructuredValue(block, section.content);
      sections.appendChild(block);
    });
    body.appendChild(sections);
  }
  return article;
}

function addLoadingMessage() {
  const fragment = elements.loadingTemplate.content.cloneNode(true);
  const article = fragment.querySelector('.message--loading');
  elements.list.appendChild(fragment);
  return article;
}

function addErrorMessage(message, requestText) {
  const { article, body } = addAssistantShell('message--error');
  article.dataset.transient = 'true';
  const paragraph = document.createElement('p');
  paragraph.className = 'message-text';
  paragraph.textContent = message;
  const retry = document.createElement('button');
  retry.className = 'retry-button';
  retry.type = 'button';
  retry.textContent = '重新生成';
  retry.addEventListener('click', () => sendMessage(requestText, { appendUser: false }));
  body.append(paragraph, retry);
  return article;
}

function renderSession() {
  elements.list.replaceChildren();
  elements.welcome.hidden = state.messages.length > 0;
  state.messages.forEach(message => {
    if (message.role === 'user') addUserMessage(message.content);
    else if (message.presentation) renderAssistantMessage(message.presentation);
    else renderAssistantMessage({ intro: message.content, sections: [] });
  });
  elements.availableMinutes.value = state.availableMinutes;
  if (state.pendingRequest) {
    addErrorMessage('上次回复因页面关闭或连接中断而未完成，你可以从这里继续。', state.pendingRequest);
  }
}

function findPlanResult(body) {
  if (body?.learning_plan) return body.learning_plan;
  const outputs = Array.isArray(body?.agent_outputs) ? body.agent_outputs : [];
  return outputs.find(item => item?.producer === 'learning_plan_service')?.payload || null;
}

function textFromSections(sections) {
  return sections.map(section => {
    const content = typeof section.content === 'string'
      ? section.content
      : JSON.stringify(section.content, null, 2);
    return `${section.title}\n${content}`;
  }).join('\n\n');
}

function buildAssistantPresentation(body) {
  if (body?.status === 'interrupted') {
    const interruption = body.interrupt || {};
    const questions = Array.isArray(interruption.questions)
      ? interruption.questions.filter(Boolean)
      : [];
    const intro = `${interruption.reason || '我还需要确认一些信息。'}\n\n流程已在当前节点暂停；回答后会从检查点继续，不会重新执行已经完成的步骤。`;
    return {
      intro,
      questions,
      sections: [],
      plainText: [intro, ...questions.map((question, index) => `${index + 1}. ${question}`)].join('\n'),
    };
  }
  const plan = findPlanResult(body);
  if (plan?.requires_clarification) {
    const questions = Array.isArray(plan.clarification_questions)
      ? plan.clarification_questions.filter(Boolean)
      : [];
    const intro = plan.reason || '为了让接下来的安排真正适合你，我还需要确认一点信息。';
    return {
      intro,
      questions,
      sections: [],
      plainText: [intro, ...questions.map((question, index) => `${index + 1}. ${question}`)].join('\n'),
    };
  }

  if (plan) {
    const sections = [];
    if (plan.long_term_plan?.content) {
      sections.push({ title: '长期学习规划', content: plan.long_term_plan.content });
    }
    if (Array.isArray(plan.long_term_plan?.stages) && plan.long_term_plan.stages.length) {
      sections.push({
        title: '阶段路线',
        content: plan.long_term_plan.stages.map(stage => ({
          stage: `第 ${stage.stage} 阶段`,
          book: Array.isArray(stage.book) ? stage.book.join('、') : stage.book,
          goal: stage.goal,
        })),
      });
    }
    if (plan.short_term_plan?.content) {
      sections.push({ title: '短期计划', content: plan.short_term_plan.content });
    }
    if (plan.learning_task) {
      const task = {};
      if (plan.learning_task.task_content) task.task_content = plan.learning_task.task_content;
      if (plan.learning_task.completion_criteria) task.completion_criteria = plan.learning_task.completion_criteria;
      if (plan.learning_task.estimated_minutes) task.estimated_minutes = plan.learning_task.estimated_minutes;
      if (Object.keys(task).length) sections.push({ title: '当前学习任务', content: task });
    }
    const intros = {
      long_term: '长期规划已经整理好。原有短期计划和当日任务已失效，需要基于新规划分别重新制定。',
      short_term: '短期计划已经整理好。原有当日任务已失效，需要基于这份短期计划重新安排。',
      daily_task: '当日任务已经结合当前短期计划安排好。',
    };
    const intro = intros[plan.generated_scope]
      || '我已经结合你的目标和当前信息整理好了安排。你可以继续告诉我哪里不合适，我会据此调整。';
    return { intro, sections, questions: [], plainText: `${intro}\n\n${textFromSections(sections)}` };
  }

  if (body?.resource) {
    const title = body.resource.title || '学习内容';
    const resourceContent = body.resource.content || {};
    const sections = Object.entries(resourceContent).map(([sectionTitle, content]) => ({
      title: humanizeLabel(sectionTitle),
      content,
    }));
    const intros = {
      knowledge_explanation: `下面是关于“${title}”的梳理。`,
      paper_generation: `我根据你的要求整理了“${title}”。`,
      personalized_review_card: `这份“${title}”可以作为你本次复习的主线。`,
    };
    const intro = intros[body.task_type] || `我为你整理了“${title}”。`;
    return { intro, sections, questions: [], plainText: `${intro}\n\n${textFromSections(sections)}` };
  }

  const intro = '本次处理已经完成。你可以继续补充目标或提出下一步需求。';
  return { intro, sections: [], questions: [], plainText: intro };
}

function updatePlanningContext(body) {
  const plan = findPlanResult(body);
  if (!plan || plan.requires_clarification) return;
  if (plan.generated_scope === 'long_term') {
    if (plan.long_term_plan) state.longTermPlan = plan.long_term_plan;
    state.shortTermPlan = {};
    state.learningTask = {};
    return;
  }
  if (plan.generated_scope === 'short_term') {
    if (plan.short_term_plan) state.shortTermPlan = plan.short_term_plan;
    state.learningTask = {};
    return;
  }
  if (plan.generated_scope === 'daily_task') {
    if (plan.learning_task) state.learningTask = plan.learning_task;
    return;
  }
  if (plan.long_term_plan) state.longTermPlan = plan.long_term_plan;
  if (plan.short_term_plan) state.shortTermPlan = plan.short_term_plan;
  if (plan.learning_task) state.learningTask = plan.learning_task;
}

function targetLayersForRequest(request) {
  const scope = inferPlanScope(request);
  if (scope === 'daily_task') return ['daily_task'];
  if (scope === 'short_term') return ['short_term'];
  if (scope === 'long_term') return ['long_term'];
  const mentionsLongTerm = request.includes('长期');
  const shortTermPhrases = ['短期', '本周', '这周', '今日', '今天', '每日', '任务', '近期'];
  const mentionsShortTerm = shortTermPhrases.some(phrase => request.includes(phrase));
  if (mentionsLongTerm && !mentionsShortTerm) return ['long_term'];
  if (mentionsShortTerm && !mentionsLongTerm) return ['short_term'];
  return ['long_term', 'short_term'];
}

function buildPlanChangeContext(requestText) {
  const clarification = state.pendingClarification;
  if (!clarification) return null;
  return {
    original_request: clarification.originalRequest,
    target_layers: clarification.targetLayers,
    change_details: [...clarification.answers, requestText].join('\n'),
  };
}

function resumeScopeForClarification(plan) {
  const requestedScope = plan?.requested_scope;
  if (requestedScope === 'unspecified') return 'unspecified';
  if (!['long_term', 'short_term', 'daily_task'].includes(requestedScope)) return null;
  const clarificationText = [
    plan.reason,
    ...(Array.isArray(plan.clarification_questions) ? plan.clarification_questions : []),
  ].filter(Boolean).join('\n');
  if (requestedScope === 'short_term' && clarificationText.includes('长期规划')) return 'long_term';
  if (requestedScope === 'daily_task' && clarificationText.includes('短期计划')) return 'short_term';
  return requestedScope;
}

function updateClarificationContext(body, requestText, answeredClarification) {
  if (body?.status === 'interrupted') {
    const interruption = body.interrupt || {};
    const requestedScope = interruption.requested_scope;
    const targetLayers = ['long_term', 'short_term', 'daily_task'].includes(requestedScope)
      ? [requestedScope]
      : targetLayersForRequest(requestText);
    state.pendingClarification = {
      originalRequest: answeredClarification?.originalRequest || requestText,
      targetLayers,
      answers: answeredClarification?.answers || [],
      resumeScope: requestedScope || answeredClarification?.resumeScope || null,
    };
    state.pendingInterrupt = interruption;
    return;
  }
  const plan = findPlanResult(body);
  if (!plan?.requires_clarification) {
    state.pendingClarification = null;
    return;
  }
  if (answeredClarification) {
    state.pendingClarification = {
      ...answeredClarification,
      answers: [...answeredClarification.answers, requestText],
      resumeScope: resumeScopeForClarification(plan),
    };
    return;
  }
  state.pendingClarification = {
    originalRequest: requestText,
    targetLayers: targetLayersForRequest(requestText),
    answers: [],
    resumeScope: resumeScopeForClarification(plan),
  };
}

function updateLoadingLabel(loadingNode, event) {
  const label = loadingNode?.querySelector('.thinking-label');
  if (!label) return;
  const agentLabels = {
    planner_agent: '正在理解你的需求',
    diagnosis_agent: '正在结合你的学习状态分析',
    learning_plan_service: '正在整理学习安排',
    expert_agent: '正在编写学习内容',
    review_scheduler: '正在安排复习节奏',
    audit_agent: '正在检查内容质量',
  };
  if (event.event === 'run_started') label.textContent = '正在理解你的需求';
  if (event.event === 'run_resumed') label.textContent = '正在从上次中断处继续';
  if (event.event === 'graph_resumed') label.textContent = '检查点已恢复，正在继续处理';
  if (event.event === 'step_started') label.textContent = agentLabels[event.agent] || '正在继续处理';
}

async function consumeEventStream(response, loadingNode) {
  if (!response.ok || !response.body) {
    let detail = `请求失败（HTTP ${response.status}）`;
    try {
      const error = await response.json();
      if (typeof error.detail === 'string') detail = error.detail;
    } catch (_error) {
      // The status message above is sufficient when the body is not JSON.
    }
    throw new Error(detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() || '';
    for (const frame of frames) {
      const dataLine = frame.split('\n').find(line => line.startsWith('data: '));
      if (!dataLine) continue;
      const event = JSON.parse(dataLine.slice(6));
      handleExecutionEvent(event);
      updateLoadingLabel(loadingNode, event);
      if (event.event === 'run_failed') throw new Error(event.message || '生成失败，请稍后重试。');
      if (event.event === 'run_interrupted') return event.result;
      if (event.event === 'run_completed') return event.result;
    }
  }
  throw new Error('连接已结束，但没有收到完整回复。');
}

function requestMessages() {
  return state.messages.slice(-30).map(message => ({
    message_id: message.id,
    role: message.role,
    content: message.content,
  }));
}

async function sendMessage(rawText, options = {}) {
  const text = rawText.trim();
  if (!text || activeRequest) return;

  elements.list.querySelectorAll('.message--error[data-transient]').forEach(node => node.remove());
  elements.welcome.hidden = true;
  if (options.appendUser !== false) {
    const userMessage = { id: createId('MSG'), role: 'user', content: text };
    state.messages.push(userMessage);
    addUserMessage(text);
  }

  state.pendingRequest = text;
  state.availableMinutes = Math.max(1, Math.min(1440, Number(elements.availableMinutes.value) || 60));
  persistState();
  const loadingNode = addLoadingMessage();
  setBusy(true);
  setConnectionStatus('正在生成', 'online');
  scrollToLatest();
  activeRequest = new AbortController();
  const answeredClarification = state.pendingClarification;
  const planChangeContext = buildPlanChangeContext(text);
  const resumeScope = answeredClarification?.resumeScope;
  const inferredPlanScope = inferPlanScope(text) || null;
  const planScope = resumeScope === 'unspecified'
    ? inferPlanLayerAnswer(text) || 'unspecified'
    : resumeScope || null;
  const isResume = Boolean(answeredClarification && state.pendingThreadId);
  if (!isResume) state.pendingThreadId = createId('THREAD').replaceAll('-', '');
  persistState();

  try {
    const endpoint = isResume
      ? `/api/v1/review-cards/runs/${encodeURIComponent(state.pendingThreadId)}/resume/stream`
      : STREAM_ENDPOINT;
    const payload = isResume
      ? {
          answer: text,
          plan_scope: planScope,
          plan_change_context: planChangeContext,
        }
      : {
          thread_id: state.pendingThreadId,
          learner_id: state.learnerId,
          user_request: text,
          available_minutes: state.availableMinutes,
          messages: requestMessages(),
          long_term_plan: state.longTermPlan,
          short_term_plan: state.shortTermPlan,
          learning_task: state.learningTask,
          plan_scope: planScope,
          plan_scope_hint: inferredPlanScope,
          plan_change_context: planChangeContext,
        };
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: activeRequest.signal,
      body: JSON.stringify(payload),
    });
    const body = await consumeEventStream(response, loadingNode);
    const presentation = buildAssistantPresentation(body);
    updatePlanningContext(body);
    loadLearningContext({ quiet: true });
    updateClarificationContext(body, text, answeredClarification);
    state.messages.push({
      id: createId('MSG'),
      role: 'assistant',
      content: presentation.plainText,
      presentation,
    });
    state.pendingRequest = null;
    if (body?.status !== 'interrupted') {
      state.pendingThreadId = null;
      state.pendingInterrupt = null;
    }
    loadingNode.remove();
    renderAssistantMessage(presentation);
    persistState();
    setConnectionStatus('服务正常', 'online');
  } catch (error) {
    loadingNode.remove();
    if (error.name !== 'AbortError') {
      addErrorMessage('连接暂时中断；服务端流程会继续运行，重新打开页面后可恢复。', text);
      setConnectionStatus('连接中断', 'offline');
    }
  } finally {
    activeRequest = null;
    setBusy(false);
    elements.input.focus();
    scrollToLatest();
  }
}

async function restoreLangGraphRun() {
  if (!state.pendingThreadId) return;
  try {
    const response = await fetch(`/api/v1/review-cards/runs/${encodeURIComponent(state.pendingThreadId)}`);
    if (response.status === 404) {
      state.pendingThreadId = null;
      state.pendingInterrupt = null;
      state.pendingRequest = null;
      persistState();
      setConnectionStatus('上次检查点已失效', 'offline');
      return;
    }
    if (!response.ok) throw new Error('status unavailable');
    const run = await response.json();
    if (run.status === 'running') {
      state.executionTrace.status = 'running';
      if (!state.executionTrace.startedAt) state.executionTrace.startedAt = Date.now();
      renderExecutionMonitor();
      persistState();
      setConnectionStatus('正在后台继续', 'online');
      window.setTimeout(restoreLangGraphRun, 2500);
      return;
    }
    if (run.status === 'interrupted') {
      state.executionTrace.status = 'interrupted';
      stopExecutionClock();
      renderExecutionMonitor();
      state.pendingInterrupt = run.interrupt;
      if (!state.pendingClarification) {
        const requestText = state.pendingRequest || '请继续';
        const body = { status: 'interrupted', interrupt: run.interrupt };
        const presentation = buildAssistantPresentation(body);
        updateClarificationContext(body, requestText, null);
        state.messages.push({
          id: createId('MSG'), role: 'assistant', content: presentation.plainText, presentation,
        });
        state.pendingRequest = null;
        renderSession();
      }
      persistState();
      setConnectionStatus('等待你回答后继续', 'online');
      return;
    }
    if (run.status === 'completed' && run.result && state.pendingRequest) {
      state.executionTrace.status = 'completed';
      stopExecutionClock();
      renderExecutionMonitor();
      const presentation = buildAssistantPresentation(run.result);
      updatePlanningContext(run.result);
      loadLearningContext({ quiet: true });
      state.messages.push({
        id: createId('MSG'), role: 'assistant', content: presentation.plainText, presentation,
      });
      state.pendingRequest = null;
      state.pendingClarification = null;
      state.pendingThreadId = null;
      state.pendingInterrupt = null;
      persistState();
      renderSession();
      setConnectionStatus('已恢复并完成', 'online');
      return;
    }
    if (run.status === 'failed') {
      state.executionTrace.status = 'failed';
      stopExecutionClock();
      renderExecutionMonitor();
      persistState();
      setConnectionStatus('上次执行失败', 'offline');
    }
  } catch (_error) {
    setConnectionStatus('等待重新连接', 'offline');
    window.setTimeout(restoreLangGraphRun, 3000);
  }
}

function resizeComposer() {
  elements.input.style.height = 'auto';
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 160)}px`;
}

elements.form.addEventListener('submit', event => {
  event.preventDefault();
  const text = elements.input.value;
  elements.input.value = '';
  resizeComposer();
  sendMessage(text);
});

elements.input.addEventListener('input', resizeComposer);
elements.input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    elements.form.requestSubmit();
  }
});

elements.availableMinutes.addEventListener('change', () => {
  state.availableMinutes = Math.max(1, Math.min(1440, Number(elements.availableMinutes.value) || 60));
  elements.availableMinutes.value = state.availableMinutes;
  persistState();
});

document.querySelectorAll('[data-prompt]').forEach(button => {
  button.addEventListener('click', () => {
    elements.input.value = button.dataset.prompt;
    resizeComposer();
    elements.input.focus();
  });
});

elements.newConversation.addEventListener('click', () => {
  if (state.messages.length && !window.confirm('开始新对话后，当前页面中的会话记录将被清除。是否继续？')) return;
  activeRequest?.abort();
  localStorage.removeItem(storageKey());
  state = emptyState();
  renderSession();
  resetExecutionMonitor({ persist: true });
  setConnectionStatus('服务正常', 'online');
  elements.input.focus();
});

elements.executionToggle.addEventListener('click', () => {
  const open = elements.executionToggle.getAttribute('aria-expanded') !== 'true';
  setExecutionPanel(open);
});

elements.executionClose.addEventListener('click', () => setExecutionPanel(false));
elements.executionOverlay.addEventListener('click', () => setExecutionPanel(false));
elements.learningToggle.addEventListener('click', () => {
  setLearningPanel(elements.learningToggle.getAttribute('aria-expanded') !== 'true');
});
elements.learningClose.addEventListener('click', () => setLearningPanel(false));
elements.learningOverlay.addEventListener('click', () => setLearningPanel(false));
elements.learningRefresh.addEventListener('click', () => loadLearningContext());
elements.focusStart.addEventListener('click', startFocusSession);
elements.focusEnd.addEventListener('click', endFocusSession);
elements.currentTaskComplete.addEventListener('click', completeCurrentTask);
['pointerdown', 'keydown', 'touchstart'].forEach(eventName => {
  document.addEventListener(eventName, () => { focusInteractionObserved = true; }, { passive: true });
});
document.addEventListener('visibilitychange', () => {
  if (focusState) sendFocusHeartbeat().catch(() => {});
});
window.addEventListener('resize', () => {
  const mobile = window.matchMedia('(max-width: 68rem)').matches;
  if (mobile !== executionViewportWasMobile) {
    executionViewportWasMobile = mobile;
    setExecutionPanel(!mobile);
  }
  window.requestAnimationFrame(drawExecutionEdges);
});

async function checkHealth() {
  try {
    const response = await fetch('/health');
    if (!response.ok) throw new Error('unavailable');
    setConnectionStatus('服务正常', 'online');
  } catch (_error) {
    setConnectionStatus('服务未连接', 'offline');
  }
}

renderSession();
renderExecutionMonitor();
setExecutionPanel(!executionViewportWasMobile);
checkHealth();
restoreLangGraphRun();
if (storageOwnerId !== 'pending') {
  loadLearningContext({ quiet: true });
  restoreFocusState();
}

window.addEventListener('competition:auth-ready', event => {
  const nextOwnerId = event.detail.user_id;
  if (storageOwnerId !== nextOwnerId) {
    storageOwnerId = nextOwnerId;
    state = restoreState();
  }
  state.learnerId = nextOwnerId;
  persistState();
  renderSession();
  renderExecutionMonitor();
  restoreLangGraphRun();
  loadLearningContext({ quiet: true });
  restoreFocusState();
});
