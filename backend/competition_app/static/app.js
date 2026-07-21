const form = document.querySelector('#run-form');
const minutes = document.querySelector('#minutes');
const minutesOutput = document.querySelector('#minutes-output');
const pipeline = document.querySelector('#pipeline');
const emptyState = document.querySelector('#empty-state');
const runState = document.querySelector('#run-state');
const button = document.querySelector('#run-button');
const resultSection = document.querySelector('#result-section');
const reviewQueueList = document.querySelector('#review-queue-list');
const reviewQueueSummary = document.querySelector('#review-queue-summary');
const reviewDispatchButton = document.querySelector('#review-dispatch-button');
const reviewQueueButton = document.querySelector('#review-queue-button');
const modelIoSection = document.querySelector('#model-io-section');
const modelIoList = document.querySelector('#model-io-list');
const graphCanvas = document.querySelector('#graph-canvas');
const graphEventLog = document.querySelector('#graph-event-log');
const graphNodeElements = new Map();
const RUN_STORAGE_PREFIX = 'competition_demo_langgraph_run_v2';
let authenticatedLearnerId = sessionStorage.getItem('competition.auth.user_id') || '';
const runStorageKey = () => `${RUN_STORAGE_PREFIX}.${authenticatedLearnerId || 'pending'}`;
let environmentMode = 'unknown';
let executionEngine = 'unknown';
let currentGraph = null;
let graphRetryCount = 0;
let graphRevisionCount = 0;
let graphStartedAt = null;
let graphClock = null;
let lastSubmittedRequest = '';
let pendingPlanChangeContext = null;
let pendingPlanScope = null;
let currentLongTermPlan = null;
let currentShortTermPlan = null;
let currentLearningTask = null;
let pendingThreadId = null;
let pendingInterrupt = null;
let resumeRequested = false;
let runPollTimer = null;

const isoHoursAgo = (hours) => new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();

const presets = {
  review_due: {
    label: '到期复习 · 四君子汤', learnerId: 'demo_review_due_001', minutes: 15,
    request: '请结合我的真实掌握状态，为四君子汤生成一张可立即学习的个性化复习卡。',
    longPlan: '未来一年系统掌握方剂学核心知识，建立“组成—功用—主治—配伍—辨析”的稳定知识框架，并通过阶段测验持续检验学习效果。',
    shortPlan: '未来两周重点巩固补气类方剂。优先复习四君子汤的组成与配伍，每次复习后完成主动回忆和错题辨析。',
    packet: () => ({
      user_profile: { user_id: 'demo_review_due_001', user_name: '林同学', user_preference: { communication_style: '先对比再总结', learning_resource_preferences: ['knowledge_card', 'question'], learning_periods: ['晚间'] }, user_group: { group1: '跨专业' }, user_major_or_profession: '康复治疗师', user_area: '上海', completed_courses: ['中医基础理论导论'], goals: { long_term_goal: '建立方剂学知识体系', short_term_goal: '掌握四君子汤组成与配伍' }, daily_available_minutes: 15, education: '本科' },
      learning_profile: { current_status: { status_code: 'T1', status_name: '薄弱点巩固', confidence: 0.88, evidence: ['四君子汤相关知识点掌握度偏低'] } },
      system_data: { task_completion_rate: { learning_task_completion_rate: { value: 0.72 }, review_task_completion_rate: { value: 0.55 } }, resource_click_rate: { value: 0.8 } },
      user_knowledge_state: [{ user_id: 'demo_review_due_001', kp_id: '020490', knowledge_mastery: 0.58, answer_accuracy: 0.60, forgetting_coefficient: 0.08, kp_review_status: '需要继续复习', calculated_at: isoHoursAgo(30) }],
      question_attempt: [{ attempt_id: 'ATT_DEMO_001', user_id: 'demo_review_due_001', question_id: 'Q_FJ_001', task_id: 'TASK_DEMO_001', submitted_answer: ['B'], is_correct: false, score: 0, response_time_seconds: 48, reason_for_mistake: '组成与功效混淆', answered_at: isoHoursAgo(32) }],
      question_learning_stats: [{ question_id: 'Q_FJ_001', user_id: 'demo_review_due_001', reason_for_mistake: '组成与功效混淆', answer_accuracy: 0.5, calculated_at: isoHoursAgo(30) }],
    }),
  },
  initial_recall: {
    label: '首次复习 · 理中丸', learnerId: 'demo_initial_001', minutes: 10,
    request: '我刚学完理中丸，请生成首次主动回忆复习卡。',
    longPlan: '逐步掌握常用方剂的组成、功用、主治与配伍逻辑，形成按功效分类的方剂知识体系。',
    shortPlan: '本周完成理中丸首次学习后的主动回忆，并在后续复习中辨析理中丸与四君子汤的核心区别。',
    packet: () => ({
      user_profile: { user_id: 'demo_initial_001', user_name: '周同学', user_preference: { communication_style: '简洁', learning_resource_preferences: ['knowledge_card'], learning_periods: ['午间'] }, user_group: { group2: '学历教育' }, user_major_or_profession: '中医学学生', goals: { long_term_goal: '掌握常用方剂', short_term_goal: '完成理中丸首次回忆' }, daily_available_minutes: 10, education: '本科在读' },
      learning_profile: { current_status: { status_code: 'T0', status_name: '初始学习', confidence: 0.9, evidence: ['尚无该知识点历史复习状态'] } },
      system_data: {}, user_knowledge_state: [], question_attempt: [],
    }),
  },
  learning_plan: {
    label: '本周学习计划', learnerId: 'demo_plan_001', minutes: 25,
    request: '请结合我的学习状态，为四君子汤制定本周学习计划，只输出阶段安排、每日任务和验收标准。',
    longPlan: '系统掌握方剂学主要治法与代表方，能够根据证候判断治法、选择基础方并说明配伍依据。',
    shortPlan: '本周集中掌握补气类方剂，每天完成一个知识模块和一次简短自测，周末完成综合验收。',
    packet: () => ({
      user_profile: { user_id: 'demo_plan_001', user_name: '陈同学', user_preference: { communication_style: '任务清单式', learning_resource_preferences: ['question'], learning_periods: ['早晨'] }, user_group: { group1: '跨专业' }, user_major_or_profession: '全科医生', goals: { long_term_goal: '系统掌握方剂学', short_term_goal: '本周掌握补气类方剂' }, daily_available_minutes: 25, education: '硕士' },
      learning_profile: { current_status: { status_code: 'T2', status_name: '节奏恢复', confidence: 0.82, evidence: ['近一周任务完成率下降'] } },
      system_data: { task_completion_rate: { learning_task_completion_rate: { value: 0.45 }, review_task_completion_rate: { value: 0.4 } }, resource_click_rate: { value: 0.65 } },
      user_knowledge_state: [{ user_id: 'demo_plan_001', kp_id: '020490', knowledge_mastery: 0.68, answer_accuracy: 0.7, forgetting_coefficient: 0.07, kp_review_status: '按计划复习', calculated_at: isoHoursAgo(12) }], question_attempt: [],
    }),
  },
  explanation: {
    label: '知识讲解 · 四君子汤', learnerId: 'demo_explain_001', minutes: 12,
    request: '请解释四君子汤为什么以人参为君药，并辨析它与理中丸的核心区别。',
    longPlan: '理解常用方剂的组方原则与配伍逻辑，能够从病机、治法和药物作用三个层面解释方义。',
    shortPlan: '未来一周完成四君子汤、理中丸等基础方的对比学习，重点辨析补气与温中的治法差异。',
    packet: () => ({
      user_profile: { user_id: 'demo_explain_001', user_name: '王同学', user_preference: { communication_style: '对比辨析', learning_resource_preferences: ['knowledge_card'], learning_periods: ['晚间'] }, user_group: { group2: '学历教育' }, user_major_or_profession: '中药学学生', goals: { long_term_goal: '理解方剂配伍逻辑', short_term_goal: '辨析补气方与温中方' }, daily_available_minutes: 12, education: '本科在读' },
      learning_profile: {}, system_data: {}, user_knowledge_state: [], question_attempt: [],
    }),
  },
  paper: {
    label: '模拟组卷 · 四君子汤', learnerId: 'demo_paper_001', minutes: 45,
    request: '请围绕四君子汤组成、功效主治和配伍意义生成一份章节模拟卷。',
    longPlan: '为方剂学课程考试建立完整知识体系，按章节完成知识学习、专项练习、错题复盘和阶段模拟。',
    shortPlan: '本周完成四君子汤章节测试，覆盖组成、功效主治、配伍意义及相近方辨析，并根据错题安排补救复习。',
    packet: () => ({
      user_profile: { user_id: 'demo_paper_001', user_name: '赵同学', user_preference: { communication_style: '考试导向', learning_resource_preferences: ['question'], learning_periods: ['周末'] }, user_group: { group2: '学历教育' }, user_major_or_profession: '中医学学生', goals: { long_term_goal: '准备方剂学课程考试', short_term_goal: '完成四君子汤章节测试' }, daily_available_minutes: 45, education: '本科在读' },
      exam_constraints: { exam_type: '章节练习', audience: '中医学本科生', syllabus_scope: '四君子汤组成、功效主治、配伍意义与相近方辨析', duration_minutes: 45, total_score: 100, question_count: 4, question_type_distribution: { 单项选择题: { count: 2, score: 40 }, 简答题: { count: 2, score: 60 } }, answer_and_rubric_requirement: '需要答案、解析和评分说明', source_status: 'user-provided-unverified' },
    }),
  },
};

function clearStoredRun() {
  window.localStorage.removeItem(runStorageKey());
  pendingThreadId = null;
  pendingInterrupt = null;
  resumeRequested = false;
  if (runPollTimer) window.clearTimeout(runPollTimer);
  runPollTimer = null;
}

function readStoredRun() {
  try {
    return JSON.parse(window.localStorage.getItem(runStorageKey()) || 'null');
  } catch (_) {
    window.localStorage.removeItem(runStorageKey());
    return null;
  }
}

function persistRun(patch = {}) {
  const current = readStoredRun() || {};
  window.localStorage.setItem(runStorageKey(), JSON.stringify({ ...current, ...patch }));
}

function createThreadId() {
  return `THREAD_${window.crypto?.randomUUID?.().replaceAll('-', '') || Date.now().toString(36)}`;
}

function applyPreset(name, { preserveRun = false } = {}) {
  const preset = presets[name];
  if (!preset) return;
  if (!preserveRun) clearStoredRun();
  pendingPlanChangeContext = null;
  pendingPlanScope = null;
  currentLongTermPlan = null;
  currentShortTermPlan = null;
  currentLearningTask = null;
  lastSubmittedRequest = '';
  document.querySelector('#clarification-form').reset();
  document.querySelector('#plan-clarification').classList.add('hidden');
  document.querySelector('#learner-id').value = authenticatedLearnerId || preset.learnerId;
  document.querySelector('#user-request').value = preset.request;
  document.querySelector('#long-term-plan').value = preset.longPlan;
  document.querySelector('#short-term-plan').value = preset.shortPlan;
  minutes.value = String(preset.minutes);
  minutesOutput.textContent = `${preset.minutes} 分钟`;
  document.querySelector('#learning-packet').value = JSON.stringify(preset.packet(), null, 2);
  document.querySelector('#learning-packet').removeAttribute('aria-invalid');
  document.querySelector('#packet-error').textContent = '';
  document.querySelectorAll('.preset-button').forEach(item => {
    const selected = item.dataset.preset === name;
    item.classList.toggle('active', selected);
    item.setAttribute('aria-pressed', String(selected));
  });
  document.querySelector('#preset-status').textContent = `已填入：${preset.label}`;
  resetGraphMonitor();
}

document.querySelectorAll('.preset-button').forEach(item => item.addEventListener('click', () => applyPreset(item.dataset.preset)));
applyPreset('review_due', { preserveRun: true });

window.addEventListener('competition:auth-ready', event => {
  authenticatedLearnerId = event.detail.user_id;
  const learnerId = document.querySelector('#learner-id');
  learnerId.value = authenticatedLearnerId;
  learnerId.readOnly = true;
  restorePendingRun();
});

async function loadEnvironment() {
  const badge = document.querySelector('#environment-status');
  try {
    const response = await fetch('/health');
    const health = await response.json();
    const isLive = health.mode === 'live';
    environmentMode = health.mode;
    executionEngine = health.execution_engine || 'legacy';
    badge.classList.toggle('live', isLive);
    const engineLabel = executionEngine === 'langgraph' ? 'LangGraph' : 'Legacy';
    badge.innerHTML = `<span></span>${engineLabel} · ${isLive ? 'Live 真实模型' : 'Stub 演示模式'} · ${health.chat_model}`;
    badge.title = `Embedding: ${health.embedding_model}; Knowledge: ${health.knowledge_source}`;
    button.querySelector('span').textContent = isLive ? '▶' : '◇';
    button.lastChild.textContent = isLive ? ' 运行真实多智能体流程' : ' 运行 Stub 流程';
    if (!currentGraph) {
      const graphEngine = document.querySelector('#graph-engine');
      graphEngine.textContent = `${engineLabel} · 待编译`;
    }
  } catch (_) {
    environmentMode = 'unavailable';
    badge.textContent = '运行环境读取失败';
  }
}
loadEnvironment().then(restorePendingRun);

function formatMinutes(value) {
  const total = Number(value);
  if (total < 60) return `${total} 分钟`;
  const hours = Math.floor(total / 60);
  const remainder = total % 60;
  return remainder ? `${hours} 小时 ${remainder} 分钟` : `${hours} 小时`;
}

minutes.addEventListener('input', () => { minutesOutput.textContent = formatMinutes(minutes.value); });

const meta = {
  planner_agent: ['Planner Agent', '读取路由 Skill，动态选择最小 Agent 集合', 'agent'],
  memory_agent: ['Memory Agent', '仅在超过上下文阈值后压缩长对话', 'agent'],
  knowledge_base_agent: ['Knowledge Base Agent', '解析检索意图、调用教材向量库并整理证据', 'agent'],
  knowledge_explanation_agent: ['Expert Agent · 知识讲解', '依据教材证据直接生成知识讲解，不创建学习规划', 'agent'],
  paper_blueprint_agent: ['Expert Agent · 蓝图', '先生成结构化试卷蓝图和分单元检索需求', 'agent'],
  paper_assembly_agent: ['Expert Agent · 组卷', '根据完整候选题池一次性组成整卷', 'agent'],
  diagnosis_agent: ['Diagnosis Agent', '诊断学情并生成自然语言长短期计划', 'agent'],
  learning_plan_service: ['Learning Plan Service', '注入系统 ID、版本和状态，落为正式计划', 'system'],
  review_scheduler: ['Review Scheduler', '基于掌握度、遗忘系数和到期状态确定性选择复习任务', 'system'],
  expert_agent: ['Expert Agent', '根据证据与正式任务生成学习资源', 'agent'],
  audit_agent: ['Audit Agent', '审核事实、证据与安全边界', 'audit'],
};

const taskTypeLabels = {
  personalized_review_card: '个性化复习资源',
  learning_plan: '学习规划',
  knowledge_explanation: '知识讲解',
  paper_generation: '模拟组卷',
};

function setMetric(id, value) {
  const element = document.querySelector(`#${id}`);
  if (element) element.textContent = String(value);
}

function addGraphEvent(text, tone = 'normal') {
  const item = document.createElement('li');
  item.className = tone;
  const time = document.createElement('time');
  time.textContent = graphStartedAt
    ? `${Math.max(0, Math.round((Date.now() - graphStartedAt) / 1000))}s`
    : '0s';
  const message = document.createElement('span');
  message.textContent = text;
  item.append(time, message);
  graphEventLog.prepend(item);
  while (graphEventLog.children.length > 4) graphEventLog.lastElementChild.remove();
}

function updateElapsedMetric() {
  const seconds = graphStartedAt
    ? Math.max(0, Math.round((Date.now() - graphStartedAt) / 1000))
    : 0;
  setMetric('metric-elapsed', `${seconds}s`);
}

function startGraphClock(reset = true) {
  if (graphClock) window.clearInterval(graphClock);
  if (reset || !graphStartedAt) graphStartedAt = Date.now();
  updateElapsedMetric();
  graphClock = window.setInterval(updateElapsedMetric, 1000);
}

function stopGraphClock() {
  if (graphClock) window.clearInterval(graphClock);
  graphClock = null;
  updateElapsedMetric();
}

function resetGraphMonitor() {
  currentGraph = null;
  graphRetryCount = 0;
  graphRevisionCount = 0;
  graphNodeElements.clear();
  graphEventLog.replaceChildren();
  graphCanvas.className = 'graph-canvas graph-empty';
  graphCanvas.setAttribute('aria-label', '等待生成执行图');
  graphCanvas.innerHTML = '<div class="graph-placeholder"><span>◇</span><p>Planner 完成路由后，LangGraph 会在这里编译本次 DAG</p></div>';
  document.querySelector('#graph-engine').textContent = executionEngine === 'langgraph'
    ? 'LangGraph · 待编译'
    : 'Legacy · 待编排';
  document.querySelector('#graph-engine').className = 'engine-badge';
  document.querySelector('#graph-description').textContent = '运行后展示 Planner 实际选择的节点与依赖，不使用固定流程模板。';
  setMetric('metric-nodes', '—');
  setMetric('metric-edges', '—');
  setMetric('metric-parallel', '—');
  setMetric('metric-retries', 0);
  setMetric('metric-revisions', 0);
  setMetric('metric-elapsed', '0s');
}

function drawGraphEdges() {
  if (!currentGraph) return;
  const surface = graphCanvas.querySelector('.graph-surface');
  const svg = graphCanvas.querySelector('.graph-edges');
  if (!surface || !svg) return;
  const surfaceRect = surface.getBoundingClientRect();
  const width = Math.max(surface.scrollWidth, surfaceRect.width);
  const height = Math.max(surface.scrollHeight, surfaceRect.height);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('width', String(width));
  svg.setAttribute('height', String(height));
  svg.replaceChildren();
  const namespace = 'http://www.w3.org/2000/svg';
  const definitions = document.createElementNS(namespace, 'defs');
  const marker = document.createElementNS(namespace, 'marker');
  marker.setAttribute('id', 'graph-arrow');
  marker.setAttribute('viewBox', '0 0 10 10');
  marker.setAttribute('refX', '8');
  marker.setAttribute('refY', '5');
  marker.setAttribute('markerWidth', '5');
  marker.setAttribute('markerHeight', '5');
  marker.setAttribute('orient', 'auto-start-reverse');
  const arrow = document.createElementNS(namespace, 'path');
  arrow.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
  marker.appendChild(arrow);
  definitions.appendChild(marker);
  svg.appendChild(definitions);
  const allEdges = [
    ...(currentGraph.edges || []),
    ...(currentGraph.control_edges || []),
  ];
  allEdges.forEach(edge => {
    const source = graphNodeElements.get(edge.source);
    const target = graphNodeElements.get(edge.target);
    if (!source || !target) return;
    const sourceRect = source.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const path = document.createElementNS(namespace, 'path');
    path.classList.add('graph-edge');
    if (edge.kind === 'revision') path.classList.add('revision');
    if (edge.kind === 'revision') {
      const startX = sourceRect.left - surfaceRect.left + sourceRect.width / 2;
      const startY = sourceRect.bottom - surfaceRect.top;
      const endX = targetRect.left - surfaceRect.left + targetRect.width / 2;
      const endY = targetRect.bottom - surfaceRect.top;
      const loopY = Math.max(startY, endY) + 34;
      path.setAttribute('d', `M ${startX} ${startY} C ${startX} ${loopY}, ${endX} ${loopY}, ${endX} ${endY}`);
    } else {
      const startX = sourceRect.right - surfaceRect.left;
      const startY = sourceRect.top - surfaceRect.top + sourceRect.height / 2;
      const endX = targetRect.left - surfaceRect.left;
      const endY = targetRect.top - surfaceRect.top + targetRect.height / 2;
      const control = Math.max(24, (endX - startX) * .48);
      path.setAttribute('d', `M ${startX} ${startY} C ${startX + control} ${startY}, ${endX - control} ${endY}, ${endX} ${endY}`);
      path.setAttribute('marker-end', 'url(#graph-arrow)');
    }
    svg.appendChild(path);
  });
}

function renderExecutionGraph(event) {
  currentGraph = event;
  if (pendingThreadId) persistRun({ thread_id: pendingThreadId, graph: event });
  graphNodeElements.clear();
  graphCanvas.replaceChildren();
  graphCanvas.classList.remove('graph-empty');
  const surface = document.createElement('div');
  surface.className = 'graph-surface';
  surface.style.setProperty('--graph-level-count', String(event.levels?.length || 1));
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.classList.add('graph-edges');
  svg.setAttribute('aria-hidden', 'true');
  const levels = document.createElement('div');
  levels.className = 'graph-levels';
  const nodeData = new Map((event.nodes || []).map(node => [node.step_id, node]));
  (event.levels || []).forEach((level, levelIndex) => {
    const column = document.createElement('section');
    column.className = `graph-level${level.length > 1 ? ' parallel' : ''}`;
    const label = document.createElement('small');
    label.textContent = level.length > 1 ? `并行层 ${levelIndex}` : `阶段 ${levelIndex}`;
    column.appendChild(label);
    level.forEach(stepId => {
      const node = nodeData.get(stepId) || { step_id: stepId, agent: stepId };
      const [name, description, type] = meta[node.agent] || [node.agent, '框架节点', 'system'];
      const card = document.createElement('article');
      card.className = `graph-node ${type} ${stepId === 'planner' ? 'completed' : 'pending'}`;
      card.dataset.stepId = stepId;
      const state = document.createElement('i');
      state.className = 'graph-node-state';
      const heading = document.createElement('h5');
      heading.textContent = name;
      const id = document.createElement('code');
      id.textContent = stepId;
      const detail = document.createElement('p');
      detail.textContent = stepId === 'planner'
        ? '按本次诉求选择最小节点集合'
        : node.action || description;
      const retry = document.createElement('span');
      retry.textContent = node.max_retries ? `失败可重试 ${node.max_retries} 次` : '路由入口';
      card.append(state, heading, id, detail, retry);
      column.appendChild(card);
      graphNodeElements.set(stepId, card);
    });
    levels.appendChild(column);
  });
  surface.append(svg, levels);
  graphCanvas.appendChild(surface);
  const parallelWidth = Math.max(1, ...(event.levels || []).map(level => level.length));
  const edgeCount = (event.edges || []).length;
  setMetric('metric-nodes', (event.nodes || []).length);
  setMetric('metric-edges', edgeCount);
  setMetric('metric-parallel', `${parallelWidth}×`);
  setMetric('metric-retries', graphRetryCount);
  setMetric('metric-revisions', graphRevisionCount);
  const engine = document.querySelector('#graph-engine');
  engine.textContent = event.engine === 'langgraph' ? 'LangGraph · 已编译' : 'Legacy · 已编排';
  engine.className = `engine-badge ${event.engine === 'langgraph' ? 'active' : 'legacy'}`;
  const features = [
    '动态路由',
    event.capabilities?.parallel_execution ? `${parallelWidth} 路并行` : '依赖调度',
    event.capabilities?.controlled_revision ? '受控返修' : null,
    `${event.capabilities?.retryable_nodes || 0} 个可重试节点`,
  ].filter(Boolean);
  document.querySelector('#graph-description').textContent = `${taskTypeLabels[event.task_type] || event.task_type} · ${features.join(' · ')}`;
  graphCanvas.setAttribute('aria-label', `${event.engine} 执行图，共 ${(event.nodes || []).length} 个节点、${edgeCount} 条依赖边`);
  addGraphEvent(`Planner 选择了 ${(event.nodes || []).length - 1} 个执行节点，图已编译`, 'success');
  window.requestAnimationFrame(drawGraphEdges);
}

function markGraphNode(stepId, state, message = '') {
  const node = graphNodeElements.get(stepId);
  if (!node) return;
  node.classList.remove('pending', 'running', 'completed', 'retrying', 'revision', 'interrupted', 'failed');
  node.classList.add(state);
  if (message) node.querySelector('span').textContent = message;
}

window.addEventListener('resize', () => window.requestAnimationFrame(drawGraphEdges));

function setRunState(state, text) {
  runState.className = `run-state ${state}`;
  runState.textContent = text;
  document.querySelector('#execution-stage').setAttribute('aria-busy', String(state === 'running'));
}

function createJsonTree(value, label = null, depth = 0) {
  const isObject = value !== null && typeof value === 'object';
  if (!isObject) {
    const row = document.createElement('div');
    row.className = 'json-row';
    if (label !== null) {
      const key = document.createElement('span');
      key.className = 'json-key';
      key.textContent = `${label}: `;
      row.appendChild(key);
    }
    const scalar = document.createElement('span');
    const type = value === null ? 'null' : typeof value;
    scalar.className = `json-value json-${type}`;
    scalar.textContent = typeof value === 'string' ? `“${value}”` : String(value);
    row.appendChild(scalar);
    return row;
  }

  const entries = Array.isArray(value) ? value.map((item, index) => [index, item]) : Object.entries(value);
  const details = document.createElement('details');
  details.className = 'json-node';
  details.open = depth < 2;
  const summary = document.createElement('summary');
  const key = document.createElement('span');
  key.className = 'json-key';
  key.textContent = label === null ? 'JSON' : String(label);
  const badge = document.createElement('span');
  badge.className = 'json-count';
  badge.textContent = `${Array.isArray(value) ? 'Array' : 'Object'} · ${entries.length}`;
  summary.append(key, badge);
  details.appendChild(summary);
  const children = document.createElement('div');
  children.className = 'json-children';
  entries.forEach(([childKey, childValue]) => children.appendChild(createJsonTree(childValue, childKey, depth + 1)));
  details.appendChild(children);
  return details;
}

function renderJson(container, value) {
  container.replaceChildren(createJsonTree(value));
}

function renderKnowledgeRetrieval(event) {
  const step = ensureLiveStep('knowledge_base_agent');
  let panel = step.querySelector('.retrieval-panel');
  if (!panel) {
    panel = document.createElement('section');
    panel.className = 'retrieval-panel';
    const title = document.createElement('h5');
    title.textContent = '实际检索内容';
    panel.appendChild(title);
    step.querySelector('div:nth-child(2)').appendChild(panel);
  }
  panel.replaceChildren();
  const title = document.createElement('h5');
  title.textContent = '实际检索内容';
  const queries = document.createElement('p');
  queries.className = 'retrieval-queries';
  queries.textContent = `知识点查询：${event.kp_query} ｜ 题目查询：${event.question_query}`;
  panel.append(title, queries);

  const evidenceTitle = document.createElement('h6');
  const evidenceItems = event.evidence_items || [];
  evidenceTitle.textContent = `参考内容与外部资源 · ${evidenceItems.length} 条`;
  panel.appendChild(evidenceTitle);
  (event.evidence_items || []).forEach((item, index) => {
    const article = document.createElement('article');
    article.className = 'retrieval-item evidence-item';
    const header = document.createElement('small');
    const typeLabel = { video: '视频', reference: '参考内容', question: '外部题目' }[item.resource_type] || '教材';
    header.textContent = `${String(index + 1).padStart(2, '0')} · ${item.source_id} · ${typeLabel} · ${item.authority} · 相关度 ${Number(item.confidence || 0).toFixed(2)}`;
    const content = document.createElement('p');
    content.textContent = item.content;
    article.append(header, content);
    if (item.source_url) {
      const link = document.createElement('a');
      link.href = item.source_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = item.resource_type === 'video' ? '打开视频资源 ↗' : item.resource_type === 'question' ? '打开题目来源 ↗' : '打开参考来源 ↗';
      article.appendChild(link);
    }
    panel.appendChild(article);
  });

  const questionTitle = document.createElement('h6');
  questionTitle.textContent = `题目候选 · ${event.question_candidates?.length || 0} 条`;
  panel.appendChild(questionTitle);
  if (!event.question_candidates?.length) {
    const empty = document.createElement('p');
    empty.className = 'retrieval-empty';
    empty.textContent = '本次已执行题目检索，但未返回可用候选题。';
    panel.appendChild(empty);
  }
  (event.question_candidates || []).forEach((item, index) => {
    const article = document.createElement('article');
    article.className = 'retrieval-item question-item';
    const header = document.createElement('small');
    header.textContent = `${String(index + 1).padStart(2, '0')} · ${item.question_type} · ${(item.tags || []).join('、') || '未标注'}`;
    const content = document.createElement('p');
    content.textContent = item.stem;
    article.append(header, content);
    panel.appendChild(article);
  });
}

function renderPaperUnitRetrieval(event) {
  const step = ensureLiveStep('knowledge_base_agent');
  let panel = step.querySelector('.retrieval-panel');
  if (!panel) {
    panel = document.createElement('section');
    panel.className = 'retrieval-panel';
    step.querySelector('div:nth-child(2)').appendChild(panel);
  }
  if (!panel.querySelector('h5')) {
    const title = document.createElement('h5');
    title.textContent = '按蓝图单元检索题目';
    panel.appendChild(title);
  }
  const article = document.createElement('article');
  article.className = 'retrieval-item question-item';
  const header = document.createElement('small');
  header.textContent = `${event.unit_id} · ${event.knowledge_module}`;
  const content = document.createElement('p');
  const channels = Object.entries(event.channel_counts || {})
    .map(([channel, count]) => `${channel === 'bridge' ? '题库知识点关联' : channel.toUpperCase()} ${count}`)
    .join(' · ') || '无正式题库通道命中';
  const preferences = (event.question_type_preferences || []).join('、') || '不限题型';
  content.textContent = `查询：${event.query}｜题型：${preferences}｜过滤前 ${event.raw_candidate_count ?? event.candidate_count} 题｜可用 ${event.candidate_count} 题｜过滤 ${event.filtered_out_count || 0} 题｜通道：${channels}｜难度过滤：未启用${event.fallback_applied ? '｜已保留正式题库近似题型' : ''}`;
  article.append(header, content);
  const details = document.createElement('div');
  details.className = 'retrieval-candidate-details';
  (event.candidate_details || []).slice(0, 12).forEach((candidate, index) => {
    const row = document.createElement('p');
    const channels = (candidate.channels || []).map(channel => {
      const score = candidate.channel_scores?.[channel];
      return `${channel}${score == null ? '' : ` ${Number(score).toFixed(2)}`}`;
    }).join(' · ') || '无通道';
    row.textContent = `${String(index + 1).padStart(2, '0')} · ${candidate.question_id} · ${candidate.question_type} · ${channels} · 融合 ${Number(candidate.fusion_score || 0).toFixed(2)} · ${candidate.stem}`;
    details.appendChild(row);
  });
  if ((event.candidate_details || []).length) article.appendChild(details);
  const externalReferences = event.external_question_references || [];
  const externalTitle = document.createElement('strong');
  externalTitle.textContent = `网络题目线索 · ${externalReferences.length} 条（不可直接入卷）`;
  article.appendChild(externalTitle);
  if (externalReferences.length) {
    externalReferences.forEach(reference => {
      const clue = document.createElement('p');
      clue.className = 'retrieval-empty';
      clue.textContent = reference.content;
      article.appendChild(clue);
      if (reference.source_url) {
        const link = document.createElement('a');
        link.href = reference.source_url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = '打开网络题目来源 ↗';
        article.appendChild(link);
      }
    });
  } else {
    const emptyExternal = document.createElement('p');
    emptyExternal.className = 'retrieval-empty';
    emptyExternal.textContent = '已执行网络题目搜索，本次未返回可用线索；正式题库的题库知识点关联、BM25 与 FAISS 向量检索结果仍可独立用于组卷。';
    article.appendChild(emptyExternal);
  }
  panel.appendChild(article);
}

function summarize(producer, payload) {
  if (producer === 'planner_agent') return `工作流：${payload.task_type} · 风险：${payload.risk_level}`;
  if (producer === 'memory_agent') return payload.summary || '上下文已准备';
  if (producer === 'knowledge_base_agent' && payload.units) return `候选池：${payload.units.length} 个蓝图单元 · 未启用难度过滤`;
  if (producer === 'knowledge_base_agent') return `知识点：${(payload.resolved_kp_ids || []).join('、')} · 证据 ${payload.evidence_items?.length || 0} 条`;
  if (producer === 'paper_blueprint_agent' && payload.blueprint_id) return `试卷蓝图：${payload.title} · ${payload.units.length} 个检索单元`;
  if (producer === 'paper_assembly_agent' && payload.paper_draft_id) return `完整试卷：${payload.title} · ${payload.items.length} 题`;
  if (producer === 'diagnosis_agent') return `${payload.summary} · 阶段 ${payload.stage_id}`;
  if (producer === 'learning_plan_service') {
    if (payload.requires_clarification) return '等待用户补充重规划信息';
    const parts = [];
    if (payload.long_term_plan?.version) parts.push(`长期规划 v${payload.long_term_plan.version}`);
    if (payload.short_term_plan?.version) parts.push(`短期计划 v${payload.short_term_plan.version}`);
    if (payload.learning_task?.status) parts.push(`当日任务 ${payload.learning_task.status}`);
    return parts.join(' · ') || '规划层已落地';
  }
  if (producer === 'review_scheduler') return payload.selected_task
    ? `选中 ${payload.selected_task.primary_kp_id} · 优先级 ${Number(payload.selected_task.priority_score).toFixed(2)} · 候选 ${payload.candidates.length} 个`
    : `候选 ${payload.candidates.length} 个 · 暂无到期任务`;
  if (producer === 'expert_agent') return `资源草稿：${payload.title}`;
  if (producer === 'audit_agent') return `审核结论：${payload.decision}`;
  return '步骤已完成';
}

function detailPayload(producer, payload) {
  if (producer === 'diagnosis_agent') return payload.learning_plan_proposal;
  if (producer === 'learning_plan_service') {
    if (payload.requires_clarification) return {
      requires_clarification: true,
      clarification_questions: payload.clarification_questions,
      reason: payload.reason,
    };
    return {
      generated_scope: payload.generated_scope,
      long_term_plan_id: payload.long_term_plan?.plan_id,
      short_term_plan_id: payload.short_term_plan?.plan_id,
      task_id: payload.learning_task?.task_id,
      version: payload.long_term_plan?.version || payload.short_term_plan?.version,
      invalidated_layers: payload.invalidated_layers,
    };
  }
  if (producer === 'knowledge_base_agent') return {
    resolved_kp_ids: payload.resolved_kp_ids,
    evidence: payload.evidence_items?.map(item => item.content_summary),
  };
  if (producer === 'review_scheduler') return {
    formula_version: payload.formula_policy?.formula_version,
    selected_task: payload.selected_task,
    candidates: payload.candidates?.map(item => ({
      kp_id: item.kp_id,
      retention_estimate: item.retention_estimate,
      is_due: item.is_due,
      priority_score: item.priority_score,
      reason_codes: item.reason_codes,
    })),
  };
  return null;
}

function renderStep(output, index) {
  const [name, description, type] = meta[output.producer] || [output.producer, '框架步骤', 'system'];
  const detail = detailPayload(output.producer, output.payload);
  const article = document.createElement('article');
  article.className = `step ${type}`;
  article.style.animationDelay = `${index * 90}ms`;
  article.innerHTML = `
    <div class="step-index">${String(index + 1).padStart(2, '0')}</div>
    <div><h4>${name}</h4><p>${description}</p><p><strong>${summarize(output.producer, output.payload)}</strong></p>${detail ? `<pre>${JSON.stringify(detail, null, 2)}</pre>` : ''}</div>
    <span class="step-badge">完成</span>`;
  pipeline.appendChild(article);
}

function renderModelTrace(calls) {
  modelIoList.innerHTML = '';
  document.querySelector('#model-io-count').textContent = `${calls.length} 次模型调用`;
  modelIoSection.classList.remove('hidden');
  calls.forEach((call, index) => {
    const [name,, type] = meta[call.agent] || [call.agent, '', 'agent'];
    const item = document.createElement('details');
    item.className = `io-call ${type}`;
    item.style.animationDelay = `${index * 140}ms`;
    item.open = index === 0;
    const output = call.raw_output || { error_type: call.error_type || '模型未返回输出' };
    item.innerHTML = `
      <summary><span class="io-sequence">${String(call.sequence).padStart(2, '0')}</span><strong>${name}</strong><em>${call.error_type ? '调用失败' : 'JSON 已返回'}</em><span class="summary-hint">展开原始 I/O</span></summary>
      <div class="io-panes">
        <div><label>RAW INPUT <span>脱敏后</span></label><pre>${JSON.stringify(call.raw_input, null, 2)}</pre></div>
        <div><label>RAW OUTPUT <span>模型返回</span></label><pre>${JSON.stringify(output, null, 2)}</pre></div>
      </div>`;
    modelIoList.appendChild(item);
  });
}

function appendResourceValue(container, label, value) {
  if (value === null || value === undefined || value === '') return;
  const section = document.createElement('section');
  section.className = 'resource-section';
  if (label) {
    const heading = document.createElement('h4');
    heading.textContent = label;
    section.appendChild(heading);
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => appendResourceValue(section, `${index + 1}`, item));
  } else if (typeof value === 'object') {
    Object.entries(value).forEach(([key, item]) => appendResourceValue(section, key, item));
  } else {
    const paragraph = document.createElement('p');
    paragraph.textContent = String(value);
    section.appendChild(paragraph);
  }
  if (section.childElementCount > (label ? 1 : 0)) container.appendChild(section);
}

function renderResourceResult(body) {
  const card = document.querySelector('#resource-card');
  const content = document.querySelector('#resource-content');
  if (!card || !content) return;
  const resource = body?.resource;
  card.hidden = !resource;
  content.replaceChildren();
  if (!resource) return;
  document.querySelector('#resource-title').textContent = resource.title || '学习产物';
  const resourceContent = resource.content || {};
  if (body.task_type === 'knowledge_explanation') {
    appendResourceValue(content, '', resourceContent['知识讲解'] || resourceContent);
    appendResourceValue(content, '配套练习', resourceContent['配套练习']);
    appendResourceValue(content, '待确认项', resourceContent['待确认项']);
  } else if (body.task_type === 'paper_generation') {
    appendResourceValue(content, '试卷说明', resourceContent['试卷说明']);
    appendResourceValue(content, '试卷正文', resourceContent['试卷正文']);
    appendResourceValue(content, '参考答案', resourceContent['参考答案']);
    appendResourceValue(content, '答案解析', resourceContent['答案解析']);
    appendResourceValue(content, '待确认项', resourceContent['待确认项']);
  } else {
    const cardBody = resourceContent['知识卡片'];
    appendResourceValue(content, '知识卡片', cardBody?.exp || cardBody);
    appendResourceValue(content, '学习提示', resourceContent['学习提示']);
    appendResourceValue(content, '练习资源', resourceContent['练习资源']);
    appendResourceValue(content, '视频资源', resourceContent['视频资源']);
    appendResourceValue(content, '参考资料', resourceContent['参考资料']);
  }
}

function renderPlanClarification(planOutput) {
  const panel = document.querySelector('#plan-clarification');
  document.querySelector('#clarification-mode').textContent = 'PLAN CLARIFICATION';
  document.querySelector('#clarification-title').textContent = '需要补充重规划信息';
  document.querySelector('#clarification-checkpoint').classList.add('hidden');
  document.querySelector('#clarification-submit').textContent = '提交补充信息并重新规划';
  document.querySelector('#clarification-reason').textContent = planOutput.reason || '需要补充信息后才能安全调整现有规划。';
  const questions = document.querySelector('#clarification-questions');
  questions.replaceChildren();
  (planOutput.clarification_questions || []).forEach(question => {
    const item = document.createElement('li');
    item.textContent = question;
    questions.appendChild(item);
  });
  const requestedScope = planOutput.requested_scope;
  const inferredScope = inferPlanScope(lastSubmittedRequest);
  document.querySelector('#clarification-target').value = [
    'long_term', 'short_term', 'daily_task',
  ].includes(requestedScope)
    ? requestedScope
    : ['long_term', 'short_term', 'daily_task'].includes(inferredScope)
      ? inferredScope
      : 'long_term';
  panel.classList.remove('hidden');
}

function renderWorkflowInterrupt(result) {
  const interruptData = result?.interrupt || result || {};
  pendingThreadId = result?.thread_id || pendingThreadId;
  pendingInterrupt = interruptData;
  const panel = document.querySelector('#plan-clarification');
  document.querySelector('#clarification-mode').textContent = 'LANGGRAPH INTERRUPT';
  document.querySelector('#clarification-title').textContent = '流程已暂停，等待你的回答';
  document.querySelector('#clarification-reason').textContent = interruptData.reason || '补充信息后将从当前检查点继续，不会重跑已经完成的节点。';
  document.querySelector('#clarification-checkpoint').classList.remove('hidden');
  document.querySelector('#clarification-submit').textContent = '回答并从中断节点继续';
  const questions = document.querySelector('#clarification-questions');
  questions.replaceChildren();
  (interruptData.questions || interruptData.clarification_questions || []).forEach(question => {
    const item = document.createElement('li');
    item.textContent = question;
    questions.appendChild(item);
  });
  const requestedScope = interruptData.requested_scope;
  if (['long_term', 'short_term', 'daily_task'].includes(requestedScope)) {
    document.querySelector('#clarification-target').value = requestedScope;
  }
  (result?.completed_steps || []).forEach(stepId => {
    markGraphNode(stepId, 'completed', '检查点中已完成');
  });
  markGraphNode(interruptData.step_id, 'interrupted', '等待用户回答');
  const engine = document.querySelector('#graph-engine');
  engine.textContent = 'LangGraph · 已中断';
  engine.className = 'engine-badge interrupted';
  addGraphEvent(`${interruptData.step_id || '当前节点'} 已建立检查点，等待用户输入`, 'revision');
  panel.classList.remove('hidden');
  resultSection.classList.remove('hidden');
  resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
  stopGraphClock();
  persistRun({
    status: 'interrupted',
    thread_id: pendingThreadId,
    interrupt: interruptData,
    completed_steps: result?.completed_steps || [],
    graph: currentGraph,
    request: lastSubmittedRequest,
  });
}

function scheduleRunPoll() {
  if (runPollTimer) window.clearTimeout(runPollTimer);
  runPollTimer = window.setTimeout(restorePendingRun, 2500);
}

async function restorePendingRun() {
  const stored = readStoredRun();
  if (!stored?.thread_id) return false;
  pendingThreadId = stored.thread_id;
  lastSubmittedRequest = stored.request || lastSubmittedRequest;
  if (stored.request) document.querySelector('#user-request').value = stored.request;
  if (stored.graph && !currentGraph) renderExecutionGraph(stored.graph);
  try {
    const response = await fetch(`/api/v1/review-cards/runs/${encodeURIComponent(pendingThreadId)}`);
    if (response.status === 404) {
      clearStoredRun();
      setRunState('error', '此前的内存检查点已随服务重启失效');
      return false;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const state = await response.json();
    if (state.status === 'interrupted') {
      renderWorkflowInterrupt({ ...state, thread_id: pendingThreadId });
      setRunState('waiting', 'LangGraph 已中断 · 等待回答');
    } else if (state.status === 'completed' && state.result) {
      renderResults(state.result);
      setRunState('done', '后台流程已完成');
      addGraphEvent('断线后重新连接，并取回成功终态', 'success');
      clearStoredRun();
    } else if (state.status === 'failed') {
      setRunState('error', state.message || '后台流程失败');
      clearStoredRun();
    } else {
      setRunState('running', '连接已恢复 · 服务端流程仍在执行');
      scheduleRunPoll();
    }
    return true;
  } catch (_) {
    setRunState('waiting', '暂时无法连接服务，检查点仍保留在本机');
    scheduleRunPoll();
    return true;
  }
}

function displayPlanLayer(selector, value) {
  const card = document.querySelector(selector);
  if (card) card.style.display = value ? '' : 'none';
}

function renderResults(body) {
  document.querySelector('.processed-output').style.display = '';
  const agentOutputs = Array.isArray(body?.agent_outputs) ? body.agent_outputs : [];
  const planOutput = body?.learning_plan || agentOutputs.find(item => item.producer === 'learning_plan_service')?.payload;
  renderProcessedData(body, agentOutputs);
  const planCards = document.querySelectorAll('.long-plan, .short-plan, .task-card');
  const needsClarification = Boolean(planOutput?.requires_clarification);
  planCards.forEach(card => { card.style.display = 'none'; });
  if (planOutput && !needsClarification) {
    displayPlanLayer('.long-plan', planOutput.long_term_plan);
    displayPlanLayer('.short-plan', planOutput.short_term_plan);
    displayPlanLayer('.task-card', planOutput.learning_task);
  }
  renderResourceResult(body);
  renderResultActions(body);
  const clarificationPanel = document.querySelector('#plan-clarification');
  if (needsClarification) {
    renderPlanClarification(planOutput);
  } else {
    clarificationPanel.classList.add('hidden');
  }
  if (planOutput && !needsClarification) {
    const focusHeader = document.querySelector('#short-plan-focus');
    if (planOutput.long_term_plan) {
      currentLongTermPlan = planOutput.long_term_plan;
      document.querySelector('#long-plan').textContent = planOutput.long_term_plan.content;
      document.querySelector('#long-term-plan').value = planOutput.long_term_plan.content;
    }
    if (planOutput.short_term_plan) {
      currentShortTermPlan = planOutput.short_term_plan;
      document.querySelector('#short-plan').textContent = planOutput.short_term_plan.content;
      document.querySelector('#short-term-plan').value = planOutput.short_term_plan.content;
      const focus = planOutput.short_term_plan.short_term_focus;
      const textbookSelection = planOutput.short_term_plan.textbook_selection;
      if (textbookSelection?.stage_name && Array.isArray(textbookSelection.books)) {
        focusHeader.textContent = [
          '当前教材',
          textbookSelection.stage_name,
          textbookSelection.books.join('、'),
          textbookSelection.reason || '',
        ].filter(Boolean).join(' · ');
        focusHeader.hidden = false;
      } else if (focus?.focus_label) {
        const focusTypeLabels = {
          special_topic: '专项',
          knowledge_cluster: '知识点组',
          knowledge_point: '知识点',
          remediation: '定向补弱',
          due_review: '到期复习',
        };
        const kpCount = Array.isArray(focus.knowledge_point_ids)
          ? focus.knowledge_point_ids.length
          : 0;
        focusHeader.textContent = [
          focusTypeLabels[focus.focus_type] || '当前重点',
          focus.focus_label,
          kpCount ? `关联 ${kpCount} 个知识点` : '',
        ].filter(Boolean).join(' · ');
        focusHeader.hidden = false;
      } else {
        focusHeader.hidden = true;
        focusHeader.textContent = '';
      }
    }
    if (planOutput.learning_task) {
      currentLearningTask = planOutput.learning_task;
      document.querySelector('#task-content').textContent = planOutput.learning_task.task_content;
      document.querySelector('#task-minutes').textContent = formatMinutes(planOutput.learning_task.estimated_minutes);
      document.querySelector('#task-criteria').textContent = planOutput.learning_task.completion_criteria;
    }
    if ((planOutput.invalidated_layers || []).includes('short_term')) {
      currentShortTermPlan = null;
      document.querySelector('#short-term-plan').value = '';
    }
    if ((planOutput.invalidated_layers || []).includes('daily_task')) {
      currentLearningTask = null;
    }
    pendingPlanChangeContext = null;
    pendingPlanScope = null;
  }
  const auditBadge = document.querySelector('#audit-badge');
  auditBadge.className = 'audit-badge';
  if (needsClarification) {
    auditBadge.textContent = '等待补充信息';
    auditBadge.classList.add('review');
  } else if (body?.resource && body?.audit) {
    auditBadge.textContent = body.audit.decision === 'pass'
      ? (body.task_type === 'paper_generation'
          ? '✓ 试卷审核通过'
          : body.task_type === 'knowledge_explanation'
            ? '✓ 讲解审核通过'
            : '✓ 审核通过')
      : `审核：${body.audit.decision}`;
    auditBadge.classList.add(
      body.audit.decision === 'pass'
        ? 'pass'
        : body.audit.decision === 'revise' || body.audit.decision === 'needs_human_review'
          ? 'review'
          : 'fail'
    );
  } else {
    auditBadge.textContent = '✓ 学习计划已落地';
    auditBadge.classList.add('pass');
  }
  resultSection.classList.remove('hidden');
  resultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
  loadReviewQueue(document.querySelector('#learner-id').value.trim());
}

function renderResultActions(body) {
  const container = document.querySelector('#result-actions');
  if (!container) return;
  container.replaceChildren();
  const actions = Array.isArray(body?.ui_actions) ? body.ui_actions : [];
  actions.forEach(action => {
    if (action?.action_type !== 'navigate' || !action?.destination) return;
    const actionButton = document.createElement('button');
    actionButton.type = 'button';
    actionButton.textContent = action.label || '继续';
    actionButton.addEventListener('click', () => {
      const params = action.params && typeof action.params === 'object' ? action.params : {};
      const navigation = {
        page: 'practice',
        params: { view: 'workspace', taskType: 'question_training' },
      };
      if (action.destination === 'workshop.paper' && params.paper_id) {
        sessionStorage.setItem('training-paper-id', String(params.paper_id));
        navigation.params.taskType = 'paper_workspace';
        navigation.params.paperId = String(params.paper_id);
      }
      if (action.destination === 'workshop.knowledge_card' && params.card_id) {
        sessionStorage.setItem('training-knowledge-card-id', String(params.card_id));
        navigation.params.taskType = 'knowledge_cards';
        navigation.params.cardId = String(params.card_id);
      }
      sessionStorage.setItem('competition.pending-navigation', JSON.stringify(navigation));
      window.location.assign('/');
    });
    container.appendChild(actionButton);
  });
}

function reviewOutcomeLabel(outcome) {
  return {
    independent_correct: '独立答对',
    hinted_correct: '提示后答对',
    wrong: '答错',
    skipped: '跳过',
  }[outcome] || outcome;
}

function formatReviewTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false });
}

function createReviewQueueEntry(entry) {
  const article = document.createElement('article');
  article.className = `review-queue-entry ${entry.is_due ? 'due' : 'scheduled'}`;
  const unit = entry.memory_unit || {};
  const header = document.createElement('header');
  const titleWrap = document.createElement('div');
  const title = document.createElement('h4');
  title.textContent = unit.prompt_abstract || unit.kp_id || '复习知识点';
  const metaText = document.createElement('p');
  metaText.textContent = `${unit.kp_id || '未绑定知识点'} · 阶段 ${unit.review_stage ?? 0} · 掌握度 ${Number(unit.mastery_score || 0).toFixed(1)}`;
  titleWrap.append(title, metaText);
  const badge = document.createElement('span');
  badge.textContent = entry.is_due ? '已到期' : '待调度';
  header.append(titleWrap, badge);

  const metrics = document.createElement('dl');
  const metricValues = [
    ['保持率', `${Math.round(Number(entry.retention_estimate || 0) * 100)}%`],
    ['下次复习', formatReviewTime(unit.next_review_at)],
    ['资源状态', entry.resource ? '已推送' : entry.task ? '生成中' : '待生成'],
  ];
  metricValues.forEach(([label, value]) => {
    const group = document.createElement('div');
    const term = document.createElement('dt');
    const description = document.createElement('dd');
    term.textContent = label;
    description.textContent = value;
    group.append(term, description);
    metrics.appendChild(group);
  });
  article.append(header, metrics);

  if (entry.resource) {
    const resource = document.createElement('div');
    resource.className = 'review-pushed-resource';
    const strong = document.createElement('strong');
    strong.textContent = entry.resource.title || '已审核复习资源';
    const text = document.createElement('span');
    text.textContent = '资源已自动绑定到本次复习任务';
    resource.append(strong, text);
    article.appendChild(resource);
  }
  if (entry.task) {
    const controls = document.createElement('div');
    controls.className = 'review-feedback-actions';
    ['independent_correct', 'hinted_correct', 'wrong', 'skipped'].forEach(outcome => {
      const feedbackButton = document.createElement('button');
      feedbackButton.type = 'button';
      feedbackButton.textContent = reviewOutcomeLabel(outcome);
      feedbackButton.dataset.outcome = outcome;
      feedbackButton.addEventListener('click', async () => {
        feedbackButton.disabled = true;
        try {
          const learnerId = document.querySelector('#learner-id').value.trim();
          const response = await fetch(`/api/v1/review-tasks/${encodeURIComponent(entry.task.review_task_id)}/attempts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              learner_id: learnerId,
              outcome,
              hint_used: outcome === 'hinted_correct',
              attempt_id: `RATT_UI_${window.crypto?.randomUUID?.().replaceAll('-', '') || Date.now()}`,
            }),
          });
          if (!response.ok) throw new Error(`反馈写回失败（HTTP ${response.status}）`);
          await loadReviewQueue(learnerId);
        } catch (error) {
          reviewQueueSummary.textContent = error.message;
          feedbackButton.disabled = false;
        }
      });
      controls.appendChild(feedbackButton);
    });
    article.appendChild(controls);
  }
  return article;
}

async function loadReviewQueue(learnerId) {
  if (!learnerId || !reviewQueueList || !reviewQueueSummary) return;
  reviewQueueSummary.textContent = '正在读取队列…';
  try {
    const response = await fetch(`/api/v1/learners/${encodeURIComponent(learnerId)}/review-queue`);
    if (!response.ok) throw new Error(`队列读取失败（HTTP ${response.status}）`);
    const queue = await response.json();
    reviewQueueList.replaceChildren();
    (queue.entries || []).forEach(entry => reviewQueueList.appendChild(createReviewQueueEntry(entry)));
    if (!queue.entries?.length) {
      const empty = document.createElement('p');
      empty.className = 'review-queue-empty';
      empty.textContent = '当前没有可调度的复习知识点。';
      reviewQueueList.appendChild(empty);
    }
    reviewQueueSummary.textContent = `到期 ${queue.due_count} · 活跃任务 ${queue.active_task_count} · 待资源 ${queue.awaiting_resource_count}`;
  } catch (error) {
    reviewQueueSummary.textContent = error.message;
  }
}

reviewDispatchButton?.addEventListener('click', async () => {
  const learnerId = document.querySelector('#learner-id').value.trim();
  if (!learnerId) return;
  reviewDispatchButton.disabled = true;
  reviewDispatchButton.textContent = '正在生成并审核资源…';
  try {
    const response = await fetch(`/api/v1/learners/${encodeURIComponent(learnerId)}/review-queue/dispatch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ available_minutes: Number(minutes.value) }),
    });
    if (!response.ok) throw new Error(`到期资源推送失败（HTTP ${response.status}）`);
    const result = await response.json();
    if (result.status !== 'empty') renderResults(result);
    await loadReviewQueue(learnerId);
  } catch (error) {
    reviewQueueSummary.textContent = error.message;
  } finally {
    reviewDispatchButton.disabled = false;
    reviewDispatchButton.textContent = '推送下一个到期资源';
  }
});

reviewQueueButton?.addEventListener('click', async () => {
  const learnerId = document.querySelector('#learner-id').value.trim();
  if (!learnerId) {
    form.reportValidity();
    return;
  }
  document.querySelectorAll('.long-plan, .short-plan, .task-card').forEach(card => {
    card.style.display = 'none';
  });
  document.querySelector('#resource-card').hidden = true;
  document.querySelector('#plan-clarification').classList.add('hidden');
  document.querySelector('.processed-output').style.display = 'none';
  document.querySelector('#audit-badge').textContent = '复习队列';
  resultSection.classList.remove('hidden');
  await loadReviewQueue(learnerId);
  document.querySelector('#review-queue-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
});

document.querySelector('#clarification-form').addEventListener('submit', event => {
  event.preventDefault();
  const details = document.querySelector('#clarification-change-details').value.trim();
  if (!details) {
    event.currentTarget.reportValidity();
    return;
  }
  const target = document.querySelector('#clarification-target').value;
  pendingPlanScope = target;
  pendingPlanChangeContext = {
    original_request: lastSubmittedRequest,
    target_layers: [target],
    change_details: details,
    available_time: document.querySelector('#clarification-available-time').value.trim() || null,
    keep_items: document.querySelector('#clarification-keep-items').value.trim() || null,
    drop_items: document.querySelector('#clarification-drop-items').value.trim() || null,
    expected_outcome: document.querySelector('#clarification-expected-outcome').value.trim() || null,
  };
  resumeRequested = Boolean(pendingThreadId && pendingInterrupt);
  form.requestSubmit();
});

function renderProcessedData(body, agentOutputs) {
  const tree = document.querySelector('#processed-output-tree');
  const count = document.querySelector('#processed-output-count');
  if (!tree || !count) return;
  const processed = {
    status: body?.status,
    execution_id: body?.execution_id,
    task_type: body?.task_type,
    agent_outputs: agentOutputs,
    learning_plan: body?.learning_plan,
    review_schedule: body?.review_schedule,
    review_task: body?.review_task,
    resource: body?.resource,
    resource_version: body?.resource_version,
    resource_binding: body?.resource_binding,
    audit: body?.audit,
    writeback_intents: body?.writeback_intents,
  };
  const visibleEntries = Object.entries(processed).filter(([, value]) => value !== null && value !== undefined);
  count.textContent = `${agentOutputs.length} 个步骤产物 · ${visibleEntries.length} 个结果字段`;
  tree.replaceChildren(createJsonTree(processed));
}

const liveSteps = new Map();
const liveModelCalls = new Map();

function ensureLiveStep(agent, stepId = agent) {
  const key = stepId || agent;
  if (liveSteps.has(key)) return liveSteps.get(key);
  const [name, description, type] = meta[agent] || [agent, '框架步骤', 'system'];
  const article = document.createElement('article');
  article.className = `step ${type} live-step running`;
  article.innerHTML = `
    <div class="step-index">${String(liveSteps.size + 1).padStart(2, '0')}</div>
    <div><h4>${name}</h4><p>${description}</p><div class="live-message">等待模型或系统处理…</div></div>
    <span class="step-badge">运行中</span>`;
  pipeline.appendChild(article);
  article.dataset.stepId = key;
  article.dataset.agent = agent;
  liveSteps.set(key, article);
  return article;
}

function startModelCall(event) {
  const displayAgent = event.step_id === 'paper_blueprint'
    ? 'paper_blueprint_agent'
    : event.step_id === 'paper_assembly'
      ? 'paper_assembly_agent'
      : event.agent;
  const callKey = event.call_id || `${displayAgent}:${liveModelCalls.size + 1}`;
  const step = ensureLiveStep(displayAgent, event.step_id || displayAgent);
  step.classList.add('running');
  const item = document.createElement('details');
  item.className = 'io-call agent live-io';
  item.open = true;
  const callMode = environmentMode === 'live' ? '真实模型正在生成' : 'Stub 正在生成模拟输出';
  item.innerHTML = `
    <summary><span class="io-sequence">${String(liveModelCalls.size + 1).padStart(2, '0')}</span><strong>${meta[displayAgent]?.[0] || displayAgent}</strong><em>${callMode}</em><span class="stream-cursor"></span></summary>
    <div class="io-transport-grid">
      <section class="transport-pane transport-request">
        <label>实际 API 输入 <span>发送给模型的 system / user messages</span></label>
        <div class="transport-placeholder">等待客户端构造真实请求…</div>
        <div class="transport-messages hidden"></div>
      </section>
      <section class="transport-pane transport-response">
        <label>模型原始输出 <span>解析 JSON 之前的完整文本</span></label>
        <pre class="reasoning-text hidden"></pre>
        <pre class="raw-stream"></pre>
        <pre class="raw-response-text hidden"></pre>
      </section>
    </div>
    <details class="parsed-boundary" open>
      <summary>查看框架上下文与解析后 JSON</summary>
      <div class="io-panes">
        <div><label>AGENT CONTEXT <span>框架内结构化输入</span></label><div class="json-view raw-input"></div></div>
        <div><label>PARSED JSON <span>Schema 校验前</span></label><div class="json-view raw-output"></div></div>
      </div>
    </details>`;
  renderJson(item.querySelector('.raw-input'), event.raw_input);
  modelIoList.appendChild(item);
  modelIoSection.classList.remove('hidden');
  liveModelCalls.set(callKey, item);
  document.querySelector('#model-io-count').textContent = `${liveModelCalls.size} 次模型调用`;
}

function handleStreamEvent(event) {
  if (event.event === 'run_started') {
    pendingThreadId = event.thread_id || pendingThreadId;
    persistRun({
      status: 'running',
      thread_id: pendingThreadId,
      request: event.user_request || lastSubmittedRequest,
    });
  } else if (event.event === 'run_resumed') {
    addGraphEvent('已使用 Command(resume) 恢复原执行图', 'success');
    document.querySelector('#graph-engine').textContent = 'LangGraph · 已恢复';
  } else if (event.event === 'graph_compiled') {
    renderExecutionGraph(event);
  } else if (event.event === 'step_started') {
    ensureLiveStep(event.agent, event.step_id || event.agent);
    markGraphNode(event.step_id || event.agent, 'running', '正在执行');
  } else if (event.event === 'model_input') {
    startModelCall(event);
  } else if (event.event === 'model_delta') {
    const item = liveModelCalls.get(event.call_id) || liveModelCalls.get(event.agent);
    if (item) {
      const output = item.querySelector('.raw-stream');
      output.textContent += event.delta;
      output.scrollTop = output.scrollHeight;
    }
  } else if (event.event === 'model_output') {
    const item = liveModelCalls.get(event.call_id) || liveModelCalls.get(event.agent);
    if (item) {
      const output = item.querySelector('.raw-output');
      renderJson(output, event.raw_output);
      item.querySelector('em').textContent = 'JSON 已返回';
      item.querySelector('.stream-cursor')?.remove();
    }
  } else if (event.event === 'model_transport') {
    const item = liveModelCalls.get(event.call_id) || liveModelCalls.get(event.agent);
    if (item) {
      const requestBody = event.request_payload?.body || event.request_payload;
      const messages = Array.isArray(requestBody?.messages) ? requestBody.messages : [];
      const messageContainer = item.querySelector('.transport-messages');
      item.querySelector('.transport-placeholder')?.classList.add('hidden');
      messageContainer.classList.remove('hidden');
      messageContainer.replaceChildren();
      if (messages.length) {
        messages.forEach(message => {
          const article = document.createElement('article');
          article.className = `transport-message role-${message.role || 'unknown'}`;
          const role = document.createElement('strong');
          role.textContent = String(message.role || 'message').toUpperCase();
          const content = document.createElement('pre');
          content.textContent = String(message.content || '');
          article.append(role, content);
          messageContainer.appendChild(article);
        });
      } else {
        renderJson(messageContainer, event.request_payload);
      }
      const rawStream = item.querySelector('.raw-stream');
      const rawText = item.querySelector('.raw-response-text');
      rawStream.classList.add('hidden');
      rawText.classList.remove('hidden');
      rawText.textContent = event.response_text || '';
      const reasoning = item.querySelector('.reasoning-text');
      if (event.reasoning_text) {
        reasoning.classList.remove('hidden');
        reasoning.textContent = `推理内容\n${event.reasoning_text}`;
      }
    }
  } else if (event.event === 'knowledge_retrieval') {
    renderKnowledgeRetrieval(event);
  } else if (event.event === 'paper_unit_retrieval') {
    renderPaperUnitRetrieval(event);
  } else if (event.event === 'system_output') {
    const step = ensureLiveStep(event.agent, event.step_id || event.agent);
    const envelope = event.output;
    const payload = envelope?.payload || envelope;
    step.querySelector('.live-message').textContent = summarize(event.agent, payload);
  } else if (event.event === 'step_completed') {
    const step = ensureLiveStep(event.agent, event.step_id || event.agent);
    step.classList.remove('running');
    step.classList.add('completed');
    step.querySelector('.step-badge').textContent = '完成';
    markGraphNode(event.step_id || event.agent, 'completed', '已完成');
  } else if (event.event === 'step_retrying') {
    const step = ensureLiveStep(event.agent, event.step_id || event.agent);
    step.querySelector('.live-message').textContent = `第${event.attempt}次失败，正在重试：${event.error_type || '未知错误'}${event.error_message ? ` · ${event.error_message}` : ''}`;
    graphRetryCount += 1;
    setMetric('metric-retries', graphRetryCount);
    markGraphNode(event.step_id || event.agent, 'retrying', `第 ${event.attempt} 次重试`);
    addGraphEvent(`${event.step_id} 失败，LangGraph 保留状态并重试`, 'warning');
  } else if (event.event === 'graph_interrupted') {
    pendingInterrupt = event;
    markGraphNode(event.step_id, 'interrupted', '等待用户回答');
    addGraphEvent(`${event.step_id} 触发 interrupt，状态已检查点保存`, 'revision');
  } else if (event.event === 'graph_resume_requested') {
    addGraphEvent('收到恢复请求，正在载入检查点', 'normal');
  } else if (event.event === 'graph_resumed') {
    markGraphNode(event.step_id, 'running', '从检查点继续');
    addGraphEvent(`${event.step_id} 已由 Command(resume) 恢复`, 'success');
  } else if (event.event === 'audit_revision_started') {
    const step = ensureLiveStep('audit_agent', event.audit_step_id || 'audit');
    step.querySelector('.live-message').textContent = '审核发现阻断问题，已触发一次受控返修。';
    graphRevisionCount += 1;
    setMetric('metric-revisions', graphRevisionCount);
    markGraphNode(event.audit_step_id || 'audit', 'revision', '正在返修');
    const target = currentGraph?.control_edges?.[0]?.target;
    if (target) markGraphNode(target, 'revision', '按审核意见重生成');
    addGraphEvent('Audit 只回退受影响的 Expert 节点', 'revision');
  } else if (event.event === 'audit_revision_completed') {
    const step = ensureLiveStep('audit_agent', event.audit_step_id || 'audit');
    step.querySelector('.live-message').textContent = event.status === 'pass' ? '返修后复审通过。' : '返修后仍需人工复核。';
    markGraphNode(event.audit_step_id || 'audit', event.status === 'pass' ? 'completed' : 'failed', event.status === 'pass' ? '返修复审通过' : '等待人工复核');
    const target = currentGraph?.control_edges?.[0]?.target;
    if (target) markGraphNode(target, 'completed', '返修完成');
    addGraphEvent(event.status === 'pass' ? '返修完成，复审通过' : '返修后转人工复核', event.status === 'pass' ? 'success' : 'warning');
  }
}

function markInterruptedCalls() {
  liveModelCalls.forEach(item => {
    if (!item.querySelector('.stream-cursor')) return;
    item.querySelector('.stream-cursor')?.remove();
    item.querySelector('em').textContent = '调用中断';
    item.classList.add('interrupted');
  });
}

async function consumeSse(response) {
  if (!response.ok || !response.body) throw new Error(`流式请求失败（HTTP ${response.status}）`);
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
      const line = frame.split('\n').find(item => item.startsWith('data: '));
      if (!line) continue;
      const event = JSON.parse(line.slice(6));
      handleStreamEvent(event);
      if (event.event === 'run_failed') throw new Error(event.message);
      if (event.event === 'run_interrupted') return event.result;
      if (event.event === 'run_completed') return event.result;
    }
  }
  throw new Error('流式连接结束但未收到最终结果');
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const isResume = Boolean(resumeRequested && pendingThreadId && pendingInterrupt);
  const learnerId = document.querySelector('#learner-id').value.trim();
  const requestText = document.querySelector('#user-request').value.trim();
  const planScopeHint = inferPlanScope(requestText);
  const planScope = pendingPlanScope;
  const inlineLongTermPlanText = extractInlineLongTermPlan(requestText);
  if (!pendingPlanChangeContext) lastSubmittedRequest = requestText;
  const longTermPlanText = document.querySelector('#long-term-plan').value.trim();
  const shortTermPlanText = document.querySelector('#short-term-plan').value.trim();
  const packetField = document.querySelector('#learning-packet');
  const packetError = document.querySelector('#packet-error');
  packetError.textContent = '';
  packetField.removeAttribute('aria-invalid');
  if (!learnerId || !requestText) {
    form.reportValidity();
    return;
  }
  let learningPacket = {};
  const packetText = packetField.value.trim();
  if (packetText) {
    try {
      learningPacket = JSON.parse(packetText);
      if (!learningPacket || Array.isArray(learningPacket) || typeof learningPacket !== 'object') {
        throw new Error('顶层必须是 JSON 对象。');
      }
    } catch (error) {
      packetField.setAttribute('aria-invalid', 'true');
      packetError.textContent = `数据包格式错误：${error.message}`;
      document.querySelector('.advanced-input').open = true;
      packetField.focus();
      return;
    }
  }
  if (!isResume) {
    clearStoredRun();
    pendingThreadId = createThreadId();
    pipeline.innerHTML = '';
    modelIoList.innerHTML = '';
    liveSteps.clear();
    liveModelCalls.clear();
    resetGraphMonitor();
    modelIoSection.classList.add('hidden');
    persistRun({
      status: 'starting',
      thread_id: pendingThreadId,
      request: requestText,
    });
  }
  startGraphClock(!isResume);
  emptyState.style.display = 'none';
  resultSection.classList.add('hidden');
  document.querySelector('#plan-clarification').classList.add('hidden');
  setRunState('running', isResume ? '正在恢复 LangGraph 检查点…' : '正在连接事件流…');
  button.disabled = true;
  try {
    const allowedPacketKeys = [
      'user_profile', 'learning_profile', 'system_data', 'user_knowledge_state',
      'question_attempt', 'question_learning_stats', 'long_term_plan', 'short_term_plan',
      'learning_task', 'exam_constraints',
    ];
    const safePacket = Object.fromEntries(
      Object.entries(learningPacket).filter(([key]) => allowedPacketKeys.includes(key))
    );
    const planPayload = (content, current, packetPlan = {}) => {
      if (current?.content === content) return current;
      if (packetPlan?.content === content) return packetPlan;
      return content ? { content, status: 'active' } : {};
    };
    const requestUrl = isResume
      ? `/api/v1/review-cards/runs/${encodeURIComponent(pendingThreadId)}/resume/stream`
      : '/api/v1/review-cards/stream';
    const requestBody = isResume
      ? {
          answer: pendingPlanChangeContext.change_details,
          plan_scope: planScope,
          plan_change_context: pendingPlanChangeContext,
        }
      : {
        thread_id: pendingThreadId,
        learner_id: learnerId,
        user_request: requestText,
        available_minutes: Number(minutes.value),
        messages: [{
          message_id: `DEMO_MSG_${Date.now()}`,
          role: 'user',
          content: requestText,
        }],
        ...safePacket,
        long_term_plan: planPayload(
          longTermPlanText || inlineLongTermPlanText,
          currentLongTermPlan,
          safePacket.long_term_plan,
        ),
        short_term_plan: planPayload(
          shortTermPlanText,
          currentShortTermPlan,
          safePacket.short_term_plan,
        ),
        learning_task: currentLearningTask || safePacket.learning_task || {},
        plan_scope: planScope,
        plan_scope_hint: planScopeHint,
        plan_change_context: pendingPlanChangeContext,
      };
    const response = await fetch(requestUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody),
    });
    const body = await consumeSse(response);
    if (body?.status === 'interrupted') {
      renderWorkflowInterrupt(body);
      setRunState('waiting', 'LangGraph 已中断 · 等待回答');
      resumeRequested = false;
      return;
    }
    renderResults(body);
    setRunState('done', `完成 · ${liveSteps.size} 个步骤`);
    stopGraphClock();
    addGraphEvent('执行图到达成功终态', 'success');
    clearStoredRun();
  } catch (error) {
    if (pendingThreadId && await restorePendingRun()) {
      stopGraphClock();
      return;
    }
    setRunState('error', '运行失败');
    stopGraphClock();
    graphNodeElements.forEach(node => {
      if (node.classList.contains('running') || node.classList.contains('retrying')) {
        node.classList.remove('running', 'retrying');
        node.classList.add('failed');
      }
    });
    addGraphEvent(`执行终止：${error.message}`, 'error');
    markInterruptedCalls();
    const article = document.createElement('article');
    article.className = 'step audit';
    article.style.cssText = 'opacity:1;transform:none';
    const index = document.createElement('div');
    index.className = 'step-index';
    index.textContent = '!';
    const content = document.createElement('div');
    const title = document.createElement('h4');
    title.textContent = '执行异常';
    const message = document.createElement('p');
    message.textContent = error.message;
    content.append(title, message);
    article.append(index, content);
    pipeline.appendChild(article);
    article.setAttribute('role', 'alert');
    article.tabIndex = -1;
    article.focus();
  } finally {
    resumeRequested = false;
    button.disabled = false;
  }
});
