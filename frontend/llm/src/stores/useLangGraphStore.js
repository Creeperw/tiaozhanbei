import { create } from 'zustand';

/** @typedef {'idle'|'running'|'done'|'error'|'rollingBack'|'archived'} NodeStatus */
/** @typedef {{ id:string, name:string, args?:object, resultSnippet?:string, status:NodeStatus, startTime:number, endTime?:number }} ToolCall */
/** @typedef {{ id:string, label:string, intent:string, status:NodeStatus, startTime:number, endTime?:number, resultSnippet?:string }} IntentCall */
/** @typedef {{ id:string, name:string, status:NodeStatus, startTime:number, endTime?:number, logs:string[], tools:ToolCall[], intents:IntentCall[], outputSnippet?:string, error?:string, archived?:boolean }} ExecutionNode */
/** @typedef {{ type:string, title?:string, text?:string, name?:string, query?:string, intent?:string, approved?:boolean }} LangGraphEvent */

const NODE_MAP = {
  context: 'InfoManager',
  memory: 'InfoManager',
  planning: 'Planner',
  intent: 'Planner',
  tool: 'Planner',
  refine: 'InfoManager',
  execution: 'Executor',
  feedback: 'Feedback',
};

const now = () => Date.now();
const eventTime = (ev) => Number.isFinite(ev?.ts) ? ev.ts : now();
const newNode = (id, name, text = '', ts = now()) => ({ id, name, status: 'running', startTime: ts, logs: text ? [text] : [], tools: [], intents: [] });

export const useLangGraphStore = create((set, get) => ({
  nodes: [],
  currentActiveNodeId: null,
  isRollingBack: false,
  finalAnswerId: null,
  finalAnswerContent: '',
  references: [],
  showPreliminaryAnswer: true,

  resetWorkflow: () => set({ nodes: [], currentActiveNodeId: null, isRollingBack: false, finalAnswerId: null, finalAnswerContent: '', references: [] }),
  setReferences: (references) => set({ references: references || [] }),
  appendAnswer: (delta) => set(state => ({ finalAnswerContent: state.finalAnswerContent + delta })),

  dispatchEvent: (event) => set(state => reduceLangGraphEvent(state, event)),

  mockPlannerTools: () => {
    get().resetWorkflow();
    ['context_start','context_done','planning_start'].forEach(type => get().dispatchEvent({ type, text: type }));
    get().dispatchEvent({ type: 'tool_start', name: 'search_rag', query: '高血压饮食' });
    get().dispatchEvent({ type: 'tool_done', name: 'search_rag', query: '高血压饮食', text: '低盐、DASH 饮食、规律运动。' });
    get().dispatchEvent({ type: 'tool_start', name: 'search_food_web', query: 'DASH diet' });
    get().dispatchEvent({ type: 'tool_done', name: 'search_food_web', query: 'DASH diet', text: '蔬果、全谷物、低脂乳制品。' });
    get().dispatchEvent({ type: 'planning_done', text: '信息足够。' });
  },
  mockFeedbackFail: () => {
    get().dispatchEvent({ type: 'feedback_start', text: '开始审核' });
    get().dispatchEvent({ type: 'feedback_done', approved: false, text: '包含过度承诺，触发回滚。' });
  },
  markNetworkInterrupted: (reason = '网络中断，事件流不完整。') => set(state => ({
    nodes: state.nodes.map(n => n.status === 'running' ? { ...n, status: 'error', endTime: now(), error: reason } : n),
    currentActiveNodeId: null,
  })),
}));

function finishNode(nodes, id, patch = {}, ts = now()) {
  return nodes.map(n => n.id === id ? { ...n, ...patch, status: patch.status || 'done', endTime: n.endTime || ts } : n);
}

function upsertNode(nodes, id, name, text, status = 'running', ts = now()) {
  const idx = nodes.findIndex(n => n.id === id);
  if (idx < 0) return [...nodes, { ...newNode(id, name, text, ts), status }];
  const node = nodes[idx];
  const logs = text ? [...node.logs, text] : node.logs;
  const next = [...nodes];
  next[idx] = { ...node, status, logs, endTime: status === 'done' ? ts : node.endTime };
  return next;
}

export function reduceLangGraphEvent(state, ev) {
  let nodes = state.nodes;
  let currentActiveNodeId = state.currentActiveNodeId;
  let isRollingBack = state.isRollingBack;
  const ts = eventTime(ev);

  if (ev.type === 'context_start') { nodes = upsertNode(nodes, 'context', NODE_MAP.context, ev.text, 'running', ts); currentActiveNodeId = 'context'; }
  else if (ev.type === 'context_done') { nodes = finishNode(upsertNode(nodes, 'context', NODE_MAP.context, ev.text, 'running', ts), 'context', {}, ts); }
  else if (ev.type === 'memory_start') { nodes = upsertNode(nodes, 'memory', NODE_MAP.memory, ev.text, 'running', ts); currentActiveNodeId = 'memory'; }
  else if (ev.type === 'memory_done') { nodes = finishNode(upsertNode(nodes, 'memory', NODE_MAP.memory, ev.text, 'running', ts), 'memory', {}, ts); }
  else if (ev.type === 'planning_start') { nodes = upsertNode(nodes, 'planner', NODE_MAP.planning, ev.text, 'running', ts); currentActiveNodeId = 'planner'; }
  else if (ev.type === 'planning_delta') { nodes = upsertNode(nodes, 'planner', NODE_MAP.planning, ev.text, 'running', ts); currentActiveNodeId = 'planner'; }
  else if (ev.type === 'intent') {
    nodes = upsertNode(nodes, 'planner', NODE_MAP.planning, '', 'running', ts);
    nodes = nodes.map(n => n.id === 'planner' ? {
      ...n,
      intents: [...(n.intents || []), {
        id: `intent-${(n.intents || []).length}-${ts}`,
        label: 'IntentClassifier',
        intent: ev.intent || '其他',
        status: 'done',
        startTime: ts,
        endTime: ts,
        resultSnippet: ev.text || `识别意图：${ev.intent || '其他'}`,
      }],
    } : n);
    currentActiveNodeId = 'planner';
  }
  else if (ev.type === 'tool_start') {
    nodes = upsertNode(nodes, 'planner', NODE_MAP.planning, '发起工具调用', 'running', ts);
    nodes = nodes.map(n => n.id === 'planner' ? { ...n, tools: [...n.tools, { id: `${ev.name}-${n.tools.length}-${ts}`, name: ev.name || 'tool', args: { query: ev.query || '' }, status: 'running', startTime: ts }] } : n);
    currentActiveNodeId = 'planner';
  } else if (ev.type === 'tool_done') {
    nodes = nodes.map(n => n.id === 'planner' ? { ...n, tools: n.tools.map((t, idx) => idx === n.tools.findIndex(x => x.status === 'running') ? { ...t, status: 'done', endTime: ts, resultSnippet: ev.text || '' } : t), logs: [...n.logs, `工具返回：${ev.name || ''}`] } : n);
  } else if (ev.type === 'planning_done') { nodes = finishNode(upsertNode(nodes, 'planner', NODE_MAP.planning, ev.text, 'running', ts), 'planner', {}, ts); }
  else if (ev.type === 'refine_start') { nodes = upsertNode(nodes, 'refine', NODE_MAP.refine, ev.text, 'running', ts); currentActiveNodeId = 'refine'; }
  else if (ev.type === 'refine_done') { nodes = finishNode(upsertNode(nodes, 'refine', NODE_MAP.refine, ev.text, 'running', ts), 'refine', {}, ts); }
  else if (ev.type === 'execution_start') { nodes = upsertNode(nodes, 'executor', NODE_MAP.execution, ev.text, 'running', ts); currentActiveNodeId = 'executor'; }
  else if (ev.type === 'execution_delta') { nodes = upsertNode(nodes, 'executor', NODE_MAP.execution, ev.text, 'running', ts); }
  else if (ev.type === 'execution_done') { nodes = finishNode(upsertNode(nodes, 'executor', NODE_MAP.execution, ev.text, 'running', ts), 'executor', {}, ts); }
  else if (ev.type === 'feedback_start') { nodes = upsertNode(nodes, 'feedback', NODE_MAP.feedback, ev.text, 'running', ts); currentActiveNodeId = 'feedback'; }
  else if (ev.type === 'feedback_regenerate') {
    isRollingBack = true;
    nodes = nodes.map(n => n.id === 'executor' ? { ...n, archived: true, status: 'archived' } : n);
    nodes = upsertNode(nodes, `feedback-${ts}`, NODE_MAP.feedback, ev.text, 'rollingBack', ts);
  } else if (ev.type === 'feedback_done') {
    if (ev.approved === false) {
      isRollingBack = true;
      nodes = upsertNode(nodes, 'feedback', NODE_MAP.feedback, ev.text, 'error', ts);
      nodes = nodes.map(n => n.id === 'feedback' ? { ...n, error: ev.text || '审核失败' } : n);
    } else {
      nodes = finishNode(upsertNode(nodes, 'feedback', NODE_MAP.feedback, ev.text, 'running', ts), 'feedback', {}, ts);
      isRollingBack = false;
    }
  } else if (ev.type === 'workflow_done') {
    nodes = nodes.map(n => n.status === 'running' ? { ...n, status: 'done', endTime: ts, tools: n.tools.map(t => t.status === 'running' ? { ...t, status: 'done', endTime: ts } : t), intents: (n.intents || []).map(t => t.status === 'running' ? { ...t, status: 'done', endTime: ts } : t) } : n);
    currentActiveNodeId = null;
  }
  return { ...state, nodes, currentActiveNodeId, isRollingBack };
}

/** Convert a persisted event list into message-level execution nodes. */
export function buildTraceFromEvents(events = [], options = {}) {
  const state = events.reduce((acc, event, idx) => reduceLangGraphEvent(acc, event.ts ? event : { ...event, ts: now() - (events.length - idx) * 120 }), {
    nodes: [],
    currentActiveNodeId: null,
    isRollingBack: false,
    finalAnswerId: null,
    finalAnswerContent: '',
    references: [],
    showPreliminaryAnswer: true,
  });
  if (!options.historical) return state.nodes;
  const endTs = Math.max(...state.nodes.map(n => n.endTime || n.startTime || now()), now());
  return state.nodes.map(n => n.status === 'running' ? { ...n, status: 'done', endTime: n.endTime || endTs, tools: n.tools.map(t => t.status === 'running' ? { ...t, status: 'done', endTime: t.endTime || endTs } : t), intents: (n.intents || []).map(t => t.status === 'running' ? { ...t, status: 'done', endTime: t.endTime || endTs } : t) } : n);
}
